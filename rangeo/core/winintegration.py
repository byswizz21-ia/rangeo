"""Intégration Windows : menu clic droit de l'Explorateur, installation, raccourcis, désinstallation."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import winreg

from .. import __version__
from . import appdirs

log = logging.getLogger(__name__)

CLASSES = r"Software\Classes"
MENU_KEYS = (r"Directory\shell\Rangeo", r"Drive\shell\Rangeo", r"Directory\Background\shell\Rangeo")
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Rangeo"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_NO_WINDOW = 0x08000000


def install_dir() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", "Rangeo")


def gui_command() -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        return sys.executable, []
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "__main__.py")
    return (pythonw if os.path.exists(pythonw) else sys.executable), [main]


def _quote(parts: list[str]) -> str:
    return " ".join(f'"{p}"' for p in parts)


# ---------------------------------------------------------------------------
# Menu contextuel
# ---------------------------------------------------------------------------
def register_context_menu(label: str, base: str = CLASSES) -> None:
    exe, args = gui_command()
    icon = sys.executable if getattr(sys, "frozen", False) else ""
    for sub in MENU_KEYS:
        target = "%V" if "Background" in sub else "%1"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{base}\\{sub}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, label)
            if icon:
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, f"{icon},0")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{base}\\{sub}\\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _quote([exe, *args, "--open-sort", target]))


def unregister_context_menu(base: str = CLASSES) -> None:
    for sub in MENU_KEYS:
        for path in (f"{base}\\{sub}\\command", f"{base}\\{sub}"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except OSError:
                pass


def context_menu_registered(base: str = CLASSES) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base}\\{MENU_KEYS[0]}\\command"):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Raccourcis
# ---------------------------------------------------------------------------
def _shortcut(path: str, target: str, workdir: str, description: str) -> None:
    import win32com.client

    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortcut(path)
    link.TargetPath = target
    link.WorkingDirectory = workdir
    link.IconLocation = f"{target},0"
    link.Description = description
    link.Save()


def start_menu_dir() -> str:
    return os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs")


def desktop_dir() -> str:
    try:
        import win32com.client

        return win32com.client.Dispatch("WScript.Shell").SpecialFolders("Desktop")
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")


# ---------------------------------------------------------------------------
# Installation / désinstallation (version exe uniquement)
# ---------------------------------------------------------------------------
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_installed_copy() -> bool:
    return is_frozen() and os.path.normcase(os.path.dirname(sys.executable)) == os.path.normcase(install_dir())


def installed_exe() -> str:
    return os.path.join(install_dir(), "Rangeo.exe")


def installed() -> bool:
    return os.path.isfile(installed_exe())


def install(desktop_shortcut: bool = True) -> str:
    """Copie le programme dans %LOCALAPPDATA%\\Programs\\Rangeo, crée les raccourcis et l'entrée de désinstallation."""
    if not is_frozen():
        raise RuntimeError("install_needs_exe")
    src = os.path.dirname(sys.executable)
    dst = install_dir()
    if os.path.normcase(src) != os.path.normcase(dst):
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
    exe = installed_exe()
    _shortcut(os.path.join(start_menu_dir(), "Rangeo.lnk"), exe, dst, "Rangeo – rangement de disques")
    if desktop_shortcut:
        _shortcut(os.path.join(desktop_dir(), "Rangeo.lnk"), exe, dst, "Rangeo – rangement de disques")
    size_kb = sum(os.path.getsize(os.path.join(dp, f)) for dp, _d, fs in os.walk(dst) for f in fs) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in (("DisplayName", "Rangeo"), ("DisplayVersion", __version__), ("Publisher", "Rangeo"),
                            ("DisplayIcon", f"{exe},0"), ("InstallLocation", dst),
                            ("UninstallString", f'"{exe}" --uninstall')):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, int(size_kb))
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
    return exe


def uninstall(remove_data: bool = False, schedule_ids: list[str] | None = None) -> None:
    """Retire tout ce que Rangeo a ajouté à Windows. Le dossier du programme est supprimé après fermeture."""
    unregister_context_menu()
    for path in (RUN_KEY,):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "Rangeo")
        except OSError:
            pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    from . import scheduler

    for job_id in schedule_ids or []:
        scheduler.remove(job_id)
    for link in (os.path.join(start_menu_dir(), "Rangeo.lnk"), os.path.join(desktop_dir(), "Rangeo.lnk")):
        try:
            os.remove(link)
        except OSError:
            pass
    targets = []
    if os.path.isdir(install_dir()):
        targets.append(install_dir())
    if remove_data:
        targets.append(str(appdirs.data_dir()))
    if targets:
        # suppression différée : le programme en cours d'exécution ne peut pas se supprimer lui-même
        cmd = "ping 127.0.0.1 -n 4 > nul & " + " & ".join(f'rmdir /s /q "{t}"' for t in targets)
        subprocess.Popen(["cmd", "/c", cmd], creationflags=_NO_WINDOW | 0x00000008, close_fds=True)
