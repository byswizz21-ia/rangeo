"""Thèmes clair / sombre (feuille de style Qt) et détection du thème Windows."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

LIGHT = {
    "bg": "#F4F5F9",
    "surface": "#FFFFFF",
    "surface2": "#F0F2F7",
    "surface3": "#E7EAF2",
    "border": "#E1E4EC",
    "border_strong": "#CDD2DE",
    "text": "#1B2130",
    "text2": "#5A6377",
    "text3": "#8790A3",
    "accent": "#4F63F5",
    "accent_hover": "#3F52E6",
    "accent_press": "#3444C8",
    "accent_soft": "#E9ECFF",
    "accent_text": "#FFFFFF",
    "success": "#18935F",
    "success_soft": "#E2F5EC",
    "warning": "#C27A00",
    "warning_soft": "#FFF3DC",
    "danger": "#D33F43",
    "danger_soft": "#FDE8E8",
    "sidebar": "#FFFFFF",
    "selection": "#DDE2FF",
    "shadow": "#1B213010",
}

DARK = {
    "bg": "#111319",
    "surface": "#191C24",
    "surface2": "#20242E",
    "surface3": "#2A2F3B",
    "border": "#2A2F3B",
    "border_strong": "#3A4050",
    "text": "#E7E9F0",
    "text2": "#A0A8BA",
    "text3": "#727B8F",
    "accent": "#6E80FF",
    "accent_hover": "#8090FF",
    "accent_press": "#5A6CF0",
    "accent_soft": "#262C4A",
    "accent_text": "#FFFFFF",
    "success": "#3CC48A",
    "success_soft": "#183326",
    "warning": "#F0A63A",
    "warning_soft": "#3A2E17",
    "danger": "#F06467",
    "danger_soft": "#3D1E21",
    "sidebar": "#151820",
    "selection": "#2D3560",
    "shadow": "#00000040",
}

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


def stylesheet(c: dict) -> str:
    return f"""
* {{ outline: none; }}
QWidget {{ color: {c['text']}; font-size: 10pt; }}
QMainWindow, #Root {{ background: {c['bg']}; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border_strong']};
           padding: 6px 8px; border-radius: 6px; }}

/* ---------- Barre latérale ---------- */
#Sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
#AppName {{ font-size: 15pt; font-weight: 700; }}
#AppTag {{ color: {c['text3']}; font-size: 8.5pt; }}
#NavButton {{ text-align: left; padding: 9px 12px; border: none; border-radius: 8px; color: {c['text2']};
             font-size: 10pt; background: transparent; }}
#NavButton:hover {{ background: {c['surface2']}; color: {c['text']}; }}
#NavButton:checked {{ background: {c['accent_soft']}; color: {c['accent']}; font-weight: 600; }}
#SidebarFooter {{ color: {c['text3']}; font-size: 8.5pt; }}

/* ---------- Pages ---------- */
#Page {{ background: {c['bg']}; }}
#PageTitle {{ font-size: 19pt; font-weight: 700; }}
#PageSubtitle {{ color: {c['text2']}; font-size: 10pt; }}
#SectionTitle {{ font-size: 11.5pt; font-weight: 650; }}
#Muted {{ color: {c['text2']}; }}
#Faint {{ color: {c['text3']}; font-size: 9pt; }}
#Card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
#CardFlat {{ background: {c['surface2']}; border-radius: 10px; }}
#StatValue {{ font-size: 18pt; font-weight: 700; }}
#StatLabel {{ color: {c['text2']}; font-size: 9pt; }}
#BigNumber {{ font-size: 26pt; font-weight: 750; }}

#DropZone {{ background: {c['surface']}; border: 2px dashed {c['border_strong']}; border-radius: 14px; }}
#DropZone[hover="true"] {{ border-color: {c['accent']}; background: {c['accent_soft']}; }}

#Banner {{ border-radius: 10px; padding: 2px; }}
#Banner[level="info"] {{ background: {c['accent_soft']}; }}
#Banner[level="warning"] {{ background: {c['warning_soft']}; }}
#Banner[level="error"] {{ background: {c['danger_soft']}; }}
#Banner[level="success"] {{ background: {c['success_soft']}; }}

#Chip {{ border-radius: 9px; padding: 2px 9px; font-size: 8.5pt; font-weight: 600; }}
#Chip[level="info"] {{ background: {c['accent_soft']}; color: {c['accent']}; }}
#Chip[level="success"] {{ background: {c['success_soft']}; color: {c['success']}; }}
#Chip[level="warning"] {{ background: {c['warning_soft']}; color: {c['warning']}; }}
#Chip[level="error"] {{ background: {c['danger_soft']}; color: {c['danger']}; }}
#Chip[level="neutral"] {{ background: {c['surface3']}; color: {c['text2']}; }}

/* ---------- Boutons ---------- */
QPushButton {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 8px;
              padding: 7px 14px; color: {c['text']}; }}
QPushButton:hover {{ background: {c['surface2']}; }}
QPushButton:pressed {{ background: {c['surface3']}; }}
QPushButton:disabled {{ color: {c['text3']}; background: {c['surface2']}; border-color: {c['border']}; }}
QPushButton#Primary {{ background: {c['accent']}; border: 1px solid {c['accent']}; color: {c['accent_text']};
                      font-weight: 600; }}
QPushButton#Primary:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton#Primary:pressed {{ background: {c['accent_press']}; }}
QPushButton#Primary:disabled {{ background: {c['surface3']}; border-color: {c['surface3']}; color: {c['text3']}; }}
QPushButton#Big {{ padding: 11px 22px; font-size: 11pt; }}
QPushButton#Danger {{ color: {c['danger']}; border-color: {c['danger']}; background: transparent; }}
QPushButton#Danger:hover {{ background: {c['danger_soft']}; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {c['text2']}; padding: 6px 8px; }}
QPushButton#Ghost:hover {{ background: {c['surface2']}; color: {c['text']}; }}
QPushButton#Link {{ background: transparent; border: none; color: {c['accent']}; padding: 2px; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {c['surface2']}; }}
QToolButton::menu-indicator {{ image: none; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; right: 8px; }}

/* ---------- Champs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QPlainTextEdit, QTextEdit {{
    background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 7px; padding: 6px 8px;
    selection-background-color: {c['accent']}; selection-color: white; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QDateEdit:focus {{
    border: 1px solid {c['accent']}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {c['text3']}; background: {c['surface2']}; }}
QLineEdit[error="true"] {{ border: 1px solid {c['danger']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({c.get('down_url', '')}); width: 13px; height: 13px; }}
QDateEdit::drop-down {{ border: none; width: 26px; }}
QDateEdit::down-arrow {{ image: url({c.get('down_url', '')}); width: 13px; height: 13px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border_strong']};
    selection-background-color: {c['selection']}; selection-color: {c['text']}; outline: none; padding: 4px; }}
QSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}
QSpinBox::up-arrow {{ image: url({c.get('up_url', '')}); width: 11px; height: 11px; }}
QSpinBox::down-arrow {{ image: url({c.get('down_url', '')}); width: 11px; height: 11px; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 17px; height: 17px; }}
QCheckBox::indicator {{ border: 1.5px solid {c['border_strong']}; border-radius: 5px; background: {c['surface']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']};
    image: url({c.get('check_url', '')}); }}
QCheckBox::indicator:indeterminate {{ background: {c['accent_soft']}; border-color: {c['accent']}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 2px solid {c['border_strong']}; border-radius: 9px;
    background: {c['surface']}; }}
QRadioButton::indicator:checked {{ border: 2px solid {c['accent']};
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {c['accent']},
                                stop:0.55 {c['accent']}, stop:0.62 {c['surface']}, stop:1 {c['surface']}); }}

/* ---------- Listes et tableaux ---------- */
QListWidget, QTreeWidget, QTreeView, QTableView, QListView {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px;
    alternate-background-color: {c['surface2']}; selection-background-color: {c['selection']};
    selection-color: {c['text']}; padding: 2px; }}
QTreeView::item, QListView::item, QTableView::item {{ padding: 4px 4px; border: none; }}
QTreeView::item:hover, QListView::item:hover {{ background: {c['surface2']}; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
    background: {c['selection']}; color: {c['text']}; }}
QHeaderView::section {{ background: {c['surface2']}; color: {c['text2']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 6px 8px; font-weight: 600; font-size: 9pt; }}
QTableCornerButton::section {{ background: {c['surface2']}; border: none; }}

/* ---------- Onglets ---------- */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {c['text2']}; padding: 8px 14px; border: none;
    border-bottom: 2px solid transparent; margin-right: 4px; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; font-weight: 600; }}

/* ---------- Progression ---------- */
QProgressBar {{ background: {c['surface3']}; border: none; border-radius: 5px; height: 10px; text-align: center;
    color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}
QProgressBar#Thin {{ max-height: 6px; border-radius: 3px; }}
QProgressBar#Thin::chunk {{ border-radius: 3px; }}

/* ---------- Défilement ---------- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['text3']}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border_strong']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c['selection']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QDialog {{ background: {c['bg']}; }}
QMessageBox {{ background: {c['surface']}; }}
QFrame#Separator {{ background: {c['border']}; max-height: 1px; min-height: 1px; border: none; }}
"""


def apply_theme(app: QApplication, mode: str = "system") -> None:
    global _current, _is_dark
    _is_dark = system_prefers_dark() if mode == "system" else mode == "dark"
    _current = DARK if _is_dark else LIGHT
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
    palette.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
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
