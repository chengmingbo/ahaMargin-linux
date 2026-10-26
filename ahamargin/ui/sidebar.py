"""Left hand sidebar: PDF outline, notes outline, page thumbnails and search."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.annotations import Annotation
from ..core.document import PdfDocument, SearchHit
from .icons import color_dot, icon
from .theme import NOTE_COLORS, Theme


class OutlineTree(QTreeWidget):
    """PDF bookmarks; double clicking jumps to the page."""

    pageActivated = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setIndentation(14)
        self.itemDoubleClicked.connect(self._on_double_click)

    def load_outline(self, entries: List[Tuple[int, str, int]]) -> None:
        self.clear()
        stack: List[Tuple[int, QTreeWidgetItem]] = []
        for level, title, page in entries:
            item = QTreeWidgetItem([f"{title}"])
            item.setData(0, Qt.ItemDataRole.UserRole, max(0, page - 1))
            item.setToolTip(0, f"{title}  (page {page})")
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                stack[-1][1].addChild(item)
            else:
                self.addTopLevelItem(item)
            stack.append((level, item))
        self.expandAll()

    def _on_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if page is not None:
            self.pageActivated.emit(int(page))


class NotesTree(QTreeWidget):
    """Tree of margin notes grouped by page."""

    noteActivated = Signal(str, int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setIndentation(14)
        self.itemDoubleClicked.connect(self._on_double_click)

    def load_notes(self, notes: List[Annotation]) -> None:
        self.clear()
        by_page: Dict[int, List[Annotation]] = {}
        for note in notes:
            by_page.setdefault(note.page, []).append(note)
        for page in sorted(by_page):
            items = by_page[page]
            parent = QTreeWidgetItem([f"Page {page + 1}  ({len(items)})"])
            parent.setData(0, Qt.ItemDataRole.UserRole, page)
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self.addTopLevelItem(parent)
            for note in items:
                preview = (note.body or note.capture or "").strip().split("\n")[0]
                label = preview or "(empty note)"
                child = QTreeWidgetItem([label[:80]])
                child.setData(0, Qt.ItemDataRole.UserRole, page)
                child.setData(
                    0, Qt.ItemDataRole.UserRole + 1, note.id
                )
                child.setToolTip(0, note.capture or label)
                parent.addChild(child)
        self.expandAll()

    def _on_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        page = item.data(0, Qt.ItemDataRole.UserRole)
        note_id = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if note_id is not None:
            self.noteActivated.emit(str(note_id), int(page or 0))


class AnnotationTree(QTreeWidget):
    """Every annotation in the document, grouped by page.

    The context menu is where marks are managed: jump to them, recolour
    them, turn a mark into a margin note, or delete them.
    """

    annotationActivated = Signal(str, int)
    colorRequested = Signal(str, str)
    deleteRequested = Signal(str)
    convertRequested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setIndentation(14)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemDoubleClicked.connect(self._on_double_click)

    # ------------------------------------------------------------------ data
    def load_annotations(self, annotations: List[Annotation]) -> None:
        self.clear()
        by_page: Dict[int, List[Annotation]] = {}
        for annotation in annotations:
            by_page.setdefault(annotation.page, []).append(annotation)

        for page in sorted(by_page):
            items = by_page[page]
            parent = QTreeWidgetItem([f"Page {page + 1}  ({len(items)})"])
            parent.setData(0, Qt.ItemDataRole.UserRole, "")
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self.addTopLevelItem(parent)

            for annotation in items:
                child = QTreeWidgetItem([self._label_for(annotation)])
                child.setIcon(0, color_dot(annotation.color))
                child.setData(0, Qt.ItemDataRole.UserRole, annotation.id)
                child.setData(0, Qt.ItemDataRole.UserRole + 1, annotation.page)
                child.setData(0, Qt.ItemDataRole.UserRole + 2, annotation.is_margin)
                child.setToolTip(
                    0, f"{annotation.kind.label}  ·  {annotation.color}"
                )
                parent.addChild(child)
        self.expandAll()

    @staticmethod
    def _label_for(annotation: Annotation) -> str:
        if annotation.is_margin:
            preview = (annotation.body or annotation.capture or "").strip()
            return (preview.split("\n")[0][:64] or "Margin note")
        if annotation.capture:
            return annotation.capture[:64]
        if annotation.text:
            return annotation.text[:64]
        return annotation.kind.label

    # -------------------------------------------------------------- actions
    def _selected_annotation(self) -> Optional[str]:
        item = self.currentItem()
        if item is None:
            return None
        value = item.data(0, Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def _on_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        annotation_id = item.data(0, Qt.ItemDataRole.UserRole)
        page = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if annotation_id and page is not None:
            self.annotationActivated.emit(str(annotation_id), int(page))

    def _show_menu(self, position) -> None:
        item = self.itemAt(position)
        if item is None:
            return
        annotation_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not annotation_id:
            return
        page = int(item.data(0, Qt.ItemDataRole.UserRole + 1) or 0)
        is_margin = bool(item.data(0, Qt.ItemDataRole.UserRole + 2))

        menu = QMenu(self)

        go = QAction("Go to annotation", menu)
        go.triggered.connect(
            lambda: self.annotationActivated.emit(str(annotation_id), page)
        )
        menu.addAction(go)

        colour_menu = menu.addMenu("Colour")
        for colour in NOTE_COLORS:
            action = QAction(colour, colour_menu)
            action.setIcon(color_dot(colour, 12))
            action.triggered.connect(
                lambda _checked=False, c=colour, a=str(annotation_id):
                self.colorRequested.emit(a, c)
            )
            colour_menu.addAction(action)

        if not is_margin:
            convert = QAction("Turn into a margin note", menu)
            convert.triggered.connect(
                lambda: self.convertRequested.emit(str(annotation_id))
            )
            menu.addAction(convert)

        menu.addSeparator()
        delete = QAction("Delete", menu)
        delete.triggered.connect(
            lambda: self.deleteRequested.emit(str(annotation_id))
        )
        menu.addAction(delete)

        menu.exec(self.viewport().mapToGlobal(position))


class ThumbnailPanel(QListWidget):
    """Lazy page thumbnail grid."""

    pageActivated = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(104, 132))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setWrapping(True)
        self.setSpacing(10)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self._doc: Optional[PdfDocument] = None
        self._cache: Dict[int, QIcon] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._render_visible)
        self.verticalScrollBar().valueChanged.connect(self._schedule)
        self.itemClicked.connect(self._on_click)

    def set_document(self, doc: Optional[PdfDocument]) -> None:
        self._doc = doc
        self._cache.clear()
        self.clear()
        if not doc:
            return
        for page in range(doc.page_count):
            item = QListWidgetItem(f"{page + 1}")
            item.setData(Qt.ItemDataRole.UserRole, page)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            item.setSizeHint(QSize(120, 156))
            self.addItem(item)
        self._schedule()

    def _schedule(self) -> None:
        self._timer.start()

    def _render_visible(self) -> None:
        if not self._doc:
            return
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()
        for index in range(self.count()):
            item = self.item(index)
            rect = self.visualItemRect(item)
            if rect.bottom() < top - 40 or rect.top() > bottom + 40:
                continue
            page = int(item.data(Qt.ItemDataRole.UserRole))
            if page in self._cache:
                continue
            try:
                image = self._doc.thumbnail(page, max_width=104)
            except Exception:
                continue
            self._cache[page] = QIcon(QPixmap.fromImage(image))
            item.setIcon(self._cache[page])

    def _on_click(self, item: QListWidgetItem) -> None:
        page = item.data(Qt.ItemDataRole.UserRole)
        if page is not None:
            self.pageActivated.emit(int(page))


class SearchPanel(QWidget):
    """Full text search with a result list."""

    searchRequested = Signal(str)
    hitActivated = Signal(int, object)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Search this document...")
        self.field.setClearButtonEnabled(True)
        self.field.returnPressed.connect(self._submit)
        row.addWidget(self.field, 1)

        self.button = QPushButton()
        self.button.setIcon(icon("search", "#ffffff", 15))
        self.button.setObjectName("Primary")
        self.button.setFixedWidth(38)
        self.button.clicked.connect(self._submit)
        row.addWidget(self.button)
        layout.addLayout(row)

        self.status = QLabel("Type to search the whole document.")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.results = QListWidget()
        self.results.itemClicked.connect(self._on_result)
        self.results.setAlternatingRowColors(False)
        layout.addWidget(self.results, 1)

    def _submit(self) -> None:
        self.searchRequested.emit(self.field.text())

    def focus_field(self) -> None:
        self.field.setFocus()
        self.field.selectAll()

    def show_hits(self, hits: List[SearchHit], query: str) -> None:
        self.results.clear()
        if not query.strip():
            self.status.setText("Type to search the whole document.")
            return
        if not hits:
            self.status.setText(f"No matches for “{query.strip()}”.")
            return
        self.status.setText(f"{len(hits)} match{'' if len(hits) == 1 else 'es'} for “{query.strip()}”.")
        for hit in hits:
            item = QListWidgetItem(
                f"p{hit.page + 1}   {hit.context[:110]}" if hit.context else f"p{hit.page + 1}"
            )
            item.setData(Qt.ItemDataRole.UserRole, hit.page)
            item.setData(Qt.ItemDataRole.UserRole + 1, hit.rect)
            item.setToolTip(hit.context)
            self.results.addItem(item)

    def _on_result(self, item: QListWidgetItem) -> None:
        page = item.data(Qt.ItemDataRole.UserRole)
        rect = item.data(Qt.ItemDataRole.UserRole + 1)
        if page is not None:
            self.hitActivated.emit(int(page), rect)


class Sidebar(QTabWidget):
    """Container tying the four side panels together."""

    pageActivated = Signal(int)
    noteActivated = Signal(str, int)
    searchRequested = Signal(str)
    hitActivated = Signal(int, object)
    annotationActivated = Signal(str, int)
    annotationColorRequested = Signal(str, str)
    annotationDeleteRequested = Signal(str)
    annotationConvertRequested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setDocumentMode(True)

        self.outline = OutlineTree()
        self.notes = NotesTree()
        self.pages = ThumbnailPanel()
        self.search = SearchPanel()

        self.marks = AnnotationTree()

        # Text-only tabs keep every panel reachable in a narrow dock.
        self.addTab(self.outline, QIcon(), "Outline")
        self.addTab(self.notes, QIcon(), "Notes")
        self.addTab(self.marks, QIcon(), "Marks")
        self.addTab(self.pages, QIcon(), "Pages")
        self.addTab(self.search, QIcon(), "Search")
        self.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        self.tabBar().setExpanding(False)
        self.tabBar().setUsesScrollButtons(False)
        self.setMinimumWidth(300)

        self.outline.pageActivated.connect(self.pageActivated.emit)
        self.pages.pageActivated.connect(self.pageActivated.emit)
        self.search.searchRequested.connect(self.searchRequested.emit)
        self.search.hitActivated.connect(self.hitActivated.emit)
        self.notes.noteActivated.connect(self.noteActivated.emit)
        self.marks.annotationActivated.connect(self.annotationActivated.emit)
        self.marks.colorRequested.connect(self.annotationColorRequested.emit)
        self.marks.deleteRequested.connect(self.annotationDeleteRequested.emit)
        self.marks.convertRequested.connect(self.annotationConvertRequested.emit)

    def set_document(self, doc: Optional[PdfDocument]) -> None:
        self.outline.load_outline(doc.outline() if doc else [])
        self.pages.set_document(doc)
        self.search.show_hits([], "")

    def load_notes(self, notes: List[Annotation]) -> None:
        self.notes.load_notes(notes)

    def load_annotations(self, annotations: List[Annotation]) -> None:
        self.marks.load_annotations(annotations)

    def set_theme(self, theme: Theme) -> None:
        for index in range(self.count()):
            self.setTabIcon(index, self.tabIcon(index))
