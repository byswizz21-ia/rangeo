"""Mouvement : transitions de page, pastilles qui glissent, pile qui se trie, icônes animées.

Les durées et courbes suivent le design system (section Mouvement) : sortie douce (OutCubic) pour presque
tout, léger dépassement (OutBack) seulement pour ce qui se pose à sa place. Si Windows demande moins
d'animations (« Effets d'animation » désactivé) ou en rendu hors écran, tout devient instantané.
"""

from __future__ import annotations

import os
from functools import lru_cache

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QStackedWidget, QTabBar, QTabWidget, QWidget

from . import icons, theme

INSTANT, FAST, STANDARD, GLIDE, SETTLE, LOOP = 90, 160, 240, 280, 600, 1800


@lru_cache(maxsize=1)
def enabled() -> bool:
    forced = os.environ.get("RANGEO_ANIMATIONS")
    if forced in ("0", "1"):
        return forced == "1"
    if QGuiApplication.platformName() in ("offscreen", "minimal"):
        return False
    try:
        import ctypes

        value = ctypes.c_int(1)
        # SPI_GETCLIENTAREAANIMATION : « Afficher les animations dans Windows »
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
            return bool(value.value)
    except Exception:
        pass
    return True


def out_cubic() -> QEasingCurve:
    return QEasingCurve(QEasingCurve.OutCubic)


def settle_curve(overshoot: float = 1.2) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.OutBack)
    curve.setOvershoot(overshoot)
    return curve


def animation(parent, duration: int, on_value, curve: QEasingCurve | None = None, start=0.0, end=1.0,
              on_done=None) -> QVariantAnimation:
    anim = QVariantAnimation(parent)
    anim.setDuration(duration)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(curve or out_cubic())
    anim.valueChanged.connect(on_value)
    if on_done is not None:
        anim.finished.connect(on_done)
    return anim


def mix(a: str | QColor, b: str | QColor, t: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
        round(ca.alpha() + (cb.alpha() - ca.alpha()) * t),
    )


def _lerp_rect(a: QRectF, b: QRectF, t: float) -> QRectF:
    return QRectF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t,
                  a.width() + (b.width() - a.width()) * t, a.height() + (b.height() - a.height()) * t)


# ---------------------------------------------------------------------------
# Changement de page : la nouvelle page apparaît en montant de 12px
# ---------------------------------------------------------------------------
class _FadeOverlay(QWidget):
    def __init__(self, parent: QWidget, old_pm, new_pm, offset: int):
        super().__init__(parent)
        self.old_pm, self.new_pm, self.offset, self.t = old_pm, new_pm, offset, 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.colors()["bg"]))
        old_alpha = max(0.0, 1.0 - self.t * 2.5)
        if old_alpha > 0 and self.old_pm is not None:
            p.setOpacity(old_alpha)
            p.drawPixmap(0, 0, self.old_pm)
        p.setOpacity(min(1.0, self.t * 1.4))
        p.drawPixmap(QPointF(0, self.offset * (1.0 - self.t)), self.new_pm)
        p.end()


class FadeStack(QStackedWidget):
    """QStackedWidget dont les changements de page sont animés (fondu + montée)."""

    def __init__(self, parent=None, offset: int = 12, duration: int = STANDARD):
        super().__init__(parent)
        self._offset, self._duration = offset, duration
        self._overlay: _FadeOverlay | None = None
        self._anim: QVariantAnimation | None = None

    def _finish(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if self._overlay is not None:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        widget = self.widget(index)
        if widget is not None:
            self.setCurrentWidget(widget)

    def setCurrentWidget(self, widget: QWidget) -> None:  # noqa: N802
        old = self.currentWidget()
        self._finish()
        if widget is old or old is None or not enabled() or not self.isVisible() or self.width() < 10:
            super().setCurrentWidget(widget)
            return
        old_pm = old.grab()
        super().setCurrentWidget(widget)
        if widget.geometry() != self.rect():
            widget.setGeometry(self.rect())
        QApplication.sendPostedEvents(None, QEvent.LayoutRequest)
        new_pm = widget.grab()
        overlay = _FadeOverlay(self, old_pm, new_pm, self._offset)
        overlay.show()
        overlay.raise_()
        self._overlay = overlay

        def step(v):
            overlay.t = float(v)
            overlay.update()

        self._anim = animation(self, self._duration, step, on_done=self._finish)
        self._anim.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._finish()


# ---------------------------------------------------------------------------
# Pastille qui glisse (navigation)
# ---------------------------------------------------------------------------
class NavPill(QWidget):
    """Fond arrondi `accent_soft` qui glisse jusqu'à l'élément actif de la barre latérale."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._rect = QRectF()
        self._from = QRectF()
        self._target: QWidget | None = None
        self._t = 1.0
        self._anim: QVariantAnimation | None = None
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.lower()

    def _target_rect(self) -> QRectF:
        if self._target is None:
            return QRectF()
        return QRectF(self._target.geometry())

    def follow(self, widget: QWidget, animate: bool = True) -> None:
        if widget is self._target:
            return
        if self._target is not None:
            self._target.removeEventFilter(self)
        current = self._current_rect()
        self._target = widget
        widget.installEventFilter(self)
        if self._anim is not None:
            self._anim.stop()
        if not animate or not enabled() or current.isNull() or not self.isVisible():
            self._t = 1.0
            self.update()
            return
        self._from = current
        self._t = 0.0

        def step(v):
            self._t = float(v)
            self.update()

        self._anim = animation(self, GLIDE, step, settle_curve(0.9))
        self._anim.start()

    def _current_rect(self) -> QRectF:
        target = self._target_rect()
        if self._t >= 1.0 or self._from.isNull():
            return target
        return _lerp_rect(self._from, target, self._t)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parent() and event.type() == QEvent.Resize:
            self.setGeometry(self.parent().rect())
        elif obj is self._target and event.type() in (QEvent.Move, QEvent.Resize):
            self.update()
        return False

    def paintEvent(self, _event) -> None:
        rect = self._current_rect()
        if rect.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.colors()["accent_soft"]))
        p.drawRoundedRect(rect, 10, 10)
        p.end()


# ---------------------------------------------------------------------------
# La pile qui se trie (attente)
# ---------------------------------------------------------------------------
def _smooth(x: float) -> float:
    return 4 * x * x * x if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


class SortLoader(QWidget):
    """Trois barres dans le désordre se trient, puis le bloc abricot se pose (boucle de 1,8 s)."""

    SHUFFLED = (0.46, 1.0, 0.70)
    SORTED = (1.0, 0.70, 0.40)

    def __init__(self, size: int = 26, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._t = 0.5
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(LOOP)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def _tick(self, v) -> None:
        self._t = float(v)
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if enabled():
            self._anim.start()
        else:
            self._t = 0.7

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._anim.stop()

    def _state(self, t: float):
        if t < 0.18:
            k = 0.0
        elif t < 0.42:
            k = _smooth((t - 0.18) / 0.24)
        elif t < 0.86:
            k = 1.0
        else:
            k = 1.0 - _smooth((t - 0.86) / 0.14)
        widths = [a + (b - a) * k for a, b in zip(self.SHUFFLED, self.SORTED)]
        if t < 0.44:
            drop, alpha = 1.0, 0.0
        elif t < 0.62:
            x = (t - 0.44) / 0.18
            drop, alpha = 1.0 - settle_curve(1.4).valueForProgress(x), min(1.0, x * 2)
        elif t < 0.84:
            drop, alpha = 0.0, 1.0
        elif t < 0.94:
            drop, alpha = 0.0, 1.0 - (t - 0.84) / 0.10
        else:
            drop, alpha = 0.0, 0.0
        return widths, drop, alpha

    def paintEvent(self, _event) -> None:
        c = theme.colors()
        s = float(self._size)
        widths, drop, alpha = self._state(self._t)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        bar_h = max(2.0, round(s * 0.16))
        p.setBrush(QColor(c["accent"]))
        for w, top in zip(widths, (0.12, 0.42, 0.72)):
            p.drawRoundedRect(QRectF(0, round(s * top), s * w, bar_h), bar_h / 2, bar_h / 2)
        if alpha > 0:
            b = max(3.0, round(s * 0.26))
            y = s * 0.72 + bar_h / 2 - b / 2 - drop * b * 1.4
            p.setOpacity(alpha)
            p.setBrush(QColor(c["spark"]))
            p.drawRoundedRect(QRectF(s * 0.52, y, b, b), b * 0.3, b * 0.3)
        p.end()


# ---------------------------------------------------------------------------
# Icône qui bouge (zone de dépôt, flèche des cartes d'action)
# ---------------------------------------------------------------------------
class AnimIcon(QWidget):
    def __init__(self, name: str, color: str, size: int = 20, dx: int = 0, dy: int = 0, stroke: float = 2.0,
                 hover_color: str | None = None, curve: QEasingCurve | None = None, duration: int = FAST):
        super().__init__()
        self.name, self.color, self.hover_color = name, color, hover_color
        self._size, self.dx, self.dy, self.stroke = size, dx, dy, stroke
        self._p = 0.0
        self._curve, self._duration = curve, duration
        self._anim: QVariantAnimation | None = None
        self.setFixedSize(size + abs(dx), size + abs(dy))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_active(self, on: bool) -> None:
        target = 1.0 if on else 0.0
        if self._anim is not None:
            self._anim.stop()
        if not enabled():
            self._p = target
            self.update()
            return

        def step(v):
            self._p = float(v)
            self.update()

        self._anim = animation(self, self._duration, step, self._curve, self._p, target)
        self._anim.start()

    def paintEvent(self, _event) -> None:
        color = mix(self.color, self.hover_color, self._p).name() if self.hover_color else self.color
        pm = icons.pixmap(self.name, color, self._size, self.stroke)
        x = (0 if self.dx >= 0 else abs(self.dx)) + self.dx * self._p
        y = (0 if self.dy >= 0 else abs(self.dy)) + self.dy * self._p
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(QPointF(x, y), pm)
        p.end()


# ---------------------------------------------------------------------------
# Onglets : rail en creux et pastille qui glisse
# ---------------------------------------------------------------------------
class AnimatedTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(False)
        self._from = QRectF()
        self._t = 1.0
        self._anim: QVariantAnimation | None = None
        self.currentChanged.connect(self._changed)

    def _tab_rect(self, index: int) -> QRectF:
        if index < 0 or index >= self.count():
            return QRectF()
        return QRectF(self.tabRect(index)).adjusted(2, 3, -2, -3)

    def _pill(self) -> QRectF:
        target = self._tab_rect(self.currentIndex())
        if self._t >= 1.0 or self._from.isNull():
            return target
        return _lerp_rect(self._from, target, self._t)

    def _changed(self, _index: int) -> None:
        current = self._pill()
        if self._anim is not None:
            self._anim.stop()
        if not enabled() or current.isNull() or not self.isVisible():
            self._t = 1.0
            self.update()
            return
        self._from = current
        self._t = 0.0

        def step(v):
            self._t = float(v)
            self.update()

        self._anim = animation(self, GLIDE, step, settle_curve(0.9))
        self._anim.start()

    def paintEvent(self, event) -> None:
        c = theme.colors()
        if self.count():
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            rail = QRectF(self.tabRect(0)).united(QRectF(self.tabRect(self.count() - 1)))
            path = QPainterPath()
            path.addRoundedRect(rail, 13, 13)
            p.fillPath(path, QColor(c["surface2"]))
            pill = self._pill()
            if not pill.isNull():
                p.setPen(QColor(c["border"]))
                p.setBrush(QColor(c["surface3"] if theme.is_dark() else c["surface"]))
                p.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
            p.end()
        super().paintEvent(event)


class TabWidget(QTabWidget):
    """QTabWidget avec la barre d'onglets animée de Rangeo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabBar(AnimatedTabBar(self))
