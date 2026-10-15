"""Modèle de tableau pour l'aperçu du plan (supporte des centaines de milliers de lignes)."""

from __future__ import annotations

import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor

from ..core import filetypes as ft
from ..core.fsutil import nkey
from ..core.models import OP_COPY, OP_HARDLINK, OP_MOVE, OP_TRASH, PlannedOp
from . import icons, theme
from .i18n import fmt_date, fmt_size, kind_label, tr

PROBLEM_NOTES = {
    "plan.target_in_moving_folder", "plan.protected_target", "plan.into_itself", "plan.path_too_long",
    "plan.target_is_file", "plan.contains_destination",
}
INFO_NOTES = {"plan.renamed_conflict", "plan.identical_to_duplicates", "plan.identical_trashed",
              "plan.replaces_existing", "plan.conflict_loser_trashed", "plan.companion"}


def op_root(op: PlannedOp, dest_root: str) -> str:
    if dest_root:
        return dest_root
    return op.entry.root if op.entry is not None else ""


def relative_target(op: PlannedOp, dest_root: str) -> str:
    if op.op == OP_TRASH:
        return tr("op.trash_target")
    if op.op == OP_HARDLINK:
        return tr("op.hardlink_target", name=os.path.basename(op.keep_ref or ""))
    if not op.dst:
        return ""
    root = op_root(op, dest_root)
    folder = os.path.dirname(op.dst)
    try:
        rel = os.path.relpath(folder, root) if root else folder
    except ValueError:
        rel = folder
    if rel == ".":
        rel = ""
    if not dest_root and root:  # rangement sur place : on préfixe par le nom du dossier source
        rel = os.path.join(os.path.basename(root.rstrip("\\")) or root, rel)
    return rel


class OpsModel(QAbstractTableModel):
    COLS = ("name", "target", "kind", "size", "note", "rule")
    checkedChanged = Signal()

    def __init__(self, show_rule: bool = True):
        super().__init__()
        self.ops: list[PlannedOp] = []
        self.rows: list[int] = []
        self.dest_root = ""
        self.text = ""
        self.group = ""
        self.mode = "all"
        self.folder: str | None = None  # clé du dossier cible sélectionné dans l'arbre
        self.show_rule = show_rule
        self._sort = (None, Qt.AscendingOrder)

    # -- données -----------------------------------------------------------------
    def set_ops(self, ops: list[PlannedOp], dest_root: str) -> None:
        self.ops = ops
        self.dest_root = dest_root
        self.apply_filter()

    def set_filters(self, text: str | None = None, group: str | None = None, mode: str | None = None,
                    folder: str | None | bool = False) -> None:
        if text is not None:
            self.text = text.lower().strip()
        if group is not None:
            self.group = group
        if mode is not None:
            self.mode = mode
        if folder is not False:
            self.folder = folder
        self.apply_filter()

    def _match(self, op: PlannedOp) -> bool:
        if self.mode == "active" and not op.enabled:
            return False
        if self.mode == "problems" and not (op.note in PROBLEM_NOTES or not op.enabled):
            return False
        if self.mode == "notes" and not op.note:
            return False
        if self.group and (op.entry is None or ft.group_of(op.entry.kind) != self.group):
            return False
        if self.folder is not None:
            if not op.dst:
                return False
            if not nkey(os.path.dirname(op.dst) + "\\").startswith(self.folder):
                return False
        if self.text:
            hay = (op.entry.name if op.entry else os.path.basename(op.src)).lower()
            if self.text not in hay and self.text not in (op.dst or "").lower():
                return False
        return True

    def apply_filter(self) -> None:
        self.beginResetModel()
        self.rows = [i for i, op in enumerate(self.ops) if self._match(op)]
        col, order = self._sort
        if col is not None:
            self._sort_rows(col, order)
        self.endResetModel()

    def op_at(self, row: int) -> PlannedOp | None:
        if 0 <= row < len(self.rows):
            return self.ops[self.rows[row]]
        return None

    # -- Qt ---------------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return tr(f"preview.col_{self.COLS[section]}")
        return None

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 0:
            op = self.op_at(index.row())
            if op is not None and op.note not in PROBLEM_NOTES:
                base |= Qt.ItemIsUserCheckable
        return base

    def data(self, index, role=Qt.DisplayRole):
        op = self.op_at(index.row())
        if op is None:
            return None
        col = self.COLS[index.column()]
        entry = op.entry
        if role == Qt.DisplayRole:
            if col == "name":
                name = entry.name if entry else os.path.basename(op.src)
                if op.dst and op.op in (OP_MOVE, OP_COPY) and os.path.basename(op.dst) != name:
                    name = f"{name}  →  {os.path.basename(op.dst)}"
                return name
            if col == "kind":
                return kind_label(entry.kind) if entry else ""
            if col == "size":
                return fmt_size(op.size)
            if col == "target":
                return relative_target(op, self.dest_root)
            if col == "rule":
                return op.rule
            if col == "note":
                return tr(op.note) if op.note else ""
        elif role == Qt.CheckStateRole and col == "name":
            return Qt.Checked if op.enabled else Qt.Unchecked
        elif role == Qt.DecorationRole and col == "name":
            if entry is not None:
                group = "bundle" if entry.is_bundle else ft.group_of(entry.kind)
                return icons.kind_icon(group)
        elif role == Qt.ForegroundRole:
            c = theme.colors()
            if op.note in PROBLEM_NOTES:
                return QColor(c["danger"])
            if not op.enabled:
                return QColor(c["text3"])
            if col == "note" and op.note in INFO_NOTES:
                return QColor(c["warning"] if op.note != "plan.companion" else c["text2"])
            if col in ("kind", "size", "rule"):
                return QColor(c["text2"])
        elif role == Qt.ToolTipRole:
            lines = [op.src]
            if op.dst:
                lines.append("→ " + op.dst)
            if entry is not None and entry.date:
                lines.append(tr("preview.date_tip", date=fmt_date(entry.date),
                                source=tr(f"date_source.{entry.date_source}")))
            if op.note:
                lines.append(tr(op.note))
            return "\n".join(lines)
        elif role == Qt.TextAlignmentRole and col == "size":
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        if role == Qt.CheckStateRole and index.column() == 0:
            op = self.op_at(index.row())
            if op is None or op.note in PROBLEM_NOTES:
                return False
            op.enabled = Qt.CheckState(value) == Qt.Checked
            self.dataChanged.emit(index, index.siblingAtColumn(len(self.COLS) - 1))
            self.checkedChanged.emit()
            return True
        return False

    def set_all(self, enabled: bool) -> None:
        for i in self.rows:
            op = self.ops[i]
            if op.note not in PROBLEM_NOTES:
                op.enabled = enabled
        self.dataChanged.emit(self.index(0, 0), self.index(max(0, len(self.rows) - 1), len(self.COLS) - 1))
        self.checkedChanged.emit()

    def _sort_rows(self, column: int, order) -> None:
        col = self.COLS[column]
        ops = self.ops

        def key(i):
            op = ops[i]
            if col == "name":
                return (op.entry.name if op.entry else op.src).lower()
            if col == "kind":
                return op.entry.kind if op.entry else ""
            if col == "size":
                return op.size
            if col == "target":
                return (op.dst or "").lower()
            if col == "rule":
                return op.rule.lower()
            return op.note

        self.rows.sort(key=key, reverse=order == Qt.DescendingOrder)

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        self._sort = (column, order)
        self.layoutAboutToBeChanged.emit()
        self._sort_rows(column, order)
        self.layoutChanged.emit()
