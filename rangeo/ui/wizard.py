"""Assistant de premier lancement."""

from __future__ import annotations

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QStackedWidget, QWidget

from . import icons, theme
from .i18n import LANGUAGES, set_language, tr
from .widgets import Card, button, hbox, icon_label, label, vbox


class FirstRunWizard(QDialog):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setWindowTitle("Rangeo")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(720, 560)
        self._build()

    def _build(self) -> None:
        if self.layout() is not None:
            QWidget().setLayout(self.layout())  # détache l'ancienne mise en page
        lay = vbox(self, (36, 30, 36, 24), 14)
        top = hbox(spacing=14)
        logo = label()
        logo.setPixmap(icons.app_icon_pixmap(128).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        top.addWidget(logo)
        texts = vbox(spacing=2)
        texts.addWidget(label(tr("wizard.welcome"), "PageTitle"))
        texts.addWidget(label(tr("wizard.welcome_text"), "PageSubtitle", wrap=True))
        top.addLayout(texts, 1)
        lay.addLayout(top)

        self.pages = QStackedWidget()
        lay.addWidget(self.pages, 1)

        # page 1 : langue et thème
        p1 = QWidget()
        l1 = vbox(p1, spacing=12)
        card = Card(tr("wizard.prefs"), None, "settings")
        row = hbox(spacing=10)
        row.addWidget(label(tr("settings.language")), 1)
        self.lang = QComboBox()
        for code, name in LANGUAGES.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(self.config.get("language"))))
        self.lang.currentIndexChanged.connect(self._lang_changed)
        row.addWidget(self.lang)
        card.add(row)
        row2 = hbox(spacing=10)
        row2.addWidget(label(tr("settings.theme")), 1)
        self.theme = QComboBox()
        for key in ("system", "light", "dark"):
            self.theme.addItem(tr(f"settings.theme_{key}"), key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(self.config.get("theme"))))
        self.theme.currentIndexChanged.connect(self._theme_changed)
        row2.addWidget(self.theme)
        card.add(row2)
        l1.addWidget(card)
        l1.addStretch(1)
        self.pages.addWidget(p1)

        # page 2 : promesses de sécurité
        p2 = QWidget()
        l2 = vbox(p2, spacing=10)
        c = theme.colors()
        for icon_name, key in (("search", "preview"), ("shield", "verify"), ("undo", "undo"),
                               ("lock", "protect"), ("trash", "trash")):
            card = Card(margins=(16, 12, 16, 12))
            r = hbox(spacing=12)
            r.addWidget(icon_label(icon_name, c["accent"], 22), 0, Qt.AlignTop)
            t = vbox(spacing=2)
            t.addWidget(label(tr(f"wizard.safe_{key}"), "SectionTitle"))
            t.addWidget(label(tr(f"wizard.safe_{key}_text"), "Muted", wrap=True))
            r.addLayout(t, 1)
            card.add(r)
            l2.addWidget(card)
        l2.addStretch(1)
        self.pages.addWidget(p2)

        # page 3 : surveillance
        p3 = QWidget()
        l3 = vbox(p3, spacing=12)
        card = Card(tr("wizard.watch"), tr("wizard.watch_text"), "eye")
        self.watch_dl = QCheckBox(tr("wizard.watch_downloads"))
        card.add(self.watch_dl)
        self.autostart = QCheckBox(tr("settings.autostart"))
        card.add(self.autostart)
        l3.addWidget(card)
        l3.addStretch(1)
        self.pages.addWidget(p3)

        nav = hbox(spacing=8)
        self.step = label("", "Faint")
        nav.addWidget(self.step, 1)
        self.back_btn = button(tr("wizard.back"), "arrow_left", on_click=lambda: self._go(-1))
        self.next_btn = button(tr("wizard.next"), "arrow_right", "primary", on_click=lambda: self._go(1))
        nav.addWidget(self.back_btn)
        nav.addWidget(self.next_btn)
        lay.addLayout(nav)
        self._go(0)

    def _go(self, delta: int) -> None:
        idx = self.pages.currentIndex() + delta
        if idx >= self.pages.count():
            self._finish()
            return
        self.pages.setCurrentIndex(max(0, idx))
        idx = self.pages.currentIndex()
        self.back_btn.setEnabled(idx > 0)
        last = idx == self.pages.count() - 1
        self.next_btn.setText(tr("wizard.start") if last else tr("wizard.next"))
        self.step.setText(tr("wizard.step", n=idx + 1, total=self.pages.count()))

    def _lang_changed(self) -> None:
        self.config.set("language", self.lang.currentData(), save=False)
        set_language(self.lang.currentData())
        self._build()

    def _theme_changed(self) -> None:
        self.config.set("theme", self.theme.currentData(), save=False)
        theme.apply_theme(QApplication.instance(), self.theme.currentData())
        self._build()

    def _finish(self) -> None:
        cfg = self.config
        if self.watch_dl.isChecked():
            path = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
            if path:
                import os

                watch = cfg.get("watch", []) or []
                watch.append({"path": os.path.normpath(path), "profile": "downloads", "enabled": True,
                              "recursive": False, "delay": 5})
                cfg.set("watch", watch, save=False)
        if self.autostart.isChecked():
            from .pages.settings import set_autostart

            try:
                set_autostart(True)
                cfg.set("autostart", True, save=False)
            except OSError:
                pass
        # règles par défaut dans la langue choisie
        from ..core.defaults import default_profiles

        if cfg.get("language") != "fr":
            cfg.replace_profiles(default_profiles(cfg.get("language")), save=False)
            cfg.set("duplicates_folder", "_Duplicates", save=False)
        cfg.set("first_run_done", True)
        self.accept()
