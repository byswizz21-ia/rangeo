"""Page « Surveillance » : rangement automatique des nouveaux fichiers."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QSpinBox, QWidget

from .. import icons, theme
from ..context import AppContext
from ..i18n import fmt_date, fmt_int, plural, tr
from ..widgets import Banner, Card, PageHeader, ScrollPage, Toggle, button, choose_folder, confirm, hbox, icon_label, label, vbox


class WatchRow(QWidget):
    def __init__(self, page: WatchPage, cfg: dict):
        super().__init__()
        self.page = page
        self.cfg = cfg
        lay = hbox(self, (0, 4, 0, 4), 10)
        self.toggle = Toggle(cfg.get("enabled", True))
        self.toggle.toggled.connect(self._changed)
        lay.addWidget(self.toggle)
        lay.addWidget(icon_label("folder", theme.colors()["accent"], 20))
        texts = vbox(spacing=2)
        path = label(cfg["path"])
        path.setToolTip(cfg["path"])
        texts.addWidget(path)
        self.status = label("", "Faint")
        texts.addWidget(self.status)
        lay.addLayout(texts, 1)
        lay.addWidget(label(tr("watch.profile"), "Muted"))
        self.profile = QComboBox()
        for p in page.ctx.config.profiles:
            self.profile.addItem(p["name"], p["id"])
        self.profile.setCurrentIndex(max(0, self.profile.findData(cfg.get("profile", "downloads"))))
        self.profile.currentIndexChanged.connect(self._changed)
        lay.addWidget(self.profile)
        self.recursive = QCheckBox(tr("watch.subfolders"))
        self.recursive.setChecked(bool(cfg.get("recursive")))
        self.recursive.toggled.connect(self._changed)
        lay.addWidget(self.recursive)
        lay.addWidget(label(tr("watch.delay"), "Muted"))
        self.delay = QSpinBox()
        self.delay.setRange(2, 3600)
        self.delay.setSuffix(" s")
        self.delay.setValue(int(cfg.get("delay", 5)))
        self.delay.valueChanged.connect(self._changed)
        lay.addWidget(self.delay)
        lay.addWidget(button("", "trash", "ghost", tooltip=tr("watch.remove"), on_click=lambda: page.remove(self)))
        self.update_status()

    def _changed(self, *_):
        self.cfg.update(enabled=self.toggle.isChecked(), profile=self.profile.currentData(),
                        recursive=self.recursive.isChecked(), delay=self.delay.value())
        self.page.save()

    def update_status(self) -> None:
        from ...core.fsutil import nkey, norm

        active = nkey(norm(self.cfg["path"])) in self.page.ctx.watch.watchers
        if not os.path.isdir(self.cfg["path"]):
            self.status.setText(tr("watch.missing"))
        elif active and self.page.ctx.watch.paused:
            self.status.setText(tr("watch.paused"))
        elif active:
            self.status.setText(tr("watch.active"))
        else:
            self.status.setText(tr("watch.inactive"))


class WatchPage(ScrollPage):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        b = self.body
        self.pause_btn = button("", "pause", on_click=self._toggle_pause)
        b.addWidget(PageHeader(tr("watch.title"), tr("watch.subtitle"), [self.pause_btn]))
        b.addWidget(Banner("info", tr("watch.how")))
        self.card = Card(tr("watch.folders"), tr("watch.folders_text"), "eye")
        self.rows_box = vbox(spacing=4)
        self.card.add(self.rows_box)
        self.empty = label(tr("watch.empty"), "Muted", wrap=True)
        self.card.add(self.empty)
        add_row = hbox(spacing=8)
        add_row.addWidget(button(tr("watch.add"), "plus", "primary", on_click=self._add))
        downloads = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        if downloads:
            add_row.addWidget(button(tr("watch.add_downloads"), "download", on_click=lambda: self._add(downloads)))
        add_row.addStretch(1)
        self.card.add(add_row)
        b.addWidget(self.card)

        self.activity = Card(tr("watch.activity"), tr("watch.activity_text"), "history")
        self.activity_box = vbox(spacing=6)
        self.activity.add(self.activity_box)
        b.addWidget(self.activity)
        b.addStretch(1)
        ctx.watchEvent.connect(lambda _e: self._fill_activity())
        ctx.configChanged.connect(self._fill_rows)
        self._fill_rows()
        self._fill_activity()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fill_rows()
        self._fill_activity()

    def _fill_rows(self) -> None:
        while self.rows_box.count():
            w = self.rows_box.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.rows = []
        for cfg in self.ctx.config.get("watch", []) or []:
            if not cfg.get("path"):
                continue
            row = WatchRow(self, cfg)
            self.rows.append(row)
            self.rows_box.addWidget(row)
        self.empty.setVisible(not self.rows)
        paused = self.ctx.watch.paused
        self.pause_btn.setText(tr("watch.resume_all") if paused else tr("watch.pause_all"))
        self.pause_btn.setIcon(icons.icon("play" if paused else "pause"))
        self.pause_btn.setVisible(bool(self.rows))

    def _fill_activity(self) -> None:
        while self.activity_box.count():
            w = self.activity_box.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        events = self.ctx.watch.history[:30]
        if not events:
            self.activity_box.addWidget(label(tr("watch.no_activity"), "Muted"))
            return
        c = theme.colors()
        for e in events:
            w = QWidget()
            lay = hbox(w, spacing=10)
            lay.addWidget(icon_label("check_circle" if not e.failed else "alert",
                                     c["success"] if not e.failed else c["warning"], 18))
            texts = vbox(spacing=1)
            texts.addWidget(label(plural(e.moved, "watch.event_moved", folder=os.path.basename(e.folder) or e.folder)))
            detail = ", ".join(e.files[:5]) + ("…" if len(e.files) > 5 else "")
            texts.addWidget(label(f"{fmt_date(e.when)} · {detail}", "Faint"))
            lay.addLayout(texts, 1)
            if e.session_id:
                lay.addWidget(button(tr("watch.see"), "arrow_right", "ghost",
                                     on_click=lambda _=False, sid=e.session_id: self.ctx.navigate.emit(
                                         "history", {"session": sid})))
            self.activity_box.addWidget(w)

    def save(self) -> None:
        self.ctx.config.set("watch", [r.cfg for r in self.rows])
        self.ctx.watch.reload()
        for r in self.rows:
            r.update_status()

    def _add(self, path: str | None = None) -> None:
        path = path or choose_folder(self, tr("watch.choose"))
        if not path:
            return
        path = os.path.normpath(path)
        from ...core.protect import Protection

        problem = Protection(self.ctx.config.get("protected")).source_problem(path)
        if problem:
            from ..widgets import error

            error(self, tr("watch.title"), tr(problem))
            return
        watch = self.ctx.config.get("watch", []) or []
        if any(os.path.normcase(w.get("path", "")) == os.path.normcase(path) for w in watch):
            return
        profile = "downloads" if any(p["id"] == "downloads" for p in self.ctx.config.profiles) else \
            self.ctx.config.profiles[0]["id"]
        watch.append({"path": path, "profile": profile, "enabled": True, "recursive": False, "delay": 5})
        self.ctx.config.set("watch", watch)
        self.ctx.watch.reload()
        self._fill_rows()

    def remove(self, row: WatchRow) -> None:
        if confirm(self, tr("watch.remove"), tr("watch.remove_text", path=row.cfg["path"]), tr("common.remove")):
            self.rows = [r for r in self.rows if r is not row]
            self.save()
            self._fill_rows()

    def _toggle_pause(self) -> None:
        self.ctx.watch.set_paused(not self.ctx.watch.paused)
        self._fill_rows()
