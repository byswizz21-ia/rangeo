"""Outils bas niveau pour le système de fichiers Windows.

Tous les chemins manipulés en interne sont des chemins absolus "normaux"
(sans préfixe \\\\?\\). La fonction :func:`xp` ajoute le préfixe uniquement au
moment d'appeler le système, ce qui permet de gérer les chemins de plus de
260 caractères.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import threading
from ctypes import wintypes

IS_WINDOWS = os.name == "nt"

# --- Attributs de fichiers Windows -------------------------------------------
FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_ARCHIVE = 0x20
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_NOT_CONTENT_INDEXED = 0x2000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ATTRIBUTES = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
# Attributs recopiés lors d'une copie entre disques
COPYABLE_ATTRIBUTES = (
    FILE_ATTRIBUTE_READONLY | FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_ARCHIVE | FILE_ATTRIBUTE_NOT_CONTENT_INDEXED
)

LONG_PREFIX = "\\\\?\\"
UNC_PREFIX = "\\\\?\\UNC\\"
LONG_PATH_THRESHOLD = 240
MAX_COMPONENT = 255

# --- Codes d'erreur Windows ---------------------------------------------------
ERROR_ACCESS_DENIED = 5
ERROR_NOT_READY = 21
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
ERROR_HANDLE_DISK_FULL = 39
ERROR_BAD_NETPATH = 53
ERROR_NETNAME_DELETED = 64
ERROR_DISK_FULL = 112
ERROR_NO_SUCH_DEVICE = 433
ERROR_DEVICE_NOT_CONNECTED = 1167
ERROR_DEV_NOT_EXIST = 55


def winerror(exc: BaseException) -> int | None:
    return getattr(exc, "winerror", None)


def is_locked_error(exc: BaseException) -> bool:
    return winerror(exc) in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION)


def is_device_gone_error(exc: BaseException) -> bool:
    return winerror(exc) in (
        ERROR_NOT_READY,
        ERROR_BAD_NETPATH,
        ERROR_NETNAME_DELETED,
        ERROR_NO_SUCH_DEVICE,
        ERROR_DEVICE_NOT_CONNECTED,
        ERROR_DEV_NOT_EXIST,
    )


def is_disk_full_error(exc: BaseException) -> bool:
    import errno

    return winerror(exc) in (ERROR_DISK_FULL, ERROR_HANDLE_DISK_FULL) or getattr(exc, "errno", None) == errno.ENOSPC


# --- Chemins ------------------------------------------------------------------
def norm(path: str | os.PathLike) -> str:
    """Chemin absolu normalisé, sans préfixe long."""
    return os.path.normpath(os.path.abspath(unxp(os.fspath(path))))


def nkey(path: str) -> str:
    """Clé de comparaison (NTFS est insensible à la casse)."""
    return os.path.normcase(path)


def is_within(path: str, parent: str) -> bool:
    """Vrai si ``path`` est ``parent`` ou se trouve à l'intérieur."""
    p = nkey(path).rstrip("\\")
    q = nkey(parent).rstrip("\\")
    return p == q or p.startswith(q + "\\")


def is_strictly_within(path: str, parent: str) -> bool:
    p = nkey(path).rstrip("\\")
    q = nkey(parent).rstrip("\\")
    return p != q and p.startswith(q + "\\")


def xp(path: str) -> str:
    """Ajoute le préfixe \\\\?\\ si le chemin est long (API Win32 étendues)."""
    if not IS_WINDOWS or len(path) < LONG_PATH_THRESHOLD or path.startswith(LONG_PREFIX):
        return path
    path = os.path.normpath(path)
    if path.startswith("\\\\"):
        return UNC_PREFIX + path[2:]
    return LONG_PREFIX + path


def unxp(path: str) -> str:
    if path.startswith(UNC_PREFIX):
        return "\\\\" + path[len(UNC_PREFIX):]
    if path.startswith(LONG_PREFIX):
        return path[len(LONG_PREFIX):]
    return path


def split_ext(name: str) -> tuple[str, str]:
    """Sépare nom et extension ('.bashrc' n'a pas d'extension). L'extension garde le point."""
    stem, ext = os.path.splitext(name)
    if not stem:
        return name, ""
    return stem, ext


def existing_ancestor(path: str) -> str:
    """Plus proche dossier existant (le chemin lui-même s'il existe)."""
    current = path
    while True:
        if os.path.exists(xp(current)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return current
        current = parent


# --- Noms de fichiers valides -------------------------------------------------
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
    "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³",
}
_INVALID_MAP = {
    "/": "-", "\\": "-", ":": " -", "*": "", "?": "", '"': "'", "<": "(", ">": ")", "|": "-",
}
_CONTROL_RE = re.compile(r"[\x00-\x1f]")
_SPACES_RE = re.compile(r"\s{2,}")


def sanitize_component(name: str, max_len: int = 180, fallback: str = "_") -> str:
    """Rend un nom de fichier/dossier valide sous Windows."""
    if not name:
        return fallback
    out = _CONTROL_RE.sub("", "".join(_INVALID_MAP.get(c, c) for c in str(name)))
    out = _SPACES_RE.sub(" ", out).strip().rstrip(". ")
    if not out:
        return fallback
    base = out.split(".")[0].strip().upper()
    if base in _WIN_RESERVED:
        out = "_" + out
    if len(out) > max_len:
        stem, ext = split_ext(out)
        if len(ext) > 16:
            ext = ""
        out = stem[: max(1, max_len - len(ext))].rstrip(". ") + ext
    return out


def is_valid_component(name: str) -> bool:
    return bool(name) and sanitize_component(name, max_len=MAX_COMPONENT) == name


def unique_name(name: str, taken: set[str], pattern: str = "{stem} ({n}){ext}") -> str:
    """Trouve un nom libre. ``taken`` contient des noms normalisés (nkey)."""
    if nkey(name) not in taken:
        return name
    stem, ext = split_ext(name)
    stem = re.sub(r" \(\d+\)$", "", stem)
    n = 2
    while True:
        candidate = pattern.format(stem=stem, n=n, ext=ext)
        if nkey(candidate) not in taken:
            return candidate
        n += 1


# --- Horodatages et attributs ------------------------------------------------
def creation_time(st: os.stat_result) -> float:
    return getattr(st, "st_birthtime", None) or st.st_ctime


def file_attributes(st: os.stat_result) -> int:
    return getattr(st, "st_file_attributes", 0)


def is_cloud_placeholder(attrs: int) -> bool:
    """Fichier OneDrive/Dropbox non téléchargé : le lire déclencherait un téléchargement."""
    return bool(attrs & CLOUD_ATTRIBUTES)


def cloud_roots() -> list[str]:
    """Dossiers synchronisés connus (OneDrive personnel/professionnel, Dropbox, iCloud, Creative Cloud)."""
    roots = []
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(var)
        if value:
            roots.append(value)
    home = os.environ.get("USERPROFILE")
    if home:
        for name in ("Dropbox", "iCloudDrive", "Creative Cloud Files", "Google Drive"):
            roots.append(os.path.join(home, name))
    out = []
    for r in roots:
        r = norm(r)
        if r not in out:
            out.append(r)
    return out


def cloud_root_of(path: str, roots: list[str] | None = None) -> str | None:
    """Dossier synchronisé qui contient ``path`` (le plus précis), ou None."""
    best = None
    for root in cloud_roots() if roots is None else roots:
        if is_within(path, root) and (best is None or len(root) > len(best)):
            best = root
    return best


if IS_WINDOWS:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _k32.SetFileTime.restype = wintypes.BOOL
    _k32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    _k32.GetFileAttributesW.restype = wintypes.DWORD
    _k32.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    _k32.SetFileAttributesW.restype = wintypes.BOOL
    _k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _k32.GetDriveTypeW.restype = wintypes.UINT
    _k32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _k32.GetVolumePathNameW.restype = wintypes.BOOL
    _k32.GetVolumeNameForVolumeMountPointW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _k32.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
    _k32.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    _k32.DeviceIoControl.restype = wintypes.BOOL
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.SetPriorityClass.restype = wintypes.BOOL
    _k32.GetCurrentThread.restype = wintypes.HANDLE
    _k32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
    _k32.SetThreadPriority.restype = wintypes.BOOL

    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _GENERIC_READ = 0x80000000
    _FILE_WRITE_ATTRIBUTES = 0x100
    _FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
    _OPEN_EXISTING = 3
    _DELETE = 0x00010000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def _to_filetime(ts: float) -> ctypes.c_ulonglong:
    return ctypes.c_ulonglong(int(ts * 10_000_000) + 116444736000000000)


def set_creation_time(path: str, ts: float) -> bool:
    if not IS_WINDOWS:
        return False
    handle = _k32.CreateFileW(
        xp(path), _FILE_WRITE_ATTRIBUTES, _FILE_SHARE_ALL, None, _OPEN_EXISTING, _FILE_FLAG_BACKUP_SEMANTICS, None
    )
    if handle in (None, _INVALID_HANDLE):
        return False
    try:
        ft = _to_filetime(ts)
        return bool(_k32.SetFileTime(handle, ctypes.byref(ft), None, None))
    finally:
        _k32.CloseHandle(handle)


def ensure_deletable(path: str) -> None:
    """Vérifie, AVANT une copie vers un autre disque, que l'original pourra être supprimé ensuite.

    Lève l'erreur « fichier ouvert dans un autre programme » si un programme le garde ouvert sans
    autoriser sa suppression : sinon on copierait pour rien et une copie resterait à la destination."""
    if not IS_WINDOWS:
        return
    handle = _k32.CreateFileW(xp(path), _DELETE, _FILE_SHARE_ALL, None, _OPEN_EXISTING,
                              _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT, None)
    if handle in (None, _INVALID_HANDLE):
        err = ctypes.get_last_error()
        if err in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
            raise OSError(0, "in_use", path, err)
        return  # autre souci (droits…) : l'opération elle-même le signalera
    _k32.CloseHandle(handle)


def get_attributes(path: str) -> int:
    if not IS_WINDOWS:
        return 0
    value = _k32.GetFileAttributesW(xp(path))
    return 0 if value == _INVALID_FILE_ATTRIBUTES else value


def set_attributes(path: str, attrs: int) -> bool:
    if not IS_WINDOWS:
        return False
    return bool(_k32.SetFileAttributesW(xp(path), attrs or FILE_ATTRIBUTE_NORMAL))


def clear_readonly(path: str) -> None:
    attrs = get_attributes(path)
    if attrs & FILE_ATTRIBUTE_READONLY:
        set_attributes(path, attrs & ~FILE_ATTRIBUTE_READONLY)


def copy_metadata(src_stat: os.stat_result, dst: str) -> None:
    """Recopie dates (création, modification, accès) et attributs."""
    os.utime(xp(dst), ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))
    set_creation_time(dst, creation_time(src_stat))
    attrs = file_attributes(src_stat) & COPYABLE_ATTRIBUTES
    if attrs:
        set_attributes(dst, attrs)


# --- Volumes et disques -------------------------------------------------------
DRIVE_TYPES = {0: "unknown", 1: "invalid", 2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}

_volume_cache: dict[str, str] = {}
_kind_cache: dict[str, str] = {}
_cache_lock = threading.Lock()


def volume_root(path: str) -> str:
    """Racine du volume (ex. 'C:\\\\' ou point de montage)."""
    path = existing_ancestor(path)
    if IS_WINDOWS:
        buf = ctypes.create_unicode_buffer(1024)
        if _k32.GetVolumePathNameW(xp(path), buf, 1024):
            return unxp(buf.value)
    drive, _ = os.path.splitdrive(path)
    return (drive + "\\") if drive else os.path.abspath(os.sep)


def volume_guid(path: str) -> str | None:
    root = volume_root(path)
    with _cache_lock:
        if root in _volume_cache:
            return _volume_cache[root]
    guid = None
    if IS_WINDOWS:
        buf = ctypes.create_unicode_buffer(128)
        mount = root if root.endswith("\\") else root + "\\"
        if _k32.GetVolumeNameForVolumeMountPointW(mount, buf, 128):
            guid = buf.value
    with _cache_lock:
        _volume_cache[root] = guid
    return guid


def volume_id(path: str) -> int:
    """Identifiant du volume (numéro de série) – permet de savoir si un déplacement est un simple renommage."""
    return os.stat(xp(existing_ancestor(path))).st_dev


def same_volume(a: str, b: str) -> bool:
    try:
        return volume_id(a) == volume_id(b)
    except OSError:
        return False


def drive_type(path: str) -> str:
    if not IS_WINDOWS:
        return "fixed"
    root = volume_root(path)
    if not root.endswith("\\"):
        root += "\\"
    return DRIVE_TYPES.get(_k32.GetDriveTypeW(root), "unknown")


def _seek_penalty(root: str) -> bool | None:
    """Interroge le disque : True = disque dur (HDD), False = SSD, None = inconnu."""
    if not IS_WINDOWS:
        return None

    class STORAGE_PROPERTY_QUERY(ctypes.Structure):
        _fields_ = [("PropertyId", ctypes.c_int), ("QueryType", ctypes.c_int), ("Extra", ctypes.c_ubyte * 1)]

    class DEVICE_SEEK_PENALTY_DESCRIPTOR(ctypes.Structure):
        _fields_ = [("Version", wintypes.DWORD), ("Size", wintypes.DWORD), ("IncursSeekPenalty", wintypes.BOOLEAN)]

    drive = os.path.splitdrive(root)[0]
    if not drive or drive.startswith("\\\\"):
        return None
    handle = _k32.CreateFileW("\\\\.\\" + drive, 0, _FILE_SHARE_ALL, None, _OPEN_EXISTING, 0, None)
    if handle in (None, _INVALID_HANDLE):
        return None
    try:
        query = STORAGE_PROPERTY_QUERY(7, 0)  # StorageDeviceSeekPenaltyProperty, PropertyStandardQuery
        out = DEVICE_SEEK_PENALTY_DESCRIPTOR()
        returned = wintypes.DWORD()
        ok = _k32.DeviceIoControl(
            handle, 0x2D1400, ctypes.byref(query), ctypes.sizeof(query),
            ctypes.byref(out), ctypes.sizeof(out), ctypes.byref(returned), None,
        )
        if not ok or returned.value < ctypes.sizeof(out):
            return None
        return bool(out.IncursSeekPenalty)
    finally:
        _k32.CloseHandle(handle)


def disk_kind(path: str) -> str:
    """'ssd', 'hdd', 'removable', 'network' ou 'unknown' (mis en cache par volume)."""
    root = volume_root(path)
    with _cache_lock:
        if root in _kind_cache:
            return _kind_cache[root]
    dtype = drive_type(root)
    if dtype == "network":
        kind = "network"
    elif dtype in ("removable", "cdrom"):
        kind = "removable"
    else:
        penalty = _seek_penalty(root)
        kind = "unknown" if penalty is None else ("hdd" if penalty else "ssd")
    with _cache_lock:
        _kind_cache[root] = kind
    return kind


def has_recycle_bin(path: str) -> bool:
    """Les clés USB, cartes SD et lecteurs réseau n'ont pas de corbeille."""
    return drive_type(path) == "fixed"


def disk_usage(path: str) -> tuple[int, int, int]:
    usage = shutil.disk_usage(xp(existing_ancestor(path)))
    return usage.total, usage.used, usage.free


def free_space(path: str) -> int:
    return disk_usage(path)[2]


# --- Priorité du processus ----------------------------------------------------
_NORMAL_PRIORITY_CLASS = 0x20
_BELOW_NORMAL_PRIORITY_CLASS = 0x4000
_THREAD_MODE_BACKGROUND_BEGIN = 0x00010000
_THREAD_MODE_BACKGROUND_END = 0x00020000


def set_process_low_priority(low: bool) -> None:
    if IS_WINDOWS:
        _k32.SetPriorityClass(_k32.GetCurrentProcess(), _BELOW_NORMAL_PRIORITY_CLASS if low else _NORMAL_PRIORITY_CLASS)


def set_thread_background(on: bool) -> None:
    """Mode « discret » : baisse la priorité disque et CPU du thread courant."""
    if IS_WINDOWS:
        _k32.SetThreadPriority(
            _k32.GetCurrentThread(), _THREAD_MODE_BACKGROUND_BEGIN if on else _THREAD_MODE_BACKGROUND_END
        )


if IS_WINDOWS:
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetExitCodeProcess.restype = wintypes.BOOL
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    _k32.GetProcessTimes.restype = wintypes.BOOL


def process_start(pid: int | None) -> int | None:
    """Heure de démarrage d'un processus encore actif (None s'il n'existe plus).

    Le couple (numéro, heure de démarrage) identifie un processus même si Windows a réattribué
    son numéro à un autre programme après un redémarrage."""
    if not pid or pid <= 0:
        return None
    if not IS_WINDOWS:
        try:
            os.kill(pid, 0)
            return 0
        except OSError:
            return None
    handle = _k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return None
        created, ended, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
        if not _k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kernel),
                                    ctypes.byref(user)):
            return 0
        return int(created.value)
    finally:
        _k32.CloseHandle(handle)


def process_alive(pid: int | None) -> bool:
    """Vrai si le processus existe encore (ex. un rangement planifié qui tourne en arrière-plan)."""
    return process_start(pid) is not None


def human_size(num: float, lang: str = "fr") -> str:
    units = ["o", "Ko", "Mo", "Go", "To"] if lang == "fr" else ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            if unit in ("o", "B"):
                return f"{int(value)} {unit}"
            text = f"{value:.1f}"
            if lang == "fr":
                text = text.replace(".", ",")
            return f"{text} {unit}"
        value /= 1024
    return f"{value:.1f}"
