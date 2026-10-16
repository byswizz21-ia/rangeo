"""Page « Paramètres »."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QWidget,
)

from ... import __version__
from ...core import appdirs
from ...core.config import CONFLICT_STRATEGIES, DUP_ACTIONS, DUP_CRITERIA, IDENTICAL_STRATEGIES
from ...core.defaults import DEFAULT_EXCLUSIONS
from .. import icons
from ..context import AppContext
from ..i18n import LANGUAGES, tr
from ..widgets import Card, PageHeader, ScrollPage, Toggle, button, confirm, error, hbox, info, label, open_in_explorer, vbox

DATE_PRESETS = {
    "smart": ["metadata", "filename", "oldest"],
    "exif_created": ["metadata", "created", "modified"],
    "modified": ["metadata", "modified"],
    "created": ["created"],
}

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    return f'"{exe}" -m rangeo --tray'


def set_autostart(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "Rangeo", 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, "Rangeo")
            except FileNotFoundError:
                pass


class Row(QWidget):
    """Ligne de paramètre : titre + description à gauche, contrôle à droite."""

    def __init__(self, title: str, text: str, control: QWidget):
        super().__init__()
        lay = hbox(self, (0, 6, 0, 6), 16)
        texts = vbox(spacing=2)
        texts.addWidget(label(title))
        if text:
            texts.addWidget(label(text, "Faint", wrap=True))
        lay.addLayout(texts, 1)
        lay.addWidget(control, 0, Qt.AlignVCenter)


class EditableList(QWidget):
    def __init__(self, values: list[str], on_change, add_title: str, folders: bool = False, reset=None):
        super().__init__()
        self.on_change = on_change
        self.folders = folders
        self.add_title = add_title
        lay = vbox(self, spacing=6)
        self.list = QListWidget()
        self.list.setMaximumHeight(170)
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        lay.addWidget(self.list)
        row = hbox(spacing=6)
        row.addWidget(button(tr("common.add"), "plus", on_click=self._add))
        row.addWidget(button(tr("common.remove"), "trash", "ghost", on_click=self._remove))
        if reset:
            row.addWidget(button(tr("settings.reset_list"), "refresh", "ghost", on_click=lambda: self._reset(reset)))
        row.addStretch(1)
        lay.addLayout(row)
        self.set_values(values)

    def set_values(self, values: list[str]) -> None:
        self.list.clear()
        for v in values:
            item = QListWidgetItem(icons.icon("folder" if self.folders else "x"), v)
            self.list.addItem(item)

    def values(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def _add(self) -> None:
        if self.folders:
            path = QFileDialog.getExistingDirectory(self, self.add_title)
            value = os.path.normpath(path) if path else ""
        else:
            value, ok = QInputDialog.getText(self, self.add_title, tr("settings.exclusion_prompt"))
            value = value.strip() if ok else ""
        if value and value not in self.values():
            self.list.addItem(QListWidgetItem(icons.icon("folder" if self.folders else "x"), value))
            self.on_change(self.values())

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self.on_change(self.values())

    def _reset(self, values) -> None:
        self.set_values(list(values))
        self.on_change(self.values())


class SettingsPage(ScrollPage):
    def __init__(self, ctx: AppContext):
        super().__init__(max_width=980)
        self.ctx = ctx
        cfg = ctx.config
        b = self.body
        b.addWidget(PageHeader(tr("settings.title"), tr("settings.subtitle")))

        # --- général ---
        g = Card(tr("settings.general"), None, "settings")
        self.lang = QComboBox()
        for code, name in LANGUAGES.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(cfg.get("language"))))
        self.lang.currentIndexChanged.connect(self._lang_changed)
        g.add(Row(tr("settings.language"), tr("settings.language_text"), self.lang))
        self.theme = QComboBox()
        for key in ("system", "light", "dark"):
            self.theme.addItem(icons.icon({"system": "monitor", "light": "sun", "dark": "moon"}[key]),
                               tr(f"settings.theme_{key}"), key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(cfg.get("theme"))))
        self.theme.currentIndexChanged.connect(self._theme_changed)
        g.add(Row(tr("settings.theme"), "", self.theme))
        self.autostart = self._toggle("autostart", self._autostart_changed)
        g.add(Row(tr("settings.autostart"), tr("settings.autostart_text"), self.autostart))
        g.add(Row(tr("settings.tray"), tr("settings.tray_text"), self._toggle("minimize_to_tray")))
        g.add(Row(tr("settings.notifications"), tr("settings.notifications_text"), self._toggle("notifications")))
        b.addWidget(g)

        # --- rangement ---
        s = Card(tr("settings.sorting"), None, "sort")
        self.conflict = self._combo("conflict", CONFLICT_STRATEGIES, "conflict")
        s.add(Row(tr("settings.conflict"), tr("settings.conflict_text"), self.conflict))
        s.add(Row(tr("settings.identical"), tr("settings.identical_text"),
                  self._combo("identical", IDENTICAL_STRATEGIES, "identical")))
        self.dates = QComboBox()
        current = cfg.get("date_order")
        for key, order in DATE_PRESETS.items():
            self.dates.addItem(tr(f"settings.dates_{key}"), key)
            if order == current:
                self.dates.setCurrentIndex(self.dates.count() - 1)
        self.dates.currentIndexChanged.connect(
            lambda: cfg.set("date_order", DATE_PRESETS[self.dates.currentData()]))
        s.add(Row(tr("settings.dates"), tr("settings.dates_text"), self.dates))
        s.add(Row(tr("settings.detect_bundles"), tr("settings.detect_bundles_text"), self._toggle("detect_bundles")))
        s.add(Row(tr("settings.companions"), tr("settings.companions_text"), self._toggle("companions")))
        s.add(Row(tr("settings.cleanup"), tr("settings.cleanup_text"), self._toggle("cleanup_empty")))
        s.add(Row(tr("settings.skip_hidden"), tr("settings.skip_hidden_text"), self._toggle("skip_hidden")))
        s.add(Row(tr("settings.deep_check"), tr("settings.deep_check_text"), self._toggle("deep_check")))
        s.add(Row(tr("settings.fix_ext"), tr("settings.fix_ext_text"), self._toggle("fix_extensions")))
        dup_name = QLineEdit(cfg.get("duplicates_folder", "_Doublons"))
        dup_name.setMaximumWidth(220)
        dup_name.editingFinished.connect(lambda: cfg.set("duplicates_folder", dup_name.text().strip() or "_Doublons"))
        s.add(Row(tr("settings.dup_folder"), tr("settings.dup_folder_text"), dup_name))
        b.addWidget(s)

        # --- performance ---
        p = Card(tr("settings.performance"), None, "zap")
        self.priority = self._combo("priority", ["fast", "discreet"], "priority")
        p.add(Row(tr("settings.priority"), tr("settings.priority_text"), self.priority))
        b.addWidget(p)

        # --- doublons ---
        d = Card(tr("settings.duplicates"), None, "copy")
        d.add(Row(tr("settings.dup_action"), "", self._combo("dup_action", DUP_ACTIONS, "dups.action")))
        self.min_size = QSpinBox()
        self.min_size.setRange(0, 1024 * 1024)
        self.min_size.setSuffix(" Ko")
        self.min_size.setValue(int(cfg.get("dup_min_size", 1)) // 1024)
        self.min_size.valueChanged.connect(lambda v: cfg.set("dup_min_size", max(1, v * 1024)))
        d.add(Row(tr("dups.min_size"), tr("settings.dup_min_text"), self.min_size))
        d.add(label(tr("settings.dup_keep"), "Muted"))
        self.criteria = QListWidget()
        self.criteria.setMaximumHeight(160)
        self.criteria.setDragDropMode(QListWidget.InternalMove)
        selected = cfg.get("dup_keep", [])
        for crit in selected + [c for c in DUP_CRITERIA if c not in selected]:
            item = QListWidgetItem(tr(f"dup_keep.{crit}"))
            item.setData(Qt.UserRole, crit)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if crit in selected else Qt.Unchecked)
            self.criteria.addItem(item)
        self.criteria.itemChanged.connect(self._criteria_changed)
        self.criteria.model().rowsMoved.connect(lambda *a: self._criteria_changed())
        d.add(self.criteria)
        d.add(label(tr("settings.dup_preferred"), "Muted"))
        d.add(EditableList(cfg.get("dup_preferred", []), lambda v: cfg.set("dup_preferred", v),
                           tr("settings.dup_preferred"), folders=True))
        b.addWidget(d)

        # --- exclusions et protections ---
        e = Card(tr("settings.exclusions"), tr("settings.exclusions_text"), "lock")
        e.add(EditableList(cfg.get("exclusions", []), lambda v: cfg.set("exclusions", v), tr("settings.exclusions"),
                           reset=DEFAULT_EXCLUSIONS))
        b.addWidget(e)
        k = Card(tr("settings.keep_whole"), tr("settings.keep_whole_text"), "folder")
        k.add(EditableList(cfg.get("keep_whole", []), lambda v: cfg.set("keep_whole", v), tr("settings.keep_whole"),
                           folders=True))
        b.addWidget(k)
        pr = Card(tr("settings.protected"), tr("settings.protected_text"), "shield")
        pr.add(EditableList(cfg.get("protected", []), lambda v: cfg.set("protected", v), tr("settings.protected"),
                            folders=True))
        b.addWidget(pr)

        # --- intégration Windows ---
        from ...core import winintegration as wi

        w = Card(tr("settings.windows"), None, "monitor")
        self.menu_toggle = Toggle(wi.context_menu_registered())
        self.menu_toggle.toggled.connect(self._context_menu)
        w.add(Row(tr("settings.context_menu"), tr("settings.context_menu_text"), self.menu_toggle))
        irow = hbox(spacing=8)
        if wi.is_frozen() and not wi.is_installed_copy():
            irow.addWidget(button(tr("install.install"), "download", "primary", on_click=self._install))
        if wi.is_installed_copy() or wi.installed():
            irow.addWidget(button(tr("install.uninstall"), "trash", "danger", on_click=self._uninstall))
        if not wi.is_frozen():
            irow.addWidget(label(tr("install.dev_mode"), "Faint", wrap=True), 1)
        irow.addStretch(1)
        w.add(irow)
        b.addWidget(w)

        # --- données ---
        dt = Card(tr("settings.data"), tr("settings.data_text"), "archive")
        row = hbox(spacing=8)
        row.addWidget(button(tr("settings.export"), "download", on_click=self._export))
        row.addWidget(button(tr("settings.import"), "upload", on_click=self._import))
        row.addWidget(button(tr("settings.open_logs"), "file", "ghost",
                             on_click=lambda: open_in_explorer(str(appdirs.logs_dir()))))
        row.addWidget(button(tr("settings.clear_cache"), "trash", "ghost", on_click=self._clear_cache))
        row.addStretch(1)
        dt.add(row)
        b.addWidget(dt)

        about = Card(flat=True, margins=(16, 12, 16, 12))
        about.add(label(tr("settings.about", version=__version__), "Muted", wrap=True))
        b.addWidget(about)
        b.addStretch(1)

    # -- helpers -------------------------------------------------------------------
    def _toggle(self, key: str, extra=None) -> Toggle:
        t = Toggle(bool(self.ctx.config.get(key)))

        def changed(on):
            self.ctx.config.set(key, bool(on))
            if extra:
                extra(on)
        t.toggled.connect(changed)
        return t

    def _combo(self, key: str, values, prefix: str) -> QComboBox:
        combo = QComboBox()
        for v in values:
            combo.addItem(tr(f"{prefix}_{v}" if prefix.startswith("dups.") else f"{prefix}.{v}"), v)
        combo.setCurrentIndex(max(0, combo.findData(self.ctx.config.get(key))))
        combo.currentIndexChanged.connect(lambda: self.ctx.config.set(key, combo.currentData()))
        combo.setMinimumWidth(240)
        return combo

    def _criteria_changed(self, *_):
        values = []
        for i in range(self.criteria.count()):
            item = self.criteria.item(i)
            if item.checkState() == Qt.Checked:
                values.append(item.data(Qt.UserRole))
        self.ctx.config.set("dup_keep", values or ["clean_name"])

    def _lang_changed(self) -> None:
        self.ctx.config.set("language", self.lang.currentData())
        self.ctx.rebuildRequested.emit()

    def _theme_changed(self) -> None:
        self.ctx.config.set("theme", self.theme.currentData())
        self.ctx.rebuildRequested.emit()

    def _autostart_changed(self, on: bool) -> None:
        try:
            set_autostart(on)
        except OSError as exc:
            error(self, tr("settings.autostart"), str(exc))

    def _context_menu(self, on: bool) -> None:
        from ...core import winintegration as wi

        try:
            if on:
                wi.register_context_menu(tr("winmenu.label"))
            else:
                wi.unregister_context_menu()
            self.ctx.config.set("context_menu", on)
        except OSError as exc:
            error(self, tr("settings.context_menu"), str(exc))

    def _install(self) -> None:
        import subprocess

        from ...core import winintegration as wi
        from PySide6.QtWidgets import QApplication

        if not confirm(self, tr("install.install"), tr("install.install_text", path=wi.install_dir()),
                       tr("install.install")):
            return
        try:
            exe = wi.install()
        except Exception as exc:
            error(self, tr("install.install"), tr("install.failed"), str(exc))
            return
        if self.ctx.config.get("context_menu"):
            pass  # le menu sera réenregistré par la copie installée
        info(self, tr("install.done"), tr("install.done_text"))
        win = self.window()
        if hasattr(win, "_quitting"):
            win._quitting = True
            win.tray.hide()
        subprocess.Popen([exe], close_fds=True)
        QApplication.instance().quit()

    def _uninstall(self) -> None:
        import subprocess

        from ...core import winintegration as wi

        exe = wi.installed_exe() if wi.installed() else None
        if exe:
            subprocess.Popen([exe, "--uninstall"], close_fds=True)

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("settings.export"),
                                              os.path.expanduser("~/rangeo-configuration.json"),
                                              "JSON (*.json);;YAML (*.yaml *.yml)")
        if path:
            self.ctx.config.export_all(path)
            info(self, tr("settings.export"), tr("settings.export_done"))

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("settings.import"), os.path.expanduser("~"),
                                              "Rangeo (*.json *.yaml *.yml)")
        if not path:
            return
        try:
            self.ctx.config.import_all(path)
        except Exception as exc:
            error(self, tr("settings.import"), tr("settings.import_failed"), str(exc))
            return
        self.ctx.watch.reload()
        self.ctx.configChanged.emit()
        self.ctx.rebuildRequested.emit()

    def _clear_cache(self) -> None:
        if confirm(self, tr("settings.clear_cache"), tr("settings.clear_cache_text"), tr("common.ok")):
            self.ctx.engine.index.clear()
            info(self, tr("settings.clear_cache"), tr("settings.cache_cleared"))
