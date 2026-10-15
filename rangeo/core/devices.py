"""Disques externes et clés USB : détection du branchement."""

from __future__ import annotations

import ctypes
import string
from ctypes import wintypes
from dataclasses import dataclass

from .fsutil import DRIVE_TYPES, IS_WINDOWS


@dataclass(frozen=True)
class Volume:
    root: str  # « E:\\ »
    label: str
    serial: str  # numéro de série du volume (identifie le disque même si la lettre change)
    kind: str  # removable | fixed | network | cdrom…

    @property
    def display(self) -> str:
        return f"{self.label or self.root.rstrip(chr(92))} ({self.root.rstrip(chr(92))})"


if IS_WINDOWS:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.GetLogicalDrives.restype = wintypes.DWORD
    _k32.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD,
    ]
    _k32.GetVolumeInformationW.restype = wintypes.BOOL
    _k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _k32.GetDriveTypeW.restype = wintypes.UINT
    _k32.SetErrorMode.argtypes = [wintypes.UINT]
    _k32.SetErrorMode.restype = wintypes.UINT


def volumes() -> list[Volume]:
    """Volumes présents (avec un support inséré)."""
    if not IS_WINDOWS:
        return []
    out = []
    mask = _k32.GetLogicalDrives()
    old_mode = _k32.SetErrorMode(0x0001)  # pas de boîte « insérez un disque » pour les lecteurs vides
    try:
        for i, letter in enumerate(string.ascii_uppercase):
            if not mask & (1 << i):
                continue
            root = f"{letter}:\\"
            dtype = DRIVE_TYPES.get(_k32.GetDriveTypeW(root), "unknown")
            if dtype in ("invalid", "unknown"):
                continue
            label = ctypes.create_unicode_buffer(261)
            fs = ctypes.create_unicode_buffer(261)
            serial = wintypes.DWORD()
            maxlen = wintypes.DWORD()
            flags = wintypes.DWORD()
            if not _k32.GetVolumeInformationW(root, label, 261, ctypes.byref(serial), ctypes.byref(maxlen),
                                              ctypes.byref(flags), fs, 261):
                continue  # lecteur sans support (lecteur de cartes vide…)
            out.append(Volume(root, label.value, f"{serial.value:08X}", dtype))
    finally:
        _k32.SetErrorMode(old_mode)
    return out


class DeviceMonitor:
    """À appeler régulièrement : renvoie les volumes nouvellement branchés."""

    def __init__(self):
        self._known = {(v.root, v.serial) for v in volumes()}

    def poll(self) -> list[Volume]:
        current = volumes()
        keys = {(v.root, v.serial) for v in current}
        new = [v for v in current if (v.root, v.serial) not in self._known]
        self._known = keys
        return new


def find_rule(device_rules: list[dict], volume: Volume) -> dict | None:
    for rule in device_rules or []:
        if rule.get("serial") == volume.serial:
            return rule
    return None
