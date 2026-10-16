"""Carte de l'espace disque (treemap « squarified »)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QMenu, QToolTip, QWidget

from . import theme
from .i18n import fmt_size, tr

GROUP_COLORS = {
    "image": "#4F8DF5", "video": "#E5484D", "audio": "#8E4EC6", "document": "#F2A33A", "archive": "#12A594",
    "software": "#6E56CF", "game": "#E54666", "code": "#0091FF", "font": "#AD7F58", "model3d": "#3E63DD",
    "shortcut": "#8B8D98", "technical": "#7C8496", "unknown": "#A5ABB8", "bundle": "#5B6475",
}


def squarify(values: list[float], x: float, y: float, w: float, h: float) -> list[QRectF]:
    """Algorithme de Bruls, Huizing et van Wijk : rectangles proches du carré, dans l'ordre des valeurs."""
    rects: list[QRectF] = []
    total = sum(values)
    if total <= 0 or w <= 0 or h <= 0:
        return [QRectF() for _ in values]
    scale = (w * h) / total
    areas = [v * scale for v in values]

    def worst(row, length):
        s = sum(row)
        if s <= 0:
            return float("inf")
        return max(max(length * length * r / (s * s), (s * s) / (length * length * r)) for r in row if r > 0)

    i = 0
    while i < len(areas):
        length = min(w, h)
        row = [areas[i]]
        i += 1
        while i < len(areas) and worst(row + [areas[i]], length) <= worst(row, length):
            row.append(areas[i])
            i += 1
        s = sum(row)
        if w >= h:  # colonne à gauche
            col_w = s / h if h else 0
            cy = y
            for a in row:
                rh = a / col_w if col_w else 0
                rects.append(QRectF(x, cy, col_w, rh))
                cy += rh
            x += col_w
            w -= col_w
        else:  # ligne en haut
            row_h = s / w if w else 0
            cx = x
            for a in row:
                rw = a / row_h if row_h else 0
                rects.append(QRectF(cx, y, rw, row_h))
                cx += rw
            y += row_h
            h -= row_h
    return rects


class Treemap(QWidget):
    """Affiche les sous-dossiers d'un dossier, proportionnellement à leur taille."""

    folderOpened = Signal(object)  # DirStat
    revealRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumHeight(320)
        self.node = None
        self.items: list[tuple[QRectF, object, str, int, str]] = []  # (rect, noeud|None, nom, taille, groupe)

    def set_node(self, node) -> None:
        self.node = node
        self._layout()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout()

    def _layout(self) -> None:
        self.items = []
        node = self.node
        if node is None:
            return
        entries = [(c.size, c, c.name, c.dominant_group()) for c in node.children if c.size > 0]
        if node.own_size > 0:
            dominant = max(node.groups, key=node.groups.get) if node.groups else "unknown"
            entries.append((node.own_size, None, tr("analysis.loose_files", n=node.own_files), dominant))
        entries.sort(key=lambda e: -e[0])
        # les éléments minuscules sont regroupés pour garder une carte lisible
        total = sum(e[0] for e in entries) or 1
        main = [e for e in entries if e[0] / total >= 0.002][:300]
        rest = sum(e[0] for e in entries) - sum(e[0] for e in main)
        if rest > 0:
            main.append((rest, None, tr("analysis.others"), "unknown"))
        rects = squarify([e[0] for e in main], 2, 2, self.width() - 4, self.height() - 4)
        for rect, (size, child, name, group) in zip(rects, main):
            self.items.append((rect, child, name, size, group))

    def _item_at(self, pos):
        for item in self.items:
            if item[0].contains(pos):
                return item
        return None

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = theme.colors()
        p.fillRect(self.rect(), QColor(c["surface"]))
        if not self.items:
            p.setPen(QColor(c["text3"]))
            p.drawText(self.rect(), Qt.AlignCenter, tr("analysis.empty_map"))
            return
        font = QFont(self.font())
        font.setPointSizeF(9)
        p.setFont(font)
        for rect, child, name, size, group in self.items:
            r = rect.adjusted(1.5, 1.5, -1.5, -1.5)
            if r.width() <= 0 or r.height() <= 0:
                continue
            color = QColor(GROUP_COLORS.get(group, "#A5ABB8"))
            if child is None:
                color = color.lighter(125)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(r, 5, 5)
            if r.width() > 70 and r.height() > 34:
                p.setPen(QPen(QColor("#FFFFFF")))
                text_rect = r.adjusted(7, 5, -7, -5)
                metrics = p.fontMetrics()
                p.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop,
                           metrics.elidedText(name, Qt.ElideRight, int(text_rect.width())))
                if r.height() > 48:
                    p.drawText(text_rect.adjusted(0, metrics.height() + 1, 0, 0), Qt.AlignLeft | Qt.AlignTop,
                               fmt_size(size))
        p.end()

    def mouseMoveEvent(self, event) -> None:
        item = self._item_at(event.position())
        if item is None:
            QToolTip.hideText()
            self.setCursor(Qt.ArrowCursor)
            return
        rect, child, name, size, group = item
        self.setCursor(Qt.PointingHandCursor if child is not None and child.children else Qt.ArrowCursor)
        total = self.node.size or 1
        QToolTip.showText(event.globalPosition().toPoint(),
                          f"<b>{name}</b><br>{fmt_size(size)} · {size * 100 / total:.1f} %<br>{tr('group.' + group)}",
                          self)

    def mouseReleaseEvent(self, event) -> None:
        item = self._item_at(event.position())
        if item is None:
            return
        child = item[1]
        if event.button() == Qt.LeftButton and child is not None and (child.children or child.own_files):
            self.folderOpened.emit(child)
        elif event.button() == Qt.RightButton:
            path = child.path if child is not None else (self.node.path if self.node else "")
            if path:
                menu = QMenu(self)
                menu.addAction(tr("preview.open_existing"), lambda: self.revealRequested.emit(path))
                menu.exec(event.globalPosition().toPoint())
