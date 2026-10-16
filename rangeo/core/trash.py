"""Corbeille sûre.

* Disque interne : corbeille Windows (send2trash), après avoir vérifié que la
  corbeille est active et assez grande (sinon Windows supprimerait
  définitivement le fichier sans prévenir).
* Clé USB, carte SD, lecteur réseau (pas de corbeille) : le fichier est
  déplacé dans un dossier « Rangeo - Corbeille » à la racine du même disque.

Dans tous les cas le fichier peut être restauré (annulation).
"""

from __future__ import annotations

import logging
import os
import re
import struct
import time

from .fsutil import (
    disk_usage,
    has_recycle_bin,
    nkey,
    sanitize_component,
    unique_name,
    volume_guid,
    volume_root,
    xp,
)

log = logging.getLogger(__name__)

QUARANTINE_DIR = "Rangeo - Corbeille"
_BITBUCKET = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"


def _recycle_limits(path: str) -> tuple[bool, int | None]:
    """(corbeille active, taille max en octets) pour le volume du chemin."""
    try:
        import winreg

        guid = volume_guid(path) or ""
        m = re.search(r"\{[0-9a-fA-F-]+\}", guid)
        if not m:
            return True, None
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{_BITBUCKET}\\{m.group(0)}") as key:
            try:
                nuke = winreg.QueryValueEx(key, "NukeOnDelete")[0]
            except OSError:
                nuke = 0
            try:
                max_mb = winreg.QueryValueEx(key, "MaxCapacity")[0]
            except OSError:
                max_mb = None
        return (not nuke), (int(max_mb) * 1024 * 1024 if max_mb else None)
    except OSError:
        return True, None


def _tree_size(path: str) -> int:
    if not os.path.isdir(xp(path)):
        try:
            return os.stat(xp(path)).st_size
        except OSError:
            return 0
    total = 0
    for dirpath, _dirs, files in os.walk(xp(path)):
        for name in files:
            try:
                total += os.stat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def can_use_recycle_bin(path: str, size: int | None = None) -> bool:
    if not has_recycle_bin(path):
        return False
    enabled, max_bytes = _recycle_limits(path)
    if not enabled:
        return False
    if size is None:
        size = _tree_size(path)
    if max_bytes is None:
        try:
            max_bytes = int(disk_usage(path)[0] * 0.05)  # valeur par défaut prudente
        except OSError:
            max_bytes = 0
    return size < max_bytes * 0.9


def quarantine_target(path: str, session_id: int) -> str:
    root = volume_root(path)
    drive_less = os.path.splitdrive(path)[1].lstrip("\\")
    rel_root = os.path.relpath(path, root) if nkey(path).startswith(nkey(root)) else drive_less
    stamp = time.strftime("%Y-%m-%d %H%M")
    parts = [sanitize_component(p) for p in rel_root.split(os.sep) if p]
    return os.path.join(root, QUARANTINE_DIR, f"{stamp} - session {session_id}", *parts)


def trash(path: str, session_id: int = 0, size: int | None = None) -> dict:
    """Met un fichier ou dossier à la corbeille. Renvoie de quoi le restaurer."""
    if can_use_recycle_bin(path, size):
        from send2trash import send2trash

        try:  # taille notée pour retrouver le bon élément dans la corbeille à l'annulation
            file_size = os.stat(xp(path)).st_size if os.path.isfile(xp(path)) else None
        except OSError:
            file_size = None
        when = time.time()
        try:
            send2trash(xp(path) if len(path) >= 240 else path)
        except Exception as exc:
            log.warning("Corbeille Windows impossible pour %s (%s) : mise en quarantaine", path, exc)
        else:
            if not os.path.lexists(xp(path)):
                info = {"method": "recycle", "time": when}
                if file_size is not None:
                    info["size"] = file_size
                return info
    target = quarantine_target(path, session_id)
    folder = os.path.dirname(target)
    os.makedirs(xp(folder), exist_ok=True)
    if os.path.lexists(xp(target)):
        with os.scandir(xp(folder)) as it:
            taken = {nkey(de.name) for de in it}
        target = os.path.join(folder, unique_name(os.path.basename(target), taken))
    os.rename(xp(path), xp(target))  # même disque : simple renommage
    return {"method": "quarantine", "qpath": target}


# ---------------------------------------------------------------------------
# Restauration depuis la corbeille Windows
# ---------------------------------------------------------------------------
def _current_sid() -> str | None:
    try:
        import win32api
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        return win32security.ConvertSidToStringSid(sid)
    except Exception:
        return None


def _read_info(path: str) -> tuple[str, float] | None:
    """Lit un fichier $I de la corbeille : (chemin d'origine, date de suppression)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(4096)
        version = struct.unpack_from("<q", data, 0)[0]
        filetime = struct.unpack_from("<q", data, 16)[0]
        if version == 2:
            length = struct.unpack_from("<i", data, 24)[0]
            name = data[28:28 + length * 2].decode("utf-16-le", "ignore").rstrip("\x00")
        else:
            name = data[24:24 + 520].decode("utf-16-le", "ignore").split("\x00")[0]
        deleted = filetime / 10_000_000 - 11644473600
        return name, deleted
    except (OSError, struct.error):
        return None


def _pick(candidates: list[tuple[float, str, str, int | None]], when: float,
          size: int | None) -> tuple[str, str] | None:
    """Parmi les éléments supprimés au même emplacement, celui que Rangeo a mis à la corbeille :
    même taille, et supprimé au moment noté (pas une autre version supprimée plus tard à la main)."""
    if size is not None:
        same = [c for c in candidates if c[3] is None or c[3] == size]
        candidates = same or candidates
    if not candidates:
        return None
    if when > 0:
        best = min(candidates, key=lambda c: abs(c[0] - when))
    else:  # moment inconnu (coupure avant l'écriture du journal) : le plus récent
        best = max(candidates, key=lambda c: c[0])
    return best[1], best[2]


def find_in_recycle_bin(original: str, deleted_after: float, size: int | None = None) -> tuple[str, str] | None:
    """Renvoie ($R, $I) de l'élément supprimé à cet emplacement par Rangeo."""
    root = volume_root(original)
    bin_root = os.path.join(root, "$Recycle.Bin")
    sid = _current_sid()
    folders = [os.path.join(bin_root, sid)] if sid else []
    if not folders:
        try:
            folders = [os.path.join(bin_root, d) for d in os.listdir(bin_root)]
        except OSError:
            return None
    candidates: list[tuple[float, str, str, int | None]] = []
    for folder in folders:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not name.startswith("$I"):
                continue
            info = _read_info(os.path.join(folder, name))
            if not info:
                continue
            orig, deleted = info
            if nkey(orig) == nkey(original) and deleted >= deleted_after - 120:
                rpath = os.path.join(folder, "$R" + name[2:])
                if os.path.lexists(rpath):
                    try:
                        rsize = os.stat(rpath).st_size if os.path.isfile(rpath) else None
                    except OSError:
                        rsize = None
                    candidates.append((deleted, rpath, os.path.join(folder, name), rsize))
    return _pick(candidates, deleted_after, size)


def restore(original: str, info: dict, target: str | None = None) -> str:
    """Restaure un élément mis à la corbeille par Rangeo. Renvoie le chemin restauré."""
    target = target or original
    if os.path.lexists(xp(target)):
        folder = os.path.dirname(target)
        with os.scandir(xp(folder)) as it:
            taken = {nkey(de.name) for de in it}
        target = os.path.join(folder, unique_name(os.path.basename(target), taken))
    os.makedirs(xp(os.path.dirname(target)), exist_ok=True)
    if info.get("method") == "quarantine":
        os.rename(xp(info["qpath"]), xp(target))
        _cleanup_empty_quarantine(info["qpath"])
        return target
    found = None
    for _ in range(25):  # Windows écrit parfois l'entrée de la corbeille avec un léger délai
        found = find_in_recycle_bin(original, float(info.get("time", 0)), info.get("size"))
        if found:
            break
        time.sleep(0.2)
    if not found:
        raise FileNotFoundError(2, "not_in_recycle_bin", original)
    rpath, ipath = found
    os.rename(xp(rpath), xp(target))
    try:
        os.remove(ipath)
    except OSError:
        pass
    return target


def _cleanup_empty_quarantine(qpath: str) -> None:
    folder = os.path.dirname(qpath)
    for _ in range(64):
        if os.path.basename(folder) == QUARANTINE_DIR:
            try:
                os.rmdir(xp(folder))
            except OSError:
                pass
            return
        try:
            os.rmdir(xp(folder))
        except OSError:
            return
        folder = os.path.dirname(folder)
