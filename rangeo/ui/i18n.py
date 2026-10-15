"""Traductions : fichiers JSON par langue dans rangeo/i18n (fr.json, en.json…)."""

from __future__ import annotations

import json
import logging

from ..core.appdirs import resource_path

log = logging.getLogger(__name__)

LANGUAGES = {"fr": "Français", "en": "English"}
_current = "fr"
_strings: dict[str, dict[str, str]] = {}


def _load(lang: str) -> dict[str, str]:
    if lang not in _strings:
        path = resource_path("i18n", f"{lang}.json")
        try:
            with open(path, encoding="utf-8") as fh:
                _strings[lang] = json.load(fh)
        except (OSError, ValueError):
            log.exception("Traductions introuvables : %s", path)
            _strings[lang] = {}
    return _strings[lang]


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in LANGUAGES else "fr"
    _load(_current)


def language() -> str:
    return _current


def tr(key: str, **kwargs) -> str:
    text = _load(_current).get(key)
    if text is None:
        text = _load("fr").get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def kind_label(kind: str) -> str:
    return tr(f"kind.{kind}")


def plural(n: int, key: str, **kwargs) -> str:
    """tr('x.one') si n <= 1 sinon tr('x.many')."""
    return tr(f"{key}.{'one' if abs(n) <= 1 else 'many'}", n=fmt_int(n), **kwargs)


def fmt_int(n: int) -> str:
    text = f"{int(n):,}"
    return text.replace(",", " ") if _current == "fr" else text


def fmt_size(n: float) -> str:
    from ..core.fsutil import human_size

    return human_size(n, _current)


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def fmt_date(ts: float | None, with_time: bool = True) -> str:
    import datetime as dt

    if not ts:
        return "—"
    d = dt.datetime.fromtimestamp(ts)
    if _current == "fr":
        return d.strftime("%d/%m/%Y %H:%M" if with_time else "%d/%m/%Y")
    return d.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")
