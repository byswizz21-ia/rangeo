"""Page « Renommer » : renommage en masse avec aperçu avant / après."""

from __future__ import annotations

import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QSpinBox,
    QSplitter,
    QTableView,
    QToolButton,
    QWidget,
)

from ...core import filetypes as ft
from ...core import rename as rn
from ...core import rules as R
from ...core.engine import SortRequest
from ...core.models import Cancelled, TaskControl
from .. import icons, theme
from ..context import AppContext
from ..i18n import fmt_int, kind_label, language, plural, tr
from ..widgets import (
    Card,
    FolderList,
    ProgressPanel,
    ScrollPage,
    button,
    confirm,
    error,
    hbox,
    info,
    label,
    vbox,
)
from ..workers import run_task

PRESETS = {
    "keep": "",
    "datetime": "{annee}-{mois}-{jour} {heure}h{minute}",
    "date_name": "{annee}-{mois}-{jour} {nom}",
    "date_place": "{annee}-{mois}-{jour} - {ville}",
    "music": "{piste} - {artiste} - {titre}",
    "episode": "{serie} S{saison}E{episode}",
    "custom": None,
}
PRESETS_EN = {
    "keep": "",
    "datetime": "{year}-{month}-{day} {hour}h{minute}",
    "date_name": "{year}-{month}-{day} {name}",
    "date_place": "{year}-{month}-{day} - {city}",
    "music": "{track} - {artist} - {title}",
    "episode": "{series} S{season}E{episode}",
    "custom": None,
}
GROUP_FILTERS = ("image", "video", "audio", "document", "archive", "other")


def _compact(combo: QComboBox, chars: int = 14) -> QComboBox:
    """Liste déroulante qui ne s'élargit pas selon son option la plus longue."""
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(chars)
    return combo


class RenameModel(QAbstractTableModel):
    COLS = ("old", "new", "folder", "status")

    def __init__(self):
        super().__init__()
        self.items: list[rn.RenameItem] = []
        self.rows: list[int] = []
        self.only_changes = True

    def set_items(self, items) -> None:
        self.beginResetModel()
        self.items = items
        self.rows = [i for i, it in enumerate(items) if it.changed or not self.only_changes]
        self.endResetModel()

    def set_only_changes(self, on: bool) -> None:
        self.only_changes = on
        self.set_items(self.items)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return tr(f"rename.col_{self.COLS[section]}")
        return None

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        item = self.items[self.rows[index.row()]]
        if index.column() == 0 and item.changed:
            base |= Qt.ItemIsUserCheckable
        return base

    def data(self, index, role=Qt.DisplayRole):
        item = self.items[self.rows[index.row()]]
        col = self.COLS[index.column()]
        if role == Qt.DisplayRole:
            if col == "old":
                return item.entry.name
            if col == "new":
                return item.new_name
            if col == "folder":
                return item.entry.dir
            return tr(f"rename.status_{item.status}")
        if role == Qt.CheckStateRole and col == "old" and item.changed:
            return Qt.Checked if item.enabled else Qt.Unchecked
        if role == Qt.DecorationRole and col == "old":
            return icons.kind_icon(ft.group_of(item.entry.kind))
        if role == Qt.ForegroundRole:
            c = theme.colors()
            if not item.changed or not item.enabled:
                return QColor(c["text3"])
            if col == "new":
                return QColor(c["accent"])
            if col == "status" and item.status in ("renamed_conflict", "missing_info"):
                return QColor(c["warning"])
        if role == Qt.ToolTipRole:
            return os.path.join(item.entry.dir, item.new_name)
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and index.column() == 0:
            self.items[self.rows[index.row()]].enabled = Qt.CheckState(value) == Qt.Checked
            self.dataChanged.emit(index, index.siblingAtColumn(len(self.COLS) - 1))
            return True
        return False


class RenamePage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.entries = []
        self.control: TaskControl | None = None
        self._seq = 0
        outer = vbox(self, (28, 22, 28, 18), 12)
        head = hbox(spacing=10)
        texts = vbox(spacing=2)
        texts.addWidget(label(tr("rename.title"), "PageTitle"))
        texts.addWidget(label(tr("rename.subtitle"), "PageSubtitle", wrap=True))
        head.addLayout(texts, 1)
        self.apply_btn = button(tr("rename.apply"), "check", "primary", big=True, on_click=self.apply)
        head.addWidget(self.apply_btn, 0, Qt.AlignBottom)
        outer.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        outer.addWidget(split, 1)
        left = ScrollPage(max_width=None)
        left.widget().layout().setContentsMargins(0, 0, 8, 0)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left.setMinimumWidth(480)
        b = left.body
        b.setSpacing(12)

        files = Card(tr("rename.files"), None, "folder")
        self.folders = FolderList(tr("sort.add_folder"), tr("sort.no_source"), tr("sort.choose_source"),
                                  max_height=90)
        files.add(self.folders)
        self.recursive = QCheckBox(tr("sort.recursive"))
        self.recursive.setChecked(True)
        files.add(self.recursive)
        groups = QGridLayout()
        groups.setHorizontalSpacing(12)
        self.group_checks = {}
        for i, g in enumerate(GROUP_FILTERS):
            cb = QCheckBox(tr(f"group.{g}") if g != "other" else tr("rename.other"))
            cb.setChecked(True)
            cb.toggled.connect(self._schedule)
            self.group_checks[g] = cb
            groups.addWidget(cb, i // 3, i % 3)
        files.add(groups)
        files.add(button(tr("rename.load"), "refresh", on_click=self.load))
        b.addWidget(files)

        name = Card(tr("rename.new_name"), None, "edit")
        self.preset = _compact(QComboBox(), 24)
        for key in PRESETS:
            self.preset.addItem(tr(f"rename.preset_{key}"), key)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        name.add(self.preset)
        trow = hbox(spacing=6)
        self.template = QLineEdit()
        self.template.setPlaceholderText(tr("rename.template_placeholder"))
        self.template.textEdited.connect(self._template_edited)
        trow.addWidget(self.template, 1)
        trow.addWidget(self._var_button())
        name.add(trow)
        name.add(label(tr("rename.template_help"), "Faint", wrap=True))
        b.addWidget(name)

        clean = Card(tr("rename.cleaning"), None, "sparkles")
        self.clean_copies = QCheckBox(tr("rename.clean_copies"))
        self.clean_copies.setChecked(True)
        clean.add(self.clean_copies)
        self.separators = self._combo(("keep", "spaces", "dashes", "underscores"), "rename.sep_")
        clean.add(self._row(tr("rename.separators"), self.separators))
        self.case = self._combo(("keep", "lower", "upper", "title", "sentence"), "rename.case_")
        clean.add(self._row(tr("rename.case"), self.case))
        self.accents = QCheckBox(tr("rename.remove_accents"))
        clean.add(self.accents)
        frow = hbox(spacing=6)
        self.find = QLineEdit()
        self.find.setPlaceholderText(tr("rename.find"))
        self.replace = QLineEdit()
        self.replace.setPlaceholderText(tr("rename.replace"))
        self.regex = QCheckBox(tr("rename.regex"))
        frow.addWidget(self.find, 1)
        frow.addWidget(self.replace, 1)
        clean.add(frow)
        clean.add(self.regex)
        prow = hbox(spacing=6)
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText(tr("rename.prefix"))
        self.suffix = QLineEdit()
        self.suffix.setPlaceholderText(tr("rename.suffix"))
        prow.addWidget(self.prefix, 1)
        prow.addWidget(self.suffix, 1)
        clean.add(prow)
        b.addWidget(clean)

        num = Card(tr("rename.numbering"), None, "rules")
        self.numbering = self._combo(("none", "prefix", "suffix"), "rename.num_")
        num.add(self._row(tr("rename.numbering_where"), self.numbering))
        nrow = hbox(spacing=6)
        self.start = QSpinBox()
        self.start.setRange(0, 999999)
        self.start.setValue(1)
        self.padding = QSpinBox()
        self.padding.setRange(1, 6)
        self.padding.setValue(3)
        self.num_sep = QLineEdit(" - ")
        self.num_sep.setMaximumWidth(60)
        nrow.addWidget(label(tr("rename.start")))
        nrow.addWidget(self.start)
        nrow.addWidget(label(tr("rename.digits")))
        nrow.addWidget(self.padding)
        nrow.addWidget(label(tr("rename.num_sep")))
        nrow.addWidget(self.num_sep)
        nrow.addStretch(1)
        num.add(nrow)
        self.sort_by = self._combo(("name", "date", "size"), "rename.sort_")
        num.add(self._row(tr("rename.order"), self.sort_by))
        b.addWidget(num)

        ext = Card(tr("rename.extension"), None, "file")
        self.ext_case = self._combo(("lower", "keep", "upper"), "rename.ext_")
        ext.add(self._row(tr("rename.ext_case"), self.ext_case))
        self.fix_ext = QCheckBox(tr("settings.fix_ext"))
        ext.add(self.fix_ext)
        b.addWidget(ext)
        b.addStretch(1)
        split.addWidget(left)

        right = QWidget()
        rl = vbox(right, (8, 0, 0, 0), 8)
        top = hbox(spacing=8)
        self.summary = label(tr("rename.empty"), "Muted", wrap=True)
        top.addWidget(self.summary, 1)
        self.only_changes = QCheckBox(tr("rename.only_changes"))
        self.only_changes.setChecked(True)
        self.only_changes.toggled.connect(lambda on: self.model.set_only_changes(on))
        top.addWidget(self.only_changes)
        rl.addLayout(top)
        self.model = RenameModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, w in enumerate((260, 280, 260)):
            self.table.setColumnWidth(col, w)
        rl.addWidget(self.table, 1)
        self.progress = ProgressPanel()
        self.progress.hide()
        rl.addWidget(self.progress)
        split.addWidget(right)
        split.setSizes([500, 820])

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh_preview)
        for w in (self.clean_copies, self.accents, self.regex, self.fix_ext):
            w.toggled.connect(self._schedule)
        for w in (self.template, self.find, self.replace, self.prefix, self.suffix, self.num_sep):
            w.textChanged.connect(self._schedule)
        for w in (self.separators, self.case, self.numbering, self.sort_by, self.ext_case):
            w.currentIndexChanged.connect(self._schedule)
        for w in (self.start, self.padding):
            w.valueChanged.connect(self._schedule)
        self.folders.changed.connect(self.load)
        self.recursive.toggled.connect(self.load)
        self.apply_btn.setEnabled(False)

    # -- petits widgets ------------------------------------------------------------
    def _combo(self, keys, prefix: str) -> QComboBox:
        combo = _compact(QComboBox())
        for k in keys:
            combo.addItem(tr(prefix + k), k)
        return combo

    @staticmethod
    def _row(text: str, widget) -> QWidget:
        w = QWidget()
        lay = hbox(w, spacing=8)
        lay.addWidget(label(text, wrap=True), 1)
        widget.setMinimumWidth(150)
        lay.addWidget(widget)
        return w

    def _var_button(self) -> QToolButton:
        btn = QToolButton()
        btn.setText(tr("rules.variables"))
        btn.setIcon(icons.icon("sparkles"))
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(btn)
        for v in (R.VARIABLE_LIST if language() == "fr" else R.VARIABLE_LIST_EN):
            act = menu.addAction(f"{{{v}}}  —  {tr('var.' + R.VARIABLES[v])}")
            act.triggered.connect(lambda _=False, v=v: self._insert_var(v))
        btn.setMenu(menu)
        return btn

    def _insert_var(self, v: str) -> None:
        self.template.insert("{" + v + "}")
        self.preset.setCurrentIndex(self.preset.findData("custom"))

    def _preset_changed(self) -> None:
        key = self.preset.currentData()
        presets = PRESETS if language() == "fr" else PRESETS_EN
        if presets.get(key) is not None:
            self.template.blockSignals(True)
            self.template.setText(presets[key])
            self.template.blockSignals(False)
        self._schedule()

    def _template_edited(self, _text) -> None:
        idx = self.preset.findData("custom")
        if self.preset.currentIndex() != idx:
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(idx)
            self.preset.blockSignals(False)

    def receive(self, payload) -> None:
        if payload and payload.get("sources"):
            self.folders.set_paths(payload["sources"])
            self.load()

    # -- chargement des fichiers ----------------------------------------------------
    def load(self) -> None:
        folders = self.folders.paths()
        if not folders:
            self.entries = []
            self.refresh_preview()
            return
        if self.ctx.busy:
            self._reload_pending = True  # rechargé dès que l'opération en cours se termine
            return
        self._reload_pending = False
        req = SortRequest(sources=folders, profile=self.ctx.config.profile(None), in_place=True,
                          recursive=self.recursive.isChecked())
        problems = self.ctx.engine.check_request(req)
        if problems:
            error(self, tr("sort.cannot_start"), "<br>".join(f"• {tr(k)} — {p}" for k, p in problems))
            return
        self.control = TaskControl()
        self.progress.show()
        self.progress.attach(self.control, allow_pause=False)
        self.ctx.set_busy(True, tr("busy.rename"))
        run_task(self.ctx.engine.scan_and_classify, req, self.control, on_done=self._loaded, on_error=self._failed)

    def _loaded(self, scan) -> None:
        self.progress.detach()
        self.progress.hide()
        self.ctx.set_busy(False)
        self.entries = [e for e in scan.entries if not e.is_bundle]
        self.refresh_preview()
        if getattr(self, "_reload_pending", False):
            self.load()

    def _failed(self, exc) -> None:
        self.progress.detach()
        self.progress.hide()
        self.ctx.set_busy(False)
        if not isinstance(exc, Cancelled):
            error(self, tr("rename.failed"), str(exc), getattr(exc, "traceback_text", None))

    # -- aperçu ------------------------------------------------------------------
    def _schedule(self, *_):
        self._timer.start()

    def options(self) -> rn.RenameOptions:
        groups = [g for g, cb in self.group_checks.items() if cb.isChecked()]
        selected = []
        for g in groups:
            if g == "other":
                selected += ["software", "code", "font", "model3d", "shortcut", "game", "technical", "unknown"]
            else:
                selected.append(g)
        return rn.RenameOptions(
            template=self.template.text(),
            clean_copies=self.clean_copies.isChecked(),
            separators=self.separators.currentData(),
            case=self.case.currentData(),
            remove_accents=self.accents.isChecked(),
            find=self.find.text(),
            replace=self.replace.text(),
            regex=self.regex.isChecked(),
            prefix=self.prefix.text(),
            suffix=self.suffix.text(),
            numbering=self.numbering.currentData(),
            start=self.start.value(),
            padding=self.padding.value(),
            num_sep=self.num_sep.text(),
            ext_case=self.ext_case.currentData(),
            fix_ext=self.fix_ext.isChecked(),
            sort_by=self.sort_by.currentData(),
            groups=selected if len(groups) < len(GROUP_FILTERS) else [],
        )

    def refresh_preview(self) -> None:
        if not self.entries:
            self.model.set_items([])
            self.summary.setText(tr("rename.empty"))
            self.apply_btn.setEnabled(False)
            return
        items = rn.preview(self.entries, self.options(), language(), kind_label)
        self.model.set_items(items)
        changed = [i for i in items if i.changed]
        conflicts = sum(1 for i in changed if i.status == "renamed_conflict")
        missing = sum(1 for i in items if i.status == "missing_info")
        parts = [plural(len(changed), "rename.n_changed"), tr("rename.n_total", n=fmt_int(len(items)))]
        if conflicts:
            parts.append(plural(conflicts, "rename.n_conflicts"))
        if missing:
            parts.append(plural(missing, "rename.n_missing"))
        self.summary.setText(" · ".join(parts))
        self.apply_btn.setEnabled(bool(changed))

    # -- application ---------------------------------------------------------------
    def apply(self) -> None:
        if self.ctx.busy:
            return
        plan = rn.to_plan(self.model.items)
        if not plan.ops:
            return
        if not confirm(self, tr("rename.confirm_title"), tr("rename.confirm_text", n=fmt_int(len(plan.ops))),
                       tr("rename.apply")):
            return
        self.control = TaskControl()
        self.progress.show()
        self.progress.attach(self.control)
        self.ctx.set_busy(True, tr("busy.rename"))

        def done(result):
            self.progress.detach()
            self.progress.hide()
            self.ctx.set_busy(False)
            self.ctx.sessionsChanged.emit()
            text = tr("rename.done_text", n=fmt_int(result.files))
            if result.failed:
                text += "<br>" + plural(result.failed, "rename.n_failed")
            info(self, tr("rename.done"), text)
            self.load()

        def failed(exc):
            self._failed(exc)
            self.ctx.sessionsChanged.emit()

        run_task(self.ctx.engine.execute, plan, self.control, "rename", tr("rename.title"), False,
                 on_done=done, on_error=failed)
