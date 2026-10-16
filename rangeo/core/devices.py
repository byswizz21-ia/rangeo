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


def _drive_type(root: str) -> str:
    return DRIVE_TYPES.get(_k32.GetDriveTypeW(root), "unknown")


def _volume_info(root: str, dtype: str) -> Volume | None:
    """Nom et numéro de série du volume, ou None s'il n'y a pas de support (lecteur de cartes vide…)."""
    label = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD()
    maxlen = wintypes.DWORD()
    flags = wintypes.DWORD()
    if not _k32.GetVolumeInformationW(root, label, 261, ctypes.byref(serial), ctypes.byref(maxlen),
                                      ctypes.byref(flags), fs, 261):
        return None
    return Volume(root, label.value, f"{serial.value:08X}", dtype)


def volumes(skip_network: bool = False) -> list[Volume]:
    """Volumes présents (avec un support inséré).

    skip_network : ne pas interroger les lecteurs réseau (un serveur injoignable peut bloquer plusieurs secondes)."""
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
            dtype = _drive_type(root)
            if dtype in ("invalid", "unknown") or skip_network and dtype == "network":
                continue
            vol = _volume_info(root, dtype)
            if vol:
                out.append(vol)
    finally:
        _k32.SetErrorMode(old_mode)
    return out


class DeviceMonitor:
    """À appeler régulièrement (toutes les quelques secondes, dans l'interface) : renvoie les volumes
    nouvellement branchés.

    Reste très léger : un disque déjà connu n'est pas réinterrogé (pas de réveil d'un disque en veille),
    les lecteurs réseau sont ignorés (pas de blocage), seuls les lecteurs amovibles sont revérifiés
    (carte mémoire insérée dans un lecteur dont la lettre existe déjà)."""

    def __init__(self):
        self._mask = 0
        self._removable: set[str] = set()
        self._known: dict[str, str] = {}  # « E:\\ » -> numéro de série ("" = pas de support)
        self.poll()  # état initial : rien n'est « nouveau »

    def poll(self) -> list[Volume]:
        if not IS_WINDOWS:
            return []
        mask = _k32.GetLogicalDrives()
        new: list[Volume] = []
        old_mode = _k32.SetErrorMode(0x0001)
        try:
            for i, letter in enumerate(string.ascii_uppercase):
                root = f"{letter}:\\"
                if not mask & (1 << i):
                    self._known.pop(root, None)
                    continue
                appeared = not self._mask & (1 << i)
                if not appeared and self._known.get(root) and root not in self._removable:
                    continue
                dtype = _drive_type(root)
                if dtype in ("invalid", "unknown", "network"):
                    continue
                vol = _volume_info(root, dtype)
                serial = vol.serial if vol else ""
                if vol and self._known.get(root) != serial:
                    new.append(vol)
                self._known[root] = serial
                if dtype in ("removable", "cdrom"):
                    self._removable.add(root)
                else:
                    self._removable.discard(root)
        finally:
            _k32.SetErrorMode(old_mode)
        first = self._mask == 0
        self._mask = mask
        return [] if first else new


def find_rule(device_rules: list[dict], volume: Volume) -> dict | None:
    for rule in device_rules or []:
        if rule.get("serial") == volume.serial:
            return rule
    return None
