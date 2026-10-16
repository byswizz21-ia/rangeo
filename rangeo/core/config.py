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
from .defaults import DEFAULT_EXCLUSIONS, FOLDERS, default_profiles, migrate_rules
from .rules import normalize_rule

log = logging.getLogger(__name__)

CONFLICT_STRATEGIES = ["rename", "skip", "overwrite", "keep_newest", "keep_largest"]
IDENTICAL_STRATEGIES = ["duplicates", "trash", "skip"]
DUP_ACTIONS = ["move", "trash", "hardlink"]
DUP_CRITERIA = ["preferred", "best_quality", "clean_name", "oldest", "newest", "shortest_path", "longest_name"]

DEFAULT_SETTINGS: dict = {
    "version": 1,
    "language": "fr",
    "theme": "system",
    "first_run_done": False,
    "conflict": "rename",
    "identical": "duplicates",
    "duplicates_folder": FOLDERS["fr"]["duplicates"],
    "dup_keep": ["preferred", "best_quality", "clean_name", "oldest", "shortest_path"],
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
    "schedules": [],
    "devices": [],
    "devices_ask_unknown": True,
    "context_menu": False,
    "analysis_old_years": 3,
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


def _is_yaml(path) -> bool:
    return str(path).lower().endswith((".yaml", ".yml"))


def _write_any(path, data) -> None:
    """Écrit en JSON, ou en YAML si le fichier se termine par .yaml / .yml."""
    if _is_yaml(path):
        import yaml

        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
    else:
        _atomic_write(Path(path), data)


def _read_any(path):
    with open(path, encoding="utf-8") as fh:
        if _is_yaml(path):
            import yaml

            return yaml.safe_load(fh)
        return json.load(fh)


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


def _clean_settings(data) -> dict:
    """Paramètres par défaut + valeurs valides lues (une valeur du mauvais type est ignorée)."""
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        for key, value in data.items():
            if key in settings and type(value) is type(settings[key]) or key in ("window",):
                settings[key] = value
    # V2 : nouveau critère « meilleure qualité » si l'utilisateur n'a pas personnalisé la liste
    if settings.get("dup_keep") == ["preferred", "clean_name", "oldest", "shortest_path"]:
        settings["dup_keep"] = ["preferred", "best_quality", "clean_name", "oldest", "shortest_path"]
    return settings


def normalize_profile(p: dict, lang: str = "fr") -> dict:
    base = default_profiles(lang)[0]
    out = copy.deepcopy(base)
    out.update({k: v for k, v in p.items() if k in base or k in ("id", "name")})
    out["rules"] = migrate_rules([normalize_rule(r) for r in (p.get("rules") or [])], lang)
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
        # Ce que CE processus a modifié : à l'enregistrement, on relit le disque et on n'écrase que ça
        # (l'interface et un rangement planifié peuvent tourner en même temps).
        self._dirty: set[str] = set()
        self._dirty_profiles: set[str] = set()
        self._deleted_profiles: set[str] = set()
        self._replace_profiles = False
        self.load()

    # -- chargement / sauvegarde ------------------------------------------------
    def _read_profiles(self, lang: str) -> list[dict] | None:
        profiles = _load_json(self.profiles_path)
        if isinstance(profiles, list):
            out = [normalize_profile(p, lang) for p in profiles if isinstance(p, dict) and p.get("id")]
            return out or None
        return None

    def _clear_dirty(self) -> None:
        self._dirty.clear()
        self._dirty_profiles.clear()
        self._deleted_profiles.clear()
        self._replace_profiles = False

    def load(self) -> None:
        with self._lock:
            self.settings = _clean_settings(_load_json(self.settings_path))
            lang = self.settings.get("language", "fr")
            self.profiles = self._read_profiles(lang) or default_profiles(lang)
            self._clear_dirty()

    def save(self) -> None:
        """Enregistre les changements de ce processus sans écraser ceux d'un autre (relecture + fusion)."""
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            data = _load_json(self.settings_path)
            if isinstance(data, dict):
                settings = _clean_settings(data)
                for key in self._dirty:
                    if key in self.settings:
                        settings[key] = copy.deepcopy(self.settings[key])
            else:  # premier enregistrement ou fichier illisible : la mémoire fait foi
                settings = copy.deepcopy(self.settings)
            disk = None if self._replace_profiles else self._read_profiles(settings.get("language", "fr"))
            if disk is None:
                profiles = copy.deepcopy(self.profiles)
            else:
                mine = {p["id"]: p for p in self.profiles}
                profiles = [copy.deepcopy(mine[p["id"]]) if p["id"] in self._dirty_profiles and p["id"] in mine
                            else p for p in disk if p["id"] not in self._deleted_profiles]
                known = {p["id"] for p in profiles}
                profiles += [copy.deepcopy(p) for p in self.profiles
                             if p["id"] in self._dirty_profiles and p["id"] not in known]
                if not profiles:
                    profiles = copy.deepcopy(self.profiles)
            _atomic_write(self.settings_path, settings)
            _atomic_write(self.profiles_path, profiles)
            # la mémoire reflète maintenant le disque (y compris ce qu'un autre processus a changé)
            self.settings = settings
            self.profiles = profiles
            self._clear_dirty()

    # -- paramètres --------------------------------------------------------------
    def get(self, key: str, default=None):
        with self._lock:
            return copy.deepcopy(self.settings.get(key, default))

    def set(self, key: str, value, save: bool = True) -> None:
        with self._lock:
            self.settings[key] = value
            self._dirty.add(key)
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
            self._dirty_profiles.add(profile["id"])
            self._deleted_profiles.discard(profile["id"])
            self.save()

    def delete_profile(self, profile_id: str) -> None:
        with self._lock:
            if len(self.profiles) <= 1:
                return
            self.profiles = [p for p in self.profiles if p["id"] != profile_id]
            self._deleted_profiles.add(profile_id)
            self._dirty_profiles.discard(profile_id)
            self.save()

    def replace_profiles(self, profiles: list[dict], save: bool = True) -> None:
        """Remplace TOUS les profils (assistant de premier lancement, import complet)."""
        with self._lock:
            lang = self.settings.get("language", "fr")
            new = [normalize_profile(p, lang) for p in profiles if isinstance(p, dict)]
            if not new:
                return
            self.profiles = new
            self._replace_profiles = True
            if save:
                self.save()

    # -- export / import -------------------------------------------------------
    def export_all(self, path: str) -> None:
        with self._lock:
            data = {"app": "Rangeo", "settings": self.settings, "profiles": self.profiles}
        _write_any(path, data)

    def import_all(self, path: str) -> None:
        data = _read_any(path)
        if not isinstance(data, dict) or data.get("app") != "Rangeo":
            raise ValueError("not_rangeo_file")
        with self._lock:
            if isinstance(data.get("settings"), dict):
                for key, value in data["settings"].items():
                    # même contrôle de type qu'au chargement : un fichier modifié à la main ne casse rien
                    if key in DEFAULT_SETTINGS and type(value) is type(DEFAULT_SETTINGS[key]):
                        self.settings[key] = value
                        self._dirty.add(key)
            if isinstance(data.get("profiles"), list) and data["profiles"]:
                self.replace_profiles(data["profiles"], save=False)
            self.save()

    @staticmethod
    def export_profile(profile: dict, path: str) -> None:
        _write_any(path, {"app": "Rangeo", "profile": profile})

    def import_profile(self, path: str) -> dict:
        data = _read_any(path)
        if not isinstance(data, dict) or "profile" not in data:
            raise ValueError("not_rangeo_file")
        from .rules import new_id

        profile = normalize_profile(data["profile"], self.settings.get("language", "fr"))
        profile["id"] = new_id()
        self.save_profile(profile)
        return profile
