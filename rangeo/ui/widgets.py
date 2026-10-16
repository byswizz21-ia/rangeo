"""Composants d'interface réutilisables."""

from __future__ import annotations

import os
import re

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import icons, motion, theme
from .i18n import fmt_duration, fmt_int, fmt_size, tr

_TIGHT = {"Display": 97.5, "PageTitle": 98.5, "StatValue": 99.0, "BigNumber": 98.5, "AppName": 99.0}


# ---------------------------------------------------------------------------
# Petits utilitaires
# ---------------------------------------------------------------------------
def vbox(widget: QWidget | None = None, margins=(0, 0, 0, 0), spacing: int = 8) -> QVBoxLayout:
    layout = QVBoxLayout(widget) if widget is not None else QVBoxLayout()
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def hbox(widget: QWidget | None = None, margins=(0, 0, 0, 0), spacing: int = 8) -> QHBoxLayout:
    layout = QHBoxLayout(widget) if widget is not None else QHBoxLayout()
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def label(text: str = "", name: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
        if name in _TIGHT:
            font = lab.font()
            font.setLetterSpacing(QFont.PercentageSpacing, _TIGHT[name])
            lab.setFont(font)
    lab.setWordWrap(wrap)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


def button(text: str = "", icon_name: str | None = None, kind: str = "default", big: bool = False,
           tooltip: str | None = None, on_click=None) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    if kind == "primary":
        btn.setObjectName("Primary")
    elif kind == "danger":
        btn.setObjectName("Danger")
    elif kind == "ghost":
        btn.setObjectName("Ghost")
    elif kind == "link":
        btn.setObjectName("Link")
    if big:
        btn.setProperty("big", True)
        btn.setStyleSheet("padding: 11px 24px; font-size: 11pt;")
    if icon_name:
        c = theme.colors()
        color = {"primary": c["accent_text"], "danger": c["danger"], "link": c["accent"]}.get(kind, c["text2"])
        btn.setIcon(icons.icon(icon_name, color, 18))
        btn.setIconSize(QSize(17, 17))
    if tooltip:
        btn.setToolTip(tooltip)
    if on_click:
        btn.clicked.connect(on_click)
    return btn


def overline(text: str) -> QLabel:
    """Petit intitulé en capitales (groupes de la navigation, rubriques)."""
    lab = label(text.upper(), "Overline")
    font = lab.font()
    font.setLetterSpacing(QFont.PercentageSpacing, 108)
    lab.setFont(font)
    return lab


def separator() -> QFrame:
    line = QFrame()
    line.setObjectName("Separator")
    line.setFrameShape(QFrame.NoFrame)
    return line


def icon_label(name: str, color: str | None = None, size: int = 20) -> QLabel:
    lab = QLabel()
    lab.setPixmap(icons.pixmap(name, color or theme.colors()["text2"], size))
    lab.setFixedSize(size + 2, size + 2)
    return lab


def elide_middle(text: str, width_px: int, widget: QWidget) -> str:
    return QFontMetrics(widget.font()).elidedText(text, Qt.ElideMiddle, width_px)


def open_in_explorer(path: str, select: bool = False) -> None:
    import subprocess

    if not path:
        return
    if select and os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif os.path.isdir(path):
        os.startfile(path)  # noqa: S606
    elif os.path.exists(os.path.dirname(path)):
        os.startfile(os.path.dirname(path))  # noqa: S606


# ---------------------------------------------------------------------------
# Boîtes de dialogue
# ---------------------------------------------------------------------------
def confirm(parent, title: str, text: str, ok: str | None = None, cancel: str | None = None,
            danger: bool = False, details: str | None = None) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(f"<b>{title}</b>")
    box.setInformativeText(text)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    if details:
        box.setDetailedText(details)
    yes = box.addButton(ok or tr("common.ok"), QMessageBox.AcceptRole)
    no = box.addButton(cancel or tr("common.cancel"), QMessageBox.RejectRole)
    yes.setObjectName("Danger" if danger else "Primary")
    box.setDefaultButton(no if danger else yes)
    box.exec()
    return box.clickedButton() is yes


def info(parent, title: str, text: str, details: str | None = None) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(f"<b>{title}</b>")
    box.setInformativeText(text)
    box.setIcon(QMessageBox.Information)
    if details:
        box.setDetailedText(details)
    box.addButton(tr("common.ok"), QMessageBox.AcceptRole).setObjectName("Primary")
    box.exec()


def error(parent, title: str, text: str, details: str | None = None) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(f"<b>{title}</b>")
    box.setInformativeText(text)
    box.setIcon(QMessageBox.Critical)
    if details:
        box.setDetailedText(details)
    box.addButton(tr("common.ok"), QMessageBox.AcceptRole).setObjectName("Primary")
    box.exec()


def choose_folder(parent, title: str, start: str = "") -> str | None:
    path = QFileDialog.getExistingDirectory(parent, title, start or os.path.expanduser("~"))
    return os.path.normpath(path) if path else None


# ---------------------------------------------------------------------------
# Cartes, puces, bannières
# ---------------------------------------------------------------------------
class Card(QFrame):
    def __init__(self, title: str | None = None, subtitle: str | None = None, icon_name: str | None = None,
                 flat: bool = False, margins=(20, 18, 20, 20), spacing: int = 12):
        super().__init__()
        self.setObjectName("CardFlat" if flat else "Card")
        self.body = vbox(self, margins, spacing)
        if title:
            head = hbox(spacing=12)
            if icon_name:
                head.addWidget(icon_label(icon_name, theme.colors()["accent"], 20))
            texts = vbox(spacing=2)
            texts.addWidget(label(title, "SectionTitle"))
            if subtitle:
                texts.addWidget(label(subtitle, "Muted", wrap=True))
            head.addLayout(texts, 1)
            self.header = head
            self.body.addLayout(head)

    def add(self, widget_or_layout, stretch: int = 0):
        if isinstance(widget_or_layout, QWidget):
            self.body.addWidget(widget_or_layout, stretch)
        else:
            self.body.addLayout(widget_or_layout, stretch)
        return widget_or_layout


class Chip(QLabel):
    def __init__(self, text: str = "", level: str = "neutral"):
        super().__init__(text)
        self.setObjectName("Chip")
        self.set_level(level)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def set_level(self, level: str) -> None:
        self.setProperty("level", level)
        self.style().unpolish(self)
        self.style().polish(self)


class Banner(QFrame):
    """Message en couleur (info, avertissement, erreur, succès) avec action facultative."""

    def __init__(self, level: str = "info", text: str = "", action: str | None = None, on_action=None,
                 closable: bool = False):
        super().__init__()
        self.setObjectName("Banner")
        self.setProperty("level", level)
        c = theme.colors()
        color = {"info": c["accent"], "warning": c["warning"], "error": c["danger"], "success": c["success"]}[level]
        icon_name = {"info": "info", "warning": "alert", "error": "error", "success": "check_circle"}[level]
        lay = hbox(self, (16, 12, 12, 12), 12)
        lay.addWidget(icon_label(icon_name, color, 20), 0, Qt.AlignTop)
        self.text = label(text, wrap=True, selectable=True)
        self.text.setTextFormat(Qt.RichText)
        lay.addWidget(self.text, 1)
        if action:
            btn = button(action, kind="default")
            if on_action:
                btn.clicked.connect(on_action)
            lay.addWidget(btn, 0, Qt.AlignVCenter)
        if closable:
            close = button("", "x", "ghost")
            close.clicked.connect(self.hide)
            lay.addWidget(close, 0, Qt.AlignTop)

    def set_text(self, text: str) -> None:
        self.text.setText(text)


_NUMBER = re.compile(r"^(\D*?)(\d[\d\s  .,]*\d|\d)(\D*)$")


def _split_number(text: str):
    """« 64,4 Go » -> ("", 64.4, 1, ",", "", " Go") ; None si le texte n'est pas un simple nombre."""
    m = _NUMBER.match(text or "")
    if not m:
        return None
    prefix, num, suffix = m.groups()
    dec_sep, decimals, thousands = ",", 0, ""
    last = max(num.rfind(","), num.rfind("."))
    if last >= 0 and (("," in num and "." in num) or (len(num) - last - 1 in (1, 2) and num.count(num[last]) == 1)):
        dec_sep, decimals = num[last], len(num) - last - 1
        int_part, frac = num[:last], num[last + 1:]
    else:
        int_part, frac = num, ""
    seps = [ch for ch in int_part if not ch.isdigit()]
    thousands = seps[0] if seps else ""
    digits = "".join(ch for ch in int_part if ch.isdigit())
    try:
        value = float(digits + ("." + frac if frac else ""))
    except ValueError:
        return None
    return prefix, value, decimals, dec_sep, thousands, suffix


def _format_number(value: float, decimals: int, dec_sep: str, thousands: str) -> str:
    whole, _, frac = f"{value:.{decimals}f}".partition(".")
    if thousands:
        whole = f"{int(whole):,}".replace(",", thousands)
    return whole + (dec_sep + frac if decimals else "")


class StatCard(QFrame):
    """Chiffre clé : icône et légende en haut, valeur dessous, qui se compte jusqu'à sa valeur."""

    def __init__(self, icon_name: str, value: str, caption: str, color: str | None = None, tone: str = "default"):
        super().__init__()
        self.setObjectName("StatSpark" if tone == "spark" else "StatCard")
        c = theme.colors()
        lay = vbox(self, (18, 14, 18, 16), 6)
        top = hbox(spacing=8)
        icon_color = c["spark_text"] if tone == "spark" else (color or c["accent"])
        top.addWidget(icon_label(icon_name, icon_color, 18), 0, Qt.AlignVCenter)
        cap = label(caption, "StatLabel")
        cap.setMinimumWidth(10)
        top.addWidget(cap, 1)
        lay.addLayout(top)
        self.value = label("", "StatValue")
        lay.addWidget(self.value)
        self._text = ""
        self._shown = False
        self._anim = None
        self._current = 0.0
        self.set_value(value)

    def set_value(self, text: str) -> None:
        old = self._text
        self._text = text
        parts = _split_number(text)
        if parts is None or not motion.enabled() or any(ch.isdigit() for ch in parts[5]):
            self._stop()
            self.value.setText(text)
            if parts is not None:
                self._current = parts[1]
            return
        if not self._shown:
            self.value.setText(text)
            return
        if text != old:
            self._count(self._current if old else 0.0, parts)

    def _stop(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

    def _count(self, start: float, parts) -> None:
        prefix, target, decimals, dec_sep, thousands, suffix = parts
        self._stop()
        if abs(target - start) < 10 ** -decimals:
            self.value.setText(self._text)
            self._current = target
            return

        def step(v):
            self._current = float(v)
            self.value.setText(prefix + _format_number(self._current, decimals, dec_sep, thousands) + suffix)

        def done():
            self._current = target
            self.value.setText(self._text)

        self._anim = motion.animation(self, motion.SETTLE, step, start=float(start), end=float(target), on_done=done)
        step(start)
        self._anim.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._shown:
            self._shown = True
            parts = _split_number(self._text)
            if parts is not None and motion.enabled() and not any(ch.isdigit() for ch in parts[5]):
                self._count(0.0, parts)


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", actions: list[QWidget] | None = None):
        super().__init__()
        lay = hbox(self, (0, 0, 0, 6), 12)
        texts = vbox(spacing=3)
        texts.addWidget(label(title, "PageTitle"))
        if subtitle:
            texts.addWidget(label(subtitle, "PageSubtitle", wrap=True))
        lay.addLayout(texts, 1)
        for w in actions or []:
            lay.addWidget(w, 0, Qt.AlignBottom)


class ScrollPage(QScrollArea):
    """Page défilante avec marges confortables."""

    def __init__(self, max_width: int | None = 1180):
        super().__init__()
        self.setObjectName("Page")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        outer = QWidget()
        outer.setObjectName("Page")
        outer_lay = hbox(outer, (32, 28, 32, 32), 0)
        self.content = QWidget()
        if max_width:
            self.content.setMaximumWidth(max_width)
        self.body = vbox(self.content, (0, 0, 0, 0), 16)
        outer_lay.addWidget(self.content, 1)
        self.setWidget(outer)


# ---------------------------------------------------------------------------
# Interrupteur
# ---------------------------------------------------------------------------
class Toggle(QAbstractButton):
    def __init__(self, checked: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 24)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(motion.FAST)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        if not motion.enabled():
            self.set_knob(1.0 if on else 0.0)
            return
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_knob(self) -> float:
        return self._pos

    def set_knob(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = Property(float, get_knob, set_knob)

    def sizeHint(self) -> QSize:
        return QSize(40, 24)

    def paintEvent(self, _event) -> None:
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = motion.mix(c["border_strong"], c["accent"], self._pos)
        knob = motion.mix(c["surface"], c["accent_text"], self._pos)
        if not self.isEnabled():
            p.setOpacity(0.45)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 40, 24), 12, 12)
        p.setBrush(knob)
        x = 3 + self._pos * 16
        p.drawEllipse(QRectF(x, 3, 18, 18))
        p.end()


# ---------------------------------------------------------------------------
# Zone de dépôt
# ---------------------------------------------------------------------------
class DropZone(QFrame):
    pathsDropped = Signal(list)
    browseClicked = Signal()

    def __init__(self, title: str, subtitle: str, button_text: str):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(220)
        self._level = 0.0      # 0 = repos, 0,35 = survol souris, 1 = dossier au-dessus
        self._dash = 0.0
        self._level_anim = None
        self._march = motion.animation(self, 700, self._set_dash, QEasingCurve(QEasingCurve.Linear), 0.0, 8.0)
        self._march.setLoopCount(-1)
        lay = vbox(self, (28, 36, 28, 36), 8)
        lay.addStretch(1)
        c = theme.colors()
        self.icon = motion.AnimIcon("folder_open", c["accent"], 40, dy=-4, stroke=1.75,
                                    curve=motion.settle_curve(1.3), duration=motion.STANDARD)
        lay.addWidget(self.icon, 0, Qt.AlignHCenter)
        lay.addSpacing(4)
        t = label(title, "SectionTitle")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        s = label(subtitle, "Muted", wrap=True)
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)
        lay.addSpacing(8)
        btn = button(button_text, "folder", "primary")
        btn.clicked.connect(self.browseClicked.emit)
        lay.addWidget(btn, 0, Qt.AlignHCenter)
        lay.addStretch(1)

    def _set_dash(self, v) -> None:
        self._dash = float(v)
        self.update()

    def _to_level(self, target: float) -> None:
        if self._level_anim is not None:
            self._level_anim.stop()
        if target >= 1.0 and motion.enabled():
            self._march.start()
        else:
            self._march.stop()
            self._dash = 0.0
        if not motion.enabled():
            self._level = target
            self.update()
            return

        def step(v):
            self._level = float(v)
            self.update()

        self._level_anim = motion.animation(self, motion.STANDARD, step, start=self._level, end=target)
        self._level_anim.start()

    def paintEvent(self, _event) -> None:
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(motion.mix(c["surface"], c["accent_soft"], self._level))
        pen = QPen(motion.mix(c["border_strong"], c["accent"], min(1.0, self._level * 1.6)), 1.5)
        pen.setDashPattern([4, 4])
        pen.setDashOffset(-self._dash)
        p.setPen(pen)
        p.drawRoundedRect(rect, 22, 22)
        p.end()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        if self._level < 1.0:
            self._to_level(0.35)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self._level < 1.0:
            self._to_level(0.0)

    def _set_hover(self, on: bool) -> None:
        self.icon.set_active(on)
        self._to_level(1.0 if on else 0.0)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_hover(True)

    def dragLeaveEvent(self, _event) -> None:
        self._set_hover(False)

    def dropEvent(self, event) -> None:
        self._set_hover(False)
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and os.path.isdir(path):
                paths.append(os.path.normpath(path))
        if paths:
            self.pathsDropped.emit(paths)


# ---------------------------------------------------------------------------
# Liste de dossiers
# ---------------------------------------------------------------------------
class FolderList(QWidget):
    changed = Signal()

    def __init__(self, add_text: str, empty_text: str, dialog_title: str, allow_files: bool = False,
                 max_height: int = 120):
        super().__init__()
        self.dialog_title = dialog_title
        self.setAcceptDrops(True)
        lay = vbox(self, spacing=6)
        self.list = QListWidget()
        self.list.setMaximumHeight(max_height)
        self.list.setMinimumHeight(60)
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.setIconSize(QSize(16, 16))
        lay.addWidget(self.list)
        self.empty = label(empty_text, "Faint")
        row = hbox(spacing=6)
        row.addWidget(button(add_text, "plus", on_click=self._add))
        self.remove_btn = button(tr("common.remove"), "trash", "ghost", on_click=self._remove)
        row.addWidget(self.remove_btn)
        row.addStretch(1)
        row.addWidget(self.empty)
        lay.addLayout(row)
        self._refresh()

    def _refresh(self) -> None:
        self.empty.setVisible(self.list.count() == 0)
        self.remove_btn.setEnabled(self.list.count() > 0)

    def paths(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def set_paths(self, paths: list[str]) -> None:
        self.list.clear()
        for p in paths:
            self.add_path(p, emit=False)
        self._refresh()

    def add_path(self, path: str, emit: bool = True) -> None:
        path = os.path.normpath(path)
        if path in self.paths():
            return
        item = QListWidgetItem(icons.icon("drive" if len(path) <= 3 else "folder"), path)
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        self.list.addItem(item)
        self._refresh()
        if emit:
            self.changed.emit()

    def _add(self) -> None:
        path = choose_folder(self, self.dialog_title)
        if path:
            self.add_path(path)

    def _remove(self) -> None:
        for item in self.list.selectedItems() or [self.list.item(self.list.count() - 1)]:
            if item is not None:
                self.list.takeItem(self.list.row(item))
        self._refresh()
        self.changed.emit()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and os.path.isdir(path):
                self.add_path(path)


# ---------------------------------------------------------------------------
# Progression d'une tâche
# ---------------------------------------------------------------------------
class ProgressPanel(Card):
    """Affiche l'avancement d'un TaskControl (lu toutes les 150 ms) avec pause / annulation."""

    cancelRequested = Signal()

    STAGES = ("scan", "analyze", "plan", "execute", "undo", "dup_partial", "dup_full")

    def __init__(self):
        super().__init__(margins=(24, 22, 24, 20), spacing=12)
        self.control = None
        top = hbox(spacing=12)
        self.loader = motion.SortLoader(26)
        top.addWidget(self.loader, 0, Qt.AlignVCenter)
        self.pause_icon = icon_label("pause", theme.colors()["text2"], 24)
        self.pause_icon.hide()
        top.addWidget(self.pause_icon, 0, Qt.AlignVCenter)
        self.stage = label("", "SectionTitle")
        top.addWidget(self.stage, 1)
        self.percent = label("", "StatValue")
        top.addWidget(self.percent)
        self.add(top)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self._bar_anim = QPropertyAnimation(self.bar, b"value", self)
        self._bar_anim.setDuration(420)
        self._bar_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.add(self.bar)
        stats = hbox(spacing=24)
        self.counts = label("", "Muted")
        self.speed = label("", "Muted")
        self.eta = label("", "Muted")
        self.errors = label("", "Muted")
        for w in (self.counts, self.speed, self.eta, self.errors):
            stats.addWidget(w)
        stats.addStretch(1)
        self.add(stats)
        self.current = label("", "Faint")
        self.current.setMinimumWidth(100)
        self.add(self.current)
        btns = hbox(spacing=8)
        btns.addStretch(1)
        self.pause_btn = button(tr("progress.pause"), "pause", on_click=self._toggle_pause)
        self.cancel_btn = button(tr("progress.cancel"), "stop", "danger", on_click=self._cancel)
        btns.addWidget(self.pause_btn)
        btns.addWidget(self.cancel_btn)
        self.add(btns)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)
        self._history: list[tuple[float, int]] = []

    def attach(self, control, allow_pause: bool = True) -> None:
        self.control = control
        self._history = []
        self._bar_anim.stop()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.pause_btn.setVisible(allow_pause)
        self.pause_btn.setText(tr("progress.pause"))
        self.pause_btn.setIcon(icons.icon("pause"))
        self.cancel_btn.setEnabled(True)
        self.refresh()
        self.timer.start()

    def detach(self) -> None:
        self.timer.stop()
        self.refresh()
        self.control = None

    def _toggle_pause(self) -> None:
        if not self.control:
            return
        if self.control.paused:
            self.control.resume()
            self.pause_btn.setText(tr("progress.pause"))
            self.pause_btn.setIcon(icons.icon("pause"))
        else:
            self.control.pause()
            self.pause_btn.setText(tr("progress.resume"))
            self.pause_btn.setIcon(icons.icon("play"))

    def _cancel(self) -> None:
        if self.control and confirm(self.window(), tr("progress.cancel_title"), tr("progress.cancel_text"),
                                    tr("progress.cancel"), tr("common.continue"), danger=True):
            self.control.cancel()
            self.cancel_btn.setEnabled(False)
            self.cancelRequested.emit()

    def refresh(self) -> None:
        if not self.control:
            return
        snap = self.control.snapshot()
        stage = snap["stage"] or "scan"
        title = tr(f"stage.{stage}")
        if snap["paused"]:
            title += " — " + tr("progress.paused")
        self.stage.setText(title)
        self.loader.setVisible(not snap["paused"])
        self.pause_icon.setVisible(bool(snap["paused"]))
        done, total = snap["done"], snap["total"]
        bdone, btotal = snap["bytes_done"], snap["bytes_total"]
        if btotal > 0 and stage in ("execute", "dup_full"):
            frac = min(1.0, bdone / btotal) if btotal else 0
            frac = max(frac, min(1.0, done / total) if total else 0) if stage == "execute" else frac
        elif total > 0:
            frac = min(1.0, done / total)
        else:
            frac = -1
        if frac < 0:
            self._bar_anim.stop()
            self.bar.setRange(0, 0)
            self.percent.setText("")
        else:
            self.bar.setRange(0, 1000)
            target = int(frac * 1000)
            if target > self.bar.value() and motion.enabled():
                if self._bar_anim.endValue() != target:
                    self._bar_anim.stop()
                    self._bar_anim.setStartValue(max(0, self.bar.value()))
                    self._bar_anim.setEndValue(target)
                    self._bar_anim.start()
            else:
                self._bar_anim.stop()
                self.bar.setValue(target)
            self.percent.setText(f"{int(frac * 100)} %")
        elapsed = max(0.001, snap["elapsed"])
        if total:
            self.counts.setText(tr("progress.items", done=fmt_int(done), total=fmt_int(total)))
        else:
            self.counts.setText(tr("progress.found", n=fmt_int(done)))
        rate_items = done / elapsed
        rate_bytes = bdone / elapsed
        speed = tr("progress.per_second", n=fmt_int(int(rate_items)))
        if bdone:
            speed += f" · {fmt_size(rate_bytes)}/s"
        self.speed.setText(speed)
        if 0 < frac < 1 and elapsed > 2:
            remaining = elapsed * (1 - frac) / frac
            self.eta.setText(tr("progress.eta", t=fmt_duration(remaining)))
        else:
            self.eta.setText(tr("progress.elapsed", t=fmt_duration(elapsed)))
        self.errors.setText(tr("progress.errors", n=snap["errors"]) if snap["errors"] else "")
        cur = snap["current"]
        self.current.setText(elide_middle(cur, max(200, self.current.width() - 10), self.current) if cur else "")
