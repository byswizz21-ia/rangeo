"""Lecture du texte d'un fichier (pour la condition « contenu du texte » des règles).

Formats lus : texte brut (txt, md, csv, json, xml, html, srt…), Word (.docx), OpenDocument (.odt).
Seul le début du fichier est lu (1 Mo) pour rester rapide.
"""

from __future__ import annotations

import html
import re
import zipfile

from .fsutil import xp

MAX_BYTES = 1024 * 1024
TEXT_EXT = set(
    "txt md markdown csv tsv json jsonc xml html htm xhtml srt vtt ass ssa ini cfg conf log nfo yaml yml toml "
    "rtf tex rst sql py js ts css".split()
)
_TAGS = re.compile(r"<[^>]+>")
_RTF_CTRL = re.compile(r"\\[a-z]+-?\d* ?|[{}]")


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "ignore")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start >= len(data) - 4:  # lecture limitée à 1 Mo : dernier caractère coupé en deux
            return data[:exc.start].decode("utf-8", "ignore")
        return data.decode("cp1252", "ignore")


def read_text(path: str, ext: str, size: int = 0) -> str:
    """Texte du fichier (chaîne vide si illisible ou format non pris en charge)."""
    ext = (ext or "").lower()
    try:
        if ext in TEXT_EXT:
            with open(xp(path), "rb") as fh:
                text = _decode(fh.read(MAX_BYTES))
            if ext == "rtf":
                text = _RTF_CTRL.sub(" ", text)
            elif ext in ("html", "htm", "xhtml", "xml"):
                text = html.unescape(_TAGS.sub(" ", text))
            return text
        if ext in ("docx", "docm", "odt"):
            inner = "word/document.xml" if ext != "odt" else "content.xml"
            with zipfile.ZipFile(xp(path)) as zf:
                with zf.open(inner) as fh:
                    xml = fh.read(MAX_BYTES * 4).decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>|<text:p[^>]*>", "\n", xml)
            return html.unescape(_TAGS.sub("", xml))
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError, ValueError):
        return ""
    return ""
