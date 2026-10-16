"""Page d'accueil."""

from __future__ import annotations

import os

from PySide6.QtCore import QStorageInfo, Qt, QTime, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QGridLayout, QProgressBar, QWidget

from ...core.protect import Protection
from .. import motion, theme
from ..context import AppContext
from ..i18n import fmt_date, fmt_int, fmt_size, tr
from ..widgets import Card, Chip, DropZone, ScrollPage, StatCard, button, hbox, icon_label, label, vbox
from .history import SESSION_ICONS, STATUS_LEVEL


class ActionCard(QFrame):
    """Carte cliquable de l'accueil : au survol elle se soulève et sa flèche avance."""

    clicked = Signal()

    def __init__(self, icon_name: str, title: str, text: str):
        super().__init__()
        self.setObjectName("ActionCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        lay = vbox(self, (20, 18, 20, 20), 6)
        c = theme.colors()
        top = hbox(spacing=8)
        top.addWidget(icon_label(icon_name, c["accent"], 24))
        top.addStretch(1)
        self.arrow = motion.AnimIcon("arrow_right", c["text2"], 18, dx=4, hover_color=c["accent"])
        top.addWidget(self.arrow, 0, Qt.AlignVCenter)
        lay.addLayout(top)
        lay.addSpacing(6)
        lay.addWidget(label(title, "SectionTitle"))
        lay.addWidget(label(text, "Muted", wrap=True))
        lay.addStretch(1)
        self._lift = 0.0
        self._anim = None
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setColor(QColor(0, 0, 0, 0))
        self._shadow.setOffset(0, 0)
        self._shadow.setBlurRadius(0)
        self._shadow.setEnabled(False)
        self.setGraphicsEffect(self._shadow)

    def _hover(self, on: bool) -> None:
        self.arrow.set_active(on)
        if not motion.enabled():
            return
        if self._anim is not None:
            self._anim.stop()
        self._shadow.setEnabled(True)
        base = QColor(theme.colors()["shadow"])

        def step(v):
            self._lift = float(v)
            base.setAlphaF((0.45 if theme.is_dark() else 0.12) * self._lift)
            self._shadow.setColor(base)
            self._shadow.setBlurRadius(28 * self._lift)
            self._shadow.setOffset(0, 8 * self._lift)

        def done():
            if self._lift <= 0.001:
                self._shadow.setEnabled(False)

        self._anim = motion.animation(self, motion.FAST, step, start=self._lift, end=1.0 if on else 0.0,
                                      on_done=done)
        self._anim.start()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._hover(True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hover(False)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()


def greeting() -> str:
    hour = QTime.currentTime().hour()
    return tr("home.title_evening") if hour >= 18 or hour < 4 else tr("home.title")


class HomePage(ScrollPage):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        b = self.body
        b.setSpacing(18)
        b.addSpacing(6)
        texts = vbox(spacing=4)
        self.hello = label(greeting(), "Display")
        texts.addWidget(self.hello)
        texts.addWidget(label(tr("home.subtitle"), "PageSubtitle", wrap=True))
        b.addLayout(texts)
        b.addSpacing(4)

        from ...core import winintegration as wi

        if wi.is_frozen() and not wi.is_installed_copy() and not wi.installed():
            from ..widgets import Banner

            b.addWidget(Banner("info", tr("home.install_banner"), tr("install.install"),
                               lambda: ctx.navigate.emit("settings", None)))

        drop = DropZone(tr("home.drop_title"), tr("home.drop_text"), tr("home.drop_button"))
        drop.pathsDropped.connect(lambda paths: ctx.navigate.emit("sort", {"sources": paths}))
        drop.browseClicked.connect(self._browse)
        b.addWidget(drop)

        grid = QGridLayout()
        grid.setSpacing(14)
        actions = [
            ("sort", tr("home.action_sort"), tr("home.action_sort_text"), lambda: ctx.navigate.emit("sort", None)),
            ("copy", tr("home.action_dups"), tr("home.action_dups_text"),
             lambda: ctx.navigate.emit("duplicates", None)),
            ("eye", tr("home.action_watch"), tr("home.action_watch_text"), lambda: ctx.navigate.emit("watch", None)),
        ]
        for i, (ic, title, text, fn) in enumerate(actions):
            card = ActionCard(ic, title, text)
            card.clicked.connect(fn)
            grid.addWidget(card, 0, i)
        b.addLayout(grid)

        stats = QGridLayout()
        stats.setSpacing(14)
        self.stat_files = StatCard("check_circle", "0", tr("home.stat_files"))
        self.stat_bytes = StatCard("layers", "0", tr("home.stat_bytes"))
        self.stat_sessions = StatCard("history", "0", tr("home.stat_sessions"))
        self.stat_freed = StatCard("sparkles", "0", tr("home.stat_freed"), tone="spark")
        for i, w in enumerate((self.stat_files, self.stat_bytes, self.stat_sessions, self.stat_freed)):
            stats.addWidget(w, 0, i)
        b.addLayout(stats)

        row = hbox(spacing=14)
        self.drives_card = Card(tr("home.drives"), tr("home.drives_text"), "drive")
        self.drives_box = vbox(spacing=10)
        self.drives_card.add(self.drives_box)
        row.addWidget(self.drives_card, 1)
        self.recent_card = Card(tr("home.recent"), None, "history")
        self.recent_box = vbox(spacing=8)
        self.recent_card.add(self.recent_box)
        more = button(tr("home.see_history"), "arrow_right", "link",
                      on_click=lambda: ctx.navigate.emit("history", None))
        self.recent_card.add(more)
        row.addWidget(self.recent_card, 1)
        b.addLayout(row)

        safety = Card(flat=True, margins=(16, 12, 16, 12))
        srow = hbox(spacing=10)
        srow.addWidget(_icon("shield", theme.colors()["success"]), 0, Qt.AlignTop)
        srow.addWidget(label(tr("home.safety"), "Muted", wrap=True), 1)
        safety.add(srow)
        b.addWidget(safety)
        b.addStretch(1)
        ctx.sessionsChanged.connect(self.refresh)
        self.refresh()

    def _browse(self) -> None:
        from ..widgets import choose_folder

        path = choose_folder(self, tr("sort.choose_source"))
        if path:
            self.ctx.navigate.emit("sort", {"sources": [path]})

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        try:
            stats = self.ctx.engine.journal.stats()
        except Exception:
            stats = {"files": 0, "bytes": 0, "sessions": 0, "freed": 0}
        self.stat_files.set_value(fmt_int(stats["files"]))
        self.stat_bytes.set_value(fmt_size(stats["bytes"]))
        self.stat_sessions.set_value(fmt_int(stats["sessions"]))
        self.stat_freed.set_value(fmt_size(stats["freed"]))
        self._fill_drives()
        self._fill_recent()

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout():
                self._clear(item.layout())

    def _fill_drives(self) -> None:
        self._clear(self.drives_box)
        prot = Protection(self.ctx.config.get("protected"))
        c = theme.colors()
        for vol in QStorageInfo.mountedVolumes():
            if not vol.isValid() or not vol.isReady() or vol.bytesTotal() <= 0:
                continue
            root = os.path.normpath(vol.rootPath())
            name = vol.displayName() or root
            if name.rstrip("\\/") != root.rstrip("\\/"):
                name = f"{name} ({root.rstrip(chr(92))})"
            used = vol.bytesTotal() - vol.bytesAvailable()
            frac = used / vol.bytesTotal()
            w = QWidget()
            lay = hbox(w, spacing=10)
            lay.addWidget(_icon("drive", c["text2"]))
            texts = vbox(spacing=4)
            top = hbox(spacing=6)
            top.addWidget(label(name))
            top.addStretch(1)
            top.addWidget(label(tr("home.free", free=fmt_size(vol.bytesAvailable()),
                                   total=fmt_size(vol.bytesTotal())), "Faint"))
            texts.addLayout(top)
            bar = QProgressBar()
            bar.setObjectName("Thin")
            bar.setFixedHeight(6)
            bar.setRange(0, 1000)
            bar.setValue(int(frac * 1000))
            bar.setProperty("high", frac > 0.9)
            texts.addWidget(bar)
            lay.addLayout(texts, 1)
            problem = prot.source_problem(root)
            btn = button(tr("home.sort_drive"), kind="ghost")
            btn.setEnabled(problem is None)
            btn.setToolTip(tr(problem) if problem else tr("home.sort_drive_tip"))
            btn.clicked.connect(lambda _=False, r=root: self.ctx.navigate.emit("sort", {"sources": [r]}))
            lay.addWidget(btn)
            self.drives_box.addWidget(w)

    def _fill_recent(self) -> None:
        self._clear(self.recent_box)
        try:
            sessions = self.ctx.engine.journal.sessions(5)
        except Exception:
            sessions = []
        if not sessions:
            self.recent_box.addWidget(label(tr("home.no_recent"), "Muted", wrap=True))
            return
        for s in sessions:
            w = QWidget()
            lay = hbox(w, spacing=10)
            lay.addWidget(_icon(SESSION_ICONS.get(s.kind, "sort"),
                                theme.colors()["text2"]))
            texts = vbox(spacing=1)
            texts.addWidget(label(tr(f"session.kind_{s.kind}") + (f" — {s.profile}" if s.profile else "")))
            done = s.summary.get("files", s.summary.get("done", 0))
            texts.addWidget(label(f"{fmt_date(s.created)} · {tr('home.n_ops', n=fmt_int(done))}", "Faint"))
            lay.addLayout(texts, 1)
            lay.addWidget(Chip(tr(f"status.{s.status}"), STATUS_LEVEL.get(s.status, "neutral")))
            self.recent_box.addWidget(w)


def _icon(name: str, color: str):
    from ..widgets import icon_label

    return icon_label(name, color, 20)
