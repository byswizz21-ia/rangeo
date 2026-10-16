"""Thèmes clair / sombre (feuille de style Qt) et détection du thème Windows.

Les valeurs viennent du design system Rangeo (tokens.json) : papier chaud, encre profonde, pétrole pour
l'action, abricot pour l'éclat. Correspondance des clés : bg = paper, sidebar = paper-deep,
surface2 = surface-sunk, surface3 = surface-press, border = line, border_strong = line-strong,
text* = ink*, accent* = petrol*, spark* = apricot*, success* = jade*, warning* = ochre*, danger* = brick*.
Les tailles sont en points (px × 0,75).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

LIGHT = {
    "bg": "#F6F5F0",
    "sidebar": "#EEECE5",
    "surface": "#FFFFFF",
    "surface2": "#F2F0EA",
    "surface3": "#E8E5DC",
    "border": "#E4E1D7",
    "border_strong": "#8F8A7F",
    "text": "#162125",
    "text2": "#4D595E",
    "text3": "#626C71",
    "accent": "#0F6B7A",
    "accent_hover": "#0C5C69",
    "accent_press": "#094D58",
    "accent_soft": "#DDEEEF",
    "accent_text": "#FFFFFF",
    "selection": "#CFE5E7",
    "spark": "#F29A4A",
    "spark_soft": "#FCEAD9",
    "spark_text": "#9E4D0B",
    "on_spark": "#162125",
    "success": "#0E7462",
    "success_soft": "#DDF1E8",
    "warning": "#8F5F00",
    "warning_soft": "#FBEFD3",
    "danger": "#BC3B2A",
    "danger_soft": "#FBE5E0",
    "scroll": "rgba(22, 33, 37, 0.20)",
    "scroll_hover": "rgba(22, 33, 37, 0.38)",
    "nav_hover": "rgba(22, 33, 37, 0.05)",
    "shadow": "#162125",
}

DARK = {
    "bg": "#0E1416",
    "sidebar": "#0A0F11",
    "surface": "#151C1F",
    "surface2": "#1B2327",
    "surface3": "#243035",
    "border": "#253034",
    "border_strong": "#66747A",
    "text": "#E9EEEF",
    "text2": "#A6B2B6",
    "text3": "#87949A",
    "accent": "#4DB4C2",
    "accent_hover": "#67C2CE",
    "accent_press": "#3A9DAB",
    "accent_soft": "#14343A",
    "accent_text": "#06171A",
    "selection": "#1A4148",
    "spark": "#F5A562",
    "spark_soft": "#3A2617",
    "spark_text": "#F7B47D",
    "on_spark": "#1C1006",
    "success": "#52C79E",
    "success_soft": "#10302A",
    "warning": "#E8B04A",
    "warning_soft": "#33280F",
    "danger": "#F07C66",
    "danger_soft": "#3B1E19",
    "scroll": "rgba(233, 238, 239, 0.18)",
    "scroll_hover": "rgba(233, 238, 239, 0.34)",
    "nav_hover": "rgba(233, 238, 239, 0.05)",
    "shadow": "#000000",
}

# Catégories de fichiers (carte de l'espace, légendes) : mêmes valeurs dans les deux thèmes.
CATEGORY = {
    "image": "#2F78A0", "video": "#BF4A30", "audio": "#8A4F86", "document": "#9C6410", "archive": "#3A7A55",
    "software": "#4F5D9E", "game": "#B03A63", "code": "#56692A", "font": "#86654A", "model3d": "#4F5D9E",
    "shortcut": "#68737A", "technical": "#68737A", "unknown": "#68737A", "bundle": "#68737A",
}

DISPLAY_FAMILY = '"Segoe UI Variable Display", "Segoe UI"'

_current: dict = LIGHT
_is_dark = False


def colors() -> dict:
    return _current


def is_dark() -> bool:
    return _is_dark


def system_prefers_dark() -> bool:
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def display_font(point_size: float, weight: int = QFont.Bold) -> QFont:
    """Police des titres et des chiffres (Segoe UI Variable Display, interlettrage serré)."""
    font = QFont("Segoe UI Variable Display")
    if not font.exactMatch():
        font = QFont("Segoe UI")
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    font.setLetterSpacing(QFont.PercentageSpacing, 98.5)
    return font


def stylesheet(c: dict) -> str:
    return f"""
* {{ outline: none; }}
QWidget {{ color: {c['text']}; font-size: 10pt; }}
QMainWindow, #Root {{ background: {c['bg']}; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']};
           padding: 6px 10px; border-radius: 8px; }}

/* ---------- Barre latérale ---------- */
#Sidebar {{ background: {c['sidebar']}; }}
#AppName {{ font-family: {DISPLAY_FAMILY}; font-size: 14.25pt; font-weight: 700; }}
#AppTag {{ color: {c['text2']}; font-size: 9pt; }}
#NavGroup {{ color: {c['text2']}; font-size: 8.25pt; font-weight: 600; padding: 14px 12px 4px 12px; }}
#NavButton {{ text-align: left; padding: 0 12px; min-height: 36px; max-height: 36px; border: none; border-radius: 10px;
             color: {c['text2']}; font-size: 10pt; background: transparent; }}
#NavButton:hover {{ background: {c['nav_hover']}; color: {c['text']}; }}
#NavButton:checked, #NavButton:checked:hover {{ background: transparent; color: {c['accent']}; font-weight: 600; }}
#SidebarFooter {{ color: {c['text2']}; font-size: 9pt; padding: 0 4px; }}
#BusyLabel {{ color: {c['accent']}; font-size: 9pt; font-weight: 600; }}

/* ---------- Pages ---------- */
#Page {{ background: {c['bg']}; }}
#Display {{ font-family: {DISPLAY_FAMILY}; font-size: 25.5pt; font-weight: 700; }}
#PageTitle {{ font-family: {DISPLAY_FAMILY}; font-size: 19.5pt; font-weight: 700; }}
#PageSubtitle {{ color: {c['text2']}; font-size: 10pt; }}
#SectionTitle {{ font-size: 11.25pt; font-weight: 600; }}
#Overline {{ color: {c['text2']}; font-size: 8.25pt; font-weight: 600; }}
#Muted {{ color: {c['text2']}; }}
#Faint {{ color: {c['text3']}; font-size: 9pt; }}
#Card, #ActionCard {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 16px; }}
#CardFlat {{ background: {c['surface2']}; border-radius: 16px; }}
#StatCard {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 16px; }}
#StatSpark {{ background: {c['spark_soft']}; border: 1px solid {c['spark_soft']}; border-radius: 16px; }}
#StatSpark #StatLabel {{ color: {c['spark_text']}; }}
#StatValue {{ font-family: {DISPLAY_FAMILY}; font-size: 20pt; font-weight: 600; }}
#StatLabel {{ color: {c['text2']}; font-size: 9pt; }}
#BigNumber {{ font-family: {DISPLAY_FAMILY}; font-size: 26pt; font-weight: 700; }}

#DropZone {{ background: transparent; border: none; }}

#Banner {{ border-radius: 16px; padding: 2px; }}
#Banner[level="info"] {{ background: {c['accent_soft']}; }}
#Banner[level="warning"] {{ background: {c['warning_soft']}; }}
#Banner[level="error"] {{ background: {c['danger_soft']}; }}
#Banner[level="success"] {{ background: {c['success_soft']}; }}

#Chip {{ border-radius: 11px; padding: 3px 10px; font-size: 9pt; font-weight: 600; }}
#Chip[level="info"] {{ background: {c['accent_soft']}; color: {c['accent']}; }}
#Chip[level="success"] {{ background: {c['success_soft']}; color: {c['success']}; }}
#Chip[level="warning"] {{ background: {c['warning_soft']}; color: {c['warning']}; }}
#Chip[level="error"] {{ background: {c['danger_soft']}; color: {c['danger']}; }}
#Chip[level="neutral"] {{ background: {c['surface3']}; color: {c['text2']}; }}

/* ---------- Boutons ---------- */
QPushButton {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px;
              padding: 7px 16px; color: {c['text']}; }}
QPushButton:hover {{ background: {c['surface2']}; }}
QPushButton:pressed {{ background: {c['surface3']}; }}
QPushButton:disabled {{ color: {c['text3']}; background: {c['surface2']}; border-color: {c['surface2']}; }}
QPushButton#Primary {{ background: {c['accent']}; border: 1px solid {c['accent']}; color: {c['accent_text']};
                      font-weight: 600; }}
QPushButton#Primary:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton#Primary:pressed {{ background: {c['accent_press']}; border-color: {c['accent_press']}; }}
QPushButton#Primary:disabled {{ background: {c['surface3']}; border-color: {c['surface3']}; color: {c['text3']}; }}
QPushButton#Danger {{ color: {c['danger']}; border-color: {c['danger']}; background: transparent; }}
QPushButton#Danger:hover {{ background: {c['danger_soft']}; }}
QPushButton#Danger:disabled {{ color: {c['text3']}; border-color: {c['surface3']}; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {c['text2']}; padding: 6px 10px; }}
QPushButton#Ghost:hover {{ background: {c['surface2']}; color: {c['text']}; }}
QPushButton#Ghost:disabled {{ color: {c['text3']}; background: transparent; }}
QPushButton#Link {{ background: transparent; border: none; color: {c['accent']}; padding: 2px 4px; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QToolButton {{ background: transparent; border: none; border-radius: 8px; padding: 4px; }}
QToolButton:hover {{ background: {c['surface2']}; }}
QToolButton::menu-indicator {{ image: none; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; right: 8px; }}

/* ---------- Champs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QPlainTextEdit, QTextEdit {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-bottom: 1px solid {c['border_strong']};
    border-radius: 10px; padding: 6px 10px;
    selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover, QDateEdit:hover {{ background: {c['surface2']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus,
QDateEdit:focus {{ border: 1px solid {c['accent']}; background: {c['surface']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {c['text3']}; background: {c['surface2']};
    border-color: {c['surface2']}; }}
QLineEdit[error="true"] {{ border: 1px solid {c['danger']}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({c.get('down_url', '')}); width: 13px; height: 13px; }}
QDateEdit::drop-down {{ border: none; width: 28px; }}
QDateEdit::down-arrow {{ image: url({c.get('down_url', '')}); width: 13px; height: 13px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px;
    selection-background-color: {c['selection']}; selection-color: {c['text']}; outline: none; padding: 4px; }}
QSpinBox {{ padding-right: 24px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}
QSpinBox::up-arrow {{ image: url({c.get('up_url', '')}); width: 11px; height: 11px; }}
QSpinBox::down-arrow {{ image: url({c.get('down_url', '')}); width: 11px; height: 11px; }}

QCheckBox, QRadioButton {{ spacing: 9px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; }}
QCheckBox::indicator {{ border: 1.5px solid {c['border_strong']}; border-radius: 5px; background: {c['surface']}; }}
QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']};
    image: url({c.get('check_url', '')}); }}
QCheckBox::indicator:indeterminate {{ background: {c['accent_soft']}; border-color: {c['accent']}; }}
QCheckBox::indicator:disabled {{ border-color: {c['surface3']}; background: {c['surface2']}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 2px solid {c['border_strong']}; border-radius: 9px;
    background: {c['surface']}; }}
QRadioButton::indicator:hover {{ border-color: {c['accent']}; }}
QRadioButton::indicator:checked {{ border: 2px solid {c['accent']};
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {c['accent']},
                                stop:0.5 {c['accent']}, stop:0.58 {c['surface']}, stop:1 {c['surface']}); }}

/* ---------- Listes et tableaux ---------- */
QListWidget, QTreeWidget, QTreeView, QTableView, QListView {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px;
    alternate-background-color: {c['surface2']}; selection-background-color: {c['selection']};
    selection-color: {c['text']}; padding: 3px; }}
QTreeView::item, QListView::item, QTableView::item {{ padding: 5px 4px; border: none; }}
QTreeView::item:hover, QListView::item:hover {{ background: {c['surface2']}; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
    background: {c['selection']}; color: {c['text']}; }}
QTreeView::indicator, QTableView::indicator, QListView::indicator {{ width: 16px; height: 16px;
    border: 1.5px solid {c['border_strong']}; border-radius: 4px; background: {c['surface']}; }}
QTreeView::indicator:checked, QTableView::indicator:checked, QListView::indicator:checked {{
    background: {c['accent']}; border-color: {c['accent']}; image: url({c.get('check_url', '')}); }}
QTreeView::indicator:indeterminate, QTableView::indicator:indeterminate {{
    background: {c['accent_soft']}; border-color: {c['accent']}; }}
QHeaderView::section {{ background: {c['surface']}; color: {c['text2']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 7px 8px; font-weight: 600; font-size: 9pt; }}
QTableCornerButton::section {{ background: {c['surface']}; border: none; }}

/* ---------- Onglets (rail et pastille dessinés par motion.AnimatedTabBar) ---------- */
QTabWidget::pane {{ border: none; }}
QTabWidget::tab-bar {{ left: 0px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {c['text2']}; padding: 7px 12px; border: none;
    margin: 3px 2px; border-radius: 10px; }}
QTabBar::tab:first {{ margin-left: 3px; }}
QTabBar::tab:last {{ margin-right: 3px; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{ color: {c['text']}; }}
QTabBar QToolButton {{ background: {c['bg']}; border-radius: 8px; }}

/* ---------- Progression ---------- */
QProgressBar {{ background: {c['surface3']}; border: none; border-radius: 4px; height: 8px; text-align: center;
    color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
QProgressBar#Thin {{ max-height: 6px; border-radius: 3px; }}
QProgressBar#Thin::chunk {{ border-radius: 3px; }}
QProgressBar#Thin[high="true"]::chunk {{ background: {c['danger']}; }}

/* ---------- Défilement ---------- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px 2px; }}
QScrollBar::handle:vertical {{ background: {c['scroll']}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {c['scroll_hover']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 3px; }}
QScrollBar::handle:horizontal {{ background: {c['scroll']}; border-radius: 3px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: {c['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:vertical {{ height: 10px; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 8px; }}
QMenu::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 5px 8px; }}
QDialog {{ background: {c['bg']}; }}
QMessageBox {{ background: {c['surface']}; }}
QFrame#Separator {{ background: {c['border']}; max-height: 1px; min-height: 1px; border: none; }}
"""


def apply_theme(app: QApplication, mode: str = "system") -> None:
    global _current, _is_dark
    _is_dark = system_prefers_dark() if mode == "system" else mode == "dark"
    _current = dict(DARK if _is_dark else LIGHT)
    c = _current
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(c["bg"]))
    palette.setColor(QPalette.WindowText, QColor(c["text"]))
    palette.setColor(QPalette.Base, QColor(c["surface"]))
    palette.setColor(QPalette.AlternateBase, QColor(c["surface2"]))
    palette.setColor(QPalette.Text, QColor(c["text"]))
    palette.setColor(QPalette.Button, QColor(c["surface"]))
    palette.setColor(QPalette.ButtonText, QColor(c["text"]))
    palette.setColor(QPalette.Highlight, QColor(c["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor(c["accent_text"]))
    palette.setColor(QPalette.ToolTipBase, QColor(c["surface"]))
    palette.setColor(QPalette.ToolTipText, QColor(c["text"]))
    palette.setColor(QPalette.PlaceholderText, QColor(c["text3"]))
    palette.setColor(QPalette.Link, QColor(c["accent"]))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(c["text3"]))
    app.setPalette(palette)
    font = QFont("Segoe UI Variable Text")
    if not font.exactMatch():
        font = QFont("Segoe UI")
    font.setPointSizeF(10)
    app.setFont(font)
    from . import icons

    icons.register_resources(c)
    app.setStyleSheet(stylesheet(c))
