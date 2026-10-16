"""Lecture des métadonnées : EXIF des photos, tags audio, dates des vidéos, analyse des noms."""

from __future__ import annotations

import datetime as _dt
import logging
import re
import struct
import time

from .fsutil import xp

log = logging.getLogger(__name__)

_MIN_TS = _dt.datetime(1985, 1, 1).timestamp()


def _max_ts() -> float:
    return time.time() + 86400 * 2


def _valid_ts(ts: float | None) -> float | None:
    if ts is None:
        return None
    return ts if _MIN_TS <= ts <= _max_ts() else None


def _parse_exif_datetime(value) -> float | None:
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    value = str(value).strip().replace("\x00", "")
    m = re.match(r"(\d{4})[:\-/](\d{2})[:\-/](\d{2})[ T]?(\d{2})?:?(\d{2})?:?(\d{2})?", value)
    if not m:
        return None
    try:
        parts = [int(x) if x else 0 for x in m.groups()]
        return _valid_ts(_dt.datetime(*parts).timestamp())
    except (ValueError, OverflowError, OSError):
        return None


def _clean_camera(make, model) -> str | None:
    make = str(make or "").replace("\x00", "").strip()
    model = str(model or "").replace("\x00", "").strip()
    if not make and not model:
        return None
    if model and make and model.lower().startswith(make.split()[0].lower()):
        return model
    if make and model:
        # « NIKON CORPORATION » → « NIKON »
        short = re.sub(r"\s+(CORPORATION|CORP\.?|COMPANY|CO\.,? ?LTD\.?|IMAGING|OPTICAL|INC\.?)$", "", make, flags=re.I)
        return f"{short} {model}".strip()
    return make or model


def _dms(values) -> float | None:
    try:
        vals = [float(v) for v in values]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not vals:
        return None
    vals += [0.0] * (3 - len(vals))
    return vals[0] + vals[1] / 60 + vals[2] / 3600


def gps_from_exif(lat_ref, lat, lon_ref, lon) -> tuple[float, float] | None:
    """Coordonnées décimales à partir des champs GPS de l'EXIF."""
    la, lo = _dms(lat), _dms(lon)
    if la is None or lo is None:
        return None
    if str(lat_ref or "N").strip("\x00 ").upper().startswith("S"):
        la = -la
    if str(lon_ref or "E").strip("\x00 ").upper().startswith("W"):
        lo = -lo
    if not (-90 <= la <= 90 and -180 <= lo <= 180) or (abs(la) < 1e-6 and abs(lo) < 1e-6):
        return None
    return round(la, 6), round(lo, 6)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
_PIL_READY = False


def _ensure_pil() -> None:
    global _PIL_READY
    if _PIL_READY:
        return
    from PIL import Image  # noqa: F401

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception:  # pragma: no cover - optionnel
        log.info("pillow-heif indisponible : les HEIC seront classés sans EXIF")
    Image.MAX_IMAGE_PIXELS = None  # lecture des dimensions / EXIF seulement : jamais de décodage complet ici
    _PIL_READY = True


# Au-delà, une image n'est pas décodée (empreinte, contrôle d'intégrité) : plusieurs Go de mémoire sinon,
# multipliés par le nombre de fils de calcul. Les JPEG sont d'abord réduits au décodage (draft).
MAX_DECODE_PIXELS = 80_000_000


def too_big_to_decode(im) -> bool:
    width, height = im.size
    return width * height > MAX_DECODE_PIXELS


def _tiff_exif(data: bytes, base: int = 0) -> dict:
    """Mini-lecteur EXIF/TIFF (photos RAW, et secours pour les JPEG)."""
    out: dict = {}
    try:
        order = data[base:base + 2]
        if order == b"II":
            e = "<"
        elif order == b"MM":
            e = ">"
        else:
            return out

        def u16(off):
            return struct.unpack_from(e + "H", data, base + off)[0]

        def u32(off):
            return struct.unpack_from(e + "I", data, base + off)[0]

        def read_ifd(off):
            entries = {}
            count = u16(off)
            if count > 512:
                return entries
            for i in range(count):
                p = off + 2 + i * 12
                tag, typ, cnt = u16(p), u16(p + 2), u32(p + 4)
                if typ == 2:  # ASCII
                    voff = p + 8 if cnt <= 4 else u32(p + 8)
                    entries[tag] = data[base + voff: base + voff + cnt].split(b"\x00")[0].decode("latin-1", "ignore")
                elif typ == 4:
                    entries[tag] = u32(p + 8)
                elif typ == 3:
                    entries[tag] = u16(p + 8)
                elif typ == 5 and cnt <= 8:  # RATIONAL
                    voff = u32(p + 8)
                    vals = []
                    for k in range(cnt):
                        num, den = u32(voff + k * 8), u32(voff + k * 8 + 4)
                        vals.append(num / den if den else 0.0)
                    entries[tag] = vals
            return entries

        ifd0 = read_ifd(u32(4))
        out["make"] = ifd0.get(271)
        out["model"] = ifd0.get(272)
        out["datetime"] = ifd0.get(306)
        exif_ptr = ifd0.get(0x8769)
        if exif_ptr:
            exif = read_ifd(exif_ptr)
            out["original"] = exif.get(36867) or exif.get(36868)
        gps_ptr = ifd0.get(0x8825)
        if gps_ptr:
            gps = read_ifd(gps_ptr)
            out["gps"] = gps_from_exif(gps.get(1), gps.get(2), gps.get(3), gps.get(4))
    except (struct.error, IndexError, ValueError):
        pass
    return out


def read_image_meta(path: str, ext: str) -> dict:
    """Date de prise de vue, appareil et dimensions."""
    from .filetypes import RAW_EXT

    meta: dict = {}
    if ext in RAW_EXT and ext not in ("cr3", "raf", "x3f"):
        try:
            with open(xp(path), "rb") as fh:
                data = fh.read(256 * 1024)
            info = _tiff_exif(data)
            meta["exif_date"] = _parse_exif_datetime(info.get("original") or info.get("datetime"))
            meta["camera"] = _clean_camera(info.get("make"), info.get("model"))
            if info.get("gps"):
                meta["gps"] = list(info["gps"])
        except OSError as exc:
            meta["error"] = str(exc)
        return {k: v for k, v in meta.items() if v is not None}

    _ensure_pil()
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(xp(path)) as im:
            meta["width"], meta["height"] = im.size
            meta["format"] = im.format
            exif = im.getexif()
            if exif:
                sub = {}
                try:
                    sub = exif.get_ifd(0x8769) or {}
                except Exception:
                    sub = {}
                meta["exif_date"] = _parse_exif_datetime(sub.get(36867) or sub.get(36868) or exif.get(306))
                meta["camera"] = _clean_camera(exif.get(271), exif.get(272))
                if exif.get(305):
                    meta["software"] = str(exif.get(305)).strip("\x00 ")[:60]
                try:
                    gps = exif.get_ifd(0x8825) or {}
                except Exception:
                    gps = {}
                if gps:
                    coords = gps_from_exif(gps.get(1), gps.get(2), gps.get(3), gps.get(4))
                    if coords:
                        meta["gps"] = list(coords)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, struct.error) as exc:
        meta["error"] = str(exc)[:200]
    except Exception as exc:  # fichiers corrompus : ne jamais planter
        meta["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return {k: v for k, v in meta.items() if v is not None}


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
def _first(tags, *keys):
    for key in keys:
        try:
            value = tags.get(key)
        except Exception:
            value = None
        if value:
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            if value is not None:
                text = str(value).replace("\x00", "").strip()
                if text:
                    return text
    return None


def _num(value) -> int | None:
    if value is None:
        return None
    m = re.match(r"\s*(\d+)", str(value))
    return int(m.group(1)) if m else None


def read_audio_meta(path: str) -> dict:
    import mutagen

    meta: dict = {}
    audio = None
    try:
        audio = mutagen.File(xp(path), easy=True)
    except Exception as exc:
        meta["error"] = str(exc)[:200]
    if audio is None and path.lower().endswith(".mp3"):
        try:
            from mutagen.easyid3 import EasyID3

            audio = EasyID3(xp(path))
        except Exception:
            audio = None
    if audio is None:
        return meta
    tags = audio.tags if hasattr(audio, "tags") and not isinstance(audio, dict) else audio
    if tags is None and hasattr(audio, "get"):
        tags = audio
    if tags is not None:
        meta["artist"] = _first(tags, "artist", "performer")
        meta["albumartist"] = _first(tags, "albumartist", "album artist", "band")
        meta["album"] = _first(tags, "album")
        meta["title"] = _first(tags, "title")
        meta["track"] = _num(_first(tags, "tracknumber", "track"))
        disc_raw = _first(tags, "discnumber", "disc")
        meta["disc"] = _num(disc_raw)
        if meta["disc"] is not None:
            # « 2/3 » ou étiquette séparée ; 0 = inconnu (évite de relire le fichier à chaque analyse)
            total = _num(str(disc_raw).partition("/")[2]) if "/" in str(disc_raw) else None
            meta["disc_total"] = total or _num(_first(tags, "disctotal", "totaldiscs")) or 0
        meta["genre"] = _first(tags, "genre")
        year = _num(_first(tags, "date", "originaldate", "year"))
        if year and 1000 <= year <= 2200:
            meta["year"] = year
    info = getattr(audio, "info", None)
    if info is not None and getattr(info, "length", None):
        meta["duration"] = round(float(info.length), 1)
    if info is not None and getattr(info, "bitrate", None):
        meta["bitrate"] = int(info.bitrate)
    return {k: v for k, v in meta.items() if v is not None}


# ---------------------------------------------------------------------------
# Vidéos (date de création stockée dans le conteneur MP4/MOV)
# ---------------------------------------------------------------------------
_MP4_EPOCH = 2082844800  # secondes entre 1904 et 1970


def read_video_meta(path: str, ext: str) -> dict:
    """Date de création, durée, dimensions et position GPS (MP4 / MOV / 3GP)."""
    meta: dict = {}
    if ext not in ("mp4", "m4v", "mov", "3gp", "3g2", "qt"):
        return meta
    try:
        with open(xp(path), "rb") as fh:
            fh.seek(0, 2)
            end = fh.tell()
            pos = 0
            for _ in range(64):  # nombre de boîtes de premier niveau parcourues
                if pos + 8 > end:
                    break
                fh.seek(pos)
                header = fh.read(16)
                size, typ = struct.unpack(">I4s", header[:8])
                hdr = 8
                if size == 1:
                    size = struct.unpack(">Q", header[8:16])[0]
                    hdr = 16
                elif size == 0:
                    size = end - pos
                if size < hdr:
                    break
                if typ == b"moov":
                    length = min(size - hdr, 32 * 1024 * 1024)
                    fh.seek(pos + hdr)
                    _parse_moov(fh.read(length), meta)
                    break
                pos += size
    except (OSError, struct.error) as exc:
        meta["error"] = str(exc)[:200]
    return meta


_ISO6709 = re.compile(rb"([+-]\d{1,2}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)(?:[+-]\d+(?:\.\d+)?)?(?:CRS[^/]*)?/")


def _boxes(data: bytes, start: int = 0, stop: int | None = None):
    pos, stop = start, len(data) if stop is None else stop
    while pos + 8 <= stop:
        size, typ = struct.unpack_from(">I4s", data, pos)
        hdr = 8
        if size == 1 and pos + 16 <= stop:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            hdr = 16
        elif size == 0:
            size = stop - pos
        if size < hdr:
            return
        yield typ, pos + hdr, min(pos + size, stop)
        pos += size


def _parse_moov(data: bytes, meta: dict) -> None:
    width = height = 0
    for typ, body, stop in _boxes(data):
        if typ == b"mvhd" and stop - body >= 32:
            version = data[body]
            if version == 1:
                created = struct.unpack_from(">Q", data, body + 4)[0]
                timescale, duration = struct.unpack_from(">IQ", data, body + 20)
            else:
                created = struct.unpack_from(">I", data, body + 4)[0]
                timescale, duration = struct.unpack_from(">II", data, body + 12)
            if created:
                meta["media_date"] = _valid_ts(created - _MP4_EPOCH)
            if timescale:
                meta["duration"] = round(duration / timescale, 1)
        elif typ == b"trak":
            for t2, b2, s2 in _boxes(data, body, stop):
                if t2 == b"tkhd" and s2 - b2 >= 84:
                    off = b2 + (92 if data[b2] == 1 else 80) - 8
                    w = struct.unpack_from(">I", data, off)[0] >> 16
                    h = struct.unpack_from(">I", data, off + 4)[0] >> 16
                    if w * h > width * height:
                        width, height = w, h
    if width and height:
        meta["width"], meta["height"] = width, height
    m = _ISO6709.search(data)
    if m:
        try:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180 and not (abs(lat) < 1e-6 and abs(lon) < 1e-6):
                meta["gps"] = [round(lat, 6), round(lon, 6)]
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Analyse des noms de fichiers
# ---------------------------------------------------------------------------
CAMERA_NAME_RE = re.compile(
    r"^(IMG|DSC|DSCN|DSCF|DSC_|PXL|MVI|VID|MOV|GOPR|GP\d|GH\d|GX\d|GS\d|DJI|P\d{7}|PANO|BURST|MMEXPORT|WP_\d|IMAG\d|"
    r"SAM_|CIMG|PICT|HPIM|KIF|_MG|_DSC|_IMG|PHOTO[-_ ]\d|WHATSAPP (IMAGE|VIDEO)|SNAPCHAT-|SIGNAL-|"
    r"(IMG|VID)-\d{8}-WA|\d{8}_\d{6}|\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2})",
    re.I,
)
SCREENSHOT_NAME_RE = re.compile(
    r"(screenshot|screen ?shot|capture d.?[ée]cran|^capture|bildschirmfoto|schermata|captura de pantalla|"
    r"^snip|screen[ _-]?recording|enregistrement d.?[ée]cran|^scr_|^screen_|^scrn|^snapshot|^schermafbeelding)",
    re.I,
)
RECORDING_NAME_RE = re.compile(
    r"(recording|enregistrement|voice|vocal|memo|m[ée]mo|^ptt-|^aud-|^rec_|^record|dictaphone|^audio_)", re.I
)
_DATE_IN_NAME_RE = re.compile(
    r"(?<!\d)(19[89]\d|20\d{2})[-_. ]?(0[1-9]|1[0-2])[-_. ]?(0[1-9]|[12]\d|3[01])(?!\d)"
    r"(?:[ _T\-.at]{0,4}([01]\d|2[0-3])[-_.h: ]?([0-5]\d)[-_.m: ]?([0-5]\d)?)?",
    re.I,
)

SCREEN_SIZES = {
    (1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (1280, 720), (1600, 900), (1440, 900), (1280, 800),
    (1680, 1050), (1920, 1200), (2560, 1600), (2880, 1800), (3024, 1964), (3456, 2234), (2736, 1824),
    (3000, 2000), (2256, 1504), (2560, 1080), (3440, 1440), (5120, 1440), (1280, 1024), (1024, 768),
    (1360, 768), (1536, 864), (2048, 1152), (2160, 1440), (2496, 1664), (2880, 1920), (3200, 1800),
    (1170, 2532), (1179, 2556), (1284, 2778), (1290, 2796), (1080, 2400), (1080, 2340), (1440, 3200),
    (750, 1334), (1242, 2688), (828, 1792), (1125, 2436), (1080, 1920), (1440, 2560), (1536, 2048),
    (1620, 2160), (2048, 2732), (1668, 2388), (1640, 2360), (1488, 2266), (1080, 2408), (1080, 2412),
    (1220, 2712), (1260, 2800), (1344, 2992), (1206, 2622), (1320, 2868), (720, 1600), (720, 1520),
}


def is_camera_name(stem: str) -> bool:
    return bool(CAMERA_NAME_RE.search(stem))


def is_screenshot_name(stem: str) -> bool:
    return bool(SCREENSHOT_NAME_RE.search(stem))


def is_recording_name(stem: str) -> bool:
    return bool(RECORDING_NAME_RE.search(stem))


def is_screen_size(width: int | None, height: int | None) -> bool:
    if not width or not height:
        return False
    return (width, height) in SCREEN_SIZES or (height, width) in SCREEN_SIZES


# « Scan 15-07-2023 », « Facture 03.04.2021 » : jour-mois-année (séparateurs obligatoires)
_DMY_RE = re.compile(
    r"(?<![\d.vV])(0?[1-9]|[12]\d|3[01])([-_.])(0?[1-9]|1[0-2]|1[3-9]|2\d|3[01])\2(19[89]\d|20\d{2})(?![\d.])"
)
# Messenger / Facebook : « received_1689412345678 », « FB_IMG_1689412345678 » (millisecondes)
_MS_STAMP_RE = re.compile(r"(?:^|[_\-\s])(?:fb_img|received)_(1[2-9]\d{11})(?!\d)", re.I)


def date_from_name(stem: str) -> float | None:
    """Date contenue dans le nom (IMG_20240312_143200, Screenshot 2024-03-12, Scan 15-07-2023…)."""
    for m in _DATE_IN_NAME_RE.finditer(stem):
        y, mo, d, hh, mm, ss = m.groups()
        try:
            dt = _dt.datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
        except ValueError:
            continue
        ts = _valid_ts(dt.timestamp())
        if ts:
            return ts
    for m in _DMY_RE.finditer(stem):
        a, _sep, b, y = m.groups()
        a, b = int(a), int(b)
        # jour en premier (usage français) ; mois en premier seulement si l'ordre inverse est impossible
        day, month = (b, a) if b > 12 else (a, b)
        if month > 12:
            continue
        try:
            ts = _valid_ts(_dt.datetime(int(y), month, day).timestamp())
        except ValueError:
            continue
        if ts:
            return ts
    m = _MS_STAMP_RE.search(stem)
    if m:
        return _valid_ts(int(m.group(1)) / 1000)
    return None


# --- Séries et films ---------------------------------------------------------
_SERIES_PATTERNS = [
    re.compile(r"^(?P<show>.*?)[\s._\-\[(]*\bS(?P<season>\d{1,2})[\s._-]*E(?P<ep>\d{1,3})(?!\d)", re.I),
    re.compile(r"^(?P<show>.*?)[\s._\-\[(]+(?P<season>\d{1,2})x(?P<ep>\d{2,3})(?!\d)", re.I),
    re.compile(
        r"^(?P<show>.*?)[\s._\-]*\b(?:saison|season|staffel|temporada)[\s._-]*(?P<season>\d{1,2})"
        r"[\s._\-,]*(?:e|ep|episode|épisode|folge)[\s._-]*(?P<ep>\d{1,3})(?!\d)",
        re.I,
    ),
]
_RELEASE_TAGS = re.compile(
    r"(?<![a-z0-9])(2160p|1080p|720p|576p|480p|4k|uhd|bluray|blu-ray|bdrip|brrip|bdremux|webrip|web-dl|webdl|"
    r"hdrip|dvdrip|dvdscr|hdtv|x264|x265|h\.?264|h\.?265|hevc|xvid|divx|10bit|hdr10?|remux|multi|vff|vfq|vf2|"
    r"vostfr|truefrench|french|subfrench|ac3|dts|aac|imax|extended|unrated|proper|repack|amzn|nf|dsnp|hmax)"
    r"(?![a-z0-9])",
    re.I,
)
_YEAR_TOKEN = re.compile(r"(?<![0-9a-z])[(\[]?(19[2-9]\d|20[0-4]\d)[)\]]?(?![0-9a-z])", re.I)
_SEASON_DIR = re.compile(r"^(saison|season|staffel|temporada|s)[\s._-]*\d{1,2}$", re.I)
_LEADING_TAG = re.compile(r"^\s*[\[(][^\])]{1,40}[\])]\s*")


def clean_title(text: str) -> str:
    text = _LEADING_TAG.sub("", text)
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s+-\s*$", "", text)
    text = re.sub(r"[\s\-\[(]+$", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -")
    if text and (text.islower() or text.isupper()):
        text = text.title()
    return text


def parse_series(stem: str, parent_dir: str = "", grand_dir: str = "") -> dict | None:
    for pattern in _SERIES_PATTERNS:
        m = pattern.search(stem)
        if not m:
            continue
        show = clean_title(m.group("show"))
        if not show:
            folder = parent_dir
            if _SEASON_DIR.match(folder or ""):
                folder = grand_dir
            show = clean_title(folder or "")
        return {"series": show or None, "season": int(m.group("season")), "episode": int(m.group("ep"))}
    return None


def parse_movie(stem: str, size: int) -> dict | None:
    """Reconnaît « Titre (2010) » ou « Titre.2010.1080p.BluRay ». Prudent pour éviter « Vacances 2019 »."""
    tags = _RELEASE_TAGS.search(stem)
    head = stem[: tags.start()] if tags else stem
    big = size >= 300 * 1024 * 1024
    best = None
    for m in _YEAR_TOKEN.finditer(head):  # on garde la dernière année (« Blade Runner 2049 (2017) »)
        title = clean_title(head[: m.start()])
        if title:
            best = (title, int(m.group(1)), m.group(0)[0] in "([")
    if best:
        title, year, in_parens = best
        if tags or (in_parens and big):
            return {"title": title, "release_year": year}
    if tags and big:
        title = clean_title(head)
        if title:
            return {"title": title}
    return None
