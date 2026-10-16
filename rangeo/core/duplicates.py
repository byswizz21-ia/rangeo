"""Recherche de doublons exacts : taille → empreinte partielle → empreinte complète."""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import hashing
from .fsutil import disk_kind, is_cloud_placeholder, is_within, nkey, sanitize_component, unique_name, volume_id, xp
from .index import MetaIndex
from .models import OP_HARDLINK, OP_MOVE, OP_TRASH, Cancelled, FileEntry, Plan, PlannedOp, TaskControl

log = logging.getLogger(__name__)

COPY_MARKERS = re.compile(
    r"(\(\d+\)|\[\d+\]|\s-\s(copie|copy|kopie)|^(copie de|copy of|copia de)\s|[_ -]copy\b|\bcopie\b|\bcopy\b|~\d+$"
    r"|\s\d+$)",
    re.I,
)


@dataclass
class DupOptions:
    min_size: int = 1
    keep_criteria: list[str] = field(
        default_factory=lambda: ["preferred", "best_quality", "clean_name", "oldest", "shortest_path"])
    preferred: list[str] = field(default_factory=list)
    threads: int | None = None


@dataclass(eq=False)
class DupGroup:
    digest: str
    size: int
    files: list[FileEntry]
    keep: FileEntry
    enabled: bool = True
    similar: bool = False  # fichiers ressemblants (pas identiques octet par octet)

    @property
    def wasted(self) -> int:
        if self.similar:
            return sum(e.size for e in self.files if e is not self.keep)
        return self.size * (len(self.files) - 1)


def _keep_key(entry: FileEntry, opt: DupOptions):
    key = []
    for crit in opt.keep_criteria:
        if crit == "preferred":
            key.append(0 if any(is_within(entry.path, p) for p in opt.preferred) else 1)
        elif crit == "best_quality":
            from .similar import quality_of

            key.append(-quality_of(entry))
        elif crit == "clean_name":
            key.append(len(COPY_MARKERS.findall(entry.stem)))
        elif crit == "oldest":
            key.append(min(t for t in (entry.mtime, entry.ctime) if t) if (entry.mtime or entry.ctime) else 0)
        elif crit == "newest":
            key.append(-(max(entry.mtime, entry.ctime) or 0))
        elif crit == "shortest_path":
            key.append(len(entry.path))
        elif crit == "longest_name":
            key.append(-len(entry.name))
    key.append(entry.path.lower())
    return tuple(key)


def choose_keep(files: list[FileEntry], opt: DupOptions) -> FileEntry:
    return min(files, key=lambda e: _keep_key(e, opt))


def find_duplicates(entries: list[FileEntry], opt: DupOptions, control: TaskControl | None = None,
                    index: MetaIndex | None = None) -> list[DupGroup]:
    control = control or TaskControl()
    min_size = max(1, opt.min_size)
    by_size: dict[int, list[FileEntry]] = {}
    for e in entries:
        if e.is_bundle or e.size < min_size or is_cloud_placeholder(e.attrs):
            continue
        by_size.setdefault(e.size, []).append(e)
    candidates = [e for group in by_size.values() if len(group) > 1 for e in group]
    if not candidates:
        return []
    threads = opt.threads or {"ssd": 12, "hdd": 2, "removable": 2, "network": 4}.get(disk_kind(candidates[0].dir), 4)

    cached = {}
    if index is not None:
        try:
            cached = index.lookup([(e.path, e.size, e.mtime) for e in candidates])
        except Exception:
            cached = {}

    phash: dict[int, str] = {}
    fhash: dict[int, str] = {}
    inode: dict[int, tuple[int, int]] = {}
    to_store: list = []

    def run(fn, items, stage, total_bytes=0):
        control.set_stage(stage, total=len(items), bytes_total=total_bytes)
        pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix=stage)
        try:
            for _ in pool.map(fn, items):
                pass
        except Cancelled:
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    # --- étape 1 : empreinte partielle --------------------------------------
    def partial(e: FileEntry):
        control.checkpoint()
        hit = cached.get(nkey(e.path))
        try:
            st = os.stat(xp(e.path))
            inode[id(e)] = (st.st_dev, st.st_ino)
            if hit and hit[2]:
                phash[id(e)] = hit[2]
                if hit[3]:
                    fhash[id(e)] = hit[3]
            else:
                phash[id(e)] = hashing.partial_hash(e.path, e.size)
        except OSError as exc:
            log.debug("Doublons : %s illisible (%s)", e.path, exc)
        control.advance(1, current=e.path)

    run(partial, candidates, "dup_partial")

    groups: dict[tuple[int, str], list[FileEntry]] = {}
    for e in candidates:
        h = phash.get(id(e))
        if h:
            groups.setdefault((e.size, h), []).append(e)
    second = [g for g in groups.values() if len(g) > 1]

    # --- étape 2 : empreinte complète ---------------------------------------
    need_full = [e for g in second for e in g if id(e) not in fhash and not phash[id(e)].startswith("f:")]

    def full(e: FileEntry):
        control.checkpoint()
        try:
            fhash[id(e)] = hashing.full_hash(e.path, control, count_bytes=True)
        except OSError as exc:
            log.debug("Doublons : %s illisible (%s)", e.path, exc)
        control.advance(1, current=e.path)

    run(full, need_full, "dup_full", sum(e.size for e in need_full))

    for g in second:
        for e in g:
            if phash[id(e)].startswith("f:") and id(e) not in fhash:
                fhash[id(e)] = phash[id(e)][2:]
            to_store.append((e.path, e.size, e.mtime, phash.get(id(e)), fhash.get(id(e))))
    if index is not None and to_store:
        try:
            index.store_hashes(to_store)
        except Exception:
            log.exception("Index : enregistrement des empreintes")

    final: dict[tuple[int, str], list[FileEntry]] = {}
    for g in second:
        for e in g:
            h = fhash.get(id(e))
            if h:
                final.setdefault((e.size, h), []).append(e)

    result: list[DupGroup] = []
    for (size, digest), files in final.items():
        # deux chemins vers le même fichier physique (lien physique) ne sont pas des doublons
        seen: set[tuple[int, int]] = set()
        unique: list[FileEntry] = []
        for e in sorted(files, key=lambda x: x.path.lower()):
            ino = inode.get(id(e))
            if ino and ino[1] and ino in seen:
                continue
            if ino:
                seen.add(ino)
            unique.append(e)
        if len(unique) > 1:
            result.append(DupGroup(digest, size, unique, choose_keep(unique, opt)))
    result.sort(key=lambda g: g.wasted, reverse=True)
    return result


def similar_groups(groups_of_files: list[list[FileEntry]], opt: DupOptions) -> list[DupGroup]:
    out = []
    for files in groups_of_files:
        files = sorted(files, key=lambda x: x.path.lower())
        out.append(DupGroup("similar", max(e.size for e in files), files, choose_keep(files, opt), similar=True))
    out.sort(key=lambda g: g.wasted, reverse=True)
    return out


def build_dup_plan(groups: list[DupGroup], action: str, dup_root: str | None, folder_name: str,
                   lang: str = "fr") -> Plan:
    """Plan pour traiter les doublons (tous sauf le fichier conservé de chaque groupe)."""
    plan = Plan(dest_root=dup_root or "")
    taken: dict[str, set[str]] = {}
    for g in groups:
        if not g.enabled:
            continue
        rule = "similar" if g.similar else "duplicate"
        for e in g.files:
            if e is g.keep:
                continue
            if action == "trash":
                op = PlannedOp(OP_TRASH, e.path, None, e.size, e, rule, keep_ref=g.keep.path)
            elif action == "hardlink":
                op = PlannedOp(OP_HARDLINK, e.path, g.keep.path, e.size, e, "duplicate", keep_ref=g.keep.path)
                try:
                    if volume_id(e.dir) != volume_id(g.keep.dir):
                        op.enabled = False
                        op.note = "dup.hardlink_other_disk"
                except OSError:
                    op.enabled = False
                    op.note = "dup.hardlink_other_disk"
            else:
                root = dup_root or e.root or os.path.dirname(e.dir)
                base = sanitize_component(os.path.basename(e.root.rstrip("\\")) or "racine")
                rel = os.path.relpath(e.dir, e.root) if e.root and is_within(e.dir, e.root) else ""
                parts = [p for p in rel.split(os.sep) if p and p != "."]
                folder = os.path.join(root, sanitize_component(folder_name), base, *parts)
                key = nkey(folder)
                names = taken.setdefault(key, set())
                if not names:
                    try:
                        with os.scandir(xp(folder)) as it:
                            names.update(nkey(de.name) for de in it)
                    except OSError:
                        pass
                name = unique_name(e.name, names)
                names.add(nkey(name))
                op = PlannedOp(OP_MOVE, e.path, os.path.join(folder, name), e.size, e, rule,
                               keep_ref=g.keep.path)
            plan.ops.append(op)
    for i, op in enumerate(plan.ops, 1):
        op.id = i
    plan.sources = sorted({e.root for g in groups for e in g.files if e.root})
    return plan
