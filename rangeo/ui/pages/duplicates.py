"""Page « Doublons » : recherche des fichiers identiques et choix de celui à garder."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHeaderView,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from ...core import filetypes as ft
from ...core.models import Cancelled, TaskControl
from .. import icons, theme
from ..context import AppContext
from ..i18n import fmt_date, fmt_int, fmt_size, plural, tr
from ..widgets import (
    Banner,
    Card,
    FolderList,
    PageHeader,
    ProgressPanel,
    ScrollPage,
    StatCard,
    button,
    confirm,
    error,
    hbox,
    label,
    open_in_explorer,
    vbox,
)
from ..workers import run_task
from .. import media
from .sort import ResultStep, _clear_layout

MODES = ("exact", "images", "music", "videos")
MODE_ICONS = {"exact": "copy", "images": "image", "music": "music", "videos": "video"}

ACTIONS = ("move", "trash", "hardlink")


class DuplicatesPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.groups = []
        self.control: TaskControl | None = None
        self.plan = None
        lay = vbox(self)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)

        # --- configuration ---
        setup = ScrollPage(max_width=980)
        setup.body.addWidget(PageHeader(tr("dups.title"), tr("dups.subtitle")))
        card = Card(tr("dups.where"), tr("dups.where_text"), "folder")
        self.folders = FolderList(tr("sort.add_folder"), tr("sort.no_source"), tr("dups.choose"))
        card.add(self.folders)
        self.recursive = QCheckBox(tr("sort.recursive"))
        self.recursive.setChecked(True)
        card.add(self.recursive)
        setup.body.addWidget(card)

        mode_card = Card(tr("dups.what"), None, "search")
        self.mode = QComboBox()
        for m in MODES:
            self.mode.addItem(icons.icon(MODE_ICONS[m]), tr(f"dups.mode_{m}"), m)
        mode_card.add(self.mode)
        self.mode_hint = label("", "Muted", wrap=True)
        mode_card.add(self.mode_hint)
        srow = hbox(spacing=8)
        self.sens_label = label(tr("dups.sensitivity"))
        srow.addWidget(self.sens_label)
        self.sensitivity = QComboBox()
        for s in ("strict", "normal", "loose"):
            self.sensitivity.addItem(tr(f"dups.sens_{s}"), s)
        self.sensitivity.setCurrentIndex(1)
        srow.addWidget(self.sensitivity)
        srow.addStretch(1)
        mode_card.add(srow)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        setup.body.addWidget(mode_card)

        opts = Card(tr("dups.options"), tr("dups.options_text"), "settings")
        row = hbox(spacing=8)
        row.addWidget(label(tr("dups.min_size")))
        self.min_size = QSpinBox()
        self.min_size.setRange(0, 1024 * 1024)
        self.min_size.setSuffix(" Ko")
        self.min_size.setValue(max(0, int(ctx.config.get("dup_min_size", 1)) // 1024))
        row.addWidget(self.min_size)
        row.addStretch(1)
        opts.add(row)
        crit = ", ".join(tr(f"dup_keep.{k}") for k in ctx.config.get("dup_keep", []))
        opts.add(label(tr("dups.keep_rule", rules=crit), "Muted", wrap=True))
        opts.add(button(tr("dups.change_rules"), "settings", "link", on_click=lambda: ctx.navigate.emit("settings", None)))
        setup.body.addWidget(opts)
        go = hbox(spacing=12)
        go.addWidget(label(tr("sort.nothing_changes"), "Muted", wrap=True), 1)
        go.addWidget(button(tr("dups.search"), "search", "primary", big=True, on_click=self.search))
        setup.body.addLayout(go)
        setup.body.addStretch(1)
        self.setup_page = setup

        # --- progression ---
        prog = ScrollPage(max_width=820)
        prog.body.addSpacing(40)
        prog.body.addWidget(label(tr("dups.searching"), "PageTitle"))
        prog.body.addWidget(label(tr("dups.searching_text"), "PageSubtitle", wrap=True))
        self.progress = ProgressPanel()
        prog.body.addWidget(self.progress)
        prog.body.addStretch(1)
        self.progress_page = prog

        # --- résultats ---
        res = QWidget()
        rl = vbox(res, (28, 22, 28, 18), 12)
        head = hbox(spacing=10)
        texts = vbox(spacing=2)
        texts.addWidget(label(tr("dups.results"), "PageTitle"))
        texts.addWidget(label(tr("dups.results_text"), "PageSubtitle", wrap=True))
        head.addLayout(texts, 1)
        head.addWidget(button(tr("dups.new_search"), "arrow_left", on_click=self.back), 0, Qt.AlignBottom)
        rl.addLayout(head)
        self.banner_box = vbox(spacing=6)
        rl.addLayout(self.banner_box)
        stats = hbox(spacing=12)
        self.s_groups = StatCard("copy", "0", tr("dups.stat_groups"))
        self.s_files = StatCard("file", "0", tr("dups.stat_files"))
        self.s_space = StatCard("sparkles", "0", tr("dups.stat_space"), theme.colors()["success"])
        for w in (self.s_groups, self.s_files, self.s_space):
            stats.addWidget(w)
        rl.addLayout(stats)
        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("dups.col_file"), tr("dups.col_folder"), tr("dups.col_date"),
                                   tr("dups.col_quality"), tr("preview.col_size")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(0, 320)
        self.tree.setColumnWidth(2, 130)
        self.tree.setColumnWidth(3, 110)
        self.tree.setColumnWidth(4, 90)
        self.tree.setTextElideMode(Qt.ElideMiddle)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(self._current_changed)
        split.addWidget(self.tree)
        side = Card(margins=(14, 12, 14, 12))
        self.thumb = label()
        self.thumb.setFixedSize(220, 165)
        self.thumb.setAlignment(Qt.AlignCenter)
        side.add(self.thumb)
        self.detail = label(tr("dups.select_hint"), "Muted", wrap=True, selectable=True)
        self.detail.setTextFormat(Qt.RichText)
        side.add(self.detail)
        self.play_btn = button(tr("media.play"), "play", "ghost", on_click=self._play_current)
        side.add(self.play_btn)
        side.add(button(tr("preview.show_source"), "folder_open", "ghost", on_click=self._open_current))
        side.body.addStretch(1)
        side.setMaximumWidth(280)
        split.addWidget(side)
        split.setStretchFactor(0, 4)
        rl.addWidget(split, 1)
        act = hbox(spacing=8)
        act.addWidget(label(tr("dups.what_to_do")))
        self.action = QComboBox()
        for a in ACTIONS:
            self.action.addItem(tr(f"dups.action_{a}"), a)
        idx = self.action.findData(ctx.config.get("dup_action", "move"))
        self.action.setCurrentIndex(max(0, idx))
        act.addWidget(self.action)
        self.action_hint = label("", "Faint", wrap=True)
        act.addWidget(self.action_hint, 1)
        self.action.currentIndexChanged.connect(self._action_changed)
        self.apply_btn = button(tr("dups.apply"), "check", "primary", big=True, on_click=self.apply)
        act.addWidget(self.apply_btn)
        rl.addLayout(act)
        self.results_page = res

        self.result_step = ResultStep(ctx, self.back)
        for w in (self.setup_page, self.progress_page, self.results_page, self.result_step):
            self.stack.addWidget(w)
        self._action_changed()
        self._mode_changed()
        self._updating = False
        self.similar = False

    def receive(self, payload) -> None:
        if payload and payload.get("sources") and not self.ctx.busy:
            self.folders.set_paths(payload["sources"])
            self.stack.setCurrentWidget(self.setup_page)

    def back(self) -> None:
        self.stack.setCurrentWidget(self.setup_page)

    def _action_changed(self) -> None:
        self.action_hint.setText(tr(f"dups.action_{self.action.currentData()}_hint"))

    def _mode_changed(self) -> None:
        mode = self.mode.currentData()
        self.mode_hint.setText(tr(f"dups.mode_{mode}_hint"))
        similar = mode != "exact"
        self.sens_label.setVisible(similar)
        self.sensitivity.setVisible(similar)

    # -- recherche ---------------------------------------------------------------
    def search(self) -> None:
        folders = self.folders.paths()
        if not folders:
            error(self, tr("dups.title"), tr("check.no_source"))
            return
        if self.ctx.busy:
            error(self, tr("common.busy_title"), tr("common.busy_text", task=self.ctx.busy_label))
            return
        from ...core.protect import Protection

        prot = Protection(self.ctx.config.get("protected"))
        bad = [(prot.source_problem(f), f) for f in folders if prot.source_problem(f)]
        if bad:
            error(self, tr("sort.cannot_start"), "<br>".join(f"• {tr(k)} — {p}" for k, p in bad))
            return
        self.ctx.config.set("dup_min_size", max(1, self.min_size.value() * 1024))
        self.control = TaskControl()
        self.progress.attach(self.control)
        self.stack.setCurrentWidget(self.progress_page)
        self.ctx.set_busy(True, tr("busy.duplicates"))
        mode = self.mode.currentData()
        self.similar = mode != "exact"
        if self.similar:
            run_task(self.ctx.engine.find_similar, folders, mode, self.sensitivity.currentData(), self.control,
                     self.recursive.isChecked(), on_done=self._found, on_error=self._failed)
        else:
            run_task(self.ctx.engine.find_duplicates, folders, self.control, self.recursive.isChecked(),
                     on_done=self._found, on_error=self._failed)

    def _failed(self, exc) -> None:
        self.progress.detach()
        self.ctx.set_busy(False)
        self.stack.setCurrentWidget(self.setup_page)
        if not isinstance(exc, Cancelled):
            error(self, tr("dups.failed"), str(exc), getattr(exc, "traceback_text", None))

    def _found(self, result) -> None:
        self.progress.detach()
        self.ctx.set_busy(False)
        _scan, self.groups = result
        # les liens physiques n'ont de sens que pour des fichiers strictement identiques
        index = self.action.findData("hardlink")
        item = self.action.model().item(index)
        if item is not None:
            item.setEnabled(not self.similar)
        if self.similar and self.action.currentData() == "hardlink":
            self.action.setCurrentIndex(self.action.findData("move"))
        self._fill()
        if self.similar and self.groups:
            self.banner_box.addWidget(Banner("warning", tr("dups.similar_warning")))
        self.stack.setCurrentWidget(self.results_page)

    # -- affichage ---------------------------------------------------------------
    def _fill(self) -> None:
        self._updating = True
        self.tree.clear()
        _clear_layout(self.banner_box)
        if not self.groups:
            self.banner_box.addWidget(Banner("success", tr("dups.none")))
        c = theme.colors()
        for gi, g in enumerate(self.groups):
            top = QTreeWidgetItem()
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable)
            top.setCheckState(0, Qt.Checked if g.enabled else Qt.Unchecked)
            top.setData(0, Qt.UserRole, ("group", gi))
            group = ft.group_of(g.files[0].kind)
            top.setIcon(0, icons.kind_icon(group))
            top.setText(0, tr("dups.group_title", n=len(g.files), name=g.keep.name))
            top.setText(4, fmt_size(g.size) if not g.similar else "")
            top.setTextAlignment(4, Qt.AlignRight | Qt.AlignVCenter)
            top.setToolTip(0, tr("dups.group_tip", space=fmt_size(g.wasted)))
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            for fi, e in enumerate(g.files):
                child = QTreeWidgetItem()
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setData(0, Qt.UserRole, ("file", gi, fi))
                child.setText(0, e.name)
                child.setText(1, e.dir)
                child.setToolTip(1, e.dir)
                child.setText(2, fmt_date(e.mtime))
                child.setText(3, quality_text(e))
                child.setText(4, fmt_size(e.size))
                child.setTextAlignment(4, Qt.AlignRight | Qt.AlignVCenter)
                child.setCheckState(0, Qt.Checked if e is g.keep else Qt.Unchecked)
                self._style_child(child, e is g.keep)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            if gi < 30:
                top.setExpanded(True)
        self._updating = False
        self._update_stats()
        self.thumb.clear()
        self.detail.setText(tr("dups.select_hint"))

    def _style_child(self, item: QTreeWidgetItem, keep: bool) -> None:
        c = theme.colors()
        item.setIcon(0, icons.icon("lock" if keep else "trash", c["success"] if keep else c["text3"], 16))
        item.setToolTip(0, tr("dups.keep_tip") if keep else tr("dups.remove_tip"))
        for col in range(5):
            item.setForeground(col, theme_color(c["text"] if keep else c["text2"]))

    def _update_stats(self) -> None:
        active = [g for g in self.groups if g.enabled]
        self.s_groups.set_value(fmt_int(len(active)))
        self.s_files.set_value(fmt_int(sum(len(g.files) - 1 for g in active)))
        self.s_space.set_value(fmt_size(sum(g.wasted for g in active)))
        self.apply_btn.setEnabled(bool(active))

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        self._updating = True
        try:
            if data[0] == "group":
                self.groups[data[1]].enabled = item.checkState(0) == Qt.Checked
            else:
                _, gi, fi = data
                g = self.groups[gi]
                if item.checkState(0) == Qt.Checked:
                    g.keep = g.files[fi]
                parent = item.parent()
                for i in range(parent.childCount()):
                    ch = parent.child(i)
                    is_keep = g.files[i] is g.keep
                    ch.setCheckState(0, Qt.Checked if is_keep else Qt.Unchecked)
                    self._style_child(ch, is_keep)
                parent.setText(0, tr("dups.group_title", n=len(g.files), name=g.keep.name))
        finally:
            self._updating = False
        self._update_stats()

    def _current_entry(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        data = item.data(0, Qt.UserRole)
        if not data:
            return None
        if data[0] == "group":
            return self.groups[data[1]].keep
        return self.groups[data[1]].files[data[2]]

    def _current_changed(self, *_):
        e = self._current_entry()
        if e is None:
            return
        c = theme.colors()
        self.detail.setText(f"<b>{_esc(e.name)}</b><br>{_esc(e.dir)}<br>{fmt_size(e.size)} · {fmt_date(e.mtime)}")
        self.thumb.clear()
        q = quality_text(e)
        if q:
            self.detail.setText(self.detail.text() + f"<br>{tr('dups.col_quality')} : {q}")
        self.play_btn.setVisible(media.is_playable(e.ext))
        if ft.group_of(e.kind) in ("image", "video") and e.size < 4 * 1024 ** 3:
            pm = media.thumbnail(e.path, e.ext, 220, 165)
            if pm is not None:
                self.thumb.setPixmap(pm)
                return
        self.thumb.setPixmap(icons.pixmap(icons.KIND_ICONS.get(ft.group_of(e.kind), "file"), c["text3"], 56))

    def _play_current(self) -> None:
        e = self._current_entry()
        if e is not None:
            media.play(e.path, self)

    def _open_current(self) -> None:
        e = self._current_entry()
        if e is not None:
            open_in_explorer(e.path, select=True)

    # -- application ---------------------------------------------------------------
    def apply(self) -> None:
        if self.ctx.busy:
            return
        action = self.action.currentData()
        active = [g for g in self.groups if g.enabled]
        n = sum(len(g.files) - 1 for g in active)
        space = sum(g.wasted for g in active)
        if not n:
            return
        self.ctx.config.set("dup_action", action)
        text = tr(f"dups.confirm_{action}", n=fmt_int(n), size=fmt_size(space))
        if not confirm(self, tr("dups.confirm_title"), text, tr("dups.apply"), danger=action != "move"):
            return
        plan = self.ctx.engine.duplicate_plan(self.groups, action)
        disabled = [op for op in plan.ops if not op.enabled]
        if disabled:
            self.banner_box.addWidget(Banner("warning", plural(len(disabled), "dups.hardlink_skipped")))
        self.plan = plan
        self.control = TaskControl()
        self.progress.attach(self.control)
        self.stack.setCurrentWidget(self.progress_page)
        self.ctx.set_busy(True, tr("busy.duplicates"))
        run_task(self.ctx.engine.execute, plan, self.control, "duplicates", tr("dups.title"), False,
                 on_done=self._applied, on_error=self._apply_failed)

    def _applied(self, result) -> None:
        self.progress.detach()
        self.ctx.set_busy(False)
        self.ctx.sessionsChanged.emit()
        self.result_step.show_result(result, self.plan)
        self.stack.setCurrentWidget(self.result_step)

    def _apply_failed(self, exc) -> None:
        self.progress.detach()
        self.ctx.set_busy(False)
        self.ctx.sessionsChanged.emit()
        error(self, tr("dups.failed"), str(exc), getattr(exc, "traceback_text", None))
        self.stack.setCurrentWidget(self.results_page)


def quality_text(e) -> str:
    meta = e.meta
    if meta.get("width") and meta.get("height"):
        return f"{meta['width']} × {meta['height']}"
    if meta.get("bitrate"):
        return f"{int(meta['bitrate']) // 1000} kb/s"
    if meta.get("pixels"):
        return f"{meta['pixels'] // 1000} kpx"
    return ""


def theme_color(hex_color: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(hex_color))


def _esc(text: str) -> str:
    import html

    return html.escape(text)
