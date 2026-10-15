"""Dossiers protégés : Rangeo refuse catégoriquement d'y toucher."""

from __future__ import annotations

import os
import sys

from . import appdirs
from .fsutil import is_within, nkey, norm

# Noms de dossiers système présents à la racine de n'importe quel disque
PROTECTED_COMPONENTS = {
    "$recycle.bin",
    "system volume information",
    "$windows.~bt",
    "$windows.~ws",
    "$winreagent",
    "$sysreset",
    "windowsapps",
    "config.msi",
    "recovery",
    "boot",
    "efi",
    "msocache",
    "$getcurrent",
}

# Noms protégés uniquement s'ils sont à la racine d'un disque (ex. D:\\Recovery)
_ROOT_ONLY = {"recovery", "boot", "efi", "msocache", "config.msi", "$getcurrent"}


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return norm(value) if value else None


def system_protected_paths(extra: list[str] | None = None) -> list[str]:
    windir = _env("WINDIR") or r"C:\Windows"
    sysdrive = (os.environ.get("SystemDrive") or "C:") + "\\"
    paths = [
        windir,
        _env("ProgramFiles"),
        _env("ProgramFiles(x86)"),
        _env("ProgramW6432"),
        _env("ProgramData"),
        _env("APPDATA"),
        _env("LOCALAPPDATA"),
        os.path.join(sysdrive, "PerfLogs"),
        os.path.join(sysdrive, "Intel"),
        os.path.join(sysdrive, "AMD"),
        os.path.join(sysdrive, "NVIDIA"),
        os.path.join(sysdrive, "OneDriveTemp"),
        str(appdirs.data_dir()),
        # le programme lui-même (dossier de l'exe, ou du code source en développement)
        str(appdirs.app_dir()) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(__file__)),
    ]
    local = _env("LOCALAPPDATA")
    if local:
        paths.append(os.path.dirname(local))  # ...\AppData
    for p in extra or []:
        if p:
            paths.append(norm(p))
    seen: set[str] = set()
    result = []
    for p in paths:
        if p and nkey(p) not in seen:
            seen.add(nkey(p))
            result.append(p)
    return result


def users_root() -> str:
    profile = _env("USERPROFILE")
    if profile:
        return os.path.dirname(profile)
    return r"C:\Users"


class Protection:
    """Vérifie qu'un chemin peut être lu/déplacé/écrit par Rangeo."""

    def __init__(self, extra_protected: list[str] | None = None):
        self.paths = system_protected_paths(extra_protected)
        self.sysdrive_root = norm((os.environ.get("SystemDrive") or "C:") + "\\")
        self.users_root = users_root()

    def is_protected(self, path: str) -> bool:
        for p in self.paths:
            if is_within(path, p):
                return True
        parts = [x for x in nkey(path).split("\\") if x]
        for i, part in enumerate(parts):
            if part in PROTECTED_COMPONENTS:
                # la partie 0 est la lettre de lecteur : i == 1 => à la racine du disque
                if part in _ROOT_ONLY and i != 1:
                    continue
                return True
        return False

    def source_problem(self, path: str) -> str | None:
        """Raison (clé de traduction) pour laquelle un dossier source est refusé, sinon None."""
        path = norm(path)
        if self.is_protected(path):
            return "protect.system_folder"
        if nkey(path) == nkey(self.sysdrive_root):
            return "protect.system_drive"
        if nkey(path) == nkey(self.users_root) or nkey(os.path.dirname(path)) == nkey(self.users_root):
            return "protect.profile_root"
        return None

    def destination_problem(self, path: str) -> str | None:
        path = norm(path)
        if self.is_protected(path):
            return "protect.system_folder"
        if nkey(path) == nkey(self.users_root):
            return "protect.profile_root"
        return None
