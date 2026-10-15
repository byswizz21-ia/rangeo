"""Page « Ranger » : configuration → analyse → aperçu → exécution → résultat."""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QLineEdit,
    QMenu,
    QRadioButton,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from ...core import filetypes as ft
from ...core.engine import SortRequest
from ...core.fsutil import nkey
from ...core.models import OP_COPY, OP_MOVE, OP_TRASH, Cancelled, TaskControl
from ...core.report import export_html
from .. import icons, theme
from ..context import AppContext
from ..i18n import fmt_date, fmt_duration, fmt_int, fmt_size, kind_label, plural, tr
from ..plan_model import OpsModel, PROBLEM_NOTES, relative_target
from ..widgets import (
    Banner,
    Card,
    FolderList,
    PageHeader,
    ProgressPanel,
    ScrollPage,
    StatCard,
    button,
    choose_folder,
    confirm,
    error,
    hbox,
    label,
    open_in_explorer,
    vbox,
)
from ..workers import run_task

KNOWN_FOLDERS = [
    ("sort.known_downloads", QStandardPaths.DownloadLocation),
    ("sort.known_desktop", QStandardPaths.DesktopLocation),
    ("sort.known_documents", QStandardPaths.DocumentsLocation),
    ("sort.known_pictures", QStandardPaths.PicturesLocation),
    ("sort.known_videos", QStandardPaths.MoviesLocation),
    ("sort.known_music", QStandardPaths.MusicLocation),
]


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            _clear_layout(item.layout())


# ===========================================================================
# Étape 1 : configuration
# ===========================================================================
class SetupStep(ScrollPage):
    def __init__(self, ctx: AppContext, on_analyze):
        super().__init__(max_width=980)
        self.ctx = ctx
        b = self.body
        b.addWidget(PageHeader(tr("sort.title"), tr("sort.subtitle")))

        src_card = Card(tr("sort.step_sources"), tr("sort.step_sources_text"), "folder")
        self.sources = FolderList(tr("sort.add_folder"), tr("sort.no_source"), tr("sort.choose_source"))
        src_card.add(self.sources)
        chips = hbox(spacing=6)
        chips.addWidget(label(tr("sort.suggestions"), "Faint"))
        for key, loc in KNOWN_FOLDERS:
            path = QStandardPaths.writableLocation(loc)
            if path and os.path.isdir(path):
                btn = button(tr(key), "plus", "ghost")
                btn.setToolTip(os.path.normpath(path))
                btn.clicked.connect(lambda _=False, p=path: self.sources.add_path(p))
                chips.addWidget(btn)
        chips.addStretch(1)
        src_card.add(chips)
        b.addWidget(src_card)

        dst_card = Card(tr("sort.step_destination"), tr("sort.step_destination_text"), "inbox")
        self.mode_group = QButtonGroup(self)
        self.to_folder = QRadioButton(tr("sort.to_folder"))
        self.in_place = QRadioButton(tr("sort.in_place"))
        self.mode_group.addButton(self.to_folder)
        self.mode_group.addButton(self.in_place)
        dst_card.add(self.to_folder)
        row = hbox(spacing=6)
        row.setContentsMargins(26, 0, 0, 0)
        self.dest = QLineEdit()
        self.dest.setPlaceholderText(tr("sort.dest_placeholder"))
        row.addWidget(self.dest, 1)
        row.addWidget(button(tr("common.browse"), "folder", on_click=self._browse_dest))
        dst_card.add(row)
        dst_card.add(self.in_place)
        hint = label(tr("sort.in_place_hint"), "Faint", wrap=True)
        hint.setContentsMargins(26, 0, 0, 0)
        dst_card.add(hint)
        self.to_folder.toggled.connect(self._update_mode)
        b.addWidget(dst_card)

        how = Card(tr("sort.step_how"), tr("sort.step_how_text"), "rules")
        prow = hbox(spacing=8)
        prow.addWidget(label(tr("sort.profile")))
        self.profile = QComboBox()
        self.profile.setMinimumWidth(260)
        prow.addWidget(self.profile)
        prow.addWidget(button(tr("sort.edit_rules"), "edit", "link",
                              on_click=lambda: ctx.navigate.emit("rules", {"profile": self.profile.currentData()})))
        prow.addStretch(1)
        how.add(prow)
        self.recursive = QCheckBox(tr("sort.recursive"))
        self.cleanup = QCheckBox(tr("sort.cleanup"))
        how.add(self.recursive)
        how.add(self.cleanup)
        self.profile.currentIndexChanged.connect(self._profile_changed)
        b.addWidget(how)

        go = hbox(spacing=12)
        go.addWidget(label(tr("sort.nothing_changes"), "Muted", wrap=True), 1)
        self.analyze_btn = button(tr("sort.analyze"), "search", "primary", big=True, on_click=on_analyze)
        go.addWidget(self.analyze_btn)
        b.addLayout(go)
        b.addStretch(1)
        self.reload_profiles()
        self._restore()

    def reload_profiles(self) -> None:
        current = self.profile.currentData() or self.ctx.config.get("last_profile")
        self.profile.blockSignals(True)
        self.profile.clear()
        for p in self.ctx.config.profiles:
            self.profile.addItem(icons.icon("rules"), p["name"], p["id"])
        idx = max(0, self.profile.findData(current))
        self.profile.setCurrentIndex(idx)
        self.profile.blockSignals(False)

    def _restore(self) -> None:
        cfg = self.ctx.config
        self.sources.set_paths([p for p in cfg.get("last_sources", []) if os.path.isdir(p)])
        self.dest.setText(cfg.get("last_destination", ""))
        self._profile_changed()

    def _profile_changed(self) -> None:
        p = self.ctx.config.profile(self.profile.currentData())
        if p.get("in_place"):
            self.in_place.setChecked(True)
        else:
            self.to_folder.setChecked(True)
        if p.get("destination"):
            self.dest.setText(p["destination"])
        self.recursive.setChecked(bool(p.get("recursive", True)))
        self.cleanup.setChecked(bool(p.get("cleanup_empty", True)))
        self._update_mode()

    def _update_mode(self) -> None:
        self.dest.setEnabled(self.to_folder.isChecked())

    def _browse_dest(self) -> None:
        path = choose_folder(self, tr("sort.choose_destination"), self.dest.text())
        if path:
            self.dest.setText(path)

    def request(self, open_up: list[str]) -> SortRequest:
        return SortRequest(
            sources=self.sources.paths(),
            profile=self.ctx.config.profile(self.profile.currentData()),
            destination=self.dest.text().strip() if self.to_folder.isChecked() else "",
            in_place=self.in_place.isChecked(),
            recursive=self.recursive.isChecked(),
            open_up=list(open_up),
        )

    def remember(self) -> None:
        cfg = self.ctx.config
        cfg.set("last_sources", self.sources.paths(), save=False)
        cfg.set("last_destination", self.dest.text().strip(), save=False)
        cfg.set("last_profile", self.profile.currentData(), save=True)


# ===========================================================================
# Étape 3 : aperçu
# ===========================================================================
class PreviewStep(QWidget):
    def __init__(self, ctx: AppContext, on_back, on_run, on_open_up):
        super().__init__()
        self.ctx = ctx
        self.on_open_up = on_open_up
        self.plan = None
        self.scan = None
        outer = vbox(self, (28, 22, 28, 18), 12)

        head = hbox(spacing=10)
        texts = vbox(spacing=2)
        texts.addWidget(label(tr("preview.title"), "PageTitle"))
        texts.addWidget(label(tr("preview.subtitle"), "PageSubtitle", wrap=True))
        head.addLayout(texts, 1)
        head.addWidget(button(tr("preview.back"), "arrow_left", on_click=on_back), 0, Qt.AlignBottom)
        self.run_btn = button(tr("preview.run"), "play", "primary", big=True, on_click=on_run)
        head.addWidget(self.run_btn, 0, Qt.AlignBottom)
        outer.addLayout(head)

        self.banners = vbox(spacing=6)
        outer.addLayout(self.banners)

        stats = hbox(spacing=12)
        self.s_files = StatCard("check_circle", "0", tr("preview.stat_files"))
        self.s_size = StatCard("layers", "0", tr("preview.stat_size"))
        self.s_folders = StatCard("folder", "0", tr("preview.stat_folders"))
        self.s_renamed = StatCard("edit", "0", tr("preview.stat_renamed"), theme.colors()["warning"])
        self.s_dups = StatCard("copy", "0", tr("preview.stat_dups"))
        self.s_left = StatCard("lock", "0", tr("preview.stat_left"))
        for w in (self.s_files, self.s_size, self.s_folders, self.s_renamed, self.s_dups, self.s_left):
            stats.addWidget(w)
        outer.addLayout(stats)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        # --- onglet 1 : fichiers à ranger ---
        tab = QWidget()
        tl = vbox(tab, (0, 8, 0, 0), 8)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = vbox(left, spacing=6)
        ll.addWidget(label(tr("preview.after"), "SectionTitle"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("preview.folder"), tr("preview.count"), tr("preview.col_size")])
        self.tree.setColumnWidth(0, 230)
        self.tree.setTextElideMode(Qt.ElideMiddle)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        ll.addWidget(self.tree, 1)
        split.addWidget(left)

        right = QWidget()
        rl = vbox(right, spacing=6)
        filt = hbox(spacing=6)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("preview.search"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search"), QLineEdit.LeadingPosition)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(lambda: self.model.set_filters(text=self.search.text()))
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        filt.addWidget(self.search, 1)
        self.group_filter = QComboBox()
        self.group_filter.addItem(tr("preview.all_types"), "")
        for g in ("image", "video", "audio", "document", "archive", "software", "game", "code", "bundle", "unknown"):
            self.group_filter.addItem(icons.kind_icon(g), tr(f"group.{g}"), g)
        self.group_filter.currentIndexChanged.connect(
            lambda: self.model.set_filters(group=self.group_filter.currentData()))
        filt.addWidget(self.group_filter)
        self.mode_filter = QComboBox()
        for key in ("all", "active", "notes", "problems"):
            self.mode_filter.addItem(tr(f"preview.mode_{key}"), key)
        self.mode_filter.currentIndexChanged.connect(
            lambda: self.model.set_filters(mode=self.mode_filter.currentData()))
        filt.addWidget(self.mode_filter)
        rl.addLayout(filt)

        self.model = OpsModel()
        self.model.checkedChanged.connect(self._update_counts)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideMiddle)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for col, w in enumerate((290, 270, 120, 70, 230, 160)):
            self.table.setColumnWidth(col, w)
        self.table.selectionModel().currentRowChanged.connect(self._row_selected)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        rl.addWidget(self.table, 3)

        detail = Card(margins=(12, 10, 12, 10), spacing=6)
        drow = hbox(spacing=12)
        self.thumb = label()
        self.thumb.setFixedSize(120, 90)
        self.thumb.setAlignment(Qt.AlignCenter)
        drow.addWidget(self.thumb)
        self.detail = label("", "Muted", wrap=True, selectable=True)
        self.detail.setTextFormat(Qt.RichText)
        drow.addWidget(self.detail, 1)
        detail.add(drow)
        detail.setMaximumHeight(130)
        rl.addWidget(detail)

        sel = hbox(spacing=8)
        self.count_label = label("", "Muted")
        sel.addWidget(self.count_label, 1)
        sel.addWidget(button(tr("preview.check_all"), "check", "ghost", on_click=lambda: self.model.set_all(True)))
        sel.addWidget(button(tr("preview.uncheck_all"), "x", "ghost", on_click=lambda: self.model.set_all(False)))
        rl.addLayout(sel)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([380, 900])
        tl.addWidget(split, 1)
        self.tabs.addTab(tab, icons.icon("sort"), tr("preview.tab_files"))

        # --- onglet 2 : laissés en place ---
        tab2 = QWidget()
        t2 = vbox(tab2, (0, 8, 0, 0), 8)
        t2.addWidget(label(tr("preview.left_text"), "Muted", wrap=True))
        self.left_tree = QTreeWidget()
        self.left_tree.setHeaderLabels([tr("preview.col_name"), tr("preview.col_reason"), tr("preview.col_size")])
        self.left_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.left_tree.header().setStretchLastSection(False)
        self.left_tree.setColumnWidth(1, 320)
        self.left_tree.setColumnWidth(2, 90)
        self.left_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.left_tree.customContextMenuRequested.connect(self._left_menu)
        t2.addWidget(self.left_tree, 1)
        brow = hbox(spacing=8)
        brow.addStretch(1)
        self.open_up_btn = button(tr("preview.open_up"), "folder_open", on_click=self._open_up_selected)
        brow.addWidget(self.open_up_btn)
        t2.addLayout(brow)
        self.tabs.addTab(tab2, icons.icon("lock"), tr("preview.tab_left"))

    # -- remplissage ---------------------------------------------------------------
    def set_plan(self, scan, plan) -> None:
        self.scan, self.plan = scan, plan
        self.model.set_ops(plan.ops, plan.dest_root)
        self.search.clear()
        self.group_filter.setCurrentIndex(0)
        self.mode_filter.setCurrentIndex(0)
        self.model.sort(1, Qt.AscendingOrder)
        self.table.horizontalHeader().setSortIndicator(1, Qt.AscendingOrder)
        self._fill_banners()
        self._fill_tree()
        self._fill_left()
        self._update_counts()
        self.tabs.setCurrentIndex(0)
        self.detail.setText(tr("preview.select_hint"))
        self.thumb.clear()

    def _fill_banners(self) -> None:
        _clear_layout(self.banners)
        plan, scan = self.plan, self.scan
        for key, detail in plan.errors:
            self.banners.addWidget(Banner("error", f"<b>{tr(key)}</b><br>{detail}"))
        for key, path in plan.warnings:
            self.banners.addWidget(Banner("warning", tr(key, path=path)))
        if scan.errors:
            self.banners.addWidget(Banner("warning", plural(len(scan.errors), "preview.scan_errors")))
        skipped = scan.skip_counts
        parts = [plural(n, f"skip.{reason}") for reason, n in sorted(skipped.items()) if n]
        if parts:
            self.banners.addWidget(Banner("info", tr("preview.ignored", list=", ".join(parts)), closable=True))
        if not plan.ops:
            self.banners.addWidget(Banner("success", tr("preview.nothing_to_do")))

    def _fill_tree(self) -> None:
        self.tree.clear()
        plan = self.plan
        c = theme.colors()
        root_item = QTreeWidgetItem([tr("preview.all_files"), "", ""])
        root_item.setIcon(0, icons.icon("layers", c["accent"]))
        root_item.setData(0, Qt.UserRole, None)
        self.tree.addTopLevelItem(root_item)
        nodes: dict[str, QTreeWidgetItem] = {}
        stats: dict[str, list] = {}
        trash_count = trash_size = 0
        for op in plan.ops:
            if not op.enabled:
                continue
            if op.op == OP_TRASH or not op.dst:
                trash_count += 1
                trash_size += op.size
                continue
            rel = relative_target(op, plan.dest_root)
            parts = [p for p in rel.split(os.sep) if p]
            abs_folder = os.path.dirname(op.dst)
            # chemin absolu de chaque niveau (pour filtrer le tableau)
            levels = []
            cur = abs_folder
            for _ in parts:
                levels.append(cur)
                cur = os.path.dirname(cur)
            levels.reverse()
            key = ""
            for i, part in enumerate(parts):
                key = key + "\\" + part.lower()
                st = stats.setdefault(key, [0, 0, part, levels[i] if i < len(levels) else abs_folder])
                st[0] += 1
                st[1] += op.size
        for key in sorted(stats, key=lambda k: (k.count("\\"), k)):
            count, size, name, abs_path = stats[key]
            parent_key = key.rsplit("\\", 1)[0]
            parent = nodes.get(parent_key, root_item)
            item = QTreeWidgetItem([name, fmt_int(count), fmt_size(size)])
            item.setIcon(0, icons.icon("folder", c["accent"]))
            item.setData(0, Qt.UserRole, nkey(abs_path) + "\\")
            item.setData(0, Qt.UserRole + 1, abs_path)
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            parent.addChild(item)
            nodes[key] = item
        if trash_count:
            t = QTreeWidgetItem([tr("op.trash_target"), fmt_int(trash_count), fmt_size(trash_size)])
            t.setIcon(0, icons.icon("trash", c["danger"]))
            t.setData(0, Qt.UserRole, "__trash__")
            root_item.addChild(t)
        root_item.setExpanded(True)
        for i in range(root_item.childCount()):
            root_item.child(i).setExpanded(True)
        self.s_folders.set_value(fmt_int(len(stats)))

    def _fill_left(self) -> None:
        self.left_tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        c = theme.colors()
        for op in self.plan.skipped:
            entry = op.entry
            reason = op.note or "plan.no_rule"
            if entry is not None and entry.is_bundle:
                reason = f"bundle.{entry.kind}"
            parent = groups.get(reason)
            if parent is None:
                parent = QTreeWidgetItem([tr(reason) if not reason.startswith("bundle.") else tr(reason), "", ""])
                parent.setIcon(0, icons.icon("lock" if reason.startswith("bundle.") else "info", c["text2"]))
                parent.setFirstColumnSpanned(True)
                self.left_tree.addTopLevelItem(parent)
                groups[reason] = parent
            name = entry.path if entry is not None else op.src
            why = op.rule or ""
            if entry is not None and entry.is_bundle:
                why = tr(f"bundle_reason.{entry.bundle_reason}") + " · " + plural(entry.bundle_files, "common.files")
            item = QTreeWidgetItem([name, why, fmt_size(op.size)])
            item.setToolTip(0, name)
            item.setData(0, Qt.UserRole, entry.path if entry is not None and entry.is_bundle else None)
            if entry is not None:
                group = "bundle" if entry.is_bundle else ft.group_of(entry.kind)
                item.setIcon(0, icons.kind_icon(group))
            parent.addChild(item)
        for reason, item in groups.items():
            item.setText(0, f"{tr(reason)}  ({fmt_int(item.childCount())})")
            item.setExpanded(reason.startswith("bundle.") and item.childCount() <= 50)
        self.tabs.setTabText(1, tr("preview.tab_left") + f" ({fmt_int(len(self.plan.skipped))})")

    def _update_counts(self) -> None:
        plan = self.plan
        if plan is None:
            return
        active = plan.active_ops()
        size = sum(op.size for op in active)
        self.s_files.set_value(fmt_int(len(active)))
        self.s_size.set_value(fmt_size(size))
        self.s_renamed.set_value(fmt_int(sum(1 for op in active if op.note == "plan.renamed_conflict")))
        self.s_dups.set_value(fmt_int(sum(1 for op in active if op.note in (
            "plan.identical_to_duplicates", "plan.identical_trashed"))))
        self.s_left.set_value(fmt_int(len(plan.skipped)))
        self.count_label.setText(tr("preview.selected", n=fmt_int(len(active)), size=fmt_size(size)))
        self.run_btn.setEnabled(bool(active) and not plan.errors)

    # -- interactions --------------------------------------------------------------
    def _tree_selected(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        key = items[0].data(0, Qt.UserRole)
        if key == "__trash__":
            self.model.set_filters(folder=None, mode="all")
            self.search.setText("")
            return
        self.model.set_filters(folder=key)

    def _set_folder_enabled(self, item: QTreeWidgetItem, enabled: bool) -> None:
        key = item.data(0, Qt.UserRole)
        for op in self.plan.ops:
            if op.note in PROBLEM_NOTES:
                continue
            if key is None or (key == "__trash__" and op.op == OP_TRASH) or (
                    key not in (None, "__trash__") and op.dst and
                    nkey(os.path.dirname(op.dst) + "\\").startswith(key)):
                op.enabled = enabled
        self.model.apply_filter()
        self._fill_tree()
        self._update_counts()

    def _tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction(icons.icon("x"), tr("preview.exclude_folder"), lambda: self._set_folder_enabled(item, False))
        menu.addAction(icons.icon("check"), tr("preview.include_folder"), lambda: self._set_folder_enabled(item, True))
        path = item.data(0, Qt.UserRole + 1)
        if path:
            menu.addSeparator()
            menu.addAction(icons.icon("folder_open"), tr("preview.open_existing"),
                           lambda: open_in_explorer(_existing(path)))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _table_menu(self, pos) -> None:
        rows = {i.row() for i in self.table.selectionModel().selectedRows()}
        if not rows:
            return
        menu = QMenu(self)

        def set_rows(enabled):
            for r in rows:
                op = self.model.op_at(r)
                if op is not None and op.note not in PROBLEM_NOTES:
                    op.enabled = enabled
            self.model.apply_filter()
            self._fill_tree()
            self._update_counts()

        menu.addAction(icons.icon("x"), tr("preview.exclude_selection"), lambda: set_rows(False))
        menu.addAction(icons.icon("check"), tr("preview.include_selection"), lambda: set_rows(True))
        op = self.model.op_at(min(rows))
        if op is not None:
            menu.addSeparator()
            menu.addAction(icons.icon("folder_open"), tr("preview.show_source"),
                           lambda: open_in_explorer(op.src, select=True))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _row_selected(self, current, _previous) -> None:
        op = self.model.op_at(current.row())
        if op is None:
            return
        e = op.entry
        lines = [f"<b>{_esc(e.name if e else os.path.basename(op.src))}</b>"]
        lines.append(f"{tr('preview.from')} {_esc(op.src)}")
        if op.dst:
            lines.append(f"{tr('preview.to')} {_esc(op.dst)}")
        elif op.op == OP_TRASH:
            lines.append(tr("op.trash_target"))
        if e is not None:
            info = [kind_label(e.kind), fmt_size(op.size)]
            if e.date:
                info.append(tr("preview.date_tip", date=fmt_date(e.date), source=tr(f"date_source.{e.date_source}")))
            meta = e.meta
            for key in ("camera", "artist", "album", "series", "title"):
                if meta.get(key) and not (key == "title" and e.kind not in ("music", "movie")):
                    info.append(f"{tr('meta.' + key)} : {_esc(str(meta[key]))}")
            if e.ext_mismatch and e.real_ext:
                info.append(tr("preview.mismatch", real=e.real_ext.upper()))
            lines.append(" · ".join(info))
        if op.note:
            lines.append(f"<i>{tr(op.note)}</i>")
        self.detail.setText("<br>".join(lines))
        self._show_thumb(op)

    def _show_thumb(self, op) -> None:
        self.thumb.clear()
        e = op.entry
        c = theme.colors()
        if e is None or e.is_bundle or ft.group_of(e.kind) != "image" or e.size > 60 * 1024 * 1024 \
                or e.meta.get("cloud"):
            group = "bundle" if e is not None and e.is_bundle else ft.group_of(e.kind) if e else "unknown"
            self.thumb.setPixmap(icons.pixmap(icons.KIND_ICONS.get(group, "file"), c["text3"], 48))
            return
        reader = QImageReader(op.src)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() > 0:
            size.scale(240, 180, Qt.KeepAspectRatio)
            reader.setScaledSize(size)
        img = reader.read()
        if img.isNull():
            self.thumb.setPixmap(icons.pixmap("image", c["text3"], 48))
            return
        pm = QPixmap.fromImage(img).scaled(120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.thumb.setPixmap(pm)

    def _left_menu(self, pos) -> None:
        item = self.left_tree.itemAt(pos)
        if item is None:
            return
        path = item.data(0, Qt.UserRole)
        menu = QMenu(self)
        if path:
            menu.addAction(icons.icon("folder_open"), tr("preview.open_up"), lambda: self.on_open_up([path]))
        src = item.text(0)
        if src and os.path.exists(src):
            menu.addAction(icons.icon("external"), tr("preview.show_source"), lambda: open_in_explorer(src, True))
        if not menu.isEmpty():
            menu.exec(self.left_tree.viewport().mapToGlobal(pos))

    def _open_up_selected(self) -> None:
        paths = [i.data(0, Qt.UserRole) for i in self.left_tree.selectedItems() if i.data(0, Qt.UserRole)]
        if not paths:
            error(self, tr("preview.open_up"), tr("preview.open_up_hint"))
            return
        self.on_open_up(paths)


def _esc(text: str) -> str:
    import html

    return html.escape(text)


def _existing(path: str) -> str:
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


# ===========================================================================
# Étape 5 : résultat
# ===========================================================================
class ResultStep(ScrollPage):
    def __init__(self, ctx: AppContext, on_new):
        super().__init__(max_width=980)
        self.ctx = ctx
        self.on_new = on_new
        self.result = None
        self.plan = None

    def show_result(self, result, plan) -> None:
        self.result, self.plan = result, plan
        _clear_layout(self.body)
        b = self.body
        c = theme.colors()
        ok = result.status == "completed"
        level_icon = {"completed": ("check_circle", c["success"]), "completed_errors": ("alert", c["warning"]),
                      "cancelled": ("info", c["text2"]), "interrupted": ("error", c["danger"])}
        ic, color = level_icon.get(result.status, ("info", c["text2"]))
        top = hbox(spacing=16)
        top.addWidget(_big_icon(ic, color))
        texts = vbox(spacing=4)
        texts.addWidget(label(tr(result.message), "PageTitle"))
        texts.addWidget(label(tr(f"{result.message}_text"), "PageSubtitle", wrap=True))
        top.addLayout(texts, 1)
        b.addLayout(top)

        stats = hbox(spacing=12)
        moved = sum(op.size for op in plan.active_ops() if op.op in (OP_MOVE, OP_COPY)) if ok else None
        stats.addWidget(StatCard("check_circle", fmt_int(result.files), tr("result.done"), c["success"]))
        stats.addWidget(StatCard("error", fmt_int(result.failed), tr("result.failed"),
                                 c["danger"] if result.failed else c["text3"]))
        stats.addWidget(StatCard("clock", fmt_duration(result.duration), tr("result.duration")))
        stats.addWidget(StatCard("folder", fmt_int(result.removed_dirs), tr("result.removed_dirs")))
        if moved is not None:
            stats.addWidget(StatCard("layers", fmt_size(moved), tr("result.moved")))
        b.addLayout(stats)

        if result.errors:
            card = Card(tr("result.errors_title"), tr("result.errors_text"), "alert")
            tree = QTreeWidget()
            tree.setHeaderLabels([tr("report.col_src"), tr("report.col_error")])
            tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
            tree.setColumnWidth(1, 300)
            tree.setRootIsDecorated(False)
            tree.setMinimumHeight(220)
            for path, code, detail in result.errors[:2000]:
                it = QTreeWidgetItem([path, tr(f"error.{code}")])
                it.setToolTip(0, path)
                it.setToolTip(1, detail)
                tree.addTopLevelItem(it)
            card.add(tree)
            b.addWidget(card)

        actions = hbox(spacing=8)
        dest = plan.dest_root or (plan.sources[0] if plan.sources else "")
        if dest:
            actions.addWidget(button(tr("result.open_folder"), "folder_open", on_click=lambda: open_in_explorer(dest)))
        actions.addWidget(button(tr("result.export"), "download", on_click=self._export))
        if result.files:
            actions.addWidget(button(tr("result.undo"), "undo", "danger", on_click=self._undo))
        if result.status in ("cancelled", "interrupted") or result.failed:
            actions.addWidget(button(tr("result.resume"), "refresh",
                                     on_click=lambda: self.ctx.navigate.emit("history", {"session": result.session_id})))
        actions.addStretch(1)
        actions.addWidget(button(tr("result.new"), "plus", "primary", on_click=self.on_new))
        b.addLayout(actions)
        tip = Card(flat=True, margins=(14, 10, 14, 10))
        tip.add(label(tr("result.undo_tip"), "Muted", wrap=True))
        b.addWidget(tip)
        b.addStretch(1)

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("result.export"),
                                              os.path.expanduser(f"~/Rangeo-rapport-{self.result.session_id}.html"),
                                              "HTML (*.html);;CSV (*.csv)")
        if not path:
            return
        from ...core.report import export_csv
        from ..i18n import language

        if path.lower().endswith(".csv"):
            export_csv(self.ctx.engine.journal, self.result.session_id, path, tr)
        else:
            export_html(self.ctx.engine.journal, self.result.session_id, path, tr, language())
        open_in_explorer(path, select=True)

    def _undo(self) -> None:
        self.ctx.navigate.emit("history", {"session": self.result.session_id, "undo": True})


def _big_icon(name: str, color: str):
    lab = label()
    lab.setFixedSize(64, 64)
    lab.setAlignment(Qt.AlignCenter)
    lab.setPixmap(icons.pixmap(name, color, 48))
    return lab


# ===========================================================================
# Page complète
# ===========================================================================
class SortPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.control: TaskControl | None = None
        self.open_up: list[str] = []
        self.plan = None
        self.scan = None
        self.req = None
        lay = vbox(self)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)
        self.setup = SetupStep(ctx, self.analyze)
        self.progress_page = ScrollPage(max_width=820)
        self.progress_title = label("", "PageTitle")
        self.progress_page.body.addSpacing(40)
        self.progress_page.body.addWidget(self.progress_title)
        self.progress_sub = label("", "PageSubtitle", wrap=True)
        self.progress_page.body.addWidget(self.progress_sub)
        self.progress = ProgressPanel()
        self.progress_page.body.addWidget(self.progress)
        self.progress_page.body.addStretch(1)
        self.preview = PreviewStep(ctx, self.back_to_setup, self.run, self.reanalyze_open_up)
        self.result = ResultStep(ctx, self.back_to_setup)
        for w in (self.setup, self.progress_page, self.preview, self.result):
            self.stack.addWidget(w)
        ctx.configChanged.connect(self.setup.reload_profiles)

    def receive(self, payload) -> None:
        if self.ctx.busy:
            return
        if payload and payload.get("sources"):
            if self.stack.currentWidget() is not self.setup:
                self.stack.setCurrentWidget(self.setup)
            self.setup.sources.set_paths(payload["sources"])

    def back_to_setup(self) -> None:
        self.open_up = []
        self.plan = None
        self.stack.setCurrentWidget(self.setup)

    # -- analyse -------------------------------------------------------------------
    def analyze(self) -> None:
        if self.ctx.busy:
            error(self, tr("common.busy_title"), tr("common.busy_text", task=self.ctx.busy_label))
            return
        self.open_up = []
        self._start_analysis()

    def reanalyze_open_up(self, paths: list[str]) -> None:
        for p in paths:
            if p not in self.open_up:
                self.open_up.append(p)
        self._start_analysis()

    def _start_analysis(self) -> None:
        req = self.setup.request(self.open_up)
        problems = self.ctx.engine.check_request(req)
        if problems:
            text = "<br>".join(f"• {tr(k)}" + (f" — {p}" if p else "") for k, p in problems)
            error(self, tr("sort.cannot_start"), text)
            return
        self.setup.remember()
        self.req = req
        self.control = TaskControl()
        self.progress_title.setText(tr("sort.analyzing"))
        self.progress_sub.setText(tr("sort.analyzing_text"))
        self.progress.attach(self.control)
        self.stack.setCurrentWidget(self.progress_page)
        self.ctx.set_busy(True, tr("busy.analyze"))
        run_task(self.ctx.engine.analyze, req, self.control, on_done=self._analysis_done,
                 on_error=self._analysis_failed)

    def _analysis_done(self, result) -> None:
        self.progress.detach()
        self.ctx.set_busy(False)
        self.scan, self.plan = result
        self.preview.set_plan(self.scan, self.plan)
        self.stack.setCurrentWidget(self.preview)

    def _analysis_failed(self, exc) -> None:
        self.progress.detach()
        self.ctx.set_busy(False)
        self.stack.setCurrentWidget(self.setup)
        if not isinstance(exc, Cancelled):
            error(self, tr("sort.analysis_failed"), str(exc), getattr(exc, "traceback_text", None))

    # -- exécution -------------------------------------------------------------------
    def run(self) -> None:
        plan = self.plan
        if plan is None or self.ctx.busy:
            return
        active = plan.active_ops()
        if not active:
            return
        dest = plan.dest_root or tr("sort.in_place_short")
        text = tr("sort.confirm_text", n=fmt_int(len(active)), size=fmt_size(sum(o.size for o in active)), dest=dest)
        trash_n = sum(1 for o in active if o.op == OP_TRASH)
        if trash_n:
            text += "<br><br>" + plural(trash_n, "sort.confirm_trash")
        if not confirm(self, tr("sort.confirm_title"), text, tr("sort.confirm_ok")):
            return
        self.control = TaskControl()
        self.progress_title.setText(tr("sort.running"))
        self.progress_sub.setText(tr("sort.running_text"))
        self.progress.attach(self.control)
        self.stack.setCurrentWidget(self.progress_page)
        self.ctx.set_busy(True, tr("busy.sort"))
        cleanup = self.setup.cleanup.isChecked()
        run_task(self.ctx.engine.execute, plan, self.control, "sort", plan.profile_name, cleanup,
                 on_done=self._run_done, on_error=self._run_failed)

    def _run_done(self, result) -> None:
        self.progress.detach()
        self.ctx.set_busy(False)
        self.result.show_result(result, self.plan)
        self.stack.setCurrentWidget(self.result)
        self.ctx.sessionsChanged.emit()
        win = self.window()
        if hasattr(win, "notify") and not win.isActiveWindow():
            win.notify(tr(result.message), tr("sort.notify_done", n=fmt_int(result.files)))

    def _run_failed(self, exc) -> None:
        self.progress.detach()
        self.ctx.set_busy(False)
        self.ctx.sessionsChanged.emit()
        error(self, tr("sort.run_failed"), str(exc), getattr(exc, "traceback_text", None))
        self.stack.setCurrentWidget(self.preview)
