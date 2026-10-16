"""Analyse d'un disque ou d'un dossier : où va la place, quoi nettoyer.

Lecture seule : rien n'est modifié. Les nettoyages proposés passent ensuite par le moteur habituel
(corbeille, journal, annulation).
"""

from __future__ import annotations

import heapq
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field

from . import filetypes as ft
from .fsutil import (
    FILE_ATTRIBUTE_HIDDEN,
    FILE_ATTRIBUTE_SYSTEM,
    disk_kind,
    is_cloud_placeholder,
    norm,
    xp,
)
from .models import OP_RMDIR, OP_TRASH, Cancelled, FileEntry, Plan, PlannedOp, TaskControl
from .protect import Protection

log = logging.getLogger(__name__)

TOP_BIGGEST = 500
TOP_OLD = 1000
DAY = 86400

# fichiers temporaires / caches : (raison, test sur le nom en minuscules et l'extension)
_TEMP_NAMES = {"thumbs.db": "thumbs", "ehthumbs.db": "thumbs", "ehthumbs_vista.db": "thumbs", ".ds_store": "mac"}
_TEMP_EXT = {"tmp": "temp", "temp": "temp", "dmp": "dump", "mdmp": "dump", "hdmp": "dump", "chk": "temp",
             "gid": "temp", "~mp": "temp"}
_PARTIAL_EXT = {"crdownload", "part", "partial", "download", "opdownload", "!ut", "!qb", "filepart"}


@dataclass(slots=True, eq=False)
class FileInfo:
    path: str
    size: int
    mtime: float
    ext: str
    reason: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def group(self) -> str:
        return ft.group_of(ft.kind_from_ext(self.ext))


@dataclass(slots=True, eq=False)
class DirStat:
    path: str
    parent: DirStat | None
    size: int = 0  # sous-arbre complet
    files: int = 0
    own_size: int = 0  # fichiers directement dans ce dossier
    own_files: int = 0
    children: list = field(default_factory=list)
    groups: dict = field(default_factory=dict)  # catégorie -> taille (sous-arbre)
    error: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip("\\")) or self.path

    def dominant_group(self) -> str:
        return max(self.groups, key=self.groups.get) if self.groups else "unknown"


@dataclass
class DiskReport:
    root: DirStat
    biggest: list[FileInfo] = field(default_factory=list)
    old: list[FileInfo] = field(default_factory=list)
    temp: list[FileInfo] = field(default_factory=list)
    empty_dirs: list[str] = field(default_factory=list)  # les plus hauts dossiers vides
    all_empty_dirs: list[str] = field(default_factory=list)  # tous, du plus profond au moins profond
    installers: list[FileInfo] = field(default_factory=list)
    by_group: dict = field(default_factory=dict)  # catégorie -> [nombre, taille]
    by_ext: dict = field(default_factory=dict)
    total_size: int = 0
    total_files: int = 0
    dir_count: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    duration: float = 0.0
    old_years: int = 3


class DiskAnalyzer:
    def __init__(self, control: TaskControl | None = None, old_years: int = 3, include_hidden: bool = True):
        self.control = control or TaskControl()
        self.old_limit = time.time() - old_years * 365 * DAY
        self.old_years = old_years
        self.include_hidden = include_hidden
        self.protection = Protection()
        self._lock = threading.Lock()
        self._nodes: list[DirStat] = []
        self._biggest: list[tuple[int, str, float, str]] = []
        self._old: list[tuple[int, str, float, str]] = []
        self._temp: list[FileInfo] = []
        self._installers: list[FileInfo] = []
        self._by_group: dict[str, list[int]] = {}
        self._by_ext: dict[str, list[int]] = {}
        self._errors: list[tuple[str, str]] = []
        self._cancelled = False

    def analyze(self, root: str) -> DiskReport:
        start = time.monotonic()
        root = norm(root)
        self.control.set_stage("scan")
        top = DirStat(root, None)
        self._nodes.append(top)
        threads = {"ssd": 16, "hdd": 4, "removable": 4, "network": 8}.get(disk_kind(root), 8)
        q: queue.Queue = queue.Queue()
        q.put(top)
        workers = [threading.Thread(target=self._worker, args=(q,), daemon=True) for _ in range(threads)]
        for w in workers:
            w.start()
        q.join()
        for _ in workers:
            q.put(None)
        for w in workers:
            w.join()
        if self._cancelled or self.control.cancelled:
            raise Cancelled()
        # agrégation de bas en haut
        for node in reversed(self._nodes):
            node.size += node.own_size
            node.files += node.own_files
            for c in node.children:
                node.size += c.size
                node.files += c.files
                for g, s in c.groups.items():
                    node.groups[g] = node.groups.get(g, 0) + s
        report = DiskReport(root=top, old_years=self.old_years)
        report.biggest = [FileInfo(p, s, m, e) for s, p, m, e in sorted(self._biggest, reverse=True)]
        report.old = [FileInfo(p, s, m, e, "old") for s, p, m, e in sorted(self._old, reverse=True)]
        report.temp = sorted(self._temp, key=lambda f: -f.size)
        report.by_group = self._by_group
        report.by_ext = dict(sorted(self._by_ext.items(), key=lambda kv: -kv[1][1])[:40])
        report.total_size, report.total_files = top.size, top.files
        report.dir_count = len(self._nodes)
        report.errors = self._errors
        self._empty_dirs(report)
        self._obsolete_installers(report)
        report.duration = time.monotonic() - start
        return report

    def _worker(self, q: queue.Queue) -> None:
        while True:
            node = q.get()
            if node is None:
                q.task_done()
                return
            try:
                if not self._cancelled:
                    self.control.checkpoint()
                    self._list(node, q)
            except Cancelled:
                self._cancelled = True
            except Exception as exc:
                log.exception("Analyse de %s", node.path)
                with self._lock:
                    self._errors.append((node.path, str(exc)))
            finally:
                q.task_done()

    def _list(self, node: DirStat, q: queue.Queue) -> None:
        prefix = node.path if node.path.endswith("\\") else node.path + "\\"
        try:
            it = os.scandir(xp(node.path))
        except OSError as exc:
            node.error = exc.strerror or str(exc)
            with self._lock:
                self._errors.append((node.path, node.error))
            return
        children = []
        big, old, temp, inst = [], [], [], []
        groups: dict[str, int] = {}
        exts: dict[str, list[int]] = {}
        count = size_total = 0
        now = time.time()
        with it:
            for de in it:
                try:
                    st = de.stat(follow_symlinks=False)
                    if de.is_dir(follow_symlinks=False):
                        if de.is_symlink() or de.is_junction():
                            continue
                        attrs = getattr(st, "st_file_attributes", 0)
                        if not self.include_hidden and attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
                            continue
                        children.append(DirStat(prefix + de.name, node))
                        continue
                    if de.is_symlink():
                        continue
                except OSError:
                    continue
                attrs = getattr(st, "st_file_attributes", 0)
                if not self.include_hidden and attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
                    continue
                name = de.name
                lname = name.lower()
                stem, dot = os.path.splitext(lname)
                ext = dot[1:] if stem else ""
                size = st.st_size
                # fichiers OneDrive non téléchargés : ils n'occupent pas de place sur le disque
                if is_cloud_placeholder(attrs):
                    size = 0
                path = prefix + name
                count += 1
                size_total += size
                group = ft.group_of(ft.kind_from_ext(ext))
                groups[group] = groups.get(group, 0) + size
                bucket = exts.setdefault(ext or "—", [0, 0])
                bucket[0] += 1
                bucket[1] += size
                big.append((size, path, st.st_mtime, ext))
                if st.st_mtime < self.old_limit and size >= 1024 * 1024:
                    old.append((size, path, st.st_mtime, ext))
                reason = _TEMP_NAMES.get(lname) or _TEMP_EXT.get(ext)
                if reason is None and lname.startswith("~$") and now - st.st_mtime > DAY:
                    reason = "lock"
                if reason is None and lname.startswith("._") and size <= 8192:
                    reason = "mac"
                if reason is None and ext in _PARTIAL_EXT and now - st.st_mtime > 3 * DAY:
                    reason = "partial"
                if reason:
                    temp.append(FileInfo(path, size, st.st_mtime, ext, reason))
                if ext in ("exe", "msi") and not self.protection.is_protected(path):
                    inst.append(FileInfo(path, size, st.st_mtime, ext, "installer"))
        node.own_files = count
        node.own_size = size_total
        node.groups = groups
        node.children = children
        with self._lock:
            self._nodes.extend(children)
            for item in big:
                if len(self._biggest) < TOP_BIGGEST:
                    heapq.heappush(self._biggest, item)
                elif item[0] > self._biggest[0][0]:
                    heapq.heapreplace(self._biggest, item)
            for item in old:
                if len(self._old) < TOP_OLD:
                    heapq.heappush(self._old, item)
                elif item[0] > self._old[0][0]:
                    heapq.heapreplace(self._old, item)
            self._temp.extend(temp)
            self._installers.extend(inst)
            for g, s in groups.items():
                b = self._by_group.setdefault(g, [0, 0])
                b[1] += s
            for ext, (n, s) in exts.items():
                b = self._by_ext.setdefault(ext, [0, 0])
                b[0] += n
                b[1] += s
            for ext, (n, _s) in exts.items():
                g = ft.group_of(ft.kind_from_ext("" if ext == "—" else ext))
                self._by_group[g][0] += n
        self.control.advance(count, current=node.path)
        for c in children:
            q.put(c)

    def _empty_dirs(self, report: DiskReport) -> None:
        empties = [n for n in self._nodes if n.parent is not None and n.files == 0 and not n.error
                   and not self.protection.is_protected(n.path)]
        empty_keys = {id(n) for n in empties}
        report.empty_dirs = [n.path for n in empties if id(n.parent) not in empty_keys]
        report.all_empty_dirs = [n.path for n in sorted(empties, key=lambda n: -n.path.count("\\"))]

    def _obsolete_installers(self, report: DiskReport) -> None:
        from .installers import product_info, product_key, version_key

        by_product: dict[str, list[tuple[FileInfo, str]]] = {}
        for f in self._installers:
            stem = os.path.splitext(f.name)[0]
            info = product_info(f.path, stem, f.ext)
            if not info.get("product"):
                continue
            f.reason = f"{info['product']} {info.get('version', '')}".strip()
            by_product.setdefault(product_key(info["product"]), []).append((f, info.get("version")))
        for items in by_product.values():
            if len(items) < 2:
                continue
            items.sort(key=lambda x: (version_key(x[1]), x[0].mtime), reverse=True)
            for f, _v in items[1:]:  # toutes les versions sauf la plus récente
                report.installers.append(f)
        report.installers.sort(key=lambda f: -f.size)


def analyze_disk(root: str, control: TaskControl | None = None, old_years: int = 3) -> DiskReport:
    return DiskAnalyzer(control, old_years).analyze(root)


# ---------------------------------------------------------------------------
# Fichiers corrompus
# ---------------------------------------------------------------------------
_IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif"}
_ZIP_EXT = {"zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "jar", "cbz"}
_AUDIO_EXT = {"mp3", "flac", "m4a", "ogg", "opus", "wma", "aac", "wav"}
_VIDEO_EXT = {"mp4", "mov", "m4v", "mkv", "avi", "webm", "wmv", "3gp"}
CHECKABLE_EXT = _IMAGE_EXT | _ZIP_EXT | _AUDIO_EXT | _VIDEO_EXT | {"pdf"}


def check_file(path: str, ext: str) -> str | None:
    """Renvoie une raison si le fichier semble abîmé, sinon None."""
    try:
        if ext in _IMAGE_EXT:
            from PIL import Image

            from .metadata import _ensure_pil, too_big_to_decode

            _ensure_pil()
            with Image.open(xp(path)) as im:
                im.draft("RGB", (256, 256))
                if too_big_to_decode(im):
                    return None  # trop grande pour être vérifiée sans risque : considérée saine
                im.load()
            return None
        if ext in _ZIP_EXT:
            import zipfile

            with zipfile.ZipFile(xp(path)) as zf:
                if os.path.getsize(xp(path)) < 200 * 1024 * 1024 and zf.testzip() is not None:
                    return "zip_crc"
            return None
        if ext == "pdf":
            with open(xp(path), "rb") as fh:
                head = fh.read(1024)
                fh.seek(max(0, os.path.getsize(xp(path)) - 2048))
                tail = fh.read()
            if b"%PDF" not in head:
                return "pdf_header"
            if b"%%EOF" not in tail:
                return "truncated"
            return None
        if ext in _AUDIO_EXT:
            import mutagen

            audio = mutagen.File(xp(path))
            if audio is None or not getattr(audio.info, "length", 0):
                return "unreadable"
            return None
        if ext in _VIDEO_EXT:
            import av

            with av.open(xp(path)) as container:
                if not container.streams.video and not container.streams.audio:
                    return "unreadable"
                stream = container.streams.video[0] if container.streams.video else container.streams.audio[0]
                for _frame in container.decode(stream):
                    break
                else:
                    return "unreadable"
                if container.duration:
                    container.seek(int(container.duration * 0.95), backward=True)
                    for _frame in container.decode(stream):
                        break
            return None
    except Exception as exc:
        text = str(exc).lower()
        return "truncated" if "truncat" in text else "unreadable"
    return None


def find_corrupt(root: str, control: TaskControl | None = None) -> list[FileInfo]:
    from concurrent.futures import ThreadPoolExecutor

    control = control or TaskControl()
    files: list[FileInfo] = []
    control.set_stage("scan")
    for dirpath, _dirs, names in os.walk(xp(norm(root))):
        control.checkpoint()
        for name in names:
            ext = os.path.splitext(name)[1][1:].lower()
            if ext in CHECKABLE_EXT and not name.startswith("._"):
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if is_cloud_placeholder(getattr(st, "st_file_attributes", 0)):
                    continue
                path = full[4:] if full.startswith("\\\\?\\") else full
                files.append(FileInfo(path, st.st_size, st.st_mtime, ext))
        control.advance(len(names))
    control.set_stage("check", total=len(files))
    bad: list[FileInfo] = []

    def work(f: FileInfo):
        control.checkpoint()
        reason = check_file(f.path, f.ext)
        control.advance(1, current=f.path)
        return f, reason

    with ThreadPoolExecutor(max_workers=4) as pool:
        for f, reason in pool.map(work, files):
            if reason:
                f.reason = reason
                bad.append(f)
    return bad


# ---------------------------------------------------------------------------
# Plans de nettoyage (corbeille / suppression de dossiers vides)
# ---------------------------------------------------------------------------
def cleanup_plan(files: list[FileInfo], empty_dirs: list[str] | None = None, root: str = "") -> Plan:
    plan = Plan(dest_root="")
    prot = Protection()
    for f in files:
        if prot.is_protected(f.path):
            continue
        name = os.path.basename(f.path)
        entry = FileEntry(dir=os.path.dirname(f.path), name=name, size=f.size, mtime=f.mtime, ctime=f.mtime,
                          ext=f.ext, kind=ft.kind_from_ext(f.ext), root=root)
        plan.ops.append(PlannedOp(OP_TRASH, f.path, None, f.size, entry, "cleanup", note=f"clean.{f.reason}"))
    for d in empty_dirs or []:
        if prot.is_protected(d):
            continue
        plan.ops.append(PlannedOp(OP_RMDIR, d, None, 0, None, "cleanup", is_dir=True))
    for i, op in enumerate(plan.ops, 1):
        op.id = i
    plan.sources = [root] if root else []
    return plan
