"""Page « Historique » : sessions passées, détails, annulation, reprise, rapports."""

from __future__ import annotations

import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QSplitter,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from ...core import journal as jn
from ...core.models import Cancelled, TaskControl
from ...core.report import export_csv, export_html
from .. import icons, theme
from ..context import AppContext
from ..i18n import fmt_date, fmt_duration, fmt_int, fmt_size, language, tr
from ..widgets import Chip, ProgressPanel, button, confirm, error, hbox, info, label, open_in_explorer, vbox
from ..workers import run_task

SESSION_ICONS = {"sort": "sort", "duplicates": "copy", "watch": "eye", "rename": "edit", "cleanup": "sparkles",
                 "scheduled": "clock", "device": "drive"}
STATUS_LEVEL = {
    "completed": "success", "completed_errors": "warning", "cancelled": "neutral", "interrupted": "error",
    "running": "info", "paused": "info", "undone": "neutral", "partially_undone": "warning",
}


class OpsTableModel(QAbstractTableModel):
    COLS = ("op", "state", "src", "dst", "size", "error")

    def __init__(self):
        super().__init__()
        self.rows: list[jn.OpRow] = []
        self.all: list[jn.OpRow] = []
        self.state_filter = ""

    def set_rows(self, rows) -> None:
        self.all = [r for r in rows if r.op not in ("mkdir", "rmdir")]
        self.apply()

    def apply(self) -> None:
        self.beginResetModel()
        f = self.state_filter
        self.rows = [r for r in self.all if not f or r.state == f]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return tr(f"history.col_{self.COLS[section]}")
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        r = self.rows[index.row()]
        col = self.COLS[index.column()]
        if role == Qt.DisplayRole:
            if col == "op":
                return tr(f"op.{r.op}")
            if col == "state":
                return tr(f"state.{r.state}")
            if col == "src":
                return r.src or ""
            if col == "dst":
                if r.op == "trash":
                    return tr("op.trash_target") if r.extra.get("method") != "quarantine" else r.extra.get("qpath", "")
                return r.dst or ""
            if col == "size":
                return fmt_size(r.size)
            if col == "error":
                if not r.error:
                    return ""
                code, _, detail = r.error.partition("|")
                return tr(f"error.{code}") if f"error.{code}" != tr(f"error.{code}") else r.error
        elif role == Qt.ForegroundRole:
            c = theme.colors()
            if r.state in ("failed", "undo_failed"):
                return QColor(c["danger"])
            if r.state in ("undone", "planned", "skipped"):
                return QColor(c["text3"])
        elif role == Qt.ToolTipRole:
            return "\n".join(x for x in (r.src, r.dst, r.error) if x)
        elif role == Qt.TextAlignmentRole and col == "size":
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class HistoryPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.session: jn.SessionRow | None = None
        self.control: TaskControl | None = None
        outer = vbox(self, (28, 22, 28, 18), 12)
        head = hbox(spacing=10)
        texts = vbox(spacing=2)
        texts.addWidget(label(tr("history.title"), "PageTitle"))
        texts.addWidget(label(tr("history.subtitle"), "PageSubtitle", wrap=True))
        head.addLayout(texts, 1)
        head.addWidget(button(tr("history.refresh"), "refresh", "ghost", on_click=self.refresh), 0, Qt.AlignBottom)
        outer.addLayout(head)

        split = QSplitter(Qt.Vertical)
        self.list = QTreeWidget()
        self.list.setHeaderLabels([tr("history.col_date"), tr("history.col_kind"), tr("history.col_profile"),
                                   tr("history.col_files"), tr("history.col_size"), tr("history.col_status")])
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(True)
        self.list.header().setSectionResizeMode(2, QHeaderView.Stretch)
        for col, w in ((0, 150), (1, 170), (3, 90), (4, 100), (5, 170)):
            self.list.setColumnWidth(col, w)
        self.list.currentItemChanged.connect(self._selected)
        split.addWidget(self.list)

        detail = QWidget()
        dl = vbox(detail, (0, 6, 0, 0), 8)
        dhead = hbox(spacing=10)
        self.d_title = label("", "SectionTitle")
        dhead.addWidget(self.d_title)
        self.d_chip = Chip()
        dhead.addWidget(self.d_chip)
        dhead.addStretch(1)
        self.state_filter = QComboBox()
        for key in ("", "done", "failed", "planned", "undone", "undo_failed"):
            self.state_filter.addItem(tr(f"state.{key}") if key else tr("history.all_states"), key)
        self.state_filter.currentIndexChanged.connect(self._filter)
        dhead.addWidget(self.state_filter)
        dl.addLayout(dhead)
        self.d_summary = label("", "Muted", wrap=True)
        dl.addWidget(self.d_summary)
        self.model = OpsTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, w in enumerate((100, 110, 340, 340, 80)):
            self.table.setColumnWidth(col, w)
        self.table.doubleClicked.connect(self._open_row)
        dl.addWidget(self.table, 1)
        self.progress = ProgressPanel()
        self.progress.hide()
        dl.addWidget(self.progress)
        actions = hbox(spacing=8)
        self.undo_btn = button(tr("history.undo"), "undo", "danger", on_click=self.undo)
        self.resume_btn = button(tr("history.resume"), "play", on_click=lambda: self.resume(False))
        self.retry_btn = button(tr("history.retry"), "refresh", on_click=lambda: self.resume(True))
        self.export_btn = button(tr("history.export"), "download", on_click=self.export)
        self.delete_btn = button(tr("history.delete"), "trash", "ghost", on_click=self.delete)
        for w in (self.undo_btn, self.resume_btn, self.retry_btn, self.export_btn):
            actions.addWidget(w)
        actions.addStretch(1)
        actions.addWidget(self.delete_btn)
        dl.addLayout(actions)
        split.addWidget(detail)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        outer.addWidget(split, 1)
        ctx.sessionsChanged.connect(self.refresh)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def receive(self, payload) -> None:
        if not payload:
            return
        sid = payload.get("session")
        if sid:
            self.refresh(select=sid)
            if payload.get("undo"):
                self.undo()

    def refresh(self, select: int | None = None) -> None:
        current = select or (self.session.id if self.session else None)
        self.list.blockSignals(True)
        self.list.clear()
        target = None
        for s in self.ctx.engine.journal.sessions(1000):
            files = s.summary.get("files", s.summary.get("done", 0))
            it = QTreeWidgetItem([fmt_date(s.created), tr(f"session.kind_{s.kind}"), s.profile or "",
                                  fmt_int(files), fmt_size(s.summary.get("bytes", 0) or self._bytes(s)),
                                  tr(f"status.{s.status}")])
            it.setData(0, Qt.UserRole, s.id)
            it.setIcon(1, icons.icon(SESSION_ICONS.get(s.kind, "sort")))
            c = theme.colors()
            color = {"success": c["success"], "warning": c["warning"], "error": c["danger"]}.get(
                STATUS_LEVEL.get(s.status, ""), c["text2"])
            it.setForeground(5, QColor(color))
            it.setTextAlignment(3, Qt.AlignRight | Qt.AlignVCenter)
            it.setTextAlignment(4, Qt.AlignRight | Qt.AlignVCenter)
            self.list.addTopLevelItem(it)
            if s.id == current:
                target = it
        self.list.blockSignals(False)
        if target is None and self.list.topLevelItemCount():
            target = self.list.topLevelItem(0)
        if target is not None:
            self.list.setCurrentItem(target)
            self._selected(target)
        else:
            self.session = None
            self._show_session()

    @staticmethod
    def _bytes(_s) -> int:
        return 0

    def _selected(self, item, _prev=None) -> None:
        if item is None:
            return
        self.session = self.ctx.engine.journal.session(item.data(0, Qt.UserRole))
        self._show_session()

    def _show_session(self) -> None:
        s = self.session
        busy = self.ctx.busy
        if s is None:
            self.d_title.setText(tr("history.empty"))
            self.d_chip.hide()
            self.d_summary.setText("")
            self.model.set_rows([])
            for b in (self.undo_btn, self.resume_btn, self.retry_btn, self.export_btn, self.delete_btn):
                b.setEnabled(False)
            return
        self.d_chip.show()
        self.d_title.setText(f"{tr(f'session.kind_{s.kind}')} — {fmt_date(s.created)}")
        self.d_chip.setText(tr(f"status.{s.status}"))
        self.d_chip.set_level(STATUS_LEVEL.get(s.status, "neutral"))
        counts = self.ctx.engine.journal.counts(s.id)
        parts = [tr("history.summary", done=fmt_int(s.summary.get("files", counts.get("done", 0))),
                    failed=fmt_int(counts.get("failed", 0)),
                    duration=fmt_duration(s.summary.get("duration", 0)))]
        if s.sources:
            parts.append(tr("history.sources", paths=", ".join(s.sources)))
        if s.destination:
            parts.append(tr("history.destination", path=s.destination))
        self.d_summary.setText("<br>".join(parts))
        self.model.set_rows(self.ctx.engine.journal.ops(s.id))
        undoable = counts.get("done", 0) > 0 and s.status not in ("running", "paused", "undone")
        self.undo_btn.setEnabled(undoable and not busy)
        self.resume_btn.setEnabled(s.status in ("cancelled", "interrupted") and counts.get("planned", 0) > 0
                                   and not busy)
        self.retry_btn.setEnabled(counts.get("failed", 0) > 0 and s.status not in ("undone",) and not busy)
        self.export_btn.setEnabled(True)
        self.delete_btn.setEnabled(s.status not in ("running", "paused") and not busy)

    def _filter(self) -> None:
        self.model.state_filter = self.state_filter.currentData()
        self.model.apply()

    def _open_row(self, index) -> None:
        r = self.model.rows[index.row()] if index.row() < len(self.model.rows) else None
        if r is None:
            return
        for p in (r.dst, r.src):
            if p and os.path.exists(p):
                open_in_explorer(p, select=True)
                return

    # -- actions ---------------------------------------------------------------------
    def _start(self, fn, *args, on_done) -> None:
        if self.ctx.busy:
            error(self, tr("common.busy_title"), tr("common.busy_text", task=self.ctx.busy_label))
            return
        self.control = TaskControl()
        self.progress.show()
        self.progress.attach(self.control)
        self.ctx.set_busy(True, tr("busy.history"))
        self._show_session()

        def done(result):
            self.progress.detach()
            self.progress.hide()
            self.ctx.set_busy(False)
            self.ctx.sessionsChanged.emit()
            on_done(result)

        def failed(exc):
            self.progress.detach()
            self.progress.hide()
            self.ctx.set_busy(False)
            self.ctx.sessionsChanged.emit()
            if str(exc) == "session_in_use":
                error(self, tr("history.failed"), tr("history.in_use"))
            elif not isinstance(exc, Cancelled):
                error(self, tr("history.failed"), str(exc), getattr(exc, "traceback_text", None))

        run_task(fn, *args, self.control, on_done=done, on_error=failed)

    def undo(self) -> None:
        s = self.session
        if s is None:
            return
        n = self.ctx.engine.journal.file_count(s.id)
        if not confirm(self, tr("history.undo_title"), tr("history.undo_text", n=fmt_int(n)), tr("history.undo"),
                       danger=True):
            return

        def fn(control):
            return self.ctx.engine.undo(s.id, control)

        def done(result):
            if result.failed:
                details = "\n".join(f"{p} — {e}" for p, e in result.errors[:200])
                error(self, tr("history.undo_partial"), tr("history.undo_partial_text", n=result.failed), details)
            else:
                info(self, tr("history.undo_done"), tr("history.undo_done_text", n=fmt_int(result.undone)))

        self._start(fn, on_done=done)

    def resume(self, retry_failed: bool) -> None:
        s = self.session
        if s is None:
            return

        def fn(control):
            return self.ctx.engine.resume(s.id, control, retry_failed)

        def done(result):
            info(self, tr(result.message), tr("history.resume_done", n=fmt_int(result.files), failed=result.failed))

        self._start(fn, on_done=done)

    def export(self) -> None:
        s = self.session
        if s is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("history.export"),
                                              os.path.expanduser(f"~/Rangeo-rapport-{s.id}.html"),
                                              "HTML (*.html);;CSV (*.csv)")
        if not path:
            return
        if path.lower().endswith(".csv"):
            export_csv(self.ctx.engine.journal, s.id, path, tr)
        else:
            export_html(self.ctx.engine.journal, s.id, path, tr, language())
        open_in_explorer(path, select=True)

    def delete(self) -> None:
        s = self.session
        if s is None:
            return
        if confirm(self, tr("history.delete"), tr("history.delete_text"), tr("common.delete"), danger=True):
            self.ctx.engine.journal.delete_session(s.id)
            self.session = None
            self.refresh()
