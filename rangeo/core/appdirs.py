"""Emplacements des données de l'application (%APPDATA%\\Rangeo)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """Dossier des données utilisateur. RANGEO_HOME permet de l'isoler (tests)."""
    base = os.environ.get("RANGEO_HOME")
    if not base:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Rangeo")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def app_dir() -> Path:
    """Dossier d'installation du programme (exe ou sources)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def resource_path(*parts: str) -> Path:
    """Chemin d'une ressource embarquée (fonctionne aussi dans l'exe PyInstaller)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    if getattr(sys, "frozen", False):
        base = base / "rangeo"
    return base.joinpath(*parts)
