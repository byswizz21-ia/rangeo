"""Surveillance de dossiers : range automatiquement les nouveaux fichiers."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

from . import filetypes as ft
from .engine import Engine, SortRequest
from .fsutil import is_within, nkey, norm, xp
from .models import TaskControl

log = logging.getLogger(__name__)

STABLE_SECONDS = 3.0  # un fichier doit garder la même taille pendant ce temps


@dataclass
class WatchEvent:
    when: float
    folder: str
    moved: int
    failed: int
    session_id: int | None
    message: str = ""
    files: list[str] = field(default_factory=list)


def _is_ready(path: str) -> bool:
    """Fichier complètement écrit et non utilisé par un autre programme."""
    try:
        with open(xp(path), "rb"):
            pass
    except OSError:
        return False
    if os.name == "nt":
        try:
            import win32file

            handle = win32file.CreateFile(
                xp(path), win32file.GENERIC_READ, 0, None, win32file.OPEN_EXISTING, 0, None
            )
            handle.Close()
        except Exception:
            return False
    return True


class FolderWatcher:
    """Un dossier surveillé. Les fichiers stables sont rangés par lots."""

    def __init__(self, engine: Engine, folder: str, profile_id: str, recursive: bool = False,
                 delay: float = 5.0, on_event=None):
        self.engine = engine
        self.folder = norm(folder)
        self.profile_id = profile_id
        self.recursive = recursive
        self.delay = max(2.0, float(delay))
        self.on_event = on_event
        self._pending: dict[str, tuple[int, float]] = {}  # chemin -> (taille, depuis)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._observer = None
        self._thread: threading.Thread | None = None
        self.paused = False
        self.busy = threading.Lock()

    # -- cycle de vie -----------------------------------------------------------
    def start(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        watcher = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    watcher._touch(event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    watcher._touch(event.src_path)

            def on_moved(self, event):
                if not event.is_directory:
                    watcher._touch(event.dest_path)

        self._observer = Observer()
        self._observer.schedule(Handler(), self.folder, recursive=self.recursive)
        self._observer.daemon = True
        self._observer.start()
        self._thread = threading.Thread(target=self._loop, name=f"watch:{self.folder}", daemon=True)
        self._thread.start()
        # fichiers déjà présents au démarrage
        self.scan_existing()
        log.info("Surveillance active : %s", self.folder)

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=3)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)

    def scan_existing(self) -> None:
        try:
            with os.scandir(xp(self.folder)) as it:
                for de in it:
                    if de.is_file(follow_symlinks=False):
                        self._touch(os.path.join(self.folder, de.name))
        except OSError:
            pass

    # -- événements -------------------------------------------------------------
    def _touch(self, path: str) -> None:
        name = os.path.basename(path).lower()
        stem, ext = os.path.splitext(name)
        ext = ext.lstrip(".")
        if ext in ft.PARTIAL_EXT or name in ft.ALWAYS_SKIP_NAMES or name.startswith(("~$", "~rangeo-", ".")):
            return
        if not self.recursive and nkey(os.path.dirname(path)) != nkey(self.folder):
            return
        with self._lock:
            self._pending[path] = (-1, time.monotonic())

    def _not_ignored(self, paths: list[str]) -> list[str]:
        """Retire les fichiers remis en place par une annulation (l'utilisateur ne veut pas qu'ils bougent)."""
        try:
            ignored = self.engine.index.ignored_keys(_stat_rows(paths))
        except Exception:
            log.exception("Liste des fichiers ignorés illisible")
            return paths
        return [p for p in paths if nkey(p) not in ignored]

    def _loop(self) -> None:
        while not self._stop.wait(1.0):
            if self.paused:
                continue
            ready = []
            now = time.monotonic()
            with self._lock:
                for path, (size, since) in list(self._pending.items()):
                    try:
                        current = os.stat(xp(path)).st_size
                    except OSError:
                        self._pending.pop(path, None)
                        continue
                    if current != size:
                        self._pending[path] = (current, now)
                    elif now - since >= max(STABLE_SECONDS, self.delay):
                        ready.append(path)
            ready = [p for p in ready if _is_ready(p)]
            if ready:
                with self._lock:
                    for p in ready:
                        self._pending.pop(p, None)
                self._process(ready)

    def _process(self, paths: list[str]) -> None:
        paths = self._not_ignored(paths)
        if not paths:
            return
        if not self.busy.acquire(blocking=False):
            with self._lock:
                for p in paths:
                    self._pending.setdefault(p, (-1, time.monotonic()))
            return
        try:
            profile = self.engine.config.profile(self.profile_id)
            in_place = profile.get("in_place", True) or not profile.get("destination")
            req = SortRequest(
                sources=[self.folder],
                profile=profile,
                destination=profile.get("destination", ""),
                in_place=in_place,
                recursive=self.recursive,
            )
            if self.engine.check_request(req):
                log.warning("Surveillance %s : configuration invalide", self.folder)
                return
            control = TaskControl()
            scan_result, plan = self.engine.analyze(req, control)
            wanted = {nkey(p) for p in paths}
            for op in plan.ops:
                if nkey(op.src) not in wanted:
                    op.enabled = False
            if not plan.active_ops():
                return
            # ne jamais traiter un fichier placé dans le dossier de destination lui-même
            dest = plan.dest_root
            if dest:
                for op in plan.ops:
                    if is_within(op.src, dest) and op.enabled and not in_place:
                        op.enabled = False
            result = self.engine.execute(plan, control, kind="watch", profile=profile.get("name", ""),
                                         cleanup=False)
            event = WatchEvent(time.time(), self.folder, result.files, result.failed, result.session_id,
                               files=[os.path.basename(op.src) for op in plan.active_ops()][:20])
            if self.on_event:
                try:
                    self.on_event(event)
                except Exception:
                    log.exception("Notification de surveillance")
        except Exception:
            log.exception("Erreur pendant le rangement automatique de %s", self.folder)
        finally:
            self.busy.release()


def _stat_rows(paths: list[str]) -> list[tuple[str, int, float]]:
    rows = []
    for p in paths:
        try:
            st = os.stat(xp(p))
        except OSError:
            continue
        rows.append((p, st.st_size, st.st_mtime))
    return rows


class WatchManager:
    """Gère toutes les surveillances configurées."""

    def __init__(self, engine: Engine, on_event=None):
        self.engine = engine
        self.on_event = on_event
        self.watchers: dict[str, FolderWatcher] = {}
        self.history: list[WatchEvent] = []
        self._lock = threading.Lock()

    def _event(self, event: WatchEvent) -> None:
        with self._lock:
            self.history.insert(0, event)
            del self.history[200:]
        if self.on_event:
            self.on_event(event)

    def reload(self) -> None:
        """(Re)démarre les surveillances selon la configuration."""
        wanted = {}
        for w in self.engine.config.get("watch", []) or []:
            if w.get("enabled", True) and w.get("path") and os.path.isdir(w["path"]):
                wanted[nkey(norm(w["path"]))] = w
        for key in list(self.watchers):
            current = self.watchers[key]
            cfg = wanted.get(key)
            if cfg is None or cfg.get("profile") != current.profile_id or bool(cfg.get("recursive")) != current.recursive:
                current.stop()
                del self.watchers[key]
        for key, cfg in wanted.items():
            if key in self.watchers:
                self.watchers[key].delay = float(cfg.get("delay", 5))
                continue
            watcher = FolderWatcher(self.engine, cfg["path"], cfg.get("profile", "downloads"),
                                    bool(cfg.get("recursive")), float(cfg.get("delay", 5)), self._event)
            try:
                watcher.start()
                self.watchers[key] = watcher
            except Exception:
                log.exception("Impossible de surveiller %s", cfg["path"])

    def set_paused(self, paused: bool) -> None:
        for w in self.watchers.values():
            w.paused = paused

    @property
    def paused(self) -> bool:
        return any(w.paused for w in self.watchers.values())

    def stop_all(self) -> None:
        for w in self.watchers.values():
            w.stop()
        self.watchers.clear()
