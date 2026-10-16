"""Moteur de règles : conditions SI / ALORS et modèles de chemins avec variables."""

from __future__ import annotations

import datetime as _dt
import os
import re
import time
import uuid
from dataclasses import dataclass

from . import filetypes as ft
from .fsutil import sanitize_component, split_ext
from .models import FileEntry

# ---------------------------------------------------------------------------
# Champs et opérateurs
# ---------------------------------------------------------------------------
FIELD_TYPES = {
    "name": "text",
    "stem": "text",
    "ext": "text",
    "kind": "enum",
    "group": "enum",
    "size": "size",
    "date": "date",
    "modified": "date",
    "created": "date",
    "path": "text",
    "folder": "text",
    "camera": "text",
    "country": "text",
    "city": "text",
    "product": "text",
    "content": "text",
    "artist": "text",
    "album": "text",
    "title": "text",
    "genre": "text",
    "year": "number",
    "series": "text",
    "width": "number",
    "height": "number",
    "duration": "number",
    "is_bundle": "bool",
    "ext_mismatch": "bool",
}
OPERATORS = {
    "text": ["equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with", "matches",
             "in_list", "not_in_list", "exists", "not_exists"],
    "enum": ["is", "is_not", "in_list", "not_in_list"],
    "number": ["eq", "ne", "gt", "gte", "lt", "lte", "between", "exists", "not_exists"],
    "size": ["gt", "gte", "lt", "lte", "between"],
    "date": ["before", "after", "between", "older_than_days", "newer_than_days", "in_year"],
    "bool": ["is_true", "is_false"],
}
ACTIONS = ["move", "copy", "ignore", "trash"]

MONTHS = {
    "fr": ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre",
           "Novembre", "Décembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
}
FALLBACKS = {
    "fr": {"artist": "Artiste inconnu", "album": "Album inconnu", "camera": "Appareil inconnu",
           "series": "Série inconnue", "year": "Sans date", "kind": "Divers", "console": "Autre console",
           "country": "Pays inconnu", "city": "Lieu inconnu", "product": "Autres", "version": "?"},
    "en": {"artist": "Unknown artist", "album": "Unknown album", "camera": "Unknown camera",
           "series": "Unknown series", "year": "Undated", "kind": "Misc", "console": "Other console",
           "country": "Unknown country", "city": "Unknown place", "product": "Others", "version": "?"},
}

# variable du modèle -> nom canonique
VARIABLES = {
    "annee": "year", "year": "year",
    "mois": "month", "month": "month",
    "mois_nom": "month_name", "month_name": "month_name",
    "jour": "day", "day": "day",
    "type": "kind", "kind": "kind",
    "groupe": "group", "group": "group",
    "ext": "ext",
    "nom": "stem", "name": "stem",
    "nom_complet": "filename", "filename": "filename",
    "dossier": "folder", "folder": "folder",
    "artiste": "artist", "artist": "artist",
    "album": "album",
    "titre": "title", "title": "title",
    "piste": "track", "track": "track",
    "disque": "disc", "disc": "disc",
    "annee_sortie": "release_year", "release_year": "release_year",
    "appareil": "camera", "camera": "camera",
    "serie": "series", "series": "series",
    "saison": "season", "season": "season",
    "episode": "episode",
    "genre": "genre",
    "console": "console",
    "heure": "hour", "hour": "hour",
    "minute": "minute",
    "seconde": "second", "second": "second",
    "pays": "country", "country": "country",
    "ville": "city", "city": "city",
    "logiciel": "product", "software": "product",
    "version": "version",
    "compteur": "counter", "counter": "counter",
}
# variables proposées dans l'interface (noms français)
VARIABLE_LIST = ["annee", "mois", "mois_nom", "jour", "heure", "minute", "seconde", "type", "ext", "nom",
                 "nom_complet", "dossier", "pays", "ville", "appareil", "artiste", "album", "titre", "piste",
                 "annee_sortie", "serie", "saison", "episode", "genre", "logiciel", "version", "console", "compteur"]
VARIABLE_LIST_EN = ["year", "month", "month_name", "day", "hour", "minute", "second", "kind", "ext", "name",
                    "filename", "folder", "country", "city", "camera", "artist", "album", "title", "track",
                    "release_year", "series", "season", "episode", "genre", "software", "version", "console",
                    "counter"]

_VAR_RE = re.compile(r"\{([a-zA-Z_]+)\}")
_FEAT_RE = re.compile(r"\s+(?:[(\[]\s*(?:feat\.?|ft\.?|featuring)|feat\.|ft\.|featuring)\s+.*$",
                      re.IGNORECASE)


def main_artist(artist: str | None) -> str | None:
    """« Daft Punk feat. Pharrell Williams » → « Daft Punk » (un seul dossier par artiste)."""
    if not artist:
        return artist
    main = _FEAT_RE.sub("", " " + artist).strip()
    return main or artist
_OPT_RE = re.compile(r"\[([^\[\]]*\{[a-zA-Z_]+\}[^\[\]]*)\]")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Valeurs d'un fichier
# ---------------------------------------------------------------------------
def field_value(entry: FileEntry, name: str):
    meta = entry.meta
    if name == "name":
        return entry.name
    if name == "stem":
        return entry.stem
    if name == "ext":
        return entry.ext
    if name == "kind":
        return entry.kind
    if name == "group":
        return ft.group_of(entry.kind)
    if name == "size":
        return entry.size
    if name == "date":
        return entry.date
    if name == "modified":
        return entry.mtime
    if name == "created":
        return entry.ctime
    if name == "path":
        return entry.path
    if name == "folder":
        return os.path.basename(entry.dir)
    if name == "artist":
        return meta.get("albumartist") or meta.get("artist")
    if name == "year":
        return meta.get("release_year") or meta.get("year")
    if name == "is_bundle":
        return entry.is_bundle
    if name == "ext_mismatch":
        return entry.ext_mismatch
    if name == "content":
        from .textcontent import read_text

        if "_content" not in meta:
            meta["_content"] = read_text(entry.path, entry.ext, entry.size)
        return meta["_content"]
    return meta.get(name)


def _to_number(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_size(str(value))


_SIZE_UNITS = {
    "": 1, "o": 1, "b": 1, "octet": 1, "octets": 1,
    "k": 1024, "ko": 1024, "kb": 1024, "kio": 1024, "kib": 1024,
    "m": 1024 ** 2, "mo": 1024 ** 2, "mb": 1024 ** 2, "mio": 1024 ** 2, "mib": 1024 ** 2,
    "g": 1024 ** 3, "go": 1024 ** 3, "gb": 1024 ** 3, "gio": 1024 ** 3, "gib": 1024 ** 3,
    "t": 1024 ** 4, "to": 1024 ** 4, "tb": 1024 ** 4,
}


def parse_size(text: str) -> float | None:
    m = re.match(r"^\s*([\d.,]+)\s*([a-zA-Z]*)\s*$", str(text))
    if not m:
        return None
    try:
        number = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    unit = _SIZE_UNITS.get(m.group(2).lower())
    return None if unit is None else number * unit


def _to_ts(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return _dt.datetime.fromisoformat(str(value).strip()).timestamp()
    except ValueError:
        return None


def _split_list(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"[,;]", str(value or ""))
    return [str(x).strip().lower().lstrip(".") for x in items if str(x).strip()]


def eval_condition(entry: FileEntry, cond: dict) -> bool:
    if "conditions" in cond:  # groupe imbriqué
        return eval_group(entry, cond.get("match", "all"), cond["conditions"])
    fname = cond.get("field", "")
    op = cond.get("op", "")
    value = cond.get("value")
    actual = field_value(entry, fname)
    ftype = FIELD_TYPES.get(fname, "text")

    if op == "exists":
        return actual not in (None, "")
    if op == "not_exists":
        return actual in (None, "")
    if op == "is_true":
        return bool(actual)
    if op == "is_false":
        return not actual

    if ftype in ("text", "enum"):
        a = "" if actual is None else str(actual).lower()
        if fname == "ext":
            a = a.lstrip(".")
        v = "" if value is None else str(value).lower()
        if fname == "ext":
            v = v.lstrip(".")
        if op in ("equals", "is"):
            return a == v
        if op in ("not_equals", "is_not"):
            return a != v
        if op == "contains":
            return v in a
        if op == "not_contains":
            return v not in a
        if op == "starts_with":
            return a.startswith(v)
        if op == "ends_with":
            return a.endswith(v)
        if op == "matches":
            try:
                return re.search(str(value or ""), "" if actual is None else str(actual), re.I) is not None
            except re.error:
                return False
        if op == "in_list":
            return a in _split_list(value)
        if op == "not_in_list":
            return a not in _split_list(value)
        return False

    if ftype in ("number", "size"):
        a = _to_number(actual)
        if a is None:
            return False
        if op == "between":
            lo, hi = value if isinstance(value, (list, tuple)) and len(value) == 2 else (None, None)
            lo, hi = _to_number(lo), _to_number(hi)
            return (lo is None or a >= lo) and (hi is None or a <= hi)
        v = _to_number(value)
        if v is None:
            return False
        return {
            "eq": a == v, "ne": a != v, "gt": a > v, "gte": a >= v, "lt": a < v, "lte": a <= v,
        }.get(op, False)

    if ftype == "date":
        a = _to_number(actual)
        if a is None:
            return False
        if op == "older_than_days":
            v = _to_number(value)
            return v is not None and a < time.time() - v * 86400
        if op == "newer_than_days":
            v = _to_number(value)
            return v is not None and a >= time.time() - v * 86400
        if op == "in_year":
            try:
                return _dt.datetime.fromtimestamp(a).year == int(value)
            except (TypeError, ValueError, OSError):
                return False
        if op == "between":
            lo, hi = value if isinstance(value, (list, tuple)) and len(value) == 2 else (None, None)
            lo, hi = _to_ts(lo), _to_ts(hi)
            if hi is not None:
                hi += 86399  # date de fin incluse
            return (lo is None or a >= lo) and (hi is None or a <= hi)
        v = _to_ts(value)
        if v is None:
            return False
        return a < v if op == "before" else a >= v if op == "after" else False
    return False


def eval_group(entry: FileEntry, match: str, conditions: list[dict]) -> bool:
    if not conditions:
        return True
    if match == "any":
        return any(eval_condition(entry, c) for c in conditions)
    return all(eval_condition(entry, c) for c in conditions)


# ---------------------------------------------------------------------------
# Modèles de chemins
# ---------------------------------------------------------------------------
def template_values(entry: FileEntry, lang: str, kind_label=None) -> dict[str, str | None]:
    meta = entry.meta
    values: dict[str, str | None] = {}
    if entry.date:
        try:
            d = _dt.datetime.fromtimestamp(entry.date)
            values.update(year=f"{d.year:04d}", month=f"{d.month:02d}", day=f"{d.day:02d}",
                          month_name=MONTHS.get(lang, MONTHS["fr"])[d.month - 1], hour=f"{d.hour:02d}",
                          minute=f"{d.minute:02d}", second=f"{d.second:02d}")
        except (OSError, OverflowError, ValueError):
            pass
    values["kind"] = kind_label(entry.kind) if kind_label else entry.kind
    values["group"] = ft.group_of(entry.kind)
    values["ext"] = entry.ext or None
    values["stem"] = entry.stem
    values["filename"] = entry.name
    values["folder"] = os.path.basename(entry.dir) or None
    values["artist"] = meta.get("albumartist") or main_artist(meta.get("artist"))
    values["album"] = meta.get("album")
    values["title"] = meta.get("title")
    values["track"] = f"{meta['track']:02d}" if isinstance(meta.get("track"), int) else None
    disc = meta.get("disc")
    # numéro de disque seulement pour un album en plusieurs disques (sinon « [{disque}-] » disparaît)
    values["disc"] = str(disc) if isinstance(disc, int) and (disc > 1 or (meta.get("disc_total") or 0) > 1) \
        else None
    ry = meta.get("release_year") or (meta.get("year") if entry.kind == "music" else None)
    values["release_year"] = str(ry) if ry else None
    values["camera"] = meta.get("camera")
    values["series"] = meta.get("series")
    values["season"] = f"{meta['season']:02d}" if isinstance(meta.get("season"), int) else None
    values["episode"] = f"{meta['episode']:02d}" if isinstance(meta.get("episode"), int) else None
    values["genre"] = meta.get("genre")
    values["console"] = meta.get("console")
    values["country"] = meta.get("country")
    values["city"] = meta.get("city")
    values["product"] = meta.get("product")
    values["version"] = meta.get("version")
    return {k: (str(v) if v not in (None, "") else None) for k, v in values.items()}


def _fallback(canonical: str, values: dict, lang: str) -> str | None:
    fb = FALLBACKS.get(lang, FALLBACKS["fr"])
    if canonical in ("year", "month", "day", "month_name", "hour", "minute", "second"):
        if canonical == "year":
            return fb["year"]
        return "" if canonical == "month_name" else "00"
    if canonical == "title":
        return values.get("stem")
    return fb.get(canonical)


def unknown_variables(template: str) -> list[str]:
    return [v for v in _VAR_RE.findall(template or "") if v.lower() not in VARIABLES]


def _substitute(text: str, values: dict, lang: str, use_fallback: bool, counter: str | None,
                missing: list[str]) -> str:
    def repl(m):
        name = m.group(1).lower()
        canonical = VARIABLES.get(name)
        if canonical is None:
            missing.append(name)
            return ""
        if canonical == "counter":
            return counter or "001"
        value = values.get(canonical)
        if value is None and use_fallback:
            value = _fallback(canonical, values, lang)
        if value is None:
            missing.append(name)
            return ""
        return value

    return _VAR_RE.sub(repl, text)


def render_component(template: str, values: dict, lang: str, use_fallback: bool = True,
                     counter: str | None = None) -> tuple[str, list[str]]:
    """Remplit un morceau de modèle. Les groupes [ … ] disparaissent si une variable y manque."""
    missing: list[str] = []

    def opt(m):
        inner_missing: list[str] = []
        text = _substitute(m.group(1), values, lang, False, counter, inner_missing)
        return "" if inner_missing else text

    text = _OPT_RE.sub(opt, template)
    text = _substitute(text, values, lang, use_fallback, counter, missing)
    return text, missing


def is_absolute_template(template: str) -> bool:
    t = (template or "").strip()
    return bool(re.match(r"^[a-zA-Z]:[\\/]", t)) or t.startswith("\\\\")


def render_target_dir(template: str, entry: FileEntry, dest_root: str, lang: str, kind_label=None,
                      counter: str | None = None) -> str:
    """Chemin du dossier de destination (absolu)."""
    values = template_values(entry, lang, kind_label)
    t = (template or "").strip()
    if is_absolute_template(t):
        drive_match = re.match(r"^([a-zA-Z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+[\\/]?)", t)
        base = drive_match.group(1)
        rest = t[len(base):]
        base = base.replace("/", "\\")
        if not base.endswith("\\"):
            base += "\\"
    else:
        base, rest = dest_root, t
    parts = []
    for raw in re.split(r"[\\/]+", rest):
        if not raw.strip():
            continue
        text, _ = render_component(raw, values, lang, True, counter)
        text = sanitize_component(text, max_len=120, fallback="")
        if not text or text in (".", ".."):
            continue
        parts.append(text)
    return os.path.join(base, *parts) if parts else base


def render_name(template: str, entry: FileEntry, lang: str, kind_label=None, counter: str | None = None,
                ext_override: str | None = None) -> str:
    """Nouveau nom de fichier. Si une variable manque, le nom d'origine est gardé."""
    original_stem = entry.stem
    ext = ext_override if ext_override is not None else (split_ext(entry.name)[1] if not entry.is_bundle else "")
    if not template or not template.strip() or entry.is_bundle:
        stem = original_stem
    else:
        values = template_values(entry, lang, kind_label)
        text, missing = render_component(template.strip(), values, lang, use_fallback=False, counter=counter)
        stem = original_stem if missing or not text.strip() else text
    stem = sanitize_component(stem, max_len=200, fallback=original_stem or "_")
    return stem + ext


def template_uses_counter(template: str) -> bool:
    return any(VARIABLES.get(v.lower()) == "counter" for v in _VAR_RE.findall(template or ""))


# ---------------------------------------------------------------------------
# Règles
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    rule_id: str
    rule_name: str
    action: str
    target: str
    rename: str


def normalize_rule(rule: dict) -> dict:
    out = {
        "id": rule.get("id") or new_id(),
        "name": rule.get("name") or "Règle",
        "enabled": bool(rule.get("enabled", True)),
        "match": rule.get("match", "all") if rule.get("match") in ("all", "any") else "all",
        "conditions": list(rule.get("conditions") or []),
        "action": rule.get("action", "move") if rule.get("action") in ACTIONS else "move",
        "target": rule.get("target", ""),
        "rename": rule.get("rename", ""),
    }
    if rule.get("builtin"):
        out["builtin"] = rule["builtin"]
    return out


_GETTERS = {
    "name": lambda e: e.name,
    "stem": lambda e: e.stem,
    "ext": lambda e: e.ext,
    "kind": lambda e: e.kind,
    "group": lambda e: ft.group_of(e.kind),
}


def compile_condition(cond: dict):
    """Transforme une condition en fonction rapide (évaluée des millions de fois)."""
    if "conditions" in cond:
        return compile_group(cond.get("match", "all"), cond["conditions"])
    fname, op, value = cond.get("field", ""), cond.get("op", ""), cond.get("value")
    getter = _GETTERS.get(fname)
    if getter is not None and op in ("is", "equals", "is_not", "not_equals"):
        wanted = "" if value is None else str(value).lower()
        if fname == "ext":
            wanted = wanted.lstrip(".")
        if op in ("is", "equals"):
            return lambda e: (getter(e) or "").lower() == wanted
        return lambda e: (getter(e) or "").lower() != wanted
    if getter is not None and op in ("in_list", "not_in_list"):
        items = frozenset(_split_list(value))
        if op == "in_list":
            return lambda e: (getter(e) or "").lower().lstrip(".") in items
        return lambda e: (getter(e) or "").lower().lstrip(".") not in items
    return lambda e: eval_condition(e, cond)


def compile_group(match: str, conditions: list[dict]):
    preds = [compile_condition(c) for c in conditions or []]
    if not preds:
        return lambda e: True
    if len(preds) == 1:
        return preds[0]
    if match == "any":
        return lambda e: any(p(e) for p in preds)
    return lambda e: all(p(e) for p in preds)


def compile_rules(rules: list[dict]) -> list[tuple[dict, object]]:
    return [(r, compile_group(r.get("match", "all"), r.get("conditions") or []))
            for r in rules if r.get("enabled", True)]


def decide_compiled(entry: FileEntry, compiled: list[tuple[dict, object]]) -> Decision | None:
    for rule, pred in compiled:
        if pred(entry):
            return Decision(rule.get("id", ""), rule.get("name", ""), rule.get("action", "move"),
                            rule.get("target", ""), rule.get("rename", ""))
    return None


def decide(entry: FileEntry, rules: list[dict]) -> Decision | None:
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        if eval_group(entry, rule.get("match", "all"), rule.get("conditions") or []):
            return Decision(
                rule_id=rule.get("id", ""),
                rule_name=rule.get("name", ""),
                action=rule.get("action", "move"),
                target=rule.get("target", ""),
                rename=rule.get("rename", ""),
            )
    return None


def validate_rule(rule: dict) -> list[str]:
    """Liste de problèmes (clés de traduction)."""
    problems = []
    if rule.get("action") in ("move", "copy"):
        if not (rule.get("target") or "").strip():
            problems.append("rules.err_no_target")
        if unknown_variables(rule.get("target", "")) or unknown_variables(rule.get("rename", "")):
            problems.append("rules.err_unknown_var")
        if ".." in re.split(r"[\\/]", rule.get("target", "")):
            problems.append("rules.err_parent")

    def check(conds):
        for c in conds:
            if "conditions" in c:
                check(c["conditions"])
                continue
            if c.get("op") == "matches":
                try:
                    re.compile(str(c.get("value") or ""))
                except re.error:
                    problems.append("rules.err_regex")

    check(rule.get("conditions") or [])
    return problems
