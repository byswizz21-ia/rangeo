"""État partagé de l'application (moteur, configuration, signaux entre pages)."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..core.config import Config
from ..core.engine import Engine
from ..core.watcher import WatchManager


class AppContext(QObject):
    sessionsChanged = Signal()  # une session a été créée / annulée
    configChanged = Signal()
    navigate = Signal(str, object)  # (page, données)
    watchEvent = Signal(object)
    busyChanged = Signal(bool)
    rebuildRequested = Signal()  # changement de langue / thème

    def __init__(self, config: Config, engine: Engine):
        super().__init__()
        self.config = config
        self.engine = engine
        self.watch = WatchManager(engine, on_event=self.watchEvent.emit)
        self._busy = False
        self.busy_label = ""

    @property
    def busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        self.busy_label = label if busy else ""
        self.busyChanged.emit(busy)
