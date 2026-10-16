"""Renommage en masse : modèle de nom + transformations, avec aperçu avant/après.

Les renommages passent par le moteur d'exécution habituel (journal, annulation).
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field

from . import rules as R
from .fsutil import nkey, sanitize_component, split_ext, unique_name, xp
from .models import OP_MOVE, FileEntry, Plan, PlannedOp

_COPY_MARKERS = [
    re.compile(r"^(copie de|copy of|copia de|kopie von)\s+(\(\d+\)\s+)?", re.I),
    re.compile(r"\s*-\s*(copie|copy|kopie)(\s*\(\d+\))?$", re.I),
    re.compile(r"\s*\(\d+\)$"),
    re.compile(r"\s*\[\d+\]$"),
    re.compile(r"[_\s]copy\d*$", re.I),
]
_SMALL_WORDS = {"de", "du", "des", "la", "le", "les", "et", "à", "a", "au", "aux", "en", "of", "the", "and", "in",
                "on", "for", "to", "d", "l"}


@dataclass
class RenameOptions:
    template: str = ""  # vide : on part du nom actuel
    clean_copies: bool = True  # « (1) », « Copie de », « - Copie »…
    separators: str = "keep"  # keep | spaces | dashes | underscores
    case: str = "keep"  # keep | lower | upper | title | sentence
    remove_accents: bool = False
    find: str = ""
    replace: str = ""
    regex: bool = False
    prefix: str = ""
    suffix: str = ""
    numbering: str = "none"  # none | prefix | suffix
    start: int = 1
    padding: int = 3
    num_sep: str = " "
    ext_case: str = "lower"  # keep | lower | upper
    fix_ext: bool = False
    sort_by: str = "name"  # ordre de numérotation : name | date | size
    groups: list[str] = field(default_factory=list)  # catégories à renommer (vide = toutes)


@dataclass(eq=False)
class RenameItem:
    entry: FileEntry
    new_name: str
    status: str = "ok"  # same | ok | renamed_conflict | missing_info | invalid
    enabled: bool = True

    @property
    def changed(self) -> bool:
        return self.new_name != self.entry.name


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _title(text: str, lang_small: bool = True) -> str:
    words = re.split(r"(\s+)", text.lower())
    out = []
    first = True
    for w in words:
        if w.strip() and (first or not lang_small or w not in _SMALL_WORDS):
            w = w[:1].upper() + w[1:]
        if w.strip():
            first = False
        out.append(w)
    return "".join(out)


def transform_stem(stem: str, opt: RenameOptions) -> str:
    s = stem
    if opt.clean_copies:
        for _ in range(3):
            before = s
            for pattern in _COPY_MARKERS:
                s = pattern.sub("", s)
            if s == before:
                break
        s = s.strip() or stem
    if opt.find:
        if opt.regex:
            try:
                s = re.sub(opt.find, opt.replace, s)
            except re.error:
                pass
        else:
            s = s.replace(opt.find, opt.replace)
    if opt.separators == "spaces":
        s = re.sub(r"[_.]+", " ", s)
    elif opt.separators == "dashes":
        s = re.sub(r"[_.\s]+", "-", s)
    elif opt.separators == "underscores":
        s = re.sub(r"[.\s-]+", "_", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    if opt.remove_accents:
        s = _strip_accents(s)
    if opt.case == "lower":
        s = s.lower()
    elif opt.case == "upper":
        s = s.upper()
    elif opt.case == "title":
        s = _title(s)
    elif opt.case == "sentence":
        s = s[:1].upper() + s[1:].lower()
    return f"{opt.prefix}{s}{opt.suffix}"


def _sort_key(opt: RenameOptions):
    if opt.sort_by == "date":
        return lambda e: (e.date or e.mtime or 0, e.name.lower())
    if opt.sort_by == "size":
        return lambda e: (e.size, e.name.lower())
    return lambda e: e.name.lower()


def preview(entries: list[FileEntry], opt: RenameOptions, lang: str = "fr", kind_label=None) -> list[RenameItem]:
    """Calcule les nouveaux noms (aucun fichier n'est modifié)."""
    from . import filetypes as ft

    files = [e for e in entries if not e.is_bundle and (not opt.groups or ft.group_of(e.kind) in opt.groups)]
    by_dir: dict[str, list[FileEntry]] = {}
    for e in files:
        by_dir.setdefault(nkey(e.dir), []).append(e)
    items: list[RenameItem] = []
    for _key, group in by_dir.items():
        group.sort(key=_sort_key(opt))
        folder = group[0].dir
        try:
            with os.scandir(xp(folder)) as it:
                existing = {nkey(de.name) for de in it}
        except OSError:
            existing = set()
        counter = opt.start
        planned: list[tuple[FileEntry, str, str]] = []
        for e in group:
            stem, ext = split_ext(e.name)
            status = "ok"
            if opt.template.strip():
                values = R.template_values(e, lang, kind_label)
                text, missing = R.render_component(opt.template.strip(), values, lang, use_fallback=False,
                                                   counter=f"{counter:0{opt.padding}d}")
                if missing or not text.strip():
                    status = "missing_info"
                else:
                    stem = text
            new_stem = transform_stem(stem, opt)
            if opt.numbering in ("prefix", "suffix"):
                num = f"{counter:0{max(1, opt.padding)}d}"
                new_stem = f"{num}{opt.num_sep}{new_stem}" if opt.numbering == "prefix" else \
                    f"{new_stem}{opt.num_sep}{num}"
            counter += 1
            if opt.fix_ext and e.ext_mismatch and e.real_ext:
                ext = "." + e.real_ext
            if opt.ext_case == "lower":
                ext = ext.lower()
            elif opt.ext_case == "upper":
                ext = ext.upper()
            new_stem = sanitize_component(new_stem, max_len=200, fallback=stem or "_")
            planned.append((e, new_stem + ext, status))
        # résolution des conflits : un nom ne peut être pris que par un seul fichier.
        # Les noms que des fichiers de ce lot vont quitter sont libres (renumérotation, échange de noms) :
        # l'ordre d'exécution est calculé ensuite par to_plan().
        leaving = {nkey(e.name) for e, name, _status in planned if nkey(name) != nkey(e.name)}
        taken = existing - leaving
        for e, name, status in planned:
            own = nkey(e.name)
            if name == e.name:
                items.append(RenameItem(e, name, "same", False))
                continue
            key = nkey(name)
            if key == own:  # changement de casse uniquement
                taken.add(key)
                items.append(RenameItem(e, name, status))
                continue
            if key in taken:
                name = unique_name(name, taken)
                status = "renamed_conflict"
                key = nkey(name)
            taken.add(key)
            items.append(RenameItem(e, name, status))
    return items


TEMP_RULE = "rename_tmp"  # étape intermédiaire d'un échange de noms (non comptée comme un fichier)


def _safe_order(ops: list[PlannedOp]) -> list[PlannedOp]:
    """Ordonne les renommages pour qu'aucun ne vise un nom encore occupé par un autre fichier du lot :
    « b → c » passe avant « a → b ». Un cycle (a ↔ b) passe par un nom temporaire."""
    by_src = {nkey(op.src): op for op in ops}
    done: set[int] = set()
    ordered: list[PlannedOp] = []
    temp_n = 0
    for start in ops:
        if id(start) in done:
            continue
        path: list[PlannedOp] = []
        on_path: set[int] = set()
        cur = start
        cycle_at = None
        while True:
            path.append(cur)
            on_path.add(id(cur))
            blocker = by_src.get(nkey(cur.dst))
            if blocker is None or blocker is cur or id(blocker) in done:
                break
            if id(blocker) in on_path:
                cycle_at = blocker
                break
            cur = blocker
        if cycle_at is not None:
            # on écarte d'abord le fichier qui ferme la boucle sous un nom temporaire
            folder = os.path.dirname(cycle_at.src)
            existing = set(by_src)
            while True:
                temp_n += 1
                tmp = os.path.join(folder, f"~rangeo-renommage-{temp_n}{split_ext(cycle_at.src)[1]}")
                if nkey(tmp) not in existing and not os.path.lexists(xp(tmp)):
                    break
            ordered.append(PlannedOp(OP_MOVE, cycle_at.src, tmp, 0, cycle_at.entry, TEMP_RULE,
                                     note="rename.temporary"))
            del by_src[nkey(cycle_at.src)]
            cycle_at.src = tmp
            by_src[nkey(tmp)] = cycle_at
        for op in reversed(path):
            ordered.append(op)
            done.add(id(op))
    return ordered


def to_plan(items: list[RenameItem]) -> Plan:
    plan = Plan(dest_root="")
    for item in items:
        if not item.enabled or not item.changed:
            continue
        e = item.entry
        plan.ops.append(PlannedOp(OP_MOVE, e.path, os.path.join(e.dir, item.new_name), e.size, e, "rename",
                                  note="rename." + item.status if item.status != "ok" else ""))
    plan.ops = _safe_order(plan.ops)
    for i, op in enumerate(plan.ops, 1):
        op.id = i
    plan.sources = sorted({op.entry.root for op in plan.ops if op.entry and op.entry.root})
    return plan
