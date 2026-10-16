"""Fenêtre principale : barre latérale + pages, icône de notification, reprise après interruption."""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QWidget,
)

from .. import __version__
from . import icons, theme
from .context import AppContext
from .i18n import fmt_date, plural, set_language, tr
from .pages.analysis import AnalysisPage
from .pages.automation import AutomationPage
from .pages.duplicates import DuplicatesPage
from .pages.history import HistoryPage
from .pages.home import HomePage
from .pages.rename import RenamePage
from .pages.rules import RulesPage
from .pages.settings import SettingsPage
from .pages.sort import SortPage
from .widgets import confirm, hbox, label, vbox

log = logging.getLogger(__name__)

PAGES = [
    ("home", "home", HomePage),
    ("sort", "sort", SortPage),
    ("duplicates", "copy", DuplicatesPage),
    ("rename", "edit", RenamePage),
    ("analysis", "pie", AnalysisPage),
    ("rules", "rules", RulesPage),
    ("watch", "zap", AutomationPage),
    ("history", "history", HistoryPage),
    ("settings", "settings", SettingsPage),
]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._quitting = False
        self._tray_hint_shown = not ctx.config.get("ask_tray_once", True)
        self.setWindowTitle("Rangeo")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(1080, 700)
        self.resize(1360, 880)
        self.pages: dict[str, QWidget] = {}
        self.nav: dict[str, QPushButton] = {}
        self._build()
        self._build_tray()
        ctx.navigate.connect(self.go)
        ctx.rebuildRequested.connect(self.rebuild)
        ctx.busyChanged.connect(self._busy_changed)
        ctx.watchEvent.connect(self._watch_event)
        self._click_action = None
        self.tray.messageClicked.connect(self._message_clicked)
        self._start_device_monitor()

    # -- construction --------------------------------------------------------------
    def _build(self, page: str = "home") -> None:
        root = QWidget()
        root.setObjectName("Root")
        lay = hbox(root, spacing=0)
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(232)
        sl = vbox(sidebar, (16, 20, 16, 16), 4)
        brand = hbox(spacing=10)
        logo = label()
        logo.setPixmap(icons.app_icon_pixmap(72).scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand.addWidget(logo)
        names = vbox(spacing=0)
        names.addWidget(label("Rangeo", "AppName"))
        names.addWidget(label(tr("app.tagline"), "AppTag"))
        brand.addLayout(names, 1)
        sl.addLayout(brand)
        sl.addSpacing(18)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self.stack = QStackedWidget()
        for name, icon_name, cls in PAGES:
            btn = QPushButton(tr(f"nav.{name}"))
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIcon(icons.icon(icon_name, theme.colors()["text2"], 18))
            btn.setIconSize(QSize(18, 18))
            btn.clicked.connect(lambda _=False, n=name: self.go(n))
            group.addButton(btn)
            sl.addWidget(btn)
            self.nav[name] = btn
            widget = cls(self.ctx)
            self.pages[name] = widget
            self.stack.addWidget(widget)
            if name == "watch":
                sl.addSpacing(6)
        sl.addStretch(1)
        self.busy_label = label("", "Faint", wrap=True)
        self.busy_label.setStyleSheet(f"color: {theme.colors()['accent']};")
        sl.addWidget(self.busy_label)
        sl.addWidget(label(tr("app.footer", version=__version__), "SidebarFooter", wrap=True))
        lay.addWidget(sidebar)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.go(page)

    def go(self, name: str, payload=None) -> None:
        page = self.pages.get(name)
        if page is None:
            return
        self.nav[name].setChecked(True)
        self.stack.setCurrentWidget(page)
        if hasattr(page, "receive") and payload is not None:
            page.receive(payload)
        if self.isHidden():
            self.show_window()

    def rebuild(self) -> None:
        """Changement de langue ou de thème : on reconstruit l'interface."""
        if self.ctx.busy:
            QMessageBox.information(self, "Rangeo", tr("settings.apply_later"))
            return
        current = next((n for n, w in self.pages.items() if w is self.stack.currentWidget()), "settings")
        set_language(self.ctx.config.get("language"))
        theme.apply_theme(QApplication.instance(), self.ctx.config.get("theme"))
        old = self.centralWidget()
        self.pages.clear()
        self.nav.clear()
        self._build(current)
        if old is not None:
            old.deleteLater()
        self._build_tray_menu()

    # -- icône de notification ---------------------------------------------------
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(icons.app_icon(), self)
        self.tray.setToolTip("Rangeo")
        self.tray.activated.connect(self._tray_activated)
        self._build_tray_menu()
        self.tray.show()

    def _build_tray_menu(self) -> None:
        menu = QMenu()
        menu.addAction(icons.icon("home"), tr("tray.open"), self.show_window)
        menu.addAction(icons.icon("sort"), tr("tray.sort"), lambda: self.go("sort", {}))
        menu.addSeparator()
        self.pause_action = QAction(tr("tray.pause_watch"), menu)
        self.pause_action.setCheckable(True)
        self.pause_action.setChecked(self.ctx.watch.paused)
        self.pause_action.toggled.connect(self._toggle_watch)
        menu.addAction(self.pause_action)
        menu.addSeparator()
        menu.addAction(icons.icon("x"), tr("tray.quit"), self.quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def _toggle_watch(self, paused: bool) -> None:
        self.ctx.watch.set_paused(paused)
        self.ctx.configChanged.emit()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def notify(self, title: str, text: str, on_click=None, force: bool = False) -> None:
        """Notification Windows. ``on_click`` est appelé si l'utilisateur clique dessus."""
        if (force or self.ctx.config.get("notifications", True)) and QSystemTrayIcon.supportsMessages():
            self._click_action = on_click
            self.tray.showMessage(title, text, icons.app_icon(), 8000 if on_click else 5000)

    def _message_clicked(self) -> None:
        action, self._click_action = self._click_action, None
        if action is not None:
            action()
        else:
            self.show_window()

    def handle_message(self, message: str) -> None:
        """Demande venant d'une autre instance (menu clic droit, nouveau lancement…)."""
        if message.startswith("sort:"):
            path = message[5:]
            self.show_window()
            self.go("sort", {"sources": [path]})
        elif message == "quit":
            if not self.ctx.busy:
                self._quitting = True
                self.tray.hide()
                QApplication.instance().quit()
        else:
            self.show_window()

    # -- disques externes -----------------------------------------------------------
    def _start_device_monitor(self) -> None:
        from ..core.devices import DeviceMonitor

        try:
            self._devices = DeviceMonitor()
        except Exception:
            log.exception("Détection des disques indisponible")
            return
        self._device_timer = QTimer(self)
        self._device_timer.setInterval(4000)
        self._device_timer.timeout.connect(self._poll_devices)
        self._device_timer.start()

    def _poll_devices(self) -> None:
        from ..core import devices

        try:
            new = self._devices.poll()
        except Exception:
            log.exception("Détection des disques")
            return
        for vol in new:
            rule = devices.find_rule(self.ctx.config.get("devices", []), vol)
            action = (rule or {}).get("action", "none")
            if rule and action == "auto":
                self._auto_sort_device(vol, rule)
            elif rule and action == "ask":
                self.notify(tr("devices.notify_title", name=vol.display), tr("devices.notify_ask"),
                            on_click=lambda v=vol, r=rule: self._open_device(v, r), force=True)
            elif rule is None and vol.kind == "removable" and self.ctx.config.get("devices_ask_unknown", True):
                self.notify(tr("devices.notify_title", name=vol.display), tr("devices.notify_unknown"),
                            on_click=lambda v=vol: self._open_device(v, None), force=True)

    def _device_source(self, vol, rule) -> str:
        import os

        sub = (rule or {}).get("subfolder", "").strip("\\/ ")
        path = os.path.join(vol.root, sub) if sub else vol.root
        return path if os.path.isdir(path) else vol.root

    def _open_device(self, vol, rule) -> None:
        self.show_window()
        self.go("sort", {"sources": [self._device_source(vol, rule)]})
        page = self.pages["sort"]
        if rule:
            setup = page.setup
            setup.profile.setCurrentIndex(max(0, setup.profile.findData(rule.get("profile"))))
            if rule.get("destination"):
                setup.to_folder.setChecked(True)
                setup.dest.setText(rule["destination"])

    def _auto_sort_device(self, vol, rule) -> None:
        if self.ctx.busy:
            self.notify(tr("devices.notify_title", name=vol.display), tr("devices.notify_busy"),
                        on_click=lambda: self._open_device(vol, rule), force=True)
            return
        from ..core.engine import SortRequest
        from ..core.models import TaskControl
        from .workers import run_task

        source = self._device_source(vol, rule)
        dest = rule.get("destination", "")
        req = SortRequest(sources=[source], profile=self.ctx.config.profile(rule.get("profile")), destination=dest,
                          in_place=not dest, recursive=True)
        if self.ctx.engine.check_request(req):
            self._open_device(vol, rule)
            return

        def work(control):
            _scan, plan = self.ctx.engine.analyze(req, control)
            if plan.errors or not plan.active_ops():
                return None
            return self.ctx.engine.execute(plan, control, "device", vol.display)

        def done(result):
            self.ctx.set_busy(False)
            self.ctx.sessionsChanged.emit()
            if result is not None:
                self.notify(tr(result.message), tr("devices.notify_done", n=result.files, name=vol.display),
                            on_click=lambda: self.go("history", {"session": result.session_id}), force=True)

        def failed(exc):
            self.ctx.set_busy(False)
            log.error("Rangement automatique du disque %s : %s", vol.display, exc)

        self.ctx.set_busy(True, tr("busy.sort"))
        run_task(work, TaskControl(), on_done=done, on_error=failed)

    def _watch_event(self, event) -> None:
        if event.moved or event.failed:
            text = plural(event.moved, "watch.event_moved", folder=event.folder)
            if event.failed:
                text += " · " + plural(event.failed, "watch.event_failed")
            self.notify("Rangeo", text)

    def _busy_changed(self, busy: bool) -> None:
        self.busy_label.setText(("⏳ " + self.ctx.busy_label) if busy else "")
        self.tray.setToolTip("Rangeo — " + self.ctx.busy_label if busy else "Rangeo")

    # -- fermeture --------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._quitting:
            event.accept()
            return
        if self.ctx.config.get("minimize_to_tray", True) and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.ctx.config.set("ask_tray_once", False)
                self.notify(tr("tray.still_running"), tr("tray.still_running_text"))
            return
        event.ignore()
        self.quit()

    def quit(self) -> None:
        if self.ctx.busy and not confirm(self, tr("app.quit_busy_title"), tr("app.quit_busy_text"),
                                         tr("tray.quit"), tr("common.cancel"), danger=True):
            return
        self._quitting = True
        self.tray.hide()
        self.close()
        QApplication.instance().quit()

    # -- reprise après interruption ----------------------------------------------
    def check_recovery(self) -> None:
        from ..core import journal as jn

        try:
            sessions = self.ctx.engine.journal.unfinished_sessions()
        except Exception:
            log.exception("Lecture du journal")
            return
        for s in sessions:
            counts = self.ctx.engine.journal.counts(s.id)
            done = counts.get("done", 0)
            todo = counts.get("planned", 0) + counts.get("running", 0)
            self.ctx.engine.journal.set_status(s.id, jn.S_INTERRUPTED)
            self.show_window()
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(tr("recovery.title"))
            box.setText(f"<b>{tr('recovery.title')}</b>")
            box.setInformativeText(tr("recovery.text", date=fmt_date(s.created), done=done, todo=todo))
            resume = box.addButton(tr("recovery.resume"), QMessageBox.AcceptRole)
            resume.setObjectName("Primary")
            undo = box.addButton(tr("recovery.undo"), QMessageBox.DestructiveRole)
            box.addButton(tr("recovery.later"), QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is resume:
                self.go("history", {"session": s.id})
                QTimer.singleShot(100, lambda sid=s.id: self.pages["history"].resume(False))
                break
            if clicked is undo:
                self.go("history", {"session": s.id, "undo": True})
                break
        self.ctx.sessionsChanged.emit()
