"""Paramètres et profils, enregistrés dans %APPDATA%\\Rangeo (écriture atomique)."""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from . import appdirs
from .defaults import DEFAULT_EXCLUSIONS, FOLDERS, default_profiles
from .rules import normalize_rule

log = logging.getLogger(__name__)

CONFLICT_STRATEGIES = ["rename", "skip", "overwrite", "keep_newest", "keep_largest"]
IDENTICAL_STRATEGIES = ["duplicates", "trash", "skip"]
DUP_ACTIONS = ["move", "trash", "hardlink"]
DUP_CRITERIA = ["preferred", "clean_name", "oldest", "newest", "shortest_path", "longest_name"]

DEFAULT_SETTINGS: dict = {
    "version": 1,
    "language": "fr",
    "theme": "system",
    "first_run_done": False,
    "conflict": "rename",
    "identical": "duplicates",
    "duplicates_folder": FOLDERS["fr"]["duplicates"],
    "dup_keep": ["preferred", "clean_name", "oldest", "shortest_path"],
    "dup_preferred": [],
    "dup_action": "move",
    "dup_min_size": 1,
    "exclusions": list(DEFAULT_EXCLUSIONS),
    "protected": [],
    "keep_whole": [],
    "skip_hidden": True,
    "priority": "fast",
    "date_order": ["metadata", "filename", "oldest"],
    "deep_check": False,
    "fix_extensions": False,
    "detect_bundles": True,
    "companions": True,
    "cleanup_empty": True,
    "autostart": False,
    "minimize_to_tray": True,
    "notifications": True,
    "watch": [],
    "last_sources": [],
    "last_destination": "",
    "last_profile": "general",
    "ask_tray_once": True,
}


def _atomic_write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    if path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
    os.replace(tmp, path)


def _load_json(path: Path):
    for candidate in (path, path.with_suffix(path.suffix + ".bak")):
        if candidate.exists():
            try:
                with open(candidate, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                log.warning("Fichier de configuration illisible : %s", candidate)
                try:
                    shutil.copy2(candidate, candidate.with_suffix(f".corrompu-{int(time.time())}"))
                except OSError:
                    pass
    return None


def normalize_profile(p: dict, lang: str = "fr") -> dict:
    base = default_profiles(lang)[0]
    out = copy.deepcopy(base)
    out.update({k: v for k, v in p.items() if k in base or k in ("id", "name")})
    out["rules"] = [normalize_rule(r) for r in (p.get("rules") or [])]
    out["sources"] = [s for s in out.get("sources") or [] if isinstance(s, str)]
    out["extra_dirs"] = [s for s in out.get("extra_dirs") or [] if isinstance(s, str)]
    return out


class Config:
    """Accès centralisé (thread-safe) aux paramètres et aux profils."""

    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder or appdirs.data_dir())
        self.settings_path = self.folder / "settings.json"
        self.profiles_path = self.folder / "profiles.json"
        self._lock = threading.RLock()
        self.settings: dict = copy.deepcopy(DEFAULT_SETTINGS)
        self.profiles: list[dict] = []
        self.load()

    # -- chargement / sauvegarde ------------------------------------------------
    def load(self) -> None:
        with self._lock:
            data = _load_json(self.settings_path)
            settings = copy.deepcopy(DEFAULT_SETTINGS)
            if isinstance(data, dict):
                for key, value in data.items():
                    if key in settings and type(value) is type(settings[key]) or key in ("window",):
                        settings[key] = value
            self.settings = settings
            profiles = _load_json(self.profiles_path)
            lang = settings.get("language", "fr")
            if isinstance(profiles, list) and profiles:
                self.profiles = [normalize_profile(p, lang) for p in profiles if isinstance(p, dict)]
            else:
                self.profiles = default_profiles(lang)

    def save(self) -> None:
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            _atomic_write(self.settings_path, self.settings)
            _atomic_write(self.profiles_path, self.profiles)

    # -- paramètres --------------------------------------------------------------
    def get(self, key: str, default=None):
        with self._lock:
            return copy.deepcopy(self.settings.get(key, default))

    def set(self, key: str, value, save: bool = True) -> None:
        with self._lock:
            self.settings[key] = value
            if save:
                self.save()

    # -- profils -------------------------------------------------------------------
    def profile(self, profile_id: str | None) -> dict:
        with self._lock:
            for p in self.profiles:
                if p["id"] == profile_id:
                    return copy.deepcopy(p)
            return copy.deepcopy(self.profiles[0])

    def save_profile(self, profile: dict) -> None:
        with self._lock:
            profile = normalize_profile(profile, self.settings.get("language", "fr"))
            for i, p in enumerate(self.profiles):
                if p["id"] == profile["id"]:
                    self.profiles[i] = profile
                    break
            else:
                self.profiles.append(profile)
            self.save()

    def delete_profile(self, profile_id: str) -> None:
        with self._lock:
            if len(self.profiles) <= 1:
                return
            self.profiles = [p for p in self.profiles if p["id"] != profile_id]
            self.save()

    # -- export / import -------------------------------------------------------
    def export_all(self, path: str) -> None:
        with self._lock:
            data = {"app": "Rangeo", "settings": self.settings, "profiles": self.profiles}
        _atomic_write(Path(path), data)

    def import_all(self, path: str) -> None:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or data.get("app") != "Rangeo":
            raise ValueError("not_rangeo_file")
        with self._lock:
            if isinstance(data.get("settings"), dict):
                for key, value in data["settings"].items():
                    if key in DEFAULT_SETTINGS:
                        self.settings[key] = value
            if isinstance(data.get("profiles"), list) and data["profiles"]:
                lang = self.settings.get("language", "fr")
                self.profiles = [normalize_profile(p, lang) for p in data["profiles"] if isinstance(p, dict)]
            self.save()

    @staticmethod
    def export_profile(profile: dict, path: str) -> None:
        _atomic_write(Path(path), {"app": "Rangeo", "profile": profile})

    def import_profile(self, path: str) -> dict:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "profile" not in data:
            raise ValueError("not_rangeo_file")
        from .rules import new_id

        profile = normalize_profile(data["profile"], self.settings.get("language", "fr"))
        profile["id"] = new_id()
        self.save_profile(profile)
        return profile
