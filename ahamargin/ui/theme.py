"""Visual theme: palettes, colours and the application stylesheet."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    name: str
    window: str
    surface: str
    surface_alt: str
    surface_sunken: str
    border: str
    border_strong: str
    text: str
    text_dim: str
    text_faint: str
    accent: str
    accent_text: str
    accent_soft: str
    danger: str
    selection: str
    paper_shadow: str


LIGHT = Theme(
    name="light",
    window="#eceae6",
    surface="#ffffff",
    surface_alt="#f7f6f3",
    surface_sunken="#e4e2dd",
    border="#dcd8d1",
    border_strong="#c3bdb3",
    text="#23211d",
    text_dim="#6b665e",
    text_faint="#9a948a",
    accent="#2f6fed",
    accent_text="#ffffff",
    accent_soft="#dce7ff",
    danger="#d1462f",
    selection="#2f6fed",
    paper_shadow="#c9c4ba",
)

DARK = Theme(
    name="dark",
    window="#1a1c20",
    surface="#22262b",
    surface_alt="#282d33",
    surface_sunken="#15171a",
    border="#343a41",
    border_strong="#454d56",
    text="#e7eaee",
    text_dim="#a2abb6",
    text_faint="#6f7883",
    accent="#6ea8fe",
    accent_text="#10151c",
    accent_soft="#243449",
    danger="#ff7b63",
    selection="#6ea8fe",
    paper_shadow="#0d0f12",
)


# ---------------------------------------------------------------- note colours
NOTE_COLORS: List[str] = [
    "#ffd54f",  # amber
    "#a5d6a7",  # green
    "#90caf9",  # blue
    "#f48fb1",  # pink
    "#ce93d8",  # purple
    "#ffcc80",  # orange
    "#80cbc4",  # teal
    "#b0bec5",  # slate
    "#ffab91",  # coral
    "#d7ccc8",  # clay
]


def theme_palette(theme: Theme) -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(theme.window))
    palette.setColor(QPalette.WindowText, QColor(theme.text))
    palette.setColor(QPalette.Base, QColor(theme.surface))
    palette.setColor(QPalette.AlternateBase, QColor(theme.surface_alt))
    palette.setColor(QPalette.Text, QColor(theme.text))
    palette.setColor(QPalette.Button, QColor(theme.surface_alt))
    palette.setColor(QPalette.ButtonText, QColor(theme.text))
    palette.setColor(QPalette.Highlight, QColor(theme.accent))
    palette.setColor(QPalette.HighlightedText, QColor(theme.accent_text))
    palette.setColor(QPalette.ToolTipBase, QColor(theme.surface_alt))
    palette.setColor(QPalette.ToolTipText, QColor(theme.text))
    palette.setColor(QPalette.PlaceholderText, QColor(theme.text_faint))
    palette.setColor(QPalette.Link, QColor(theme.accent))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(theme.text_faint))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(theme.text_faint))
    return palette


def stylesheet(theme: Theme) -> str:
    """Application wide stylesheet."""
    return f"""
QWidget {{
    color: {theme.text};
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {theme.window};
}}

/* ------------------------------------------------------------- tool bars */
QToolBar {{
    background: {theme.surface};
    border: none;
    border-bottom: 1px solid {theme.border};
    padding: 5px 8px;
    spacing: 4px;
}}
QToolBar::separator {{
    background: {theme.border};
    width: 1px;
    margin: 4px 7px;
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 5px;
    color: {theme.text_dim};
}}
QToolButton:hover {{
    background: {theme.surface_alt};
    color: {theme.text};
    border-color: {theme.border};
}}
QToolButton:checked {{
    background: {theme.accent_soft};
    color: {theme.accent};
    border-color: {theme.accent};
}}
QToolButton:disabled {{
    color: {theme.text_faint};
}}

/* ---------------------------------------------------------------- panels */
QFrame#Panel {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 10px;
}}
QLabel#PanelTitle {{
    color: {theme.text_dim};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 2px 2px 0px 2px;
}}
QLabel#Hint {{
    color: {theme.text_faint};
    font-size: 12px;
}}
QLabel#PageBadge {{
    color: {theme.text_dim};
    font-size: 11px;
    font-weight: 700;
    background: {theme.surface_sunken};
    border-radius: 9px;
    padding: 2px 8px;
}}

/* --------------------------------------------------------------- buttons */
QPushButton {{
    background: {theme.surface_alt};
    border: 1px solid {theme.border};
    border-radius: 7px;
    padding: 6px 12px;
    color: {theme.text};
}}
QPushButton:hover {{
    background: {theme.border};
    border-color: {theme.border_strong};
}}
QPushButton:pressed {{
    background: {theme.surface_sunken};
}}
QPushButton:disabled {{
    color: {theme.text_faint};
    background: {theme.surface_alt};
}}
QPushButton#Primary {{
    background: {theme.accent};
    color: {theme.accent_text};
    border-color: {theme.accent};
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {theme.accent};
    border-color: {theme.accent};
}}
QPushButton#Primary:disabled {{
    background: {theme.surface_sunken};
    color: {theme.text_faint};
    border-color: {theme.border};
}}

/* ------------------------------------------------------------ line edits */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 7px;
    padding: 5px 8px;
    selection-background-color: {theme.accent};
    selection-color: {theme.accent_text};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {theme.accent};
}}
QLineEdit::placeholder {{
    color: {theme.text_faint};
}}

/* --------------------------------------------------------------- scrolls */
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {theme.border_strong};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {theme.text_faint};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {theme.border_strong};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {theme.text_faint};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
    width: 0px;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ----------------------------------------------------------------- trees */
QTreeWidget, QListWidget {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 8px;
    outline: none;
}}
QTreeWidget::item, QListWidget::item {{
    padding: 4px 4px;
    border-radius: 6px;
}}
QTreeWidget::item:hover, QListWidget::item:hover {{
    background: {theme.surface_alt};
}}
QTreeWidget::item:selected, QListWidget::item:selected {{
    background: {theme.accent_soft};
    color: {theme.text};
}}
QHeaderView::section {{
    background: {theme.surface_alt};
    border: none;
    border-bottom: 1px solid {theme.border};
    padding: 5px 8px;
    color: {theme.text_dim};
    font-weight: 600;
}}

/* ------------------------------------------------------------------ tabs */
QTabWidget::pane {{
    border: 1px solid {theme.border};
    border-radius: 9px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {theme.text_dim};
    padding: 7px 7px;
    margin-right: 0;
    border: 1px solid transparent;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {theme.text};
    background: {theme.surface_alt};
}}
QTabBar::tab:selected {{
    background: {theme.surface};
    color: {theme.accent};
    border-color: {theme.border};
    border-bottom-color: {theme.surface};
}}

/* --------------------------------------------------------------- splitter */
QSplitter::handle {{
    background: {theme.border};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}

/* --------------------------------------------------------------- statusbar */
QStatusBar {{
    background: {theme.surface};
    border-top: 1px solid {theme.border};
    color: {theme.text_dim};
}}
QStatusBar::item {{
    border: none;
}}
QStatusBar QLabel {{
    padding: 1px 8px;
}}

/* ----------------------------------------------------------------- menus */
QMenuBar {{
    background: {theme.surface};
    color: {theme.text};
    border-bottom: 1px solid {theme.border};
}}
QMenuBar::item {{
    padding: 5px 11px;
    background: transparent;
    border-radius: 5px;
}}
QMenuBar::item:selected {{
    background: {theme.surface_alt};
}}
QMenu {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 9px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 26px 6px 22px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {theme.accent_soft};
}}
QMenu::separator {{
    height: 1px;
    background: {theme.border};
    margin: 5px 8px;
}}

/* -------------------------------------------------------------- scrollbar */
QCheckBox, QRadioButton {{
    spacing: 7px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px;
    height: 15px;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    selection-background-color: {theme.accent_soft};
    selection-color: {theme.text};
    padding: 4px;
}}
QToolTip {{
    background: {theme.surface_alt};
    color: {theme.text};
    border: 1px solid {theme.border_strong};
    border-radius: 6px;
    padding: 4px 7px;
}}
"""


def apply_theme(app: QApplication, theme: Theme) -> None:
    """Apply a theme to the running application."""
    app.setPalette(theme_palette(theme))
    app.setStyleSheet(stylesheet(theme))
