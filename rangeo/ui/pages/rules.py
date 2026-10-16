"""Page « Règles » : profils, éditeur visuel de règles, arborescence, options."""

from __future__ import annotations

import copy
import datetime as dt
import os

from PySide6.QtCore import QDate, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSpinBox,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from ...core import filetypes as ft
from ...core import rules as R
from ...core.classify import ClassifyOptions, apply_inspection, compute_date, inspect_file, refine_kind
from ...core.defaults import default_rules
from ...core.fsutil import creation_time, split_ext
from ...core.models import FileEntry
from .. import icons, theme
from ..context import AppContext
from ..i18n import fmt_date, kind_label, language, tr
from ..motion import TabWidget
from ..widgets import Banner, Card, ScrollPage, button, confirm, error, hbox, info, label, vbox

ACTION_ICONS = {"move": "arrow_right", "copy": "copy", "ignore": "lock", "trash": "trash"}
ENUM_VALUES = {
    "kind": [k for k in ft.KINDS],
    "group": ["image", "video", "audio", "document", "archive", "software", "game", "code", "font", "model3d",
              "shortcut", "technical", "unknown", "bundle"],
}


def _ts(y, m, d, h=12):
    return dt.datetime(y, m, d, h).timestamp()


def sample_entry(rule: dict) -> FileEntry:
    """Fichier d'exemple pour l'aperçu en direct du modèle de chemin."""
    kind = None
    for c in rule.get("conditions") or []:
        if c.get("field") == "kind" and c.get("op") in ("is", "in_list"):
            kind = str(c.get("value", "")).split(",")[0].strip()
            break
    base = dict(dir=r"C:\Users\Moi\Téléchargements", size=2_400_000, mtime=_ts(2024, 3, 12), ctime=_ts(2024, 3, 12),
                root=r"C:\Users\Moi\Téléchargements", date=_ts(2024, 3, 12))
    samples = {
        "photo": dict(name="IMG_20240312_143200.jpg", ext="jpg", meta={"camera": "Canon EOS 80D"}),
        "screenshot": dict(name="Capture d'écran 2024-03-12 143200.png", ext="png"),
        "music": dict(name="track01.mp3", ext="mp3", meta={"artist": "Daft Punk", "album": "Discovery",
                                                           "title": "One More Time", "track": 1, "year": 2001}),
        "episode": dict(name="Breaking.Bad.S01E02.720p.mkv", ext="mkv",
                        meta={"series": "Breaking Bad", "season": 1, "episode": 2}),
        "movie": dict(name="Inception.2010.1080p.BluRay.mkv", ext="mkv",
                      meta={"title": "Inception", "release_year": 2010}),
        "home_video": dict(name="VID_20240312_143200.mp4", ext="mp4"),
        "code_project": dict(name="mon-projet", ext="", is_bundle=True),
        "software": dict(name="MonJeu", ext="", is_bundle=True),
        "installer": dict(name="setup_vlc.exe", ext="exe"),
    }
    data = samples.get(kind or "", dict(name="Facture EDF.pdf", ext="pdf"))
    kind = kind if kind in ft.KINDS else ("pdf" if data["ext"] == "pdf" else ft.kind_from_ext(data["ext"]))
    entry = FileEntry(kind=kind, **base, **data)
    return entry


# ===========================================================================
# Une condition
# ===========================================================================
class ConditionRow(QWidget):
    changed = Signal()
    removed = Signal(object)

    def __init__(self, cond: dict):
        super().__init__()
        lay = hbox(self, spacing=6)
        self.field = QComboBox()
        for f in R.FIELD_TYPES:
            self.field.addItem(tr(f"field.{f}"), f)
        self.op = QComboBox()
        self.value_box = hbox(spacing=4)
        lay.addWidget(self.field, 2)
        lay.addWidget(self.op, 2)
        lay.addLayout(self.value_box, 3)
        rm = QToolButton()
        rm.setIcon(icons.icon("x"))
        rm.setToolTip(tr("rules.remove_condition"))
        rm.clicked.connect(lambda: self.removed.emit(self))
        lay.addWidget(rm)
        self._value_widgets: list[QWidget] = []
        idx = self.field.findData(cond.get("field", "kind"))
        self.field.setCurrentIndex(max(0, idx))
        self._fill_ops(cond.get("op"))
        self._build_value(cond.get("value"))
        self.field.currentIndexChanged.connect(self._field_changed)
        self.op.currentIndexChanged.connect(self._op_changed)

    def _ftype(self) -> str:
        return R.FIELD_TYPES.get(self.field.currentData(), "text")

    def _fill_ops(self, current: str | None = None) -> None:
        self.op.blockSignals(True)
        self.op.clear()
        for o in R.OPERATORS[self._ftype()]:
            self.op.addItem(tr(f"cond.{o}"), o)
        idx = self.op.findData(current) if current else 0
        self.op.setCurrentIndex(max(0, idx))
        self.op.blockSignals(False)

    def _field_changed(self) -> None:
        self._fill_ops()
        self._build_value(None)
        self.changed.emit()

    def _op_changed(self) -> None:
        self._build_value(self.value())
        self.changed.emit()

    def _clear_value(self) -> None:
        for w in self._value_widgets:
            w.deleteLater()
        self._value_widgets = []

    def _add_value(self, w: QWidget) -> None:
        self._value_widgets.append(w)
        self.value_box.addWidget(w, 1)

    def _build_value(self, value) -> None:
        self._clear_value()
        field = self.field.currentData()
        op = self.op.currentData()
        ftype = self._ftype()
        if op in ("exists", "not_exists", "is_true", "is_false"):
            return
        if ftype == "enum" and op in ("is", "is_not"):
            combo = QComboBox()
            for v in ENUM_VALUES.get(field, []):
                combo.addItem(tr(f"kind.{v}") if field == "kind" else tr(f"group.{v}"), v)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.currentIndexChanged.connect(self.changed.emit)
            self._add_value(combo)
            return
        if ftype == "date" and op in ("before", "after", "between"):
            values = value if isinstance(value, (list, tuple)) else [value]
            for i in range(2 if op == "between" else 1):
                de = QDateEdit()
                de.setCalendarPopup(True)
                de.setDisplayFormat("dd/MM/yyyy" if language() == "fr" else "yyyy-MM-dd")
                v = values[i] if i < len(values) else None
                d = QDate.fromString(str(v), "yyyy-MM-dd") if v else QDate.currentDate()
                de.setDate(d if d.isValid() else QDate.currentDate())
                de.dateChanged.connect(self.changed.emit)
                self._add_value(de)
            return
        if ftype == "date":  # jours ou année
            sb = QSpinBox()
            if op == "in_year":
                sb.setRange(1970, 2100)
                sb.setValue(int(value) if str(value or "").isdigit() else dt.date.today().year)
            else:
                sb.setRange(0, 36500)
                sb.setSuffix(" " + tr("rules.days"))
                sb.setValue(int(float(value)) if value not in (None, "") and _is_number(value) else 30)
            sb.valueChanged.connect(self.changed.emit)
            self._add_value(sb)
            return
        values = value if isinstance(value, (list, tuple)) else [value]
        for i in range(2 if op == "between" else 1):
            le = QLineEdit()
            v = values[i] if i < len(values) else ""
            le.setText("" if v is None else str(v))
            if ftype == "size":
                le.setPlaceholderText(tr("rules.size_placeholder"))
            elif op == "in_list" or op == "not_in_list":
                le.setPlaceholderText(tr("rules.list_placeholder"))
            elif op == "matches":
                le.setPlaceholderText(tr("rules.regex_placeholder"))
            le.textChanged.connect(self.changed.emit)
            self._add_value(le)

    def value(self):
        vals = []
        for w in self._value_widgets:
            if isinstance(w, QComboBox):
                vals.append(w.currentData())
            elif isinstance(w, QDateEdit):
                vals.append(w.date().toString("yyyy-MM-dd"))
            elif isinstance(w, QSpinBox):
                vals.append(w.value())
            elif isinstance(w, QLineEdit):
                vals.append(w.text())
        if not vals:
            return None
        return vals if len(vals) > 1 else vals[0]

    def to_dict(self) -> dict:
        return {"field": self.field.currentData(), "op": self.op.currentData(), "value": self.value()}


def _is_number(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


# ===========================================================================
# Éditeur d'une règle
# ===========================================================================
class RuleEditor(ScrollPage):
    changed = Signal()

    def __init__(self):
        super().__init__(max_width=None)
        self.widget().layout().setContentsMargins(4, 0, 4, 0)
        self.rule: dict | None = None
        self._loading = False
        b = self.body
        b.setSpacing(12)

        card = Card(margins=(16, 14, 16, 14))
        card.add(label(tr("rules.name"), "SectionTitle"))
        self.name = QLineEdit()
        self.name.textChanged.connect(self._emit)
        card.add(self.name)
        b.addWidget(card)

        cond = Card(margins=(16, 14, 16, 14))
        head = hbox(spacing=8)
        head.addWidget(label(tr("rules.if"), "SectionTitle"))
        self.match = QComboBox()
        self.match.addItem(tr("rules.match_all"), "all")
        self.match.addItem(tr("rules.match_any"), "any")
        self.match.currentIndexChanged.connect(self._emit)
        head.addWidget(self.match)
        head.addStretch(1)
        cond.add(head)
        self.cond_box = vbox(spacing=6)
        cond.add(self.cond_box)
        self.no_cond = label(tr("rules.no_condition"), "Faint", wrap=True)
        cond.add(self.no_cond)
        cond.add(button(tr("rules.add_condition"), "plus", "ghost", on_click=self._add_condition))
        b.addWidget(cond)

        act = Card(margins=(16, 14, 16, 14))
        act.add(label(tr("rules.then"), "SectionTitle"))
        self.action = QComboBox()
        for a in R.ACTIONS:
            self.action.addItem(icons.icon(ACTION_ICONS[a]), tr(f"action.{a}"), a)
        self.action.currentIndexChanged.connect(self._action_changed)
        act.add(self.action)
        self.target_label = label(tr("rules.target"), "Muted")
        act.add(self.target_label)
        trow = hbox(spacing=6)
        self.target = QLineEdit()
        self.target.setPlaceholderText(tr("rules.target_placeholder"))
        self.target.textChanged.connect(self._emit)
        trow.addWidget(self.target, 1)
        self.target_vars = self._var_button(self.target)
        trow.addWidget(self.target_vars)
        self.target_browse = button("", "folder", tooltip=tr("rules.absolute_target"), on_click=self._browse_target)
        trow.addWidget(self.target_browse)
        act.add(trow)
        self.rename_label = label(tr("rules.rename"), "Muted")
        act.add(self.rename_label)
        rrow = hbox(spacing=6)
        self.rename = QLineEdit()
        self.rename.setPlaceholderText(tr("rules.rename_placeholder"))
        self.rename.textChanged.connect(self._emit)
        rrow.addWidget(self.rename, 1)
        self.rename_vars = self._var_button(self.rename)
        rrow.addWidget(self.rename_vars)
        act.add(rrow)
        self.help = label(tr("rules.template_help"), "Faint", wrap=True)
        act.add(self.help)
        b.addWidget(act)

        self.preview = Banner("info", "")
        b.addWidget(self.preview)
        self.problems = Banner("error", "")
        b.addWidget(self.problems)
        b.addStretch(1)

    def _var_button(self, target: QLineEdit) -> QToolButton:
        btn = QToolButton()
        btn.setText(tr("rules.variables"))
        btn.setIcon(icons.icon("sparkles"))
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(btn)
        names = R.VARIABLE_LIST if language() == "fr" else R.VARIABLE_LIST_EN
        for v in names:
            act = menu.addAction(f"{{{v}}}  —  {tr('var.' + R.VARIABLES[v])}")
            act.triggered.connect(lambda _=False, v=v, t=target: t.insert("{" + v + "}"))
        btn.setMenu(menu)
        return btn

    def _browse_target(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("rules.absolute_target"))
        if path:
            self.target.setText(os.path.normpath(path))

    def load(self, rule: dict | None) -> None:
        self._loading = True
        self.rule = rule
        self.setEnabled(rule is not None)
        while self.cond_box.count():
            w = self.cond_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        if rule is not None:
            self.name.setText(rule.get("name", ""))
            self.match.setCurrentIndex(0 if rule.get("match", "all") == "all" else 1)
            for c in rule.get("conditions") or []:
                if "conditions" in c:  # groupe imbriqué : non éditable visuellement, conservé tel quel
                    continue
                self._add_condition_row(c)
            self.action.setCurrentIndex(max(0, self.action.findData(rule.get("action", "move"))))
            self.target.setText(rule.get("target", ""))
            self.rename.setText(rule.get("rename", ""))
        self._loading = False
        self._action_changed(emit=False)
        self._refresh_preview()

    def _add_condition_row(self, cond: dict) -> None:
        row = ConditionRow(cond)
        row.changed.connect(self._emit)
        row.removed.connect(self._remove_condition)
        self.cond_box.addWidget(row)
        self.no_cond.setVisible(False)

    def _add_condition(self) -> None:
        self._add_condition_row({"field": "ext", "op": "in_list", "value": ""})
        self._emit()

    def _remove_condition(self, row) -> None:
        self.cond_box.removeWidget(row)
        row.deleteLater()
        QTimer.singleShot(0, self._emit)

    def _action_changed(self, *_args, emit: bool = True) -> None:
        a = self.action.currentData()
        moving = a in ("move", "copy")
        for w in (self.target_label, self.target, self.target_vars, self.target_browse, self.rename_label,
                  self.rename, self.rename_vars, self.help):
            w.setVisible(moving)
        if emit:
            self._emit()

    def to_dict(self) -> dict:
        rule = dict(self.rule or {})
        conds = [self.cond_box.itemAt(i).widget().to_dict() for i in range(self.cond_box.count())
                 if isinstance(self.cond_box.itemAt(i).widget(), ConditionRow)]
        nested = [c for c in (self.rule or {}).get("conditions", []) if "conditions" in c]
        rule.update(
            name=self.name.text().strip() or tr("rules.untitled"),
            match=self.match.currentData(),
            conditions=conds + nested,
            action=self.action.currentData(),
            target=self.target.text().strip(),
            rename=self.rename.text().strip(),
        )
        return rule

    def _emit(self, *_args) -> None:
        if self._loading:
            return
        self.no_cond.setVisible(self.cond_box.count() == 0)
        self._refresh_preview()
        self.changed.emit()

    def _refresh_preview(self) -> None:
        if self.rule is None:
            self.preview.hide()
            self.problems.hide()
            return
        rule = self.to_dict()
        problems = R.validate_rule(rule)
        if problems:
            self.problems.set_text("<br>".join(tr(p) for p in problems))
            self.problems.show()
        else:
            self.problems.hide()
        if rule["action"] not in ("move", "copy"):
            text = tr("rules.preview_ignore") if rule["action"] == "ignore" else tr("rules.preview_trash")
            self.preview.set_text(text)
            self.preview.show()
            return
        e = sample_entry(rule)
        root = r"D:\Rangement"
        folder = R.render_target_dir(rule["target"], e, root, language(), kind_label)
        name = R.render_name(rule["rename"], e, language(), kind_label, "001")
        self.preview.set_text(tr("rules.preview", src=e.name, dst=os.path.join(folder, name)))
        self.preview.show()


# ===========================================================================
# Page
# ===========================================================================
class RulesPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.profile: dict = {}
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save)
        outer = vbox(self, (28, 22, 28, 18), 12)

        head = hbox(spacing=10)
        texts = vbox(spacing=2)
        texts.addWidget(label(tr("rules.title"), "PageTitle"))
        texts.addWidget(label(tr("rules.subtitle"), "PageSubtitle", wrap=True))
        head.addLayout(texts, 1)
        head.addWidget(label(tr("sort.profile")), 0, Qt.AlignBottom)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(240)
        self.profile_combo.currentIndexChanged.connect(self._profile_selected)
        head.addWidget(self.profile_combo, 0, Qt.AlignBottom)
        menu_btn = QToolButton()
        menu_btn.setIcon(icons.icon("menu"))
        menu_btn.setText(tr("rules.profile_menu"))
        menu_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(menu_btn)
        m.addAction(icons.icon("plus"), tr("rules.new_profile"), self._new_profile)
        m.addAction(icons.icon("copy"), tr("rules.duplicate_profile"), self._duplicate_profile)
        m.addAction(icons.icon("edit"), tr("rules.rename_profile"), self._rename_profile)
        m.addSeparator()
        m.addAction(icons.icon("upload"), tr("rules.import_profile"), self._import_profile)
        m.addAction(icons.icon("download"), tr("rules.export_profile"), self._export_profile)
        m.addSeparator()
        m.addAction(icons.icon("refresh"), tr("rules.reset_rules"), self._reset_rules)
        m.addAction(icons.icon("trash"), tr("rules.delete_profile"), self._delete_profile)
        menu_btn.setMenu(m)
        head.addWidget(menu_btn, 0, Qt.AlignBottom)
        outer.addLayout(head)

        self.tabs = TabWidget()
        outer.addWidget(self.tabs, 1)

        # --- règles ---
        tab = QWidget()
        tl = hbox(tab, (0, 10, 0, 0), 12)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = vbox(left, spacing=6)
        ll.addWidget(label(tr("rules.order_hint"), "Faint", wrap=True))
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setIconSize(QSize(16, 16))
        self.list.currentRowChanged.connect(self._rule_selected)
        self.list.itemChanged.connect(self._item_changed)
        self.list.model().rowsMoved.connect(lambda *a: self._reordered())
        ll.addWidget(self.list, 1)
        btns = hbox(spacing=4)
        btns.addWidget(button(tr("rules.new_rule"), "plus", on_click=self._new_rule))
        btns.addWidget(button("", "copy", "ghost", tooltip=tr("rules.duplicate_rule"), on_click=self._dup_rule))
        btns.addWidget(button("", "arrow_up", "ghost", tooltip=tr("rules.move_up"), on_click=lambda: self._move(-1)))
        btns.addWidget(button("", "arrow_down", "ghost", tooltip=tr("rules.move_down"), on_click=lambda: self._move(1)))
        btns.addWidget(button("", "trash", "ghost", tooltip=tr("rules.delete_rule"), on_click=self._delete_rule))
        btns.addStretch(1)
        ll.addLayout(btns)
        ll.addWidget(button(tr("rules.test_file"), "flask", on_click=self._test_file))
        split.addWidget(left)
        self.editor = RuleEditor()
        self.editor.changed.connect(self._editor_changed)
        split.addWidget(self.editor)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([380, 620])
        tl.addWidget(split)
        self.tabs.addTab(tab, icons.icon("rules"), tr("rules.tab_rules"))

        # --- arborescence ---
        tab2 = QWidget()
        t2 = vbox(tab2, (0, 10, 0, 0), 8)
        t2.addWidget(label(tr("rules.tree_text"), "Muted", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("rules.tree_folder"), tr("rules.tree_used_by")])
        self.tree.setColumnWidth(0, 380)
        self.tree.setIconSize(QSize(16, 16))
        t2.addWidget(self.tree, 1)
        tb = hbox(spacing=6)
        tb.addWidget(button(tr("rules.tree_new"), "folder_plus", on_click=self._tree_new))
        tb.addWidget(button(tr("rules.tree_rename"), "edit", on_click=self._tree_rename))
        tb.addWidget(button(tr("rules.tree_delete"), "trash", "ghost", on_click=self._tree_delete))
        tb.addStretch(1)
        t2.addLayout(tb)
        self.tabs.addTab(tab2, icons.icon("folder"), tr("rules.tab_tree"))

        # --- options du profil ---
        tab3 = ScrollPage(max_width=820)
        tab3.widget().layout().setContentsMargins(0, 10, 0, 0)
        card = Card(tr("rules.profile_options"), tr("rules.profile_options_text"), "settings")
        self.opt_in_place = QCheckBox(tr("sort.in_place"))
        self.opt_recursive = QCheckBox(tr("sort.recursive"))
        self.opt_cleanup = QCheckBox(tr("sort.cleanup"))
        for w in (self.opt_in_place, self.opt_recursive, self.opt_cleanup):
            w.toggled.connect(self._options_changed)
            card.add(w)
        card.add(label(tr("rules.default_destination"), "Muted"))
        drow = hbox(spacing=6)
        self.opt_dest = QLineEdit()
        self.opt_dest.setPlaceholderText(tr("sort.dest_placeholder"))
        self.opt_dest.textChanged.connect(self._options_changed)
        drow.addWidget(self.opt_dest, 1)
        drow.addWidget(button(tr("common.browse"), "folder", on_click=self._browse_dest))
        card.add(drow)
        tab3.body.addWidget(card)
        tab3.body.addStretch(1)
        self.tabs.addTab(tab3, icons.icon("settings"), tr("rules.tab_options"))

        self._loading = False
        self.reload_profiles()

    # -- profils -------------------------------------------------------------------
    def receive(self, payload) -> None:
        if payload and payload.get("profile"):
            idx = self.profile_combo.findData(payload["profile"])
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)

    def reload_profiles(self, select: str | None = None) -> None:
        current = select or self.profile_combo.currentData() or self.ctx.config.get("last_profile")
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for p in self.ctx.config.profiles:
            self.profile_combo.addItem(p["name"], p["id"])
        self.profile_combo.setCurrentIndex(max(0, self.profile_combo.findData(current)))
        self.profile_combo.blockSignals(False)
        self._profile_selected()

    def _profile_selected(self) -> None:
        self._flush()
        self.profile = self.ctx.config.profile(self.profile_combo.currentData())
        self._loading = True
        self.opt_in_place.setChecked(bool(self.profile.get("in_place")))
        self.opt_recursive.setChecked(bool(self.profile.get("recursive", True)))
        self.opt_cleanup.setChecked(bool(self.profile.get("cleanup_empty", True)))
        self.opt_dest.setText(self.profile.get("destination", ""))
        self._loading = False
        self._fill_list(0)
        self._fill_tree()

    def _flush(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save()

    def _save(self) -> None:
        if not self.profile:
            return
        self.ctx.config.save_profile(self.profile)
        self.ctx.configChanged.emit()

    def _schedule_save(self) -> None:
        self._save_timer.start()

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, tr("rules.new_profile"), tr("rules.profile_name"))
        if ok and name.strip():
            from ...core.defaults import default_profiles

            p = default_profiles(language())[0]
            p["id"] = R.new_id()
            p["name"] = name.strip()
            self.ctx.config.save_profile(p)
            self.ctx.configChanged.emit()
            self.reload_profiles(p["id"])

    def _duplicate_profile(self) -> None:
        self._flush()
        p = copy.deepcopy(self.profile)
        p["id"] = R.new_id()
        p["name"] = tr("rules.copy_of", name=p["name"])
        for r in p["rules"]:
            r["id"] = R.new_id()
        self.ctx.config.save_profile(p)
        self.ctx.configChanged.emit()
        self.reload_profiles(p["id"])

    def _rename_profile(self) -> None:
        name, ok = QInputDialog.getText(self, tr("rules.rename_profile"), tr("rules.profile_name"),
                                        text=self.profile.get("name", ""))
        if ok and name.strip():
            self.profile["name"] = name.strip()
            self._save()
            self.reload_profiles(self.profile["id"])

    def _delete_profile(self) -> None:
        if len(self.ctx.config.profiles) <= 1:
            error(self, tr("rules.delete_profile"), tr("rules.last_profile"))
            return
        if confirm(self, tr("rules.delete_profile"), tr("rules.delete_profile_text", name=self.profile["name"]),
                   tr("common.delete"), danger=True):
            self._save_timer.stop()
            self.ctx.config.delete_profile(self.profile["id"])
            self.profile = {}
            self.ctx.configChanged.emit()
            self.reload_profiles(self.ctx.config.profiles[0]["id"])

    def _import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("rules.import_profile"), os.path.expanduser("~"),
                                              "Rangeo (*.json *.yaml *.yml)")
        if not path:
            return
        try:
            p = self.ctx.config.import_profile(path)
        except Exception as exc:  # fichier invalide
            error(self, tr("rules.import_profile"), tr("rules.import_failed"), str(exc))
            return
        self.ctx.configChanged.emit()
        self.reload_profiles(p["id"])

    def _export_profile(self) -> None:
        self._flush()
        path, _ = QFileDialog.getSaveFileName(self, tr("rules.export_profile"),
                                              os.path.expanduser(f"~/{self.profile['name']}.rangeo.json"),
                                              "JSON (*.json);;YAML (*.yaml *.yml)")
        if path:
            self.ctx.config.export_profile(self.profile, path)

    def _reset_rules(self) -> None:
        if confirm(self, tr("rules.reset_rules"), tr("rules.reset_rules_text"), tr("rules.reset_rules"), danger=True):
            self.profile["rules"] = default_rules(language())
            self._save()
            self._fill_list(0)
            self._fill_tree()

    # -- liste des règles ------------------------------------------------------------
    def _fill_list(self, select: int = 0) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        c = theme.colors()
        for rule in self.profile.get("rules", []):
            item = QListWidgetItem(rule.get("name", ""))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            item.setCheckState(Qt.Checked if rule.get("enabled", True) else Qt.Unchecked)
            a = rule.get("action", "move")
            item.setIcon(icons.icon(ACTION_ICONS.get(a, "arrow_right"),
                                    {"ignore": c["text3"], "trash": c["danger"]}.get(a, c["accent"])))
            tip = tr(f"action.{a}")
            if a in ("move", "copy"):
                tip += " → " + rule.get("target", "")
            item.setToolTip(tip)
            item.setData(Qt.UserRole, rule.get("id"))
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(min(max(0, select), self.list.count() - 1))
        else:
            self.editor.load(None)

    def _rule_index(self, rule_id) -> int:
        for i, r in enumerate(self.profile.get("rules", [])):
            if r.get("id") == rule_id:
                return i
        return -1

    def _rule_selected(self, row: int) -> None:
        rules = self.profile.get("rules", [])
        self.editor.load(rules[row] if 0 <= row < len(rules) else None)

    def _item_changed(self, item: QListWidgetItem) -> None:
        idx = self._rule_index(item.data(Qt.UserRole))
        if idx >= 0:
            self.profile["rules"][idx]["enabled"] = item.checkState() == Qt.Checked
            self._schedule_save()

    def _reordered(self) -> None:
        order = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        by_id = {r["id"]: r for r in self.profile["rules"]}
        self.profile["rules"] = [by_id[i] for i in order if i in by_id]
        self._schedule_save()

    def _editor_changed(self) -> None:
        row = self.list.currentRow()
        if row < 0 or self._loading:
            return
        rule = self.editor.to_dict()
        self.profile["rules"][row] = rule
        item = self.list.item(row)
        self.list.blockSignals(True)
        item.setText(rule["name"])
        c = theme.colors()
        item.setIcon(icons.icon(ACTION_ICONS.get(rule["action"], "arrow_right"),
                                {"ignore": c["text3"], "trash": c["danger"]}.get(rule["action"], c["accent"])))
        self.list.blockSignals(False)
        self._schedule_save()
        self._fill_tree()

    def _new_rule(self) -> None:
        rule = R.normalize_rule({"name": tr("rules.new_rule_name"), "conditions": [
            {"field": "ext", "op": "in_list", "value": ""}], "action": "move", "target": ""})
        row = max(0, self.list.currentRow())
        self.profile["rules"].insert(row, rule)
        self._fill_list(row)
        self._schedule_save()

    def _dup_rule(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        rule = copy.deepcopy(self.profile["rules"][row])
        rule["id"] = R.new_id()
        rule.pop("builtin", None)
        rule["name"] = tr("rules.copy_of", name=rule["name"])
        self.profile["rules"].insert(row + 1, rule)
        self._fill_list(row + 1)
        self._schedule_save()

    def _delete_rule(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        name = self.profile["rules"][row]["name"]
        if confirm(self, tr("rules.delete_rule"), tr("rules.delete_rule_text", name=name), tr("common.delete"),
                   danger=True):
            del self.profile["rules"][row]
            self._fill_list(row)
            self._schedule_save()
            self._fill_tree()

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        new = row + delta
        rules = self.profile["rules"]
        if row < 0 or not 0 <= new < len(rules):
            return
        rules[row], rules[new] = rules[new], rules[row]
        self._fill_list(new)
        self._schedule_save()

    # -- test sur un fichier ---------------------------------------------------------
    def _test_file(self) -> None:
        self._flush()
        path, _ = QFileDialog.getOpenFileName(self, tr("rules.test_file"), os.path.expanduser("~"))
        if not path:
            return
        path = os.path.normpath(path)
        st = os.stat(path)
        name = os.path.basename(path)
        ext = split_ext(name)[1].lstrip(".").lower()
        entry = FileEntry(dir=os.path.dirname(path), name=name, size=st.st_size, mtime=st.st_mtime,
                          ctime=creation_time(st), attrs=getattr(st, "st_file_attributes", 0), ext=ext,
                          kind=ft.kind_from_ext(ext), root=os.path.dirname(path))
        real, meta = inspect_file(entry, True, True)
        apply_inspection(entry, real, meta)
        entry.kind = refine_kind(entry)
        compute_date(entry, self.ctx.config.get("date_order") or ClassifyOptions().date_order)
        decision = R.decide(entry, self.profile.get("rules", []))
        lines = [tr("rules.test_kind", kind=kind_label(entry.kind)),
                 tr("rules.test_date", date=fmt_date(entry.date), source=tr(f"date_source.{entry.date_source}"))]
        if entry.ext_mismatch:
            lines.append(tr("preview.mismatch", real=(entry.real_ext or "?").upper()))
        if decision is None:
            lines.append(tr("rules.test_no_rule"))
        else:
            lines.append(tr("rules.test_rule", rule=decision.rule_name, action=tr(f"action.{decision.action}")))
            if decision.action in ("move", "copy"):
                dest = self.profile.get("destination") or r"D:\Rangement"
                folder = R.render_target_dir(decision.target, entry, dest, language(), kind_label)
                new_name = R.render_name(decision.rename, entry, language(), kind_label, "001")
                lines.append(tr("rules.test_target", path=os.path.join(folder, new_name)))
        info(self, tr("rules.test_title", name=name), "<br>".join(lines))

    # -- arborescence ------------------------------------------------------------------
    def _fill_tree(self) -> None:
        self.tree.clear()
        c = theme.colors()
        nodes: dict[str, QTreeWidgetItem] = {}

        def add_path(path: str, rule_name: str | None, custom: bool) -> None:
            parts = [p for p in path.replace("\\", "/").split("/") if p.strip()]
            key = ""
            parent = None
            for depth, part in enumerate(parts):
                key = key + "/" + part.lower()
                item = nodes.get(key)
                if item is None:
                    item = QTreeWidgetItem([part, ""])
                    dynamic = "{" in part
                    item.setIcon(0, icons.icon("sparkles" if dynamic else "folder",
                                               c["warning"] if dynamic else c["accent"]))
                    if dynamic:
                        font = item.font(0)
                        font.setItalic(True)
                        item.setFont(0, font)
                        item.setToolTip(0, tr("rules.tree_dynamic"))
                    item.setData(0, Qt.UserRole, key)
                    item.setData(0, Qt.UserRole + 1, "/".join(parts[: depth + 1]))
                    if parent is None:
                        self.tree.addTopLevelItem(item)
                    else:
                        parent.addChild(item)
                    nodes[key] = item
                parent = item
            if parent is not None:
                used = parent.data(1, Qt.UserRole) or []
                if rule_name:
                    used.append(rule_name)
                if custom:
                    used.append(tr("rules.tree_custom"))
                parent.setData(1, Qt.UserRole, used)
                parent.setText(1, ", ".join(used))

        for rule in self.profile.get("rules", []):
            if rule.get("action") in ("move", "copy") and rule.get("target") and not R.is_absolute_template(
                    rule["target"]):
                add_path(rule["target"], rule.get("name"), False)
        for extra in self.profile.get("extra_dirs", []):
            add_path(extra, None, True)
        self.tree.sortItems(0, Qt.AscendingOrder)
        self.tree.expandToDepth(1)

    def _selected_tree_path(self) -> str:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole + 1) if item is not None else ""

    def _tree_new(self) -> None:
        base = self._selected_tree_path()
        if "{" in base:
            base = ""
        name, ok = QInputDialog.getText(self, tr("rules.tree_new"), tr("rules.tree_new_text", parent=base or "/"))
        if not ok or not name.strip():
            return
        from ...core.fsutil import sanitize_component

        clean = sanitize_component(name.strip())
        path = f"{base}/{clean}" if base else clean
        self.profile.setdefault("extra_dirs", []).append(path)
        self._schedule_save()
        self._fill_tree()

    def _tree_rename(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        old_path = item.data(0, Qt.UserRole + 1)
        if "{" in item.text(0):
            info(self, tr("rules.tree_rename"), tr("rules.tree_dynamic_rename"))
            return
        name, ok = QInputDialog.getText(self, tr("rules.tree_rename"), tr("rules.tree_rename_text"), text=item.text(0))
        if not ok or not name.strip() or name.strip() == item.text(0):
            return
        from ...core.fsutil import sanitize_component

        new_path = "/".join(old_path.split("/")[:-1] + [sanitize_component(name.strip())])

        def replace(t: str) -> str:
            norm = t.replace("\\", "/")
            if norm.lower() == old_path.lower():
                return new_path
            if norm.lower().startswith(old_path.lower() + "/"):
                return new_path + norm[len(old_path):]
            return t

        for rule in self.profile.get("rules", []):
            rule["target"] = replace(rule.get("target", ""))
        self.profile["extra_dirs"] = [replace(x) for x in self.profile.get("extra_dirs", [])]
        self._schedule_save()
        self._fill_tree()
        self._rule_selected(self.list.currentRow())

    def _tree_delete(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        path = item.data(0, Qt.UserRole + 1).lower()
        users = [r["name"] for r in self.profile.get("rules", []) if r.get("action") in ("move", "copy") and (
            r.get("target", "").replace("\\", "/").lower() + "/").startswith(path + "/")]
        if users:
            info(self, tr("rules.tree_delete"), tr("rules.tree_in_use", rules=", ".join(users)))
            return
        self.profile["extra_dirs"] = [x for x in self.profile.get("extra_dirs", [])
                                      if not (x.replace("\\", "/").lower() + "/").startswith(path + "/")]
        self._schedule_save()
        self._fill_tree()

    # -- options -------------------------------------------------------------------
    def _options_changed(self) -> None:
        if self._loading or not self.profile:
            return
        self.profile["in_place"] = self.opt_in_place.isChecked()
        self.profile["recursive"] = self.opt_recursive.isChecked()
        self.profile["cleanup_empty"] = self.opt_cleanup.isChecked()
        self.profile["destination"] = self.opt_dest.text().strip()
        self._schedule_save()

    def _browse_dest(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("sort.choose_destination"), self.opt_dest.text())
        if path:
            self.opt_dest.setText(os.path.normpath(path))

    def hideEvent(self, event) -> None:
        self._flush()
        super().hideEvent(event)
