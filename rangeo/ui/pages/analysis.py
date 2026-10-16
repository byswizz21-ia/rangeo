"""Page « Analyse » : carte de l'espace, gros / vieux / temporaires / vides / abîmés, statistiques."""

from __future__ import annotations

import os

from PySide6.QtCore import QRectF, QStorageInfo, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from ...core import diskreport
from ...core import filetypes as ft
from ...core.models import Cancelled, TaskControl
from ...core.protect import Protection
from .. import icons, theme
from ..context import AppContext
from ..i18n import fmt_date, fmt_int, fmt_size, plural, tr
from ..treemap import GROUP_COLORS, Treemap
from ..widgets import (
    Banner,
    Card,
    ProgressPanel,
    ScrollPage,
    StatCard,
    button,
    choose_folder,
    confirm,
    error,
    hbox,
    info,
    label,
    open_in_explorer,
    vbox,
)
from ..workers import run_task


class FileList(QWidget):
    """Liste de fichiers cochables avec action « corbeille »."""

    def __init__(self, page: AnalysisPage, with_reason: bool = False, hint: str = "", dirs: bool = False):
        super().__init__()
        self.page = page
        self.dirs = dirs
        self.files: list = []
        lay = vbox(self, (0, 10, 0, 0), 8)
        self.hint = label(hint, "Muted", wrap=True)
        self.hint.setVisible(bool(hint))
        lay.addWidget(self.hint)
        self.tree = QTreeWidget()
        cols = [tr("dups.col_file"), tr("dups.col_folder")]
        if not dirs:
            cols += [tr("preview.col_size"), tr("dups.col_date")]
        if with_reason:
            cols.append(tr("preview.col_reason"))
        self.tree.setHeaderLabels(cols)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setTextElideMode(Qt.ElideMiddle)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.setColumnWidth(0, 300)
        self.tree.itemChanged.connect(lambda *_: self._count())
        self.tree.itemDoubleClicked.connect(lambda item, _c: open_in_explorer(item.data(0, Qt.UserRole), True))
        lay.addWidget(self.tree, 1)
        row = hbox(spacing=8)
        self.count = label("", "Muted")
        row.addWidget(self.count, 1)
        row.addWidget(button(tr("preview.check_all"), "check", "ghost", on_click=lambda: self._check_all(True)))
        row.addWidget(button(tr("preview.uncheck_all"), "x", "ghost", on_click=lambda: self._check_all(False)))
        row.addWidget(button(tr("preview.show_source"), "folder_open", on_click=self._reveal))
        self.action = button(tr("analysis.remove_empty") if dirs else tr("analysis.trash_selected"), "trash",
                             "danger", on_click=self._apply)
        row.addWidget(self.action)
        lay.addLayout(row)

    def set_files(self, files, reason_fn=None, checked: bool = False) -> None:
        self.files = files
        self.tree.blockSignals(True)
        self.tree.clear()
        prot = Protection()
        for f in files:
            path = f if self.dirs else f.path
            if self.dirs:
                cols = [os.path.basename(path), os.path.dirname(path)]
            else:
                cols = [f.name, os.path.dirname(f.path), fmt_size(f.size), fmt_date(f.mtime, False)]
                if reason_fn:
                    cols.append(reason_fn(f))
            item = QTreeWidgetItem(cols)
            item.setData(0, Qt.UserRole, path)
            item.setData(0, Qt.UserRole + 1, f)
            item.setToolTip(1, os.path.dirname(path))
            if prot.is_protected(path):
                item.setDisabled(True)
                item.setToolTip(0, tr("protect.system_folder"))
            else:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
            ext = "" if self.dirs else f.ext
            item.setIcon(0, icons.icon("folder") if self.dirs else icons.kind_icon(
                ft.group_of(ft.kind_from_ext(ext))))
            if not self.dirs:
                item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        self._count()

    def _checked(self):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if not item.isDisabled() and item.checkState(0) == Qt.Checked:
                out.append(item.data(0, Qt.UserRole + 1))
        return out

    def _count(self) -> None:
        sel = self._checked()
        if self.dirs:
            self.count.setText(plural(len(sel), "analysis.n_dirs"))
        else:
            self.count.setText(tr("preview.selected", n=fmt_int(len(sel)), size=fmt_size(sum(f.size for f in sel))))
        self.action.setEnabled(bool(sel))

    def _check_all(self, on: bool) -> None:
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if not item.isDisabled():
                item.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._count()

    def _reveal(self) -> None:
        item = self.tree.currentItem()
        if item is not None:
            open_in_explorer(item.data(0, Qt.UserRole), True)

    def _apply(self) -> None:
        sel = self._checked()
        if not sel:
            return
        self.page.cleanup(sel, self.dirs, self)

    def remove_items(self, done_paths: set[str]) -> None:
        keys = {os.path.normcase(p) for p in done_paths}
        for i in reversed(range(self.tree.topLevelItemCount())):
            item = self.tree.topLevelItem(i)
            if os.path.normcase(item.data(0, Qt.UserRole)) in keys:
                self.tree.takeTopLevelItem(i)
        self._count()


class BarChart(QWidget):
    """Barres horizontales simples (catégories, extensions, mois)."""

    def __init__(self):
        super().__init__()
        self.rows: list[tuple[str, float, str, str]] = []  # (libellé, valeur, texte, couleur)
        self.setMinimumHeight(60)

    def set_rows(self, rows) -> None:
        self.rows = rows
        self.setMinimumHeight(28 * len(rows) + 8)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = theme.colors()
        if not self.rows:
            return
        top = max(v for _l, v, _t, _c in self.rows) or 1
        label_w, text_w = 170, 150
        bar_w = max(40, self.width() - label_w - text_w - 16)
        for i, (lab, value, text, color) in enumerate(self.rows):
            y = 4 + i * 28
            p.setPen(QColor(c["text"]))
            p.drawText(QRectF(0, y, label_w - 8, 22), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(lab, Qt.ElideRight, label_w - 10))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["surface3"]))
            p.drawRoundedRect(QRectF(label_w, y + 5, bar_w, 12), 6, 6)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(label_w, y + 5, max(4, bar_w * value / top), 12), 6, 6)
            p.setPen(QColor(c["text2"]))
            p.drawText(QRectF(label_w + bar_w + 10, y, text_w, 22), Qt.AlignVCenter | Qt.AlignLeft, text)
        p.end()


class AnalysisPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.report = None
        self.control: TaskControl | None = None
        outer = vbox(self, (28, 22, 28, 18), 12)
        head = hbox(spacing=10)
        texts = vbox(spacing=2)
        texts.addWidget(label(tr("analysis.title"), "PageTitle"))
        texts.addWidget(label(tr("analysis.subtitle"), "PageSubtitle", wrap=True))
        head.addLayout(texts, 1)
        outer.addLayout(head)

        bar = hbox(spacing=8)
        self.target = QComboBox()
        self.target.setMinimumWidth(320)
        self.target.setEditable(False)
        bar.addWidget(self.target, 1)
        bar.addWidget(button(tr("analysis.choose"), "folder", on_click=self._choose))
        bar.addWidget(label(tr("analysis.old_years")))
        self.years = QSpinBox()
        self.years.setRange(1, 30)
        self.years.setSuffix(" " + tr("analysis.years"))
        self.years.setValue(int(ctx.config.get("analysis_old_years", 3)))
        bar.addWidget(self.years)
        self.go = button(tr("analysis.start"), "search", "primary", on_click=self.start)
        bar.addWidget(self.go)
        outer.addLayout(bar)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        empty = ScrollPage(max_width=900)
        empty.body.addWidget(Banner("info", tr("analysis.intro")))
        empty.body.addStretch(1)
        self.stack.addWidget(empty)
        prog = QWidget()
        pl = vbox(prog, (0, 30, 0, 0))
        self.progress = ProgressPanel()
        pl.addWidget(self.progress)
        pl.addStretch(1)
        self.stack.addWidget(prog)

        res = QWidget()
        rl = vbox(res, spacing=10)
        stats = hbox(spacing=12)
        self.s_size = StatCard("layers", "0", tr("analysis.stat_size"))
        self.s_files = StatCard("file", "0", tr("analysis.stat_files"))
        self.s_clean = StatCard("sparkles", "0", tr("analysis.stat_clean"), theme.colors()["success"])
        for w in (self.s_size, self.s_files, self.s_clean):
            stats.addWidget(w)
        rl.addLayout(stats)
        self.tabs = QTabWidget()
        rl.addWidget(self.tabs, 1)
        self.stack.addWidget(res)

        # carte
        tab = QWidget()
        tl = vbox(tab, (0, 10, 0, 0), 8)
        self.crumbs = hbox(spacing=4)
        tl.addLayout(self.crumbs)
        split = QSplitter(Qt.Horizontal)
        self.treemap = Treemap()
        self.treemap.folderOpened.connect(self._open_node)
        self.treemap.revealRequested.connect(lambda p: open_in_explorer(p))
        split.addWidget(self.treemap)
        side = QWidget()
        sl = vbox(side, spacing=6)
        self.children_list = QTreeWidget()
        self.children_list.setHeaderLabels([tr("preview.folder"), tr("preview.col_size"), "%"])
        self.children_list.setRootIsDecorated(False)
        self.children_list.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.children_list.itemDoubleClicked.connect(self._open_child_item)
        sl.addWidget(self.children_list, 1)
        legend = QWidget()
        lg = hbox(legend, spacing=10)
        for g in ("image", "video", "audio", "document", "archive", "software", "game", "code"):
            dot = label("●")
            dot.setStyleSheet(f"color: {GROUP_COLORS[g]}; font-size: 14pt;")
            lg.addWidget(dot)
            lg.addWidget(label(tr(f"group.{g}"), "Faint"))
        lg.addStretch(1)
        sl.addWidget(legend)
        split.addWidget(side)
        split.setSizes([800, 330])
        tl.addWidget(split, 1)
        self.tabs.addTab(tab, icons.icon("pie"), tr("analysis.tab_map"))

        self.big = FileList(self, hint=tr("analysis.big_hint"))
        self.tabs.addTab(self.big, icons.icon("layers"), tr("analysis.tab_big"))
        self.old = FileList(self, hint="")
        self.tabs.addTab(self.old, icons.icon("clock"), tr("analysis.tab_old"))
        self.temp = FileList(self, with_reason=True, hint=tr("analysis.temp_hint"))
        self.tabs.addTab(self.temp, icons.icon("trash"), tr("analysis.tab_temp"))
        self.empty_dirs = FileList(self, hint=tr("analysis.empty_hint"), dirs=True)
        self.tabs.addTab(self.empty_dirs, icons.icon("folder"), tr("analysis.tab_empty"))
        self.installers = FileList(self, with_reason=True, hint=tr("analysis.installers_hint"))
        self.tabs.addTab(self.installers, icons.icon("package"), tr("analysis.tab_installers"))

        corrupt = QWidget()
        cl = vbox(corrupt, (0, 10, 0, 0), 8)
        crow = hbox(spacing=8)
        crow.addWidget(label(tr("analysis.corrupt_hint"), "Muted", wrap=True), 1)
        self.check_btn = button(tr("analysis.check_files"), "shield", on_click=self._check_corrupt)
        crow.addWidget(self.check_btn)
        cl.addLayout(crow)
        self.corrupt = FileList(self, with_reason=True)
        cl.addWidget(self.corrupt, 1)
        self.tabs.addTab(corrupt, icons.icon("alert"), tr("analysis.tab_corrupt"))

        stats_tab = ScrollPage(max_width=None)
        stats_tab.widget().layout().setContentsMargins(0, 10, 0, 0)
        c1 = Card(tr("analysis.by_category"), None, "pie")
        self.cat_chart = BarChart()
        c1.add(self.cat_chart)
        stats_tab.body.addWidget(c1)
        c2 = Card(tr("analysis.by_ext"), None, "file")
        self.ext_chart = BarChart()
        c2.add(self.ext_chart)
        stats_tab.body.addWidget(c2)
        c3 = Card(tr("analysis.history"), tr("analysis.history_text"), "history")
        self.hist_chart = BarChart()
        c3.add(self.hist_chart)
        stats_tab.body.addWidget(c3)
        stats_tab.body.addStretch(1)
        self.tabs.addTab(stats_tab, icons.icon("rules"), tr("analysis.tab_stats"))
        self._fill_targets()

    # -- choix du dossier -------------------------------------------------------------
    def _fill_targets(self, extra: str | None = None) -> None:
        current = extra or self.target.currentData()
        self.target.clear()
        for vol in QStorageInfo.mountedVolumes():
            if vol.isValid() and vol.isReady() and vol.bytesTotal() > 0:
                root = os.path.normpath(vol.rootPath())
                self.target.addItem(icons.icon("drive"), f"{vol.displayName() or root} ({root.rstrip(chr(92))}) — "
                                    f"{tr('home.free', free=fmt_size(vol.bytesAvailable()), total=fmt_size(vol.bytesTotal()))}",
                                    root)
        if extra and self.target.findData(extra) < 0:
            self.target.insertItem(0, icons.icon("folder"), extra, extra)
        idx = self.target.findData(current) if current else 0
        self.target.setCurrentIndex(max(0, idx))

    def _choose(self) -> None:
        path = choose_folder(self, tr("analysis.choose"))
        if path:
            self._fill_targets(path)

    def receive(self, payload) -> None:
        if payload and payload.get("path"):
            self._fill_targets(payload["path"])

    # -- analyse ------------------------------------------------------------------------
    def start(self) -> None:
        path = self.target.currentData()
        if not path or self.ctx.busy:
            if self.ctx.busy:
                error(self, tr("common.busy_title"), tr("common.busy_text", task=self.ctx.busy_label))
            return
        self.ctx.config.set("analysis_old_years", self.years.value())
        self.control = TaskControl()
        self.progress.attach(self.control)
        self.stack.setCurrentIndex(1)
        self.ctx.set_busy(True, tr("busy.analysis"))
        run_task(diskreport.analyze_disk, path, self.control, self.years.value(), on_done=self._done,
                 on_error=self._failed)

    def _failed(self, exc) -> None:
        self.progress.detach()
        self.ctx.set_busy(False)
        self.stack.setCurrentIndex(0 if self.report is None else 2)
        if not isinstance(exc, Cancelled):
            error(self, tr("analysis.failed"), str(exc), getattr(exc, "traceback_text", None))

    def _done(self, report) -> None:
        self.progress.detach()
        self.ctx.set_busy(False)
        self.report = report
        self.s_size.set_value(fmt_size(report.total_size))
        self.s_files.set_value(fmt_int(report.total_files))
        clean = sum(f.size for f in report.temp) + sum(f.size for f in report.installers)
        self.s_clean.set_value(fmt_size(clean))
        self._open_node(report.root)
        self.big.set_files(report.biggest)
        self.old.set_files(report.old)
        self.tabs.setTabText(2, tr("analysis.tab_old") + f" ({fmt_int(len(report.old))})")
        self.old.hint.setText(tr("analysis.old_hint", n=report.old_years))
        self.old.hint.setVisible(True)
        self.temp.set_files(report.temp, lambda f: tr(f"clean.{f.reason}"), checked=True)
        self.tabs.setTabText(3, tr("analysis.tab_temp") + f" ({fmt_int(len(report.temp))})")
        self.empty_dirs.set_files(report.empty_dirs, checked=True)
        self.tabs.setTabText(4, tr("analysis.tab_empty") + f" ({fmt_int(len(report.empty_dirs))})")
        self.installers.set_files(report.installers, lambda f: tr("analysis.obsolete", name=f.reason), checked=True)
        self.tabs.setTabText(5, tr("analysis.tab_installers") + f" ({fmt_int(len(report.installers))})")
        self.corrupt.set_files([])
        self._fill_stats()
        self.stack.setCurrentIndex(2)

    def _open_node(self, node) -> None:
        self.treemap.set_node(node)
        while self.crumbs.count():
            w = self.crumbs.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        chain = []
        n = node
        while n is not None:
            chain.append(n)
            n = n.parent
        for i, n in enumerate(reversed(chain)):
            if i:
                self.crumbs.addWidget(label("›", "Faint"))
            b = button(n.name if n.parent else n.path, kind="link" if n is not node else "ghost")
            b.clicked.connect(lambda _=False, target=n: self._open_node(target))
            self.crumbs.addWidget(b)
        self.crumbs.addStretch(1)
        self.children_list.clear()
        total = node.size or 1
        rows = sorted(node.children, key=lambda c: -c.size)
        for c in rows[:500]:
            item = QTreeWidgetItem([c.name, fmt_size(c.size), f"{c.size * 100 / total:.1f}"])
            item.setIcon(0, icons.icon("folder", GROUP_COLORS.get(c.dominant_group())))
            item.setData(0, Qt.UserRole, c)
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            self.children_list.addTopLevelItem(item)
        if node.own_files:
            item = QTreeWidgetItem([tr("analysis.loose_files", n=node.own_files), fmt_size(node.own_size),
                                    f"{node.own_size * 100 / total:.1f}"])
            item.setIcon(0, icons.icon("file"))
            self.children_list.addTopLevelItem(item)

    def _open_child_item(self, item, _col) -> None:
        node = item.data(0, Qt.UserRole)
        if node is not None:
            self._open_node(node)

    def _fill_stats(self) -> None:
        r = self.report
        rows = sorted(r.by_group.items(), key=lambda kv: -kv[1][1])
        self.cat_chart.set_rows([(tr(f"group.{g}"), size, f"{fmt_size(size)} · {fmt_int(n)}",
                                  GROUP_COLORS.get(g, "#A5ABB8")) for g, (n, size) in rows if size or n])
        self.ext_chart.set_rows([(f".{e}" if e != "—" else tr("analysis.no_ext"), size,
                                  f"{fmt_size(size)} · {fmt_int(n)}",
                                  GROUP_COLORS.get(ft.group_of(ft.kind_from_ext(e)), "#A5ABB8"))
                                 for e, (n, size) in list(r.by_ext.items())[:15]])
        months = self.ctx.engine.journal.monthly(12)
        accent = theme.colors()["accent"]
        self.hist_chart.set_rows([(m, b, f"{fmt_size(b)} · {fmt_int(f)} · {tr('analysis.freed')} {fmt_size(fr)}",
                                   accent) for m, f, b, fr in months])

    # -- fichiers abîmés -------------------------------------------------------------
    def _check_corrupt(self) -> None:
        if self.report is None or self.ctx.busy:
            return
        self.control = TaskControl()
        self.progress.attach(self.control)
        self.stack.setCurrentIndex(1)
        self.ctx.set_busy(True, tr("busy.analysis"))

        def done(bad):
            self.progress.detach()
            self.ctx.set_busy(False)
            self.stack.setCurrentIndex(2)
            self.corrupt.set_files(bad, lambda f: tr(f"corrupt.{f.reason}"))
            self.tabs.setTabText(6, tr("analysis.tab_corrupt") + f" ({fmt_int(len(bad))})")
            if not bad:
                info(self, tr("analysis.tab_corrupt"), tr("analysis.no_corrupt"))

        run_task(diskreport.find_corrupt, self.report.root.path, self.control, on_done=done, on_error=self._failed)

    # -- nettoyage --------------------------------------------------------------------
    def cleanup(self, items, dirs: bool, source: FileList) -> None:
        if self.ctx.busy:
            return
        if dirs:
            selected = set(os.path.normcase(p) for p in items)
            all_dirs = [d for d in self.report.all_empty_dirs
                        if any(os.path.normcase(d) == s or os.path.normcase(d).startswith(s + "\\") for s in selected)]
            text = tr("analysis.confirm_dirs", n=fmt_int(len(items)))
            plan = diskreport.cleanup_plan([], all_dirs, self.report.root.path)
        else:
            text = tr("analysis.confirm_trash", n=fmt_int(len(items)), size=fmt_size(sum(f.size for f in items)))
            plan = diskreport.cleanup_plan(items, [], self.report.root.path)
        if not plan.ops or not confirm(self, tr("analysis.confirm_title"), text, tr("common.ok")):
            return
        self.control = TaskControl()
        self.progress.attach(self.control)
        self.stack.setCurrentIndex(1)
        self.ctx.set_busy(True, tr("busy.cleanup"))

        def done(result):
            self.progress.detach()
            self.ctx.set_busy(False)
            self.ctx.sessionsChanged.emit()
            self.stack.setCurrentIndex(2)
            ok_paths = {r.src for r in self.ctx.engine.journal.ops(result.session_id, ("done",)) if r.src}
            source.remove_items(ok_paths)
            info(self, tr("analysis.cleaned"), tr("analysis.cleaned_text", n=fmt_int(result.files or result.done),
                                                   size=fmt_size(result.bytes)))

        def failed(exc):
            self._failed(exc)
            self.ctx.sessionsChanged.emit()

        run_task(self.ctx.engine.execute, plan, self.control, "cleanup", tr("analysis.title"), False,
                 on_done=done, on_error=failed)
