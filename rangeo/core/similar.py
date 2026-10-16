"""Doublons « ressemblants » : images, musiques et vidéos qui ne sont pas identiques octet par octet.

* Images : empreinte visuelle (dHash 128 bits, insensible au redimensionnement et à la recompression).
* Musiques : empreinte audio (≈ Haitsma-Kalker) calculée sur les 40 premières secondes : reconnaît le
  même enregistrement en MP3, FLAC, AAC, à un autre débit…
* Vidéos : empreintes visuelles de 5 images clés + durée.

Tout est calculé localement ; les empreintes sont gardées dans l'index pour les recherches suivantes.
"""

from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .fsutil import disk_kind, xp
from .models import Cancelled, FileEntry, TaskControl

log = logging.getLogger(__name__)

# seuils de ressemblance selon la sensibilité choisie
IMAGE_THRESHOLDS = {"strict": 6, "normal": 14, "loose": 24}  # bits différents sur 128
VIDEO_THRESHOLDS = {"strict": 5, "normal": 9, "loose": 14}  # bits différents (moyenne) sur 64
AUDIO_THRESHOLDS = {"strict": 0.15, "normal": 0.25, "loose": 0.32}  # taux d'erreur binaire

AUDIO_RATE = 11025
AUDIO_SECONDS = 40
VIDEO_POINTS = (0.1, 0.3, 0.5, 0.7, 0.9)


# ---------------------------------------------------------------------------
# Empreintes
# ---------------------------------------------------------------------------
def _dhash_pil(img) -> int:
    """dHash 128 bits (64 horizontaux + 64 verticaux) d'une image PIL."""
    from PIL import Image

    g = img.convert("L")
    h = g.resize((9, 8), Image.Resampling.BILINEAR, reducing_gap=2.0)
    v = g.resize((8, 9), Image.Resampling.BILINEAR, reducing_gap=2.0)
    hp, vp = h.tobytes(), v.tobytes()
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (hp[row * 9 + col] < hp[row * 9 + col + 1])
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (vp[row * 8 + col] < vp[(row + 1) * 8 + col])
    return value


def image_signature(path: str) -> int | None:
    from PIL import Image, ImageOps

    from .metadata import _ensure_pil, too_big_to_decode

    _ensure_pil()
    try:
        with Image.open(xp(path)) as im:
            try:
                im.draft("L", (128, 128))  # JPEG : décodage directement en petit (très rapide)
            except Exception:
                pass
            if too_big_to_decode(im):
                log.debug("Image trop grande pour une empreinte : %s %s", path, im.size)
                return None
            try:
                im = ImageOps.exif_transpose(im)
            except Exception:
                pass
            return _dhash_pil(im)
    except Exception as exc:
        log.debug("Empreinte image impossible %s : %s", path, exc)
        return None


def video_signature(path: str) -> tuple[float, list[int], int] | None:
    """(durée en s, empreintes 64 bits de 5 images, largeur×hauteur)."""
    import av

    try:
        with av.open(xp(path)) as container:
            if not container.streams.video:
                return None
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            duration = None
            if stream.duration and stream.time_base:
                duration = float(stream.duration * stream.time_base)
            elif container.duration:
                duration = container.duration / 1_000_000
            if not duration or duration <= 0:
                return None
            hashes = []
            for frac in VIDEO_POINTS:
                target = int((duration * frac) / stream.time_base) + (stream.start_time or 0)
                container.seek(target, stream=stream, backward=True, any_frame=False)
                for frame in container.decode(stream):
                    img = frame.to_image()
                    hashes.append(_dhash_pil(img) >> 64)  # partie horizontale (64 bits)
                    break
            if len(hashes) < 3:
                return None
            size = (stream.codec_context.width or 0) * (stream.codec_context.height or 0)
            return round(duration, 2), hashes, size
    except Exception as exc:
        log.debug("Empreinte vidéo impossible %s : %s", path, exc)
        return None


def _band_edges(n_fft: int, rate: int, bands: int = 33, lo: float = 300.0, hi: float = 3000.0):
    import numpy as np

    freqs = np.geomspace(lo, hi, bands + 1)
    return np.clip(np.round(freqs * n_fft / rate).astype(int), 1, n_fft // 2)


def audio_fingerprint(path: str) -> tuple[float, "object"] | None:
    """(durée, tableau uint32 : une valeur de 32 bits par trame de 46 ms)."""
    import av
    import numpy as np

    try:
        with av.open(xp(path)) as container:
            if not container.streams.audio:
                return None
            stream = container.streams.audio[0]
            duration = None
            if stream.duration and stream.time_base:
                duration = float(stream.duration * stream.time_base)
            elif container.duration:
                duration = container.duration / 1_000_000
            resampler = av.AudioResampler(format="s16", layout="mono", rate=AUDIO_RATE)
            chunks, total, wanted = [], 0, AUDIO_RATE * AUDIO_SECONDS
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    arr = out.to_ndarray().reshape(-1)
                    chunks.append(arr)
                    total += arr.size
                if total >= wanted:
                    break
            if not chunks:
                return None
            samples = np.concatenate(chunks)[:wanted].astype(np.float32)
            if duration is None:
                duration = samples.size / AUDIO_RATE
    except Exception as exc:
        log.debug("Empreinte audio impossible %s : %s", path, exc)
        return None
    n_fft, hop = 2048, 512
    if samples.size < n_fft * 4:
        return None
    window = np.hanning(n_fft).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(samples, n_fft)[::hop] * window
    spectrum = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    edges = _band_edges(n_fft, AUDIO_RATE)
    energy = np.stack([spectrum[:, edges[i]:max(edges[i] + 1, edges[i + 1])].sum(axis=1)
                       for i in range(len(edges) - 1)], axis=1)
    energy = np.log1p(energy)
    diff = energy[:, :-1] - energy[:, 1:]
    bits = (diff[1:] - diff[:-1]) > 0  # (trames-1) × 32
    weights = (1 << np.arange(31, -1, -1, dtype=np.uint64)).astype(np.uint64)
    fp = (bits.astype(np.uint64) * weights).sum(axis=1).astype(np.uint32)
    return round(float(duration), 2), fp


def audio_distance(a, b, max_shift: int = 16) -> float:
    """Taux d'erreur binaire minimal entre deux empreintes (avec un léger décalage possible)."""
    import numpy as np

    best = 1.0
    for shift in range(-max_shift, max_shift + 1):
        if shift >= 0:
            x, y = a[shift:], b
        else:
            x, y = a, b[-shift:]
        n = min(len(x), len(y))
        if n < 50:
            continue
        err = np.bitwise_count(np.bitwise_xor(x[:n], y[:n])).sum() / (32.0 * n)
        if err < best:
            best = err
    return float(best)


# ---------------------------------------------------------------------------
# Sérialisation (cache dans l'index)
# ---------------------------------------------------------------------------
def encode_sig(mode: str, sig) -> str:
    if mode == "images":
        return f"i:{sig:032x}"
    if mode == "videos":
        duration, hashes, size = sig
        return f"v:{duration}:{size}:" + ",".join(f"{h:016x}" for h in hashes)
    duration, fp = sig
    return f"a:{duration}:" + base64.b64encode(fp.tobytes()).decode()


def decode_sig(mode: str, text: str):
    import numpy as np

    try:
        if mode == "images" and text.startswith("i:"):
            return int(text[2:], 16)
        if mode == "videos" and text.startswith("v:"):
            _, duration, size, hashes = text.split(":", 3)
            return float(duration), [int(h, 16) for h in hashes.split(",")], int(size)
        if mode == "music" and text.startswith("a:"):
            _, duration, data = text.split(":", 2)
            return float(duration), np.frombuffer(base64.b64decode(data), dtype=np.uint32)
    except (ValueError, IndexError):
        return None
    return None


# ---------------------------------------------------------------------------
# Regroupement
# ---------------------------------------------------------------------------
class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self) -> list[list[int]]:
        out: dict[int, list[int]] = {}
        for i in range(len(self.parent)):
            out.setdefault(self.find(i), []).append(i)
        return [g for g in out.values() if len(g) > 1]


def _image_pairs(sigs: list[int], threshold: int, control: TaskControl) -> list[tuple[int, int, int]]:
    """Comparaison de toutes les paires (vectorisée) : exacte quel que soit le seuil."""
    import numpy as np

    n = len(sigs)
    hi = np.array([(s >> 64) & 0xFFFFFFFFFFFFFFFF for s in sigs], dtype=np.uint64)
    lo = np.array([s & 0xFFFFFFFFFFFFFFFF for s in sigs], dtype=np.uint64)
    pairs = []
    step = 256
    control.set_stage("similar_compare", total=n)
    for start in range(0, n, step):
        control.checkpoint()
        stop = min(n, start + step)
        d = (np.bitwise_count(hi[start:stop, None] ^ hi[None, :]) +
             np.bitwise_count(lo[start:stop, None] ^ lo[None, :]))
        rows, cols = np.nonzero(d <= threshold)
        for r, c in zip(rows.tolist(), cols.tolist()):
            i = start + r
            if c > i:
                pairs.append((i, c, int(d[r, c])))
        control.advance(stop - start)
    return pairs


def _window_pairs(durations: list[float], tolerance) -> list[tuple[int, int]]:
    """Paires de fichiers de durées voisines (pour limiter les comparaisons coûteuses)."""
    order = sorted(range(len(durations)), key=lambda i: durations[i])
    out = []
    for a_pos, i in enumerate(order):
        di = durations[i]
        for j in order[a_pos + 1:]:
            if durations[j] - di > tolerance(di):
                break
            out.append((i, j) if i < j else (j, i))
    return out


@dataclass
class SimilarOptions:
    mode: str = "images"  # images | music | videos
    sensitivity: str = "normal"
    threads: int | None = None
    min_size: int = 1


MODE_GROUPS = {"images": "image", "music": "audio", "videos": "video"}


def find_similar(entries: list[FileEntry], opt: SimilarOptions, control: TaskControl | None = None,
                 index=None) -> list[list[FileEntry]]:
    """Groupes de fichiers ressemblants."""
    from . import filetypes as ft
    from .fsutil import is_cloud_placeholder

    control = control or TaskControl()
    group = MODE_GROUPS[opt.mode]
    files = [e for e in entries if not e.is_bundle and e.size >= opt.min_size and ft.group_of(e.kind) == group
             and not is_cloud_placeholder(e.attrs)]
    if opt.mode == "images":
        files = [e for e in files if e.ext not in ft.RAW_EXT or e.ext in ("dng",)]
    if len(files) < 2:
        return []
    cached = {}
    if index is not None:
        try:
            cached = index.lookup_sigs([(e.path, e.size, e.mtime) for e in files])
        except Exception:
            log.exception("Index : lecture des empreintes")
    from .fsutil import nkey

    sigs: dict[int, object] = {}
    todo = []
    for e in files:
        text = cached.get(nkey(e.path))
        sig = decode_sig(opt.mode, text) if text else None
        if sig is not None:
            sigs[id(e)] = sig
        else:
            todo.append(e)

    compute = {"images": image_signature, "videos": video_signature, "music": audio_fingerprint}[opt.mode]
    threads = opt.threads or {"ssd": 8, "hdd": 2, "removable": 2, "network": 4}.get(disk_kind(files[0].dir), 4)
    control.set_stage("similar_sign", total=len(todo), bytes_total=0)
    new_rows = []

    def work(e):
        control.checkpoint()
        sig = compute(e.path)
        control.advance(1, current=e.path)
        return e, sig

    pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="empreinte")
    try:
        for e, sig in pool.map(work, todo):
            if sig is not None:
                sigs[id(e)] = sig
                new_rows.append((e.path, e.size, e.mtime, encode_sig(opt.mode, sig)))
            if index is not None and len(new_rows) >= 500:
                index.store_sigs(new_rows)
                new_rows = []
    except Cancelled:
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    if index is not None and new_rows:
        index.store_sigs(new_rows)

    usable = [e for e in files if id(e) in sigs]
    if len(usable) < 2:
        return []
    uf = _UnionFind(len(usable))
    if opt.mode == "images":
        for i, j, d in _image_pairs([sigs[id(e)] for e in usable], IMAGE_THRESHOLDS[opt.sensitivity], control):
            uf.union(i, j)
    elif opt.mode == "videos":
        vals = [sigs[id(e)] for e in usable]
        pairs = _window_pairs([v[0] for v in vals], lambda d: max(2.0, d * 0.02))
        control.set_stage("similar_compare", total=len(pairs))
        limit = VIDEO_THRESHOLDS[opt.sensitivity]
        for i, j in pairs:
            control.checkpoint()
            a, b = vals[i][1], vals[j][1]
            n = min(len(a), len(b))
            mean = sum(bin(a[k] ^ b[k]).count("1") for k in range(n)) / n
            if mean <= limit:
                uf.union(i, j)
            control.advance(1)
    else:
        vals = [sigs[id(e)] for e in usable]
        pairs = _window_pairs([v[0] for v in vals], lambda d: max(2.0, d * 0.03))
        control.set_stage("similar_compare", total=len(pairs))
        limit = AUDIO_THRESHOLDS[opt.sensitivity]
        for i, j in pairs:
            control.checkpoint()
            a, b = vals[i][1], vals[j][1]
            if audio_distance(a[:220], b[:220], 6) < 0.42 and audio_distance(a, b) <= limit:
                uf.union(i, j)
            control.advance(1)
    out = []
    for members in uf.groups():
        for i in members:
            if opt.mode == "videos":
                usable[i].meta.setdefault("pixels", sigs[id(usable[i])][2])
        out.append([usable[i] for i in members])
    return out


def quality_of(entry: FileEntry) -> float:
    """Valeur de qualité (plus grand = meilleur) pour choisir le fichier à garder."""
    meta = entry.meta
    if meta.get("width") and meta.get("height"):
        return float(meta["width"] * meta["height"])
    if meta.get("pixels"):
        return float(meta["pixels"])
    if meta.get("bitrate"):
        return float(meta["bitrate"])
    return float(entry.size)


__all__ = ["SimilarOptions", "find_similar", "quality_of", "image_signature", "audio_fingerprint",
           "video_signature"]
