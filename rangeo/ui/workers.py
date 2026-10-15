"""Exécution des tâches longues hors du fil de l'interface (l'interface ne gèle jamais)."""

from __future__ import annotations

import logging
import threading
import traceback

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

_running: set = set()


class Task(QObject):
    """Lance ``fn(*args)`` dans un thread ; ``finished``/``failed`` arrivent dans le fil de l'interface."""

    finished = Signal(object)
    failed = Signal(object)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.thread: threading.Thread | None = None

    def start(self) -> Task:
        _running.add(self)
        self.thread = threading.Thread(target=self._run, name="rangeo-task", daemon=True)
        self.thread.start()
        return self

    def _run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except BaseException as exc:  # noqa: BLE001 - transmis à l'interface
            log.error("Tâche en échec :\n%s", traceback.format_exc())
            exc.traceback_text = traceback.format_exc()
            self.failed.emit(exc)
        else:
            self.finished.emit(result)
        finally:
            _running.discard(self)

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


def run_task(fn, *args, on_done=None, on_error=None, **kwargs) -> Task:
    task = Task(fn, *args, **kwargs)
    if on_done:
        task.finished.connect(on_done)
    if on_error:
        task.failed.connect(on_error)
    return task.start()


def any_running() -> bool:
    return any(t.running for t in list(_running))
