"""Calcul du plan de rangement (aucune modification du disque à cette étape)."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

from . import filetypes as ft
from . import hashing
from . import rules as rules_mod
from .fsutil import (
    FAT32_MAX_FILE,
    cloud_root_of,
    cloud_roots,
    disk_usage,
    existing_ancestor,
    filesystem,
    human_size,
    is_strictly_within,
    is_within,
    nkey,
    norm,
    sanitize_component,
    split_ext,
    unique_name,
    volume_id,
    xp,
)
from .models import OP_COPY, OP_MOVE, OP_SKIP, OP_TRASH, Cancelled, FileEntry, Plan, PlannedOp, TaskControl
from .protect import Protection

log = logging.getLogger(__name__)

# fichiers qui suivent leur fichier principal (sous-titres, fichiers annexes)
COMPANIONS = {
    "srt": "video", "sub": "video", "idx": "video", "ass": "video", "ssa": "video", "vtt": "video",
    "sup": "video", "smi": "video", "nfo": "video",
    "xmp": "image", "aae": "image", "thm": "video",
    "lrc": "audio", "cue": "audio",
}
SPACE_MARGIN = 512 * 1024 * 1024  # on garde toujours au moins 512 Mo libres

_COPY_SUFFIX = re.compile(r"(\s*\(\d+\)|\s*-\s*(copie|copy|kopie)(\s*\(\d+\))?|\s+copy(\s+\d+)?|_copy\d*)$", re.I)
_COPY_PREFIX = re.compile(r"^(copie de|copy of|copia de)\s+(\(\d+\)\s+)?", re.I)


def strip_copy_marker(name: str) -> str | None:
    """« IMG (1).jpg » → « IMG.jpg » ; None si le nom ne ressemble pas à une copie."""
    stem, ext = split_ext(name)
    cleaned = _COPY_PREFIX.sub("", _COPY_SUFFIX.sub("", stem)).strip()
    if cleaned and cleaned != stem:
        return cleaned + ext
    return None


@dataclass
class PlanOptions:
    dest_root: str = ""
    in_place: bool = False
    conflict: str = "rename"
    identical: str = "duplicates"
    duplicates_folder: str = "_Doublons"
    lang: str = "fr"
    fix_extensions: bool = False
    companions: bool = True
    extra_dirs: list[str] = field(default_factory=list)
    extra_protected: list[str] = field(default_factory=list)
    kind_label: object = None


class _DirCache:
    """Contenu des dossiers de destination, lu une seule fois par dossier."""

    def __init__(self):
        self.names: dict[str, set[str]] = {}
        self.blocked: dict[str, str | None] = {}
        self.vol: dict[str, int | None] = {}

    def existing(self, folder: str) -> set[str]:
        key = nkey(folder)
        names = self.names.get(key)
        if names is None:
            names = set()
            try:
                with os.scandir(xp(folder)) as it:
                    for de in it:
                        names.add(nkey(de.name))
            except OSError:
                pass
            self.names[key] = names
        return names

    def blocked_by_file(self, folder: str) -> str | None:
        """Un des dossiers à créer existe-t-il déjà sous forme de fichier ?"""
        key = nkey(folder)
        if key in self.blocked:
            return self.blocked[key]
        result = None
        current = folder
        while True:
            p = xp(current)
            if os.path.exists(p):
                if not os.path.isdir(p):
                    result = current
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        self.blocked[key] = result
        return result

    def volume(self, folder: str) -> int | None:
        """Volume d'un dossier (existant ou à créer : on regarde son plus proche parent existant)."""
        key = nkey(folder)
        if key not in self.vol:
            parent = os.path.dirname(folder)
            if parent != folder and not os.path.isdir(xp(folder)):
                self.vol[key] = self.volume(parent)
            else:
                try:
                    self.vol[key] = volume_id(folder)
                except OSError:
                    self.vol[key] = None
        return self.vol[key]


def _bundle_size(path: str, control: TaskControl) -> tuple[int, int]:
    total = count = 0
    stack = [path]
    while stack:
        control.checkpoint()
        current = stack.pop()
        try:
            with os.scandir(xp(current)) as it:
                for de in it:
                    try:
                        if de.is_dir(follow_symlinks=False):
                            if not de.is_symlink():
                                stack.append(os.path.join(current, de.name))
                        else:
                            total += de.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, count


class Planner:
    def __init__(self, rules: list[dict], options: PlanOptions, control: TaskControl | None = None):
        self.rules = rules
        self.compiled = rules_mod.compile_rules(rules)
        self.opt = options
        self._protected: dict[str, bool] = {}
        self.control = control or TaskControl()
        self.protection = Protection(options.extra_protected)
        self.cache = _DirCache()
        self.planned: dict[str, PlannedOp] = {}  # nkey(dst) -> op
        self.planned_names: dict[str, set[str]] = {}  # nkey(dossier) -> noms prévus
        self.sources: set[str] = set()
        self.counters: dict[str, int] = {}
        self.hash_cache: dict[str, str] = {}

    # -- utilitaires -------------------------------------------------------------
    def _dest_root(self, entry: FileEntry) -> str:
        return entry.root if self.opt.in_place or not self.opt.dest_root else self.opt.dest_root

    def _taken(self, folder: str) -> set[str]:
        key = nkey(folder)
        planned = self.planned_names.get(key, set())
        return self.cache.existing(folder) | planned

    def _reserve(self, op: PlannedOp) -> None:
        folder, name = os.path.split(op.dst)
        self.planned[nkey(op.dst)] = op
        self.planned_names.setdefault(nkey(folder), set()).add(nkey(name))

    def _hash(self, path: str) -> str | None:
        key = nkey(path)
        if key not in self.hash_cache:
            try:
                self.hash_cache[key] = hashing.full_hash(path, self.control)
            except OSError:
                self.hash_cache[key] = None
        return self.hash_cache[key]

    def _identical(self, a: str, a_size: int, b: str, b_size: int) -> bool:
        if a_size != b_size:
            return False
        if a_size == 0:
            return True
        try:
            if hashing.partial_hash(a, a_size) != hashing.partial_hash(b, b_size):
                return False
        except OSError:
            return False
        ha, hb = self._hash(a), self._hash(b)
        return ha is not None and ha == hb

    def _duplicate_target(self, entry: FileEntry) -> str:
        root = self._dest_root(entry)
        folder = sanitize_component(self.opt.duplicates_folder or "_Doublons")
        rel = os.path.relpath(entry.dir, entry.root) if is_within(entry.dir, entry.root) else ""
        base = sanitize_component(os.path.basename(entry.root.rstrip("\\")) or entry.root[:1])
        parts = [p for p in rel.split(os.sep) if p and p != "."]
        return os.path.join(root, folder, base, *parts)

    # -- calcul ------------------------------------------------------------------
    def plan(self, entries: list[FileEntry]) -> Plan:
        opt = self.opt
        result = Plan(dest_root=opt.dest_root)
        self.control.set_stage("plan", total=len(entries))

        primaries: list[FileEntry] = []
        companions: list[FileEntry] = []
        for e in entries:
            if opt.companions and not e.is_bundle and e.ext in COMPANIONS:
                companions.append(e)
            else:
                primaries.append(e)
        self.sources = {nkey(e.path) for e in entries}

        # sources concernées par un déplacement de dossier entier
        moving_bundles: set[str] = set()

        primaries.sort(key=lambda e: (e.date or 0, e.path.lower()))
        ops_by_entry: dict[int, PlannedOp] = {}
        for i, entry in enumerate(primaries):
            if i % 512 == 0:
                self.control.checkpoint()
            op = self._plan_entry(entry, result)
            self.control.advance(1)
            if op is None:
                continue
            ops_by_entry[id(entry)] = op
            if entry.is_bundle and op.op == OP_MOVE:
                moving_bundles.add(nkey(entry.path))

        self._copies_to_duplicates(result)

        # fichiers annexes (sous-titres…) : suivent leur fichier principal
        if companions:
            self._attach_companions(companions, primaries, ops_by_entry, result)

        # sécurité : aucune destination à l'intérieur d'un dossier qui est lui-même déplacé
        if moving_bundles:
            for op in result.ops:
                if op.dst and op.op in (OP_MOVE, OP_COPY):
                    folder = os.path.dirname(op.dst)
                    while True:
                        if nkey(folder) in moving_bundles:
                            op.enabled = False
                            op.note = "plan.target_in_moving_folder"
                            break
                        parent = os.path.dirname(folder)
                        if parent == folder:
                            break
                        folder = parent

        for i, op in enumerate(result.ops, 1):
            op.id = i
        self._finalize(result)
        return result

    def _plan_entry(self, entry: FileEntry, result: Plan) -> PlannedOp | None:
        opt = self.opt
        decision = rules_mod.decide_compiled(entry, self.compiled)
        if decision is None:
            result.skipped.append(PlannedOp(OP_SKIP, entry.path, None, entry.size, entry, "", "plan.no_rule"))
            return None
        if decision.action == "ignore":
            result.skipped.append(
                PlannedOp(OP_SKIP, entry.path, None, entry.size, entry, decision.rule_name, "plan.rule_ignore")
            )
            return None
        if decision.action == "trash":
            op = PlannedOp(OP_TRASH, entry.path, None, entry.size, entry, decision.rule_name,
                           is_dir=entry.is_bundle)
            result.ops.append(op)
            return op

        dest_root = self._dest_root(entry)
        folder = rules_mod.render_target_dir(decision.target, entry, dest_root, opt.lang, opt.kind_label)
        counter = None
        if rules_mod.template_uses_counter(decision.rename) or rules_mod.template_uses_counter(decision.target):
            n = self.counters.get(nkey(folder), 0) + 1
            self.counters[nkey(folder)] = n
            counter = f"{n:03d}"
            folder = rules_mod.render_target_dir(decision.target, entry, dest_root, opt.lang, opt.kind_label, counter)
        ext_override = None
        if opt.fix_extensions and entry.ext_mismatch and entry.real_ext:
            ext_override = "." + entry.real_ext
        name = rules_mod.render_name(decision.rename, entry, opt.lang, opt.kind_label, counter, ext_override)
        dst = os.path.join(folder, name)
        op_type = OP_COPY if decision.action == "copy" else OP_MOVE
        op = PlannedOp(op_type, entry.path, dst, entry.size, entry, decision.rule_name, is_dir=entry.is_bundle)

        problem = self._check_target(entry, op)
        if problem:
            op.note = problem
            op.enabled = False
            if problem == "plan.already_sorted":
                op.op = OP_SKIP
                result.skipped.append(op)
                return None
            result.ops.append(op)
            return op

        self._resolve_conflict(entry, op, result)
        if op.op == OP_SKIP:
            result.skipped.append(op)
            return None
        if op.dst:
            self._reserve(op)
        result.ops.append(op)
        return op

    def _check_target(self, entry: FileEntry, op: PlannedOp) -> str | None:
        dst = op.dst
        if nkey(dst) == nkey(entry.path):
            return "plan.already_sorted"
        folder_key = nkey(os.path.dirname(dst))
        protected = self._protected.get(folder_key)
        if protected is None:
            protected = self.protection.is_protected(os.path.dirname(dst))
            self._protected[folder_key] = protected
        if protected:
            return "plan.protected_target"
        if entry.is_bundle and is_within(dst, entry.path):
            return "plan.into_itself"
        if len(dst) > 32000:
            return "plan.path_too_long"
        folder = os.path.dirname(dst)
        if self.cache.blocked_by_file(folder):
            return "plan.target_is_file"
        if entry.is_bundle and any(is_strictly_within(r, entry.path) for r in self._roots_of(entry)):
            return "plan.contains_destination"
        return None

    def _roots_of(self, entry: FileEntry) -> list[str]:
        return [self.opt.dest_root] if self.opt.dest_root and not self.opt.in_place else []

    def _resolve_conflict(self, entry: FileEntry, op: PlannedOp, result: Plan) -> None:
        opt = self.opt
        folder, name = os.path.split(op.dst)
        key = nkey(op.dst)
        on_disk = nkey(name) in self.cache.existing(folder)
        planned = self.planned.get(key)
        if not on_disk and planned is None:
            return

        # Contenu identique ?
        if not entry.is_bundle:
            other_path, other_size = None, None
            if planned is not None and planned.entry is not None and not planned.is_dir:
                other_path, other_size = planned.src, planned.size
            elif on_disk:
                try:
                    st = os.stat(xp(op.dst))
                    if not os.path.isdir(xp(op.dst)):
                        other_path, other_size = op.dst, st.st_size
                except OSError:
                    pass
            if other_path and self._identical(entry.path, entry.size, other_path, other_size):
                self._apply_identical(entry, op)
                return

        strategy = opt.conflict
        # on ne remplace jamais un fichier qui doit lui-même être déplacé, ni un fichier prévu dans ce plan
        if strategy in ("overwrite", "keep_newest", "keep_largest") and (
            planned is not None or key in self.sources or entry.is_bundle
        ):
            strategy = "rename"
        if strategy == "skip":
            op.op, op.dst, op.note = OP_SKIP, None, "plan.conflict_skipped"
            return
        if strategy in ("overwrite", "keep_newest", "keep_largest"):
            try:
                st = os.stat(xp(op.dst))
            except OSError:
                st = None
            if st is None or os.path.isdir(xp(op.dst)):
                strategy = "rename"
            else:
                keep_new = True
                if strategy == "keep_newest":
                    keep_new = entry.mtime > st.st_mtime
                elif strategy == "keep_largest":
                    keep_new = entry.size > st.st_size
                if keep_new:
                    op.replaces = op.dst
                    op.note = "plan.replaces_existing"
                else:
                    op.op, op.dst, op.note = OP_TRASH, None, "plan.conflict_loser_trashed"
                return
        # renommage automatique : « nom (2).ext »
        op.dst = os.path.join(folder, unique_name(name, self._taken(folder)))
        op.note = "plan.renamed_conflict"

    def _apply_identical(self, entry: FileEntry, op: PlannedOp) -> None:
        """Le fichier est une copie exacte d'un autre : doublon."""
        if op.op == OP_COPY or self.opt.identical == "skip":
            op.op, op.dst, op.note = OP_SKIP, None, "plan.identical_exists"
            return
        if self.opt.identical == "trash":
            op.op, op.dst, op.note = OP_TRASH, None, "plan.identical_trashed"
            return
        dup_folder = self._duplicate_target(entry)
        op.dst = os.path.join(dup_folder, unique_name(entry.name, self._taken(dup_folder)))
        op.note = "plan.identical_to_duplicates"

    def _copies_to_duplicates(self, result: Plan) -> None:
        """« photo (1).jpg » identique à « photo.jpg » dans le même dossier cible → doublon."""
        for op in list(result.ops):
            if op.op != OP_MOVE or not op.enabled or op.is_dir or op.note or op.entry is None:
                continue
            folder, name = os.path.split(op.dst)
            base = strip_copy_marker(name)
            if not base:
                continue
            other = self.planned.get(nkey(os.path.join(folder, base)))
            if other is not None and other is not op and not other.is_dir and other.enabled:
                other_path, other_size = other.src, other.size
            elif nkey(base) in self.cache.existing(folder):
                other_path = os.path.join(folder, base)
                try:
                    other_size = os.stat(xp(other_path)).st_size
                except OSError:
                    continue
            else:
                continue
            if not self._identical(op.src, op.size, other_path, other_size):
                continue
            self.planned.pop(nkey(op.dst), None)
            self.planned_names.get(nkey(folder), set()).discard(nkey(name))
            self._apply_identical(op.entry, op)
            if op.op == OP_SKIP:
                result.ops.remove(op)
                result.skipped.append(op)
            elif op.dst:
                self._reserve(op)

    def _attach_companions(self, companions, primaries, ops_by_entry, result) -> None:
        by_dir: dict[str, list[FileEntry]] = {}
        for e in primaries:
            if not e.is_bundle:
                by_dir.setdefault(nkey(e.dir), []).append(e)
        for comp in companions:
            wanted_group = COMPANIONS.get(comp.ext)
            comp_stem = comp.stem.lower()
            best = None
            for cand in by_dir.get(nkey(comp.dir), []):
                if ft.group_of(cand.kind) != wanted_group:
                    continue
                stem = cand.stem.lower()
                name = cand.name.lower()
                if comp_stem in (stem, name) or comp_stem.startswith(stem + ".") or comp_stem.startswith(name + "."):
                    if best is None or len(cand.stem) > len(best.stem):
                        best = cand
            parent_op = ops_by_entry.get(id(best)) if best is not None else None
            if (parent_op is None or parent_op.dst is None or not parent_op.enabled
                    or parent_op.op not in (OP_MOVE, OP_COPY)):
                if best is not None:
                    # le fichier principal reste en place : l'annexe aussi
                    result.skipped.append(PlannedOp(OP_SKIP, comp.path, None, comp.size, comp, "",
                                                    "plan.companion_stays"))
                    continue
                self._plan_entry(comp, result)  # sous-titre orphelin : règles normales
                continue
            # même renommage que le fichier principal : « Film.fr.srt » suit « Film.mkv »
            new_parent_stem = split_ext(os.path.basename(parent_op.dst))[0]
            suffix = comp.name[len(best.stem):] if comp.name.lower().startswith(best.stem.lower()) else "." + comp.ext
            folder = os.path.dirname(parent_op.dst)
            name = sanitize_component(new_parent_stem + suffix, max_len=220)
            op = PlannedOp(parent_op.op, comp.path, os.path.join(folder, name), comp.size, comp, parent_op.rule,
                           "plan.companion")
            if nkey(op.dst) == nkey(comp.path):
                op.op = OP_SKIP
                result.skipped.append(op)
                continue
            if nkey(name) in self._taken(folder) or nkey(op.dst) in self.planned:
                op.dst = os.path.join(folder, unique_name(name, self._taken(folder)))
            self._reserve(op)
            result.ops.append(op)

    # -- vérifications finales ---------------------------------------------------
    def _finalize(self, plan: Plan) -> None:
        opt = self.opt
        need: dict[int, int] = {}
        vol_path: dict[int, str] = {}
        sync_roots = cloud_roots()
        for op in plan.ops:
            if not op.enabled or op.dst is None or op.op not in (OP_MOVE, OP_COPY):
                continue
            src_vol = self.cache.volume(os.path.dirname(op.src))
            dst_vol = self.cache.volume(os.path.dirname(op.dst))
            op.cross_volume = op.op == OP_COPY or src_vol is None or src_vol != dst_vol
            if op.entry is not None and op.entry.meta.get("cloud") and not op.note:
                # le copier vers un autre disque, ou le sortir de son dossier OneDrive, obligerait
                # à le télécharger depuis le cloud
                root = cloud_root_of(op.src, sync_roots)
                if op.cross_volume or (root is not None and not is_within(op.dst, root)):
                    op.enabled = False
                    op.note = "plan.cloud_only"
            if op.cross_volume and not op.is_dir and op.size > FAT32_MAX_FILE and op.enabled \
                    and filesystem(os.path.dirname(op.dst)) == "FAT32":
                op.enabled = False  # sinon l'écriture échouerait (« disque plein ») et tout s'arrêterait
                op.note = "plan.too_big_for_fat32"
                continue
            if op.cross_volume:
                if op.is_dir and op.entry is not None and op.entry.meta.get("size_partial"):
                    size, count = _bundle_size(op.src, self.control)
                    op.size = size
                    op.entry.size = size
                    op.entry.bundle_files = count
                if dst_vol is not None:
                    need[dst_vol] = need.get(dst_vol, 0) + op.size
                    vol_path[dst_vol] = op.dst
        for vol, nbytes in need.items():
            try:
                free = disk_usage(vol_path[vol])[2]
            except OSError:
                continue
            if nbytes + SPACE_MARGIN > free:
                plan.errors.append(
                    ("plan.not_enough_space",
                     f"{existing_ancestor(os.path.dirname(vol_path[vol]))} — "
                     f"{human_size(nbytes, opt.lang)} / {human_size(free, opt.lang)}")
                )
        dest_roots = {self.opt.dest_root} if self.opt.dest_root and not self.opt.in_place else set()
        for root in dest_roots:
            for extra in opt.extra_dirs:
                parts = [sanitize_component(p) for p in extra.replace("/", "\\").split("\\") if p.strip()]
                if parts:
                    plan.extra_dirs.append(os.path.join(root, *parts))
        if opt.in_place:
            for src_root in {e.entry.root for e in plan.ops if e.entry is not None}:
                for extra in opt.extra_dirs:
                    parts = [sanitize_component(p) for p in extra.replace("/", "\\").split("\\") if p.strip()]
                    if parts:
                        plan.extra_dirs.append(os.path.join(src_root, *parts))


def build_plan(entries: list[FileEntry], rules: list[dict], options: PlanOptions,
               control: TaskControl | None = None) -> Plan:
    if options.dest_root:
        options.dest_root = norm(options.dest_root)
    return Planner(rules, options, control).plan(entries)
