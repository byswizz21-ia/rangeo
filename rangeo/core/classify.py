"""Analyse des fichiers : vrai type, métadonnées, type détaillé et date de référence."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import filetypes as ft
from . import metadata as md
from .fsutil import disk_kind, is_cloud_placeholder, nkey
from .index import MetaIndex
from .models import Cancelled, FileEntry, TaskControl

log = logging.getLogger(__name__)

DATE_SOURCES = ["metadata", "filename", "oldest", "created", "modified"]


@dataclass
class ClassifyOptions:
    deep_check: bool = False  # vérifier le vrai type de TOUS les fichiers (plus lent)
    read_metadata: bool = True
    date_order: list[str] = field(default_factory=lambda: ["metadata", "filename", "oldest"])
    threads: int | None = None
    low_priority: bool = False


def _media_base(ext: str) -> str:
    return ft.kind_from_ext(ext)


def inspect_file(entry: FileEntry, need_sniff: bool, need_meta: bool) -> tuple[str | None, dict]:
    """Lit le fichier (en-tête et/ou métadonnées). Ne lève jamais d'exception."""
    path = entry.path
    real: str | None = None
    meta: dict = {}
    try:
        if need_sniff:
            real = ft.sniff(ft.read_head(path))
        ext = entry.ext
        if real and (not ext or ft.kind_from_ext(ext) == "unknown" or not ft.compatible(ext, real)):
            ext = real
        base = _media_base(ext)
        if need_meta or base in ("image", "video", "audio"):
            if base == "image":
                meta = md.read_image_meta(path, ext)
            elif base == "video":
                meta = md.read_video_meta(path, ext)
            elif base == "audio":
                meta = md.read_audio_meta(path)
            if meta.get("error") and not need_sniff:
                # le fichier ne correspond peut-être pas à son extension
                real = ft.sniff(ft.read_head(path))
                if real and not ft.compatible(entry.ext, real):
                    meta = {}
                    base2 = _media_base(real)
                    if base2 == "image":
                        meta = md.read_image_meta(path, real)
                    elif base2 == "video":
                        meta = md.read_video_meta(path, real)
                    elif base2 == "audio":
                        meta = md.read_audio_meta(path)
    except OSError as exc:
        meta = {"error": exc.strerror or str(exc)}
    except Exception as exc:  # fichier corrompu, format exotique…
        log.debug("Analyse impossible pour %s", path, exc_info=True)
        meta = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    return real, meta


# format détecté par Pillow -> extension (permet de repérer un .jpg qui est en fait un .png sans relire le fichier)
_PIL_FORMATS = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp", "TIFF": "tif", "BMP": "bmp",
                "HEIF": "heic", "AVIF": "avif", "ICO": "ico", "PSD": "psd"}


def apply_inspection(entry: FileEntry, real: str | None, meta: dict | None) -> None:
    if not real and meta and meta.get("format") in _PIL_FORMATS:
        real = _PIL_FORMATS[meta["format"]]
    entry.real_ext = real or None
    if real and entry.ext and ft.kind_from_ext(entry.ext) != "unknown" and not ft.compatible(entry.ext, real):
        entry.ext_mismatch = True
    if meta:
        entry.meta.update(meta)


def effective_ext(entry: FileEntry) -> str:
    if entry.real_ext and (entry.ext_mismatch or not entry.ext or ft.kind_from_ext(entry.ext) == "unknown"):
        return entry.real_ext
    return entry.ext


def refine_kind(entry: FileEntry) -> str:
    """Type détaillé : photo / capture d'écran / film / épisode / musique…"""
    if entry.is_bundle:
        return entry.kind
    ext = effective_ext(entry)
    base = ft.kind_from_ext(ext)
    stem = entry.stem
    meta = entry.meta
    if base == "image":
        if md.is_screenshot_name(stem):
            return "screenshot"
        if ext in ft.RAW_EXT or ext in ("heic", "heif"):
            return "photo"
        if meta.get("exif_date") or meta.get("camera"):
            return "photo"
        if md.is_camera_name(stem):
            return "photo"
        if ext == "png" and md.is_screen_size(meta.get("width"), meta.get("height")):
            return "screenshot"
        return "image"
    if base == "video":
        parent = os.path.basename(entry.dir)
        grand = os.path.basename(os.path.dirname(entry.dir))
        series = md.parse_series(stem, parent, grand)
        if series:
            meta.update(series)
            return "episode"
        movie = md.parse_movie(stem, entry.size)
        if movie:
            meta.update(movie)
            return "movie"
        if md.is_screenshot_name(stem):
            return "home_video"
        if md.is_camera_name(stem) or md.date_from_name(stem) or ext in ("mts", "m2ts", "3gp", "3g2"):
            return "home_video"
        return "video"
    if base == "rom":
        meta["console"] = ft.ROM_CONSOLES.get(ext)
        return "rom"
    if base == "audio":
        if md.is_recording_name(stem):
            return "audio"
        if meta.get("artist") or meta.get("albumartist") or meta.get("album"):
            return "music"
        return "audio"
    return base


def compute_date(entry: FileEntry, order: list[str]) -> None:
    meta = entry.meta
    for source in order:
        ts = None
        if source == "metadata":
            ts = meta.get("exif_date") or meta.get("media_date")
        elif source == "filename":
            ts = md.date_from_name(entry.stem)
        elif source == "oldest":
            candidates = [t for t in (entry.ctime, entry.mtime) if t and t > 0]
            ts = min(candidates) if candidates else None
        elif source == "created":
            ts = entry.ctime or None
        elif source == "modified":
            ts = entry.mtime or None
        if ts:
            entry.date = ts
            entry.date_source = source
            return
    entry.date = entry.mtime or entry.ctime or None
    entry.date_source = "modified"


def classify(
    entries: list[FileEntry],
    options: ClassifyOptions,
    control: TaskControl | None = None,
    index: MetaIndex | None = None,
) -> None:
    """Complète les entrées (en place) : vrai type, métadonnées, type détaillé, date."""
    control = control or TaskControl()
    todo: list[tuple[FileEntry, bool, bool]] = []
    for e in entries:
        if e.is_bundle:
            continue
        if is_cloud_placeholder(e.attrs):
            e.meta["cloud"] = True  # ne pas lire : cela déclencherait un téléchargement
            continue
        base = ft.kind_from_ext(e.ext)
        need_sniff = options.deep_check or not e.ext or base == "unknown"
        need_meta = options.read_metadata and base in ("image", "video", "audio")
        if need_sniff or need_meta:
            todo.append((e, need_sniff, need_meta))

    control.set_stage("analyze", total=len(todo))
    cached: dict = {}
    if index is not None and todo:
        try:
            cached = index.lookup([(e.path, e.size, e.mtime) for e, _, _ in todo])
        except Exception:
            log.exception("Index illisible, analyse complète")
            cached = {}

    remaining: list[tuple[FileEntry, bool, bool]] = []
    for item in todo:
        e = item[0]
        hit = cached.get(nkey(e.path))
        if hit is not None and hit[1] is not None:
            apply_inspection(e, hit[0] or None, hit[1])
            control.advance(1)
        else:
            remaining.append(item)

    if remaining:
        threads = options.threads or _auto_threads(remaining[0][0].dir)
        new_rows: list = []

        def work(item):
            control.checkpoint()
            if options.low_priority:
                from .fsutil import set_thread_background

                set_thread_background(True)
            entry, sniff, meta_needed = item
            real, meta = inspect_file(entry, sniff, meta_needed)
            control.advance(1, current=entry.path)
            return entry, real, meta

        pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="analyse")
        try:
            for entry, real, meta in pool.map(work, remaining):
                apply_inspection(entry, real, meta)
                if not meta.get("error"):
                    new_rows.append((entry.path, entry.size, entry.mtime, real, meta))
                if index is not None and len(new_rows) >= 2000:
                    index.store_meta(new_rows)
                    new_rows = []
        except Cancelled:
            pool.shutdown(wait=True, cancel_futures=True)
            if index is not None and new_rows:
                index.store_meta(new_rows)
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        if index is not None and new_rows:
            index.store_meta(new_rows)

    for e in entries:
        if not e.is_bundle:
            e.kind = refine_kind(e)
        compute_date(e, options.date_order)


def _auto_threads(sample_dir: str) -> int:
    kind = disk_kind(sample_dir)
    return {"ssd": 16, "hdd": 4, "removable": 4, "network": 8}.get(kind, 8)
