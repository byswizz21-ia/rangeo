"""Installateurs : nom du logiciel et version (ressources Windows ou nom du fichier)."""

from __future__ import annotations

import re

from .fsutil import xp

_NOISE = re.compile(
    r"(?<![a-z0-9])(setup|installer|install|installation|x64|x86|amd64|arm64|win64|win32|windows|win|64bit|32bit|"
    r"64-bit|32-bit|full|offline|online|web|portable|release|final|stable|latest|en-us|fr-fr|multi|mui|lang|"
    r"inst|bin)(?![a-z0-9])",
    re.I,
)
_VERSION = re.compile(r"(?<![0-9])v?(\d+(?:[._]\d+){1,4})(?![0-9])", re.I)


def _clean_name(text: str) -> str:
    text = _NOISE.sub(" ", text)
    text = re.sub(r"[._\-()\[\]]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if text and (text.islower() or text.isupper()):
        text = text.title()
    return text


def from_filename(stem: str) -> tuple[str | None, str | None]:
    """« vlc-3.0.20-win64 » → (« Vlc », « 3.0.20 »)."""
    version = None
    m = _VERSION.search(stem)
    name_part = stem
    if m:
        version = m.group(1).replace("_", ".")
        name_part = stem[: m.start()] or stem[m.end():]
    name = _clean_name(name_part)
    return (name or None), version


def from_resources(path: str) -> tuple[str | None, str | None]:
    """Lit ProductName / ProductVersion dans les ressources de l'exécutable."""
    try:
        import win32api

        trans = win32api.GetFileVersionInfo(xp(path), "\\VarFileInfo\\Translation")
        lang, codepage = trans[0] if trans else (0x0409, 0x04B0)
        base = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\"
        name = win32api.GetFileVersionInfo(xp(path), base + "ProductName")
        version = win32api.GetFileVersionInfo(xp(path), base + "ProductVersion")
    except Exception:
        return None, None
    name = (name or "").strip() or None
    version = (version or "").strip().replace(", ", ".").replace(",", ".") or None
    if name and len(name) > 60:
        name = name[:60]
    return name, version


def product_info(path: str, stem: str, ext: str) -> dict:
    name = version = None
    if ext == "exe":
        name, version = from_resources(path)
    f_name, f_version = from_filename(stem)
    name = name or f_name
    version = version or f_version
    out = {}
    if name:
        out["product"] = name
    if version:
        out["version"] = version
    return out


def version_key(version: str | None) -> tuple:
    if not version:
        return ()
    return tuple(int(x) for x in re.findall(r"\d+", version)[:5])


def product_key(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())
