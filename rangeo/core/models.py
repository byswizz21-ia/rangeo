"""Structures de données partagées par le moteur."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field


@dataclass(slots=True, eq=False)
class FileEntry:
    """Un fichier (ou un dossier traité d'un bloc : projet, logiciel…)."""

    dir: str
    name: str
    size: int
    mtime: float
    ctime: float
    attrs: int = 0
    ext: str = ""  # en minuscules, sans le point
    kind: str = "unknown"
    real_ext: str | None = None
    ext_mismatch: bool = False
    meta: dict = field(default_factory=dict)
    date: float | None = None
    date_source: str = ""
    is_bundle: bool = False
    bundle_files: int = 0
    bundle_reason: str = ""
    root: str = ""

    @property
    def path(self) -> str:
        return os.path.join(self.dir, self.name)

    @property
    def stem(self) -> str:
        if self.is_bundle or not self.ext:
            return self.name
        return self.name[: -(len(self.ext) + 1)]


@dataclass
class ScanResult:
    roots: list[str]
    entries: list[FileEntry] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (chemin, raison) – échantillon
    skip_counts: dict[str, int] = field(default_factory=dict)  # raison -> nombre
    errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)  # (clé, chemin)
    dir_count: int = 0
    file_count: int = 0
    total_size: int = 0
    duration: float = 0.0

    @property
    def files(self) -> list[FileEntry]:
        return [e for e in self.entries if not e.is_bundle]

    @property
    def bundles(self) -> list[FileEntry]:
        return [e for e in self.entries if e.is_bundle]


# Types d'opérations
OP_MOVE = "move"
OP_COPY = "copy"
OP_TRASH = "trash"
OP_HARDLINK = "hardlink"
OP_MKDIR = "mkdir"
OP_RMDIR = "rmdir"
OP_SKIP = "skip"

# États d'une opération dans le journal
ST_PLANNED = "planned"
ST_RUNNING = "running"
ST_DONE = "done"
ST_FAILED = "failed"
ST_SKIPPED = "skipped"
ST_UNDONE = "undone"
ST_UNDO_FAILED = "undo_failed"


@dataclass(slots=True, eq=False)
class PlannedOp:
    op: str
    src: str
    dst: str | None
    size: int = 0
    entry: FileEntry | None = None
    rule: str = ""
    note: str = ""  # clé de traduction expliquant une situation particulière
    enabled: bool = True
    is_dir: bool = False
    cross_volume: bool = False
    replaces: str | None = None  # fichier existant remplacé (mis à la corbeille avant)
    keep_ref: str | None = None  # pour un lien physique : le fichier conservé
    id: int = 0

    @property
    def kind(self) -> str:
        return self.entry.kind if self.entry else ""


@dataclass
class Plan:
    dest_root: str
    ops: list[PlannedOp] = field(default_factory=list)
    skipped: list[PlannedOp] = field(default_factory=list)  # déjà rangés, ignorés…
    warnings: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    profile_name: str = ""
    sources: list[str] = field(default_factory=list)
    extra_dirs: list[str] = field(default_factory=list)  # dossiers personnalisés à créer

    def active_ops(self) -> list[PlannedOp]:
        return [op for op in self.ops if op.enabled]

    def total_bytes(self) -> int:
        return sum(op.size for op in self.active_ops())


class Cancelled(Exception):
    """L'utilisateur a annulé l'opération."""


class DeviceGone(Exception):
    """Un disque a été débranché pendant l'opération."""


class TaskControl:
    """Pause / reprise / annulation + suivi de progression (lu par l'interface)."""

    def __init__(self) -> None:
        self._running = threading.Event()
        self._running.set()
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self.stage = ""
        self.done = 0
        self.total = 0
        self.bytes_done = 0
        self.bytes_total = 0
        self.current = ""
        self.errors = 0
        self.started = time.monotonic()
        self.paused_time = 0.0
        self._pause_started: float | None = None
        self.messages: list[str] = []

    # --- contrôle ---
    def pause(self) -> None:
        with self._lock:
            if self._running.is_set():
                self._pause_started = time.monotonic()
                self._running.clear()

    def resume(self) -> None:
        with self._lock:
            if not self._running.is_set():
                if self._pause_started is not None:
                    self.paused_time += time.monotonic() - self._pause_started
                    self._pause_started = None
                self._running.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._running.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def checkpoint(self) -> None:
        """À appeler régulièrement : bloque si en pause, lève Cancelled si annulé."""
        if self._cancel.is_set():
            raise Cancelled()
        if not self._running.is_set():
            self._running.wait()
            if self._cancel.is_set():
                raise Cancelled()

    # --- progression ---
    def set_stage(self, stage: str, total: int = 0, bytes_total: int = 0) -> None:
        with self._lock:
            self.stage = stage
            self.total = total
            self.done = 0
            self.bytes_total = bytes_total
            self.bytes_done = 0
            self.current = ""
            self.started = time.monotonic()
            self.paused_time = 0.0

    def advance(self, n: int = 1, nbytes: int = 0, current: str | None = None) -> None:
        with self._lock:
            self.done += n
            self.bytes_done += nbytes
            if current is not None:
                self.current = current

    def add_bytes(self, nbytes: int) -> None:
        with self._lock:
            self.bytes_done += nbytes

    def add_total(self, n: int = 0, nbytes: int = 0) -> None:
        with self._lock:
            self.total += n
            self.bytes_total += nbytes

    def error(self) -> None:
        with self._lock:
            self.errors += 1

    def elapsed(self) -> float:
        paused = self.paused_time
        if self._pause_started is not None:
            paused += time.monotonic() - self._pause_started
        return max(0.0, time.monotonic() - self.started - paused)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "stage": self.stage,
                "done": self.done,
                "total": self.total,
                "bytes_done": self.bytes_done,
                "bytes_total": self.bytes_total,
                "current": self.current,
                "errors": self.errors,
                "elapsed": self.elapsed(),
                "paused": self.paused,
            }
