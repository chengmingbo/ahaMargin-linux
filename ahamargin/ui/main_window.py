"""The main application window."""

from __future__ import annotations

import os
import tempfile
import time
from typing import Dict, List, Optional

import pymupdf
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QImage,
    QKeySequence,
    QPageRanges,
    QPainter,
    QShortcut,
)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QSizePolicy,
    QMessageBox,
    QToolBar,
    QWidget,
)

from ..core.annotations import AnnKind, Annotation
from .. import APP_NAME
from ..core.document import PdfDocument
from ..core.exporter import (
    notes_to_csv,
    notes_to_json,
    notes_to_markdown,
    notes_to_mindmap_svg,
)
from ..core.print_layout import PrintOptions, build_print_pdf, parse_page_range
from ..core.store import Store
from ..core.undo import AnnotationHistory, clone_all
from .icons import icon
from .margin_panel import ColorSwatchButton, MarginPanel
from .pdf_view import PdfView
from .print_dialog import PrintOptionsDialog
from .sidebar import Sidebar
from .theme import DARK, LIGHT, Theme, apply_theme
from .tools import Tool
from .settings import application_settings
from .assistant_panel import AssistantPanel

# Typing that continues without a pause is treated as a single undo step.
BODY_EDIT_BURST = 1.5


def _layout_page_count(layout_path: str) -> int:
    """How many sheets a finished print layout has."""
    try:
        doc = pymupdf.open(layout_path)
        try:
            return doc.page_count
        finally:
            doc.close()
    except Exception:
        return 1


def _all_page_ranges(total: int) -> "QPageRanges":
    """A page range that covers every sheet of the layout."""
    ranges = QPageRanges()
    for page in range(1, max(1, total) + 1):
        ranges.addPage(page)
    return ranges


def _printer_page_range(printer: QPrinter) -> str:
    """The page range the user asked for, as a string such as ``"1-3"``.

    An empty string means "every page".  Qt 6 keeps the range in
    :class:`QPageRanges`, which numbers pages from one.
    """
    try:
        ranges = printer.pageRanges()
        if ranges is None or ranges.isEmpty():
            return ""
        return str(ranges.toString()).strip()
    except Exception:
        return ""


class MainWindow(QMainWindow):
    """Ties the canvas, the margin pane and the sidebar together."""

    def __init__(self, path: Optional[str] = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)

        self.store: Optional[Store] = None
        self.doc: Optional[PdfDocument] = None
        self.history = AnnotationHistory()
        self.theme: Theme = LIGHT
        self._tool = Tool.POINTER
        # Per note typing timers: a burst of typing in one note is one undo
        # step, but typing in a different note always starts a new step.
        self._body_edit_times: Dict[str, float] = {}
        self._markup_color = Tool.HIGHLIGHT.default_color
        self._note_color = "#ffd54f"
        self._current_path: Optional[str] = None
        self._current_note_id: Optional[str] = None
        self._focus_mode = False
        self._pre_focus_visibility = (True, True, True)
        self._settings = application_settings()
        self._recent: List[str] = list(self._settings.value("recent/files", []) or [])

        self._build_central()
        self._build_docks()
        self._build_toolbar()
        self._build_menus()
        self._build_statusbar()
        self._build_shortcuts()

        self._apply_theme(LIGHT)
        self._update_actions()

        if path:
            self.open_path(path)

    # ------------------------------------------------------------------ build
    def _build_central(self) -> None:
        self.view = PdfView()
        self.view.setObjectName("PdfView")
        self.setCentralWidget(self.view)

        self.view.selectionMade.connect(self._on_selection)
        self.view.annotationCreated.connect(self._on_annotation_created)
        self.view.annotationRemoved.connect(self._on_annotation_removed)
        self.view.annotationPicked.connect(self._on_annotation_picked)
        self.view.annotationEditRequested.connect(self._edit_annotation_text)
        self.view.pageChanged.connect(self._on_page_changed)
        self.view.zoomChanged.connect(self._on_zoom_changed)

    def _build_docks(self) -> None:
        self.sidebar = Sidebar()
        self.left_dock = QDockWidget("Sidebar", self)
        self.left_dock.setObjectName("LeftDock")
        self.left_dock.setWidget(self.sidebar)
        self.left_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.left_dock)

        self.margin_panel = MarginPanel()
        self.right_dock = QDockWidget("Margin Notes", self)
        self.right_dock.setObjectName("RightDock")
        self.right_dock.setWidget(self.margin_panel)
        self.right_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.right_dock)
        self.resizeDocks([self.right_dock], [380], Qt.Orientation.Horizontal)
        self.resizeDocks([self.left_dock], [340], Qt.Orientation.Horizontal)

        self.assistant_panel = AssistantPanel(self._settings)
        self.assistant_dock = QDockWidget("AI Assistant", self)
        self.assistant_dock.setObjectName("AssistantDock")
        self.assistant_dock.setWidget(self.assistant_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.assistant_dock)
        self.tabifyDockWidget(self.right_dock, self.assistant_dock)
        self.assistant_dock.hide()
        self.assistant_panel.annotationRequested.connect(self._add_annotation)
        self.assistant_panel.pageRequested.connect(self._goto_page)
        self.assistant_panel.revealRequested.connect(self._reveal_assistant_passage)

        self.sidebar.pageActivated.connect(self._goto_page)
        self.sidebar.noteActivated.connect(self._activate_note)
        self.sidebar.searchRequested.connect(self._run_search)
        self.sidebar.hitActivated.connect(self._on_search_hit)
        self.sidebar.annotationActivated.connect(self._goto_annotation)
        self.sidebar.annotationColorRequested.connect(self._on_annotation_color)
        self.sidebar.annotationDeleteRequested.connect(self._delete_annotation_by_id)
        self.sidebar.annotationConvertRequested.connect(self._convert_to_note)

        self.margin_panel.bodyChanged.connect(self._on_note_body_changed)
        self.margin_panel.deleteRequested.connect(self._delete_note_by_id)
        self.margin_panel.colorRequested.connect(self._on_note_color_changed)
        self.margin_panel.jumpRequested.connect(self._jump_to_note)
        self.margin_panel.collapseToggled.connect(self._on_note_collapsed)
        self.margin_panel.addBlankRequested.connect(self._add_blank_note)
        self.margin_panel.exportRequested.connect(self._export_notes_menu)
        self.margin_panel.orderChanged.connect(self._on_note_reordered)
        self.margin_panel.reorderRejected.connect(self._on_reorder_rejected)

    def _build_toolbar(self) -> None:
        bar = QToolBar("Main")
        bar.setObjectName("MainToolBar")
        bar.setIconSize(QSize(20, 20))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        bar.setMovable(False)
        self.addToolBar(bar)
        self.toolbar = bar

        self.act_open = QAction(icon("open", "#4b5563", 20), "Open...", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.triggered.connect(self.open_dialog)
        bar.addAction(self.act_open)

        self.act_save = QAction(icon("save", "#4b5563", 20), "Save", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)
        self.act_save.triggered.connect(self.save)
        bar.addAction(self.act_save)

        self.act_export_pdf = QAction(icon("export", "#4b5563", 20), "Export annotated PDF", self)
        self.act_export_pdf.setShortcut(QKeySequence("Ctrl+E"))
        self.act_export_pdf.triggered.connect(self.export_annotated_pdf)
        bar.addAction(self.act_export_pdf)

        bar.addSeparator()

        self.act_zoom_out = QAction(icon("zoom-out", "#4b5563", 20), "Zoom out", self)
        self.act_zoom_out.triggered.connect(self.view.zoom_out)
        bar.addAction(self.act_zoom_out)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setMinimumWidth(46)
        bar.addWidget(self.zoom_label)

        self.act_zoom_in = QAction(icon("zoom-in", "#4b5563", 20), "Zoom in", self)
        self.act_zoom_in.triggered.connect(self.view.zoom_in)
        bar.addAction(self.act_zoom_in)

        self.act_fit_width = QAction(icon("fit-width", "#4b5563", 20), "Fit width", self)
        self.act_fit_width.triggered.connect(self.view.fit_width)
        bar.addAction(self.act_fit_width)

        self.act_fit_page = QAction(icon("fit-page", "#4b5563", 20), "Fit page", self)
        self.act_fit_page.triggered.connect(self.view.fit_page)
        bar.addAction(self.act_fit_page)

        bar.addSeparator()

        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions: Dict[Tool, QAction] = {}
        for tool in (
            Tool.POINTER, Tool.HIGHLIGHT, Tool.UNDERLINE, Tool.STRIKEOUT,
            Tool.SQUIGGLY, Tool.INK, Tool.RECT, Tool.ELLIPSE, Tool.ARROW,
            Tool.TEXT, Tool.NOTE, Tool.ERASER,
        ):
            action = QAction(icon(tool.icon, "#4b5563", 20), tool.label, self)
            action.setCheckable(True)
            action.setToolTip(f"{tool.label}" + (f"  ({tool.shortcut})" if tool.shortcut else ""))
            action.triggered.connect(lambda _checked=False, t=tool: self.set_tool(t))
            self.tool_group.addAction(action)
            self.tool_actions[tool] = action
            bar.addAction(action)
        self.tool_actions[Tool.POINTER].setChecked(True)

        bar.addSeparator()
        self.markup_color = ColorSwatchButton(self._markup_color)
        self.markup_color.setToolTip("Colour for the current marking tool")
        self.markup_color.colorChanged.connect(self._on_markup_color)
        bar.addWidget(self.markup_color)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)

        self.act_toggle_sidebar = QAction(icon("panel-left", "#4b5563", 20), "Sidebar", self)
        self.act_toggle_sidebar.setCheckable(True)
        self.act_toggle_sidebar.setChecked(True)
        self.act_toggle_sidebar.triggered.connect(self.left_dock.setVisible)
        bar.addAction(self.act_toggle_sidebar)

        self.act_toggle_margin = QAction(icon("panel-right", "#4b5563", 20), "Margin notes", self)
        self.act_toggle_margin.setCheckable(True)
        self.act_toggle_margin.setChecked(True)
        self.act_toggle_margin.triggered.connect(self.right_dock.setVisible)
        bar.addAction(self.act_toggle_margin)

        self.act_assistant = QAction(icon("sparkle", "#4b5563", 20), "AI Assistant", self)
        self.act_assistant.setShortcut(QKeySequence("Ctrl+Shift+I"))
        self.act_assistant.setToolTip("AI Assistant (Ctrl+Shift+I)")
        self.act_assistant.triggered.connect(self.show_assistant)
        bar.addAction(self.act_assistant)

        self.act_theme = QAction(icon("moon", "#4b5563", 20), "Dark theme", self)
        self.act_theme.triggered.connect(self._toggle_theme)
        bar.addAction(self.act_theme)

        # Remember which icon belongs to which action so the toolbar can be
        # re-tinted when the theme changes.
        self._toolbar_icon_names = {
            self.act_open: "open",
            self.act_save: "save",
            self.act_export_pdf: "export",
            self.act_zoom_out: "zoom-out",
            self.act_zoom_in: "zoom-in",
            self.act_fit_width: "fit-width",
            self.act_fit_page: "fit-page",
            self.act_toggle_sidebar: "panel-left",
            self.act_toggle_margin: "panel-right",
            self.act_assistant: "sparkle",
        }
        for tool, action in self.tool_actions.items():
            self._toolbar_icon_names[action] = tool.icon

    def _build_menus(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        file_menu.addAction(self.act_open)
        file_menu.addAction(self.act_save)
        self.recent_menu = file_menu.addMenu("Open &recent")
        self.recent_menu.aboutToShow.connect(self._refresh_recent_menu)
        file_menu.addSeparator()
        file_menu.addAction(self.act_export_pdf)
        self.act_export_flat = QAction("Export flattened PDF...", self)
        self.act_export_flat.setToolTip(
            "Burn the annotations into the page content (no annotation objects left)"
        )
        self.act_export_flat.triggered.connect(self.export_flattened_pdf)
        file_menu.addAction(self.act_export_flat)
        self.act_export_outline = QAction("Export notes as PDF outline...", self)
        self.act_export_outline.setToolTip(
            "Write a copy of the document whose bookmarks mirror your margin notes"
        )
        self.act_export_outline.triggered.connect(self.export_notes_outline)
        file_menu.addAction(self.act_export_outline)
        export_menu = file_menu.addMenu("Export &notes")
        for label, slot in (
            ("Markdown study sheet...", lambda: self._export_text(notes_to_markdown, "Markdown (*.md)")),
            ("JSON...", lambda: self._export_text(notes_to_json, "JSON (*.json)")),
            ("CSV...", lambda: self._export_text(notes_to_csv, "CSV (*.csv)")),
            ("Mind map (SVG)...", self.export_mindmap),
        ):
            action = QAction(label, self)
            action.triggered.connect(slot)
            export_menu.addAction(action)
        file_menu.addSeparator()
        self.act_print = QAction("Print with margin notes...", self)
        self.act_print.setShortcut(QKeySequence("Ctrl+P"))
        self.act_print.setToolTip(
            "Print the document with its margin notes set in the margin"
        )
        self.act_print.triggered.connect(self.print_with_notes)
        file_menu.addAction(self.act_print)
        self.act_export_print = QAction("Export print layout PDF...", self)
        self.act_export_print.setToolTip(
            "Write the print layout - document on the left, notes in the margin - to a PDF"
        )
        self.act_export_print.triggered.connect(self.export_print_layout)
        file_menu.addAction(self.act_export_print)
        file_menu.addSeparator()
        act_close = QAction("Close document", self)
        act_close.triggered.connect(self.close_document)
        file_menu.addAction(act_close)
        file_menu.addSeparator()
        act_quit = QAction("Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        edit_menu = menu.addMenu("&Edit")
        self.act_undo = QAction("Undo", self)
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo.triggered.connect(self.undo)
        edit_menu.addAction(self.act_undo)
        self.act_redo = QAction("Redo", self)
        self.act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self.act_redo.triggered.connect(self.redo)
        edit_menu.addAction(self.act_redo)
        edit_menu.addSeparator()
        self.act_delete_note = QAction("Delete selected note", self)
        self.act_delete_note.setShortcut(QKeySequence("Del"))
        self.act_delete_note.triggered.connect(self._delete_selected_note)
        edit_menu.addAction(self.act_delete_note)

        view_menu = menu.addMenu("&View")
        view_menu.addAction(self.act_toggle_sidebar)
        view_menu.addAction(self.act_toggle_margin)
        view_menu.addAction(self.act_assistant)
        view_menu.addSeparator()
        view_menu.addAction(self.act_zoom_in)
        view_menu.addAction(self.act_zoom_out)
        view_menu.addAction(self.act_fit_width)
        view_menu.addAction(self.act_fit_page)
        actual = QAction("Actual size", self)
        actual.triggered.connect(self.view.actual_size)
        view_menu.addAction(actual)
        view_menu.addSeparator()
        act_goto = QAction("Go to page...", self)
        act_goto.setShortcut(QKeySequence("Ctrl+G"))
        act_goto.triggered.connect(self._goto_page_dialog)
        view_menu.addAction(act_goto)
        view_menu.addSeparator()
        self.act_next_note = QAction("Next margin note", self)
        self.act_next_note.setShortcut(QKeySequence("Ctrl+Alt+Down"))
        self.act_next_note.triggered.connect(lambda: self._navigate_notes(1))
        view_menu.addAction(self.act_next_note)
        self.act_prev_note = QAction("Previous margin note", self)
        self.act_prev_note.setShortcut(QKeySequence("Ctrl+Alt+Up"))
        self.act_prev_note.triggered.connect(lambda: self._navigate_notes(-1))
        view_menu.addAction(self.act_prev_note)
        view_menu.addSeparator()
        act_find = QAction("Find in document", self)
        act_find.setShortcut(QKeySequence.StandardKey.Find)
        act_find.triggered.connect(self._focus_search)
        view_menu.addAction(act_find)
        view_menu.addSeparator()
        self.act_focus_mode = QAction("Focus mode", self)
        self.act_focus_mode.setCheckable(True)
        self.act_focus_mode.setShortcut(QKeySequence("F11"))
        self.act_focus_mode.setToolTip("Hide the toolbars and panels to read the document")
        self.act_focus_mode.toggled.connect(self._set_focus_mode)
        view_menu.addAction(self.act_focus_mode)

        annotate_menu = menu.addMenu("&Annotate")
        for tool, action in self.tool_actions.items():
            annotate_menu.addAction(action)
        annotate_menu.addSeparator()
        self.act_new_note = QAction("New margin note", self)
        self.act_new_note.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self.act_new_note.triggered.connect(self._add_blank_note)
        annotate_menu.addAction(self.act_new_note)

        ai_menu = menu.addMenu("&AI")
        ai_menu.addAction(self.act_assistant)
        claude = QAction("Claude", self)
        claude.setShortcut(QKeySequence("Ctrl+Shift+A"))
        claude.triggered.connect(lambda: self.show_assistant(provider="claude"))
        ai_menu.addAction(claude)
        settings = QAction("Assistant settings…", self)
        settings.setShortcut(QKeySequence("Ctrl+,"))
        settings.triggered.connect(self.show_assistant_settings)
        ai_menu.addAction(settings)

        help_menu = menu.addMenu("&Help")
        act_shortcuts = QAction("Keyboard shortcuts", self)
        act_shortcuts.triggered.connect(self._show_shortcuts)
        help_menu.addAction(act_shortcuts)
        act_about = QAction("About", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _build_statusbar(self) -> None:
        bar = self.statusBar()
        self.status_page = QLabel("No document")
        self.status_zoom = QLabel("100%")
        self.status_notes = QLabel("0 notes")
        self.status_save = QLabel("")
        self.status_hint = QLabel("")
        bar.addWidget(self.status_page)
        bar.addWidget(self.status_notes)
        bar.addPermanentWidget(self.status_hint)
        bar.addPermanentWidget(self.status_zoom)
        bar.addPermanentWidget(self.status_save)

    def _build_shortcuts(self) -> None:
        """Single letter tool shortcuts only fire while the canvas has focus."""
        for tool in Tool:
            if not tool.shortcut:
                continue
            shortcut = QShortcut(QKeySequence(tool.shortcut), self.view)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda t=tool: self.set_tool(t))

    # ------------------------------------------------------------- documents
    def open_dialog(self) -> None:
        start = os.path.dirname(self._current_path) if self._current_path else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", start, "PDF documents (*.pdf);;All files (*)"
        )
        if path:
            self.open_path(path)

    # ------------------------------------------------------------- recents
    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        existing = [p for p in self._recent if os.path.exists(p)]
        if existing != self._recent:
            self._recent = existing
            self._settings.setValue("recent/files", self._recent)
        if not existing:
            empty = QAction("No recent documents", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for path in existing:
            action = QAction(os.path.basename(path), self)
            action.setToolTip(path)
            action.triggered.connect(lambda _checked=False, p=path: self.open_path(p))
            self.recent_menu.addAction(action)
        self.recent_menu.addSeparator()
        clear = QAction("Clear list", self)
        clear.triggered.connect(self._clear_recent)
        self.recent_menu.addAction(clear)

    def _clear_recent(self) -> None:
        self._recent = []
        self._settings.setValue("recent/files", self._recent)
        self._refresh_recent_menu()

    def _remember_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        self._recent = [p for p in self._recent if p != path]
        self._recent.insert(0, path)
        del self._recent[10:]
        self._settings.setValue("recent/files", self._recent)

    def open_path(self, path: str) -> bool:
        if not os.path.exists(path):
            QMessageBox.warning(self, "Open failed", f"The file does not exist:\n{path}")
            return False

        password = ""
        try:
            store = Store(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", f"Could not open the document:\n{exc}")
            return False

        if store.doc.needs_pass:
            accepted, password = QInputDialog.getText(
                self, "Password required", "This document is encrypted.\nPassword:",
                QLineEdit.EchoMode.Password, ""
            )
            if not accepted:
                store.doc.close()
                return False
            if not store.doc.authenticate(password):
                store.doc.close()
                QMessageBox.warning(self, "Open failed", "The password was not accepted.")
                return False

        self.close_document()
        self.store = store
        self._current_path = path
        self._remember_recent(path)

        self.doc = PdfDocument(path, password or None)
        self.assistant_panel.set_document(self.doc)
        self.view.set_document(self.doc)
        self.view.set_annotations(store.annotations)
        self.sidebar.set_document(self.doc)
        self._refresh_notes()
        self.view.fit_width()
        self._update_title()
        self._update_actions()
        self.status_hint.setText("Select text to capture a margin note")
        return True

    def close_document(self) -> None:
        self.assistant_panel.set_document(None)
        if self.store is not None:
            self.store.doc.close()
        self.store = None
        self.doc = None
        self._current_path = None
        self.history.clear()
        self.view.set_document(None)
        self.view.set_annotations([])
        self.sidebar.set_document(None)
        self.margin_panel.set_notes([])
        self._update_title()
        self._update_actions()

    # ----------------------------------------------------------------- tools
    def set_tool(self, tool: Tool) -> None:
        self._tool = tool
        self.view.set_tool(tool)
        action = self.tool_actions.get(tool)
        if action is not None:
            action.setChecked(True)
        if tool.is_text_markup:
            self.markup_color.set_color(self._markup_color)
            self.view.set_tool_color(self._markup_color)
        else:
            self.view.set_tool_color(tool.default_color)
        self.status_hint.setText(tool.hint)
        self._update_actions()

    def _on_markup_color(self, color: str) -> None:
        self._markup_color = color
        if self._tool.is_text_markup:
            self.view.set_tool_color(color)

    # ------------------------------------------------------------ annotations
    def _on_selection(self, page: int, text: str, rects: List) -> None:
        if self.store is None:
            return
        self.assistant_panel.set_selection({"page": page + 1, "text": text})
        if self._tool is Tool.POINTER:
            note = Annotation(
                kind=AnnKind.MARGIN,
                page=page,
                rects=list(rects),
                color=self._note_color,
                text="",
                extra={"capture": text},
            )
            self._add_annotation(note)
            self.margin_panel.rebuild()
            self.margin_panel.focus_note(note.id)
            self.status_hint.setText("Note captured — type your thoughts here")
        else:
            kind = self._tool.kind
            if kind is None:
                return
            annotation = Annotation(
                kind=kind,
                page=page,
                rects=list(rects),
                color=self._tool_color_for(kind),
            )
            self._add_annotation(annotation)

    def _tool_color_for(self, kind: AnnKind) -> str:
        if kind is AnnKind.MARGIN:
            return self._note_color
        if self._tool.is_text_markup:
            return self._markup_color
        return self._tool.default_color

    def _on_annotation_created(self, annotation: Annotation) -> None:
        self._add_annotation(annotation)

    def _add_annotation(self, annotation: Annotation) -> None:
        if self.store is None:
            return
        self.history.record(f"add {annotation.kind.label.lower()}", self.store.annotations)
        self.store.add(annotation)
        self._after_model_change()

    def _on_annotation_removed(self, annotation: Annotation) -> None:
        self._delete_annotation(annotation)

    def _delete_annotation(self, annotation: Annotation) -> None:
        if self.store is None:
            return
        self.history.record("delete annotation", self.store.annotations)
        self.store.remove(annotation)
        self._after_model_change()

    def _delete_note_by_id(self, annotation_id: str) -> None:
        if self.store is None:
            return
        annotation = self.store.by_id(annotation_id)
        if annotation is not None:
            self._delete_annotation(annotation)

    def _delete_selected_note(self) -> None:
        if self.store is None:
            return
        # An annotation selected on the canvas wins over the margin column.
        canvas_selection = self.view.selected_annotation
        if canvas_selection is not None:
            self._delete_annotation(canvas_selection)
            return
        selected = self.margin_panel.highlighted_id
        if selected:
            self._delete_note_by_id(selected)
            return
        # otherwise delete whatever the canvas points at
        page = self.view.current_visible_page()
        hits = [a for a in self.store.annotations if a.page == page]
        if hits:
            self._delete_annotation(hits[-1])

    def _after_model_change(self) -> None:
        if self.store is None:
            return
        self.view.set_selected_annotation(None)
        self.view.set_annotations(self.store.annotations)
        self._refresh_notes()
        self._update_actions()
        self._update_title()

    def _refresh_notes(self) -> None:
        if self.store is None:
            self.margin_panel.set_notes([])
            self.sidebar.load_notes([])
            self.status_notes.setText("0 notes")
            return
        notes = self.store.margin_notes()
        self.margin_panel.set_notes(notes)
        self.sidebar.load_notes(notes)
        self.sidebar.load_annotations(self.store.annotations)
        self.status_notes.setText(f"{len(notes)} note{'' if len(notes) == 1 else 's'}")

    # ----------------------------------------------------------------- notes
    def _on_note_body_changed(self, annotation_id: str, text: str,
                              old_text: str = "") -> None:
        if self.store is None:
            return
        annotation = self.store.by_id(annotation_id)
        if annotation is None:
            return
        if old_text is not None and old_text != text:
            now = time.monotonic()
            # Collapse a burst of typing in the *same* note into one step.
            last_edit = self._body_edit_times.get(annotation_id, 0.0)
            if now - last_edit > BODY_EDIT_BURST:
                state = clone_all(self.store.annotations)
                for item in state:
                    if item.id == annotation_id:
                        item.text = old_text
                self.history.record("edit note", state)
            self._body_edit_times[annotation_id] = now
        annotation.text = text
        annotation.touched()
        self.store.dirty = True
        self._update_title()

    def _on_note_color_changed(self, annotation_id: str, color: str) -> None:
        if self.store is None:
            return
        annotation = self.store.by_id(annotation_id)
        if annotation is None:
            return
        annotation.color = color
        annotation.touched()
        self.store.dirty = True
        self.view.set_annotations(self.store.annotations)
        self._update_title()

    def _on_note_collapsed(self, annotation_id: str, collapsed: bool) -> None:
        if self.store is None:
            return
        annotation = self.store.by_id(annotation_id)
        if annotation is None:
            return
        annotation.collapsed = collapsed
        annotation.touched()
        self.store.dirty = True

    def _add_blank_note(self) -> None:
        if self.store is None:
            return
        page = self.view.current_visible_page()
        note = Annotation(
            kind=AnnKind.MARGIN,
            page=page,
            rects=[],
            color=self._note_color,
            text="",
            extra={"capture": ""},
        )
        self._add_annotation(note)
        self.margin_panel.rebuild()
        self.margin_panel.focus_note(note.id)

    def _on_note_reordered(self, note_id: str, before_id: str) -> None:
        """Persist a card reorder that happened in the margin column."""
        if self.store is None:
            return
        # The panel mutates the shared annotation objects, so the model only
        # needs its order values normalised again.
        self.store.renumber()
        self.store.dirty = True
        self.view.set_annotations(self.store.annotations)
        self._refresh_notes()
        self._update_title()
        self.status_hint.setText("Note order updated")

    def _on_reorder_rejected(self, note_id: str) -> None:
        self.status_hint.setText(
            "A note stays with the page its quote is on"
        )

    def _navigate_notes(self, direction: int) -> None:
        """Jump to the next or previous margin note."""
        if self.store is None:
            return
        notes = self.store.margin_notes()
        if not notes:
            self.status_hint.setText("No margin notes to move through")
            return
        ids = [n.id for n in notes]
        index = ids.index(self._current_note_id) if self._current_note_id in ids else None
        if index is None:
            page = self.view.current_visible_page()
            same = [i for i, n in enumerate(notes) if n.page == page]
            if same:
                index = same[0] if direction > 0 else same[-1]
            else:
                index = 0 if direction > 0 else len(notes) - 1
        target = notes[(index + direction) % len(notes)]
        self._current_note_id = target.id
        self._jump_to_note(target.id)

    def _jump_to_note(self, annotation_id: str) -> None:
        if self.store is None:
            return
        annotation = self.store.by_id(annotation_id)
        if annotation is None:
            return
        self._current_note_id = annotation_id
        self.margin_panel.highlight_note(annotation_id)
        if annotation.rects:
            anchor = annotation.rects[0]
            self.view.scroll_to_rect(annotation.page, anchor)
            self.view.flash_rect(annotation.page, anchor)
        else:
            self.view.goto_page(annotation.page)

    def _activate_note(self, annotation_id: str, page: int) -> None:
        self._jump_to_note(annotation_id)
        self.margin_panel.focus_note(annotation_id)

    def _goto_annotation(self, annotation_id: str, page: int) -> None:
        """Jump to an annotation listed in the Marks panel."""
        if self.store is None:
            return
        annotation = self.store.by_id(annotation_id)
        if annotation is None:
            return
        if annotation.rects:
            anchor = annotation.rects[0]
            self.view.scroll_to_rect(annotation.page, anchor)
            self.view.flash_rect(annotation.page, anchor)
        else:
            self.view.goto_page(annotation.page)

    def _on_annotation_color(self, annotation_id: str, color: str) -> None:
        if self.store is None:
            return
        annotation = self.store.by_id(annotation_id)
        if annotation is None:
            return
        self.history.record("change colour", self.store.annotations)
        annotation.color = color
        annotation.touched()
        self.store.dirty = True
        self._after_model_change()
        self.status_hint.setText(f"Colour changed to {color}")

    def _delete_annotation_by_id(self, annotation_id: str) -> None:
        if self.store is None:
            return
        annotation = self.store.by_id(annotation_id)
        if annotation is not None:
            self._delete_annotation(annotation)

    def _convert_to_note(self, annotation_id: str) -> None:
        """Turn a mark into a margin note, quoting the text it covers."""
        if self.store is None:
            return
        annotation = self.store.by_id(annotation_id)
        if annotation is None or annotation.is_margin:
            return
        capture = ""
        if annotation.rects and self.doc is not None:
            capture = self.doc.text_in_rect(annotation.page, annotation.rects[0])
        self.history.record("turn into a margin note", self.store.annotations)
        annotation.kind = AnnKind.MARGIN
        annotation.extra["capture"] = capture
        annotation.touched()
        self.store.dirty = True
        self._after_model_change()
        self.status_hint.setText("Turned into a margin note")

    def _edit_annotation_text(self, annotation: Annotation) -> None:
        """Edit the wording of a text box or a sticky note."""
        if self.store is None:
            return
        title = (
            "Edit text box" if annotation.kind is AnnKind.TEXT else "Edit sticky note"
        )
        text, accepted = QInputDialog.getText(
            self, title, "Text:", QLineEdit.EchoMode.Normal, annotation.text
        )
        if not accepted:
            return
        self._apply_annotation_text(annotation, text)

    def _apply_annotation_text(self, annotation: Annotation, text: str) -> None:
        """Store new wording for a text box or sticky note."""
        if self.store is None or text == annotation.text:
            return
        self.history.record("edit annotation text", self.store.annotations)
        annotation.text = text
        annotation.touched()
        self.store.dirty = True
        self._after_model_change()
        self.status_hint.setText("Text updated")

    def _on_annotation_picked(self, annotation: Annotation) -> None:
        if annotation.is_margin:
            self.margin_panel.highlight_note(annotation.id)
            self.margin_panel.focus_note(annotation.id, select=False)
        else:
            self.status_hint.setText(
                f"{annotation.kind.label} on page {annotation.page + 1}"
            )

    # ---------------------------------------------------------------- search
    def _run_search(self, query: str) -> None:
        if self.doc is None:
            return
        hits = self.doc.search(query)
        self.sidebar.search.show_hits(hits, query)

    def _on_search_hit(self, page: int, rect) -> None:
        """Jump to the exact place a search term was found."""
        if self.doc is None:
            return
        if rect is not None:
            self.view.scroll_to_rect(page, rect)
            self.view.flash_rect(page, rect)
        else:
            self.view.goto_page(page)

    def _focus_search(self) -> None:
        self.left_dock.setVisible(True)
        self.sidebar.setCurrentIndex(3)
        self.sidebar.search.focus_field()

    # ------------------------------------------------------------ navigation
    def _goto_page(self, page: int) -> None:
        self.view.goto_page(page)

    def _goto_page_dialog(self) -> None:
        if self.doc is None:
            return
        current = self.view.current_visible_page() + 1
        page, accepted = QInputDialog.getInt(
            self, "Go to page", "Page number:", current, 1, self.doc.page_count
        )
        if accepted:
            self.view.goto_page(page - 1)

    def _on_page_changed(self, page: int) -> None:
        if self.doc is None:
            return
        self.status_page.setText(f"Page {page + 1} / {self.doc.page_count}")
        self.assistant_panel.set_page(page)

    def show_assistant(self, checked=False, provider=None) -> None:
        if provider:
            index = self.assistant_panel.picker.findData(provider)
            if index >= 0:
                self.assistant_panel.picker.setCurrentIndex(index)
        self.assistant_dock.show()
        self.assistant_dock.raise_()
        self.assistant_panel.input.setFocus()

    def show_assistant_settings(self) -> None:
        self.show_assistant()
        self.assistant_panel.open_settings()

    def _reveal_assistant_passage(self, page: int, rect) -> None:
        self.view.scroll_to_rect(page, rect)
        self.view.flash_rect(page, rect)

    def _on_zoom_changed(self, zoom: float) -> None:
        text = f"{zoom * 100:.0f}%"
        self.zoom_label.setText(text)
        self.status_zoom.setText(text)

    # --------------------------------------------------------- save / export
    def save(self) -> None:
        if self.store is None:
            return
        try:
            self.store.save()
            self.store.write_sidecar()
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", f"Could not save the document:\n{exc}")
            return
        self._update_title()
        self.status_hint.setText("Saved")
        self.status_save.setText("Saved")

    def export_annotated_pdf(self) -> None:
        if self.store is None:
            return
        base = os.path.splitext(os.path.basename(self._current_path or "document.pdf"))[0]
        suggestion = f"{base}-annotated.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export annotated PDF", suggestion, "PDF documents (*.pdf)"
        )
        if not path:
            return
        try:
            self.store.export_copy(path)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", f"Could not export the PDF:\n{exc}")
            return
        self.status_hint.setText(f"Exported to {os.path.basename(path)}")

    def export_flattened_pdf(self) -> None:
        if self.store is None:
            return
        base = os.path.splitext(os.path.basename(self._current_path or "document.pdf"))[0]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export flattened PDF", f"{base}-flattened.pdf", "PDF documents (*.pdf)"
        )
        if not path:
            return
        try:
            self.store.export_flattened(path)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", f"Could not export the PDF:\n{exc}")
            return
        self.status_hint.setText(f"Flattened PDF exported to {os.path.basename(path)}")

    def export_notes_outline(self) -> None:
        """Export a copy of the document with the notes as bookmarks."""
        if self.store is None:
            return
        base = os.path.splitext(os.path.basename(self._current_path or "document.pdf"))[0]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export notes as PDF outline", f"{base}-outlined.pdf", "PDF documents (*.pdf)"
        )
        if not path:
            return
        try:
            self.store.export_with_outline(path)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", f"Could not export the PDF:\n{exc}")
            return
        self.status_hint.setText(f"Outline exported to {os.path.basename(path)}")

    def _export_text(self, builder, file_filter: str) -> None:
        if self.store is None:
            return
        base = os.path.splitext(os.path.basename(self._current_path or "document.pdf"))[0]
        path, _ = QFileDialog.getSaveFileName(self, "Export notes", base, file_filter)
        if not path:
            return
        title = self.doc.title if self.doc else "Document"
        content = builder(self.store.annotations, title)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", f"Could not write the file:\n{exc}")
            return
        self.status_hint.setText(f"Exported to {os.path.basename(path)}")

    def export_mindmap(self) -> None:
        if self.store is None:
            return
        base = os.path.splitext(os.path.basename(self._current_path or "document.pdf"))[0]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export mind map", f"{base}-mindmap.svg", "SVG image (*.svg)"
        )
        if not path:
            return
        title = self.doc.title if self.doc else "Document"
        content = notes_to_mindmap_svg(self.store.annotations, title)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", f"Could not write the file:\n{exc}")
            return
        self.status_hint.setText(f"Mind map exported to {os.path.basename(path)}")

    def _export_notes_menu(self) -> None:
        self._export_text(notes_to_markdown, "Markdown (*.md)")

    # ------------------------------------------------------------------ print
    def _document_title(self) -> str:
        return self.doc.title if self.doc else "Document"

    def _ask_print_options(self) -> Optional[PrintOptions]:
        """Show the print layout options; ``None`` when the user cancels."""
        dialog = PrintOptionsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        dialog.save_settings()
        return dialog.current_options()

    def export_print_layout(self) -> None:
        """Write the print layout - document on the left, notes in the margin."""
        if self.store is None:
            return
        options = self._ask_print_options()
        if options is None:
            return
        base = os.path.splitext(os.path.basename(self._current_path or "document.pdf"))[0]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export print layout", f"{base}-print.pdf", "PDF documents (*.pdf)"
        )
        if not path:
            return
        errors: List[str] = []
        try:
            build_print_pdf(
                self._current_path, path, self.store.annotations, options,
                title=self._document_title(), errors=errors,
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "Export failed", f"Could not build the print layout:\n{exc}"
            )
            return
        if errors:
            QMessageBox.warning(
                self,
                "Print layout",
                "The layout was written, but some parts could not be rendered:\n\n"
                + "\n".join(errors[:10]),
            )
        self.status_hint.setText(f"Print layout exported to {os.path.basename(path)}")

    def print_with_notes(self) -> None:
        """Print the document with its margin notes set in the margin."""
        if self.store is None:
            return
        options = self._ask_print_options()
        if options is None:
            return
        errors: List[str] = []
        with tempfile.TemporaryDirectory(prefix="mnote-print-") as folder:
            layout_path = os.path.join(folder, "print-layout.pdf")
            try:
                build_print_pdf(
                    self._current_path, layout_path, self.store.annotations, options,
                    title=self._document_title(), errors=errors,
                )
            except Exception as exc:
                QMessageBox.critical(
                    self, "Print failed", f"Could not build the print layout:\n{exc}"
                )
                return
            self._send_to_printer(layout_path, errors)

    def _send_to_printer(self, layout_path: str, errors: List[str]) -> None:
        """Hand the finished layout to the system print dialog."""
        total = _layout_page_count(layout_path)
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName("Margin notes")
        printer.setPageRanges(_all_page_ranges(total))
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("Print with margin notes")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # "Print to file" produces a PDF; the layout is already a PDF, so it is
        # copied over unchanged and stays vector instead of being rasterised.
        if printer.outputFormat() == QPrinter.OutputFormat.PdfFormat:
            target = printer.outputFileName()
            if target:
                try:
                    self._write_layout_subset(layout_path, target, printer)
                except Exception as exc:
                    QMessageBox.critical(
                        self, "Print failed", f"Could not write the file:\n{exc}"
                    )
                    return
                self.status_hint.setText(f"Print layout written to {os.path.basename(target)}")
                return

        if not self._paint_layout_to_printer(printer, layout_path):
            return
        if errors:
            self.status_hint.setText(
                f"Printed, but {len(errors)} part(s) could not be rendered"
            )
        else:
            self.status_hint.setText("Sent to the printer")

    def _write_layout_subset(self, source: str, target: str, printer: QPrinter) -> None:
        """Copy the layout to ``target``, honouring a page range if one is set."""
        spec = _printer_page_range(printer)
        if not spec:
            import shutil

            shutil.copyfile(source, target)
            return
        doc = pymupdf.open(source)
        try:
            indices = parse_page_range(spec, doc.page_count)
            if not indices:
                indices = list(range(doc.page_count))
            doc.select(indices)
            doc.save(target, garbage=4, deflate=True)
        finally:
            doc.close()

    def _paint_layout_to_printer(self, printer: QPrinter, layout_path: str) -> bool:
        """Paint the layout onto the printer page by page."""
        doc = pymupdf.open(layout_path)
        try:
            total = doc.page_count
            indices = parse_page_range(_printer_page_range(printer), total)
            if not indices:
                indices = list(range(total))
            copies = max(1, int(printer.copyCount() or 1))

            painter = QPainter()
            if not painter.begin(printer):
                QMessageBox.critical(
                    self, "Print failed", "Could not start the print job."
                )
                return False
            try:
                view = painter.viewport()
                first = True
                for _ in range(copies):
                    for index in indices:
                        if not first and not printer.newPage():
                            break
                        first = False
                        self._draw_layout_page(painter, doc[index], view)
            finally:
                painter.end()
        finally:
            doc.close()
        return True

    def _draw_layout_page(self, painter: QPainter, page, view) -> None:
        """Render one layout page into the painter's viewport."""
        zoom = 300.0 / 72.0
        try:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        except Exception:
            return
        image = QImage(
            pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888
        ).copy()
        ratio = image.width() / max(1, image.height())
        width = float(view.width())
        height = width / ratio
        if height > view.height():
            height = float(view.height())
            width = height * ratio
        x = view.x() + (view.width() - width) / 2.0
        y = view.y() + (view.height() - height) / 2.0
        painter.drawImage(QRectF(x, y, width, height), image)

    # ------------------------------------------------------------ focus mode
    def _set_focus_mode(self, enabled: bool) -> None:
        """Hide the toolbars and panels so the document can be read undisturbed."""
        if enabled:
            self._pre_focus_ai_visible = self.assistant_dock.isVisible()
            self._pre_focus_visibility = (
                self.toolbar.isVisible(),
                self.left_dock.isVisible(),
                self.right_dock.isVisible(),
            )
            self.toolbar.setVisible(False)
            self.left_dock.setVisible(False)
            self.right_dock.setVisible(False)
            self.assistant_dock.hide()
            self.status_hint.setText("Focus mode - press F11 to leave")
        else:
            toolbar, left, right = self._pre_focus_visibility
            self.toolbar.setVisible(toolbar)
            self.left_dock.setVisible(left)
            self.right_dock.setVisible(right)
            self.assistant_dock.setVisible(getattr(self, "_pre_focus_ai_visible", False))
            self.act_toggle_sidebar.setChecked(left)
            self.act_toggle_margin.setChecked(right)
            self.status_hint.setText("")
        self._focus_mode = bool(enabled)

    # ------------------------------------------------------------ undo/redo
    def undo(self) -> None:
        entry = self.history.pop_undo()
        if entry is None:
            return
        label, state = entry
        self.history.push_redo(label, self.store.annotations if self.store else [])
        self._apply_state(state)
        self.status_hint.setText(f"Undid: {label}")

    def redo(self) -> None:
        entry = self.history.pop_redo()
        if entry is None:
            return
        label, state = entry
        self.history.push_undo(label, self.store.annotations if self.store else [])
        self._apply_state(state)
        self.status_hint.setText(f"Redid: {label}")

    def _apply_state(self, state: List[Annotation]) -> None:
        if self.store is None:
            return
        self.store.annotations = [a.clone() for a in state]
        self.store.dirty = True
        self._after_model_change()

    # ----------------------------------------------------------------- misc
    def _toggle_theme(self) -> None:
        self._apply_theme(DARK if self.theme is LIGHT else LIGHT)

    def _refresh_toolbar_icons(self) -> None:
        """Re-tint the toolbar icons so they stay readable in both themes."""
        color = self.theme.text_dim
        for action, name in self._toolbar_icon_names.items():
            action.setIcon(icon(name, color, 20))

    def _apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        apply_theme(QApplication.instance(), theme)
        self.view.set_theme(theme)
        self.margin_panel.set_theme(theme)
        self.assistant_panel.set_theme(theme)
        self.sidebar.set_theme(theme)
        self.act_theme.setIcon(icon("sun" if theme is DARK else "moon", theme.text_dim, 20))
        self.act_theme.setText("Light theme" if theme is DARK else "Dark theme")
        self._refresh_toolbar_icons()
        self.view.viewport().update()

    def _update_title(self) -> None:
        if self._current_path is None:
            self.setWindowTitle(APP_NAME)
            self.status_save.setText("")
            return
        name = os.path.basename(self._current_path)
        mark = " •" if self.store and self.store.dirty else ""
        self.setWindowTitle(f"{name}{mark} — {APP_NAME}")
        self.status_save.setText("Unsaved changes" if self.store and self.store.dirty else "Saved")

    def _update_actions(self) -> None:
        has_doc = self.store is not None
        for action in (self.act_save, self.act_export_pdf, self.act_export_flat,
                       self.act_export_outline, self.act_undo, self.act_redo,
                       self.act_delete_note, self.act_new_note, self.act_next_note,
                       self.act_prev_note, self.act_print, self.act_export_print):
            action.setEnabled(has_doc)
        self.act_undo.setEnabled(has_doc and self.history.can_undo)
        self.act_redo.setEnabled(has_doc and self.history.can_redo)
        if not has_doc:
            self.status_page.setText("No document")
            self.status_notes.setText("0 notes")
        else:
            self.status_page.setText(
                f"Page {self.view.current_visible_page() + 1} / {self.doc.page_count if self.doc else 0}"
            )

    def _show_shortcuts(self) -> None:
        QMessageBox.information(
            self,
            "Keyboard shortcuts",
            "<b>File</b><br>"
            "Ctrl+O — Open document<br>"
            "Ctrl+S — Save annotations into the PDF<br>"
            "Ctrl+E — Export an annotated PDF copy<br>"
            "Ctrl+P — Print with the margin notes<br>"
            "Ctrl+F — Find in document<br>"
            "Ctrl+G — Go to page<br>"
            "Ctrl+Alt+Down / Up — Next / previous margin note<br><br>"
            "<b>Tools</b> (while the document has focus)<br>"
            "N — Capture a margin note<br>"
            "H — Highlight&nbsp;&nbsp; U — Underline&nbsp;&nbsp; X — Strikethrough<br>"
            "P — Pen&nbsp;&nbsp; R — Rectangle&nbsp;&nbsp; O — Ellipse<br>"
            "A — Arrow&nbsp;&nbsp; L — Line&nbsp;&nbsp; T — Text box<br>"
            "Y — Sticky note&nbsp;&nbsp; E — Eraser<br><br>"
            "<b>View</b><br>"
            "Ctrl+= / Ctrl+- — Zoom<br>"
            "Ctrl+0 — Fit width<br>"
            "F11 — Focus mode (hide the panels)<br>"
            "Page Up / Page Down — Move page<br><br>"
            "<b>Editing</b><br>"
            "Double click — Edit a text box or sticky note<br>"
            "Click — Select an annotation<br>"
            "Del — Delete the selected annotation or note<br>"
            "Ctrl+Z / Ctrl+Shift+Z — Undo / redo<br>"
            "Shift+Enter — New line inside a note",
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<h3>{APP_NAME}</h3>"
            "<p>A MarginNote style PDF marker for Linux: capture passages into a "
            "margin note column, mark them up, and export the result.</p>"
            "<p>Built with PySide6 (Qt 6) and PyMuPDF.</p>"
            "<p>This is an independent implementation and is not affiliated with "
            "MarginNote or its authors.</p>",
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.store is not None and self.store.dirty:
            answer = QMessageBox.question(
                self,
                "Unsaved changes",
                "The document has unsaved annotations.\nSave them into the PDF before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                self.save()
        if self.store is not None:
            try:
                self.store.doc.close()
            except Exception:
                pass
        self.assistant_panel.stop()
        super().closeEvent(event)

