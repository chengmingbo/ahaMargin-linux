"""The margin note pane.

This is the centrepiece of the ahaMargin workflow: a column of note cards
beside the document.  Selecting text in the PDF drops a card into this pane
with the quoted passage attached, and the caret lands in the card so you can
type straight away.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDrag,
    QFont,
    
    QKeyEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.annotations import Annotation
from .icons import icon
from .theme import NOTE_COLORS, Theme

# Custom mime type used to drag a note card into a new position.
MARGIN_MIME = "application/x-ahamargin-note-id"


class ColorSwatchButton(QPushButton):
    """A button that shows a colour and offers a palette popup."""

    colorChanged = Signal(str)

    def __init__(self, color: str = "#ffd54f", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(20, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Change note colour")
        self.clicked.connect(self._show_menu)
        self._refresh()

    @property
    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = color
        self._refresh()

    def _refresh(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(0, 0, 0, 60), 1))
        painter.setBrush(QColor(self._color))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 5, 5)
        painter.end()

    def _show_menu(self) -> None:
        menu = QMenu(self)
        for color in NOTE_COLORS:
            action = QAction(color, menu)
            action.setIcon(icon("palette", color, 14))
            action.triggered.connect(lambda _checked=False, c=color: self._pick(c))
            menu.addAction(action)
        menu.addSeparator()
        more = QAction("Custom colour...", menu)
        more.triggered.connect(self._custom)
        menu.addAction(more)
        menu.exec(self.mapToGlobal(QPoint(0, self.height() + 2)))

    def _pick(self, color: str) -> None:
        self._color = color
        self._refresh()
        self.colorChanged.emit(color)

    def _custom(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Choose a colour")
        if chosen.isValid():
            self._pick(chosen.name())


class NoteCard(QFrame):
    """One margin note: quote, editable body and a small action row."""

    bodyChanged = Signal(str, str, str)
    deleteRequested = Signal(str)
    colorRequested = Signal(str, str)
    jumpRequested = Signal(str)
    collapseToggled = Signal(str, bool)
    focusRequested = Signal(str)

    def __init__(self, annotation: Annotation, theme: Optional[Theme] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.annotation = annotation
        self._theme = theme
        self._loading = False
        self._last_body = ""
        self._drag_origin: Optional[QPoint] = None

        self.setObjectName("NoteCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        # Cards size to their content; the column keeps the slack.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card = QFrame(self)
        card.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(card)

        body = QHBoxLayout(card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.strip = QFrame(card)
        self.strip.setFixedWidth(4)
        strip_layout = QVBoxLayout(self.strip)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.strip)

        content = QWidget(card)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 8, 8, 8)
        content_layout.setSpacing(6)
        body.addWidget(content, 1)

        # ---------------------------------------------------------- header row
        header = QHBoxLayout()
        header.setSpacing(6)

        self.page_badge = QLabel("p1")
        self.page_badge.setObjectName("PageBadge")
        header.addWidget(self.page_badge)

        self.time_label = QLabel("")
        self.time_label.setObjectName("Hint")
        header.addWidget(self.time_label)
        header.addStretch(1)

        self.swatch = ColorSwatchButton(annotation.color)
        header.addWidget(self.swatch)

        self.jump_button = QPushButton()
        self.jump_button.setIcon(icon("anchor", "#6b665e", 15))
        self.jump_button.setFixedSize(24, 22)
        self.jump_button.setToolTip("Jump to the source passage")
        self.jump_button.setFlat(True)
        header.addWidget(self.jump_button)

        self.collapse_button = QPushButton()
        self.collapse_button.setIcon(icon("chevron-down", "#6b665e", 15))
        self.collapse_button.setFixedSize(24, 22)
        self.collapse_button.setToolTip("Collapse")
        self.collapse_button.setFlat(True)
        header.addWidget(self.collapse_button)

        self.delete_button = QPushButton()
        self.delete_button.setIcon(icon("trash", "#6b665e", 15))
        self.delete_button.setFixedSize(24, 22)
        self.delete_button.setToolTip("Delete note")
        self.delete_button.setFlat(True)
        header.addWidget(self.delete_button)

        content_layout.addLayout(header)

        # -------------------------------------------------------------- quote
        self.quote = QLabel()
        self.quote.setWordWrap(True)
        self.quote.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.quote.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.quote.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        quote_font = QFont()
        quote_font.setItalic(True)
        quote_font.setPointSizeF(10.5)
        self.quote.setFont(quote_font)
        self.quote.setCursor(Qt.CursorShape.PointingHandCursor)
        content_layout.addWidget(self.quote)

        # --------------------------------------------------------------- body
        self.editor = QTextEdit()
        self.editor.setPlaceholderText("Write your note...")
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.setFrameShape(QFrame.Shape.NoFrame)
        self.editor.setMinimumHeight(56)
        self.editor.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.editor.setStyleSheet("QTextEdit { background: transparent; border: none; padding: 2px; }")
        content_layout.addWidget(self.editor)

        self._apply_style()

        # ------------------------------------------------------------- wiring
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.installEventFilter(self)
        self.swatch.colorChanged.connect(self._on_color)
        self.jump_button.clicked.connect(lambda: self.jumpRequested.emit(self.annotation.id))
        self.collapse_button.clicked.connect(self._toggle_collapse)
        self.delete_button.clicked.connect(lambda: self.deleteRequested.emit(self.annotation.id))
        self.quote.mousePressEvent = self._quote_clicked  # type: ignore[assignment]
        self.quote.setToolTip("Click to jump to the source passage")
        self.setToolTip("Drag the card header to reorder it")

        self.update_annotation(annotation)

    # ------------------------------------------------------------------ style
    def refresh_icons(self, color: str = "#6b665e") -> None:
        """Re-tint the card buttons for the active theme."""
        self.jump_button.setIcon(icon("anchor", color, 15))
        self.collapse_button.setIcon(
            icon("chevron-right" if self.annotation.collapsed else "chevron-down", color, 15)
        )
        self.delete_button.setIcon(icon("trash", color, 15))

    def _apply_style(self) -> None:
        theme = self._theme
        if theme is None:
            return
        self.strip.setStyleSheet(f"background: {self.annotation.color}; border-radius: 2px;")
        self.setStyleSheet(
            f"QFrame#NoteCard {{"
            f"  background: {theme.surface_alt};"
            f"  border: 1px solid {theme.border};"
            f"  border-radius: 9px;"
            f"}}"
        )
        self.quote.setStyleSheet(f"color: {theme.text_dim};")

    # --------------------------------------------------------------- updates
    def update_annotation(self, annotation: Annotation) -> None:
        self.annotation = annotation
        self.page_badge.setText(f"p{annotation.page + 1}")
        try:
            self.time_label.setText(time.strftime("%H:%M", time.localtime(annotation.created)))
        except Exception:
            self.time_label.setText("")

        capture = annotation.capture.replace("\n", " ").strip()
        self.quote.setText(f"“{capture}”" if capture else "")
        self.quote.setVisible(bool(capture))

        self._loading = True
        if self.editor.toPlainText() != annotation.body:
            self.editor.setPlainText(annotation.body)
        self._loading = False
        self._last_body = self.editor.toPlainText()

        self.swatch.set_color(annotation.color)
        self._apply_style()
        self._apply_collapsed(annotation.collapsed)
        self._auto_grow()

    def _apply_collapsed(self, collapsed: bool) -> None:
        self.editor.setVisible(not collapsed)
        color = self._theme.text_dim if self._theme else "#6b665e"
        if collapsed:
            self.collapse_button.setIcon(icon("chevron-right", color, 15))
            self.collapse_button.setToolTip("Expand")
        else:
            self.collapse_button.setIcon(icon("chevron-down", color, 15))
            self.collapse_button.setToolTip("Collapse")
        preview = self.annotation.body.replace("\n", " ").strip()
        if collapsed and preview:
            self.quote.setText(f"“{self.annotation.capture.replace(chr(10), ' ').strip()}”  {preview}")
        else:
            capture = self.annotation.capture.replace("\n", " ").strip()
            self.quote.setText(f"“{capture}”" if capture else "")

    def _toggle_collapse(self) -> None:
        collapsed = not self.annotation.collapsed
        self.annotation.collapsed = collapsed
        self._apply_collapsed(collapsed)
        self.collapseToggled.emit(self.annotation.id, collapsed)

    def _on_color(self, color: str) -> None:
        self.annotation.color = color
        self._apply_style()
        self.colorRequested.emit(self.annotation.id, color)

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        new_text = self.editor.toPlainText()
        old_text = self._last_body
        if new_text != old_text:
            self._last_body = new_text
            self.bodyChanged.emit(self.annotation.id, new_text, old_text)
        self._auto_grow()

    def _quote_clicked(self, event) -> None:
        self.jumpRequested.emit(self.annotation.id)

    def _auto_grow(self) -> None:
        doc = self.editor.document()
        height = doc.size().height() + 8
        self.editor.setFixedHeight(max(56, min(height, 320)))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.editor and event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent):
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        return False
                    self.editor.setFocus()
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------ dragging
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is not None:
            moved = (event.position().toPoint() - self._drag_origin).manhattanLength()
            if moved > 10:
                self._drag_origin = None
                self._start_drag()
                return
        super().mouseMoveEvent(event)

    def drag_mime(self) -> QMimeData:
        """The payload carried by a reorder drag."""
        mime = QMimeData()
        mime.setData(MARGIN_MIME, self.annotation.id.encode("utf-8"))
        return mime

    def _start_drag(self) -> None:
        drag = QDrag(self)
        drag.setMimeData(self.drag_mime())
        pixmap = self.grab()
        drag.setPixmap(pixmap.scaledToWidth(190, Qt.TransformationMode.SmoothTransformation))
        drag.setHotSpot(QPoint(18, 18))
        drag.exec(Qt.DropAction.MoveAction)

    def focus_editor(self) -> None:
        self.editor.setFocus()
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)


class PageHeader(QWidget):
    """Separator showing which page the following cards belong to."""

    def __init__(self, page: int, count: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 14, 2, 4)
        layout.setSpacing(8)

        title = QLabel(f"Page {page + 1}")
        title.setObjectName("PanelTitle")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)

        self.badge = QLabel(f"{count} note{'' if count == 1 else 's'}")
        self.badge.setObjectName("Hint")
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(line, 1, Qt.AlignmentFlag.AlignVCenter)

    def set_count(self, count: int) -> None:
        """Update the badge, used by the filter to show the visible count."""
        self.badge.setText(f"{count} note{'' if count == 1 else 's'}")


class NoteContainer(QWidget):
    """Hosts the note cards and turns drag and drop into a reorder request."""

    orderChanged = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._panel: Optional["MarginPanel"] = None
        self._drop_y: Optional[int] = None

    def set_panel(self, panel: "MarginPanel") -> None:
        self._panel = panel

    # ------------------------------------------------------------- dragging
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(MARGIN_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(MARGIN_MIME):
            event.ignore()
            return
        self._drop_y = self._insertion_y(event.position().toPoint())
        event.acceptProposedAction()
        self.update()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._drop_y = None
        self.update()

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(MARGIN_MIME):
            event.ignore()
            return
        try:
            note_id = bytes(event.mimeData().data(MARGIN_MIME)).decode("utf-8")
        except Exception:
            note_id = ""
        before = self._note_before(self._insertion_index(event.position().toPoint()))
        self._drop_y = None
        self.update()
        if note_id:
            self.orderChanged.emit(note_id, before)

    # ------------------------------------------------------------- helpers
    def _cards(self) -> List["NoteCard"]:
        if self._panel is None:
            return []
        return self._panel.ordered_cards()

    def _insertion_index(self, pos: QPoint) -> int:
        cards = self._cards()
        for index, card in enumerate(cards):
            if pos.y() < card.geometry().center().y():
                return index
        return len(cards)

    def _note_before(self, index: int) -> str:
        cards = self._cards()
        if 0 <= index < len(cards):
            return cards[index].annotation.id
        return ""

    def _insertion_y(self, pos: QPoint) -> int:
        cards = self._cards()
        if not cards:
            return 0
        index = self._insertion_index(pos)
        if index >= len(cards):
            return cards[-1].geometry().bottom()
        return cards[index].geometry().top()

    # ------------------------------------------------------------- painting
    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._drop_y is None:
            return
        painter = QPainter(self)
        theme = self._panel._theme if self._panel is not None else None
        color = QColor(theme.accent if theme else "#2f6fed")
        painter.setPen(QPen(color, 2))
        painter.drawLine(6, self._drop_y, self.width() - 6, self._drop_y)
        painter.end()


class MarginPanel(QWidget):
    """Column of note cards grouped by page."""

    bodyChanged = Signal(str, str, str)
    deleteRequested = Signal(str)
    colorRequested = Signal(str, str)
    jumpRequested = Signal(str)
    collapseToggled = Signal(str, bool)
    focusRequested = Signal(str)
    exportRequested = Signal()
    addBlankRequested = Signal()
    orderChanged = Signal(str, str)
    reorderRejected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cards: Dict[str, NoteCard] = {}
        self._groups: List[Tuple[object, List[NoteCard]]] = []
        self._notes: List[Annotation] = []
        self._theme: Optional[Theme] = None
        self._highlighted: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ------------------------------------------------------------- header
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 2, 0)
        header_layout.setSpacing(6)

        title = QLabel("MARGIN NOTES")
        title.setObjectName("PanelTitle")
        header_layout.addWidget(title)

        self.count_badge = QLabel("0")
        self.count_badge.setObjectName("PageBadge")
        header_layout.addWidget(self.count_badge)
        header_layout.addStretch(1)

        self.add_button = QPushButton()
        self.add_button.setIcon(icon("plus", "#6b665e", 15))
        self.add_button.setFixedSize(26, 24)
        self.add_button.setToolTip("Add an empty note on the current page")
        self.add_button.setFlat(True)
        header_layout.addWidget(self.add_button)

        self.export_button = QPushButton()
        self.export_button.setIcon(icon("export", "#6b665e", 15))
        self.export_button.setFixedSize(26, 24)
        self.export_button.setToolTip("Export notes")
        self.export_button.setFlat(True)
        header_layout.addWidget(self.export_button)

        root.addWidget(header)

        # ------------------------------------------------------------- filter
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(2, 0, 2, 0)
        filter_row.setSpacing(6)
        self.filter_field = QLineEdit()
        self.filter_field.setPlaceholderText("Filter notes...")
        self.filter_field.setClearButtonEnabled(True)
        self.filter_field.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_field, 1)
        root.addLayout(filter_row)

        # ------------------------------------------------------------- canvas
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.scroll, 1)

        self.container = NoteContainer()
        self.container.set_panel(self)
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(6, 2, 8, 16)
        self.container_layout.setSpacing(8)
        self.container_layout.addStretch(1)
        self.scroll.setWidget(self.container)

        self.empty_label = QLabel(
            "Select text in the document to capture it here.\n"
            "The quoted passage is attached automatically."
        )
        self.empty_label.setObjectName("Hint")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setContentsMargins(18, 26, 18, 0)
        self.container_layout.insertWidget(0, self.empty_label)

        self.add_button.clicked.connect(self.addBlankRequested.emit)
        self.export_button.clicked.connect(self.exportRequested.emit)
        self.container.orderChanged.connect(self._handle_order_change)

    # ------------------------------------------------------------------ setup
    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        for card in self._cards.values():
            card._theme = theme
            card._apply_style()
        self.refresh_icons()

    def refresh_icons(self) -> None:
        """Re-tint the panel icons for the active theme."""
        color = self._theme.text_dim if self._theme else "#6b665e"
        self.add_button.setIcon(icon("plus", color, 15))
        self.export_button.setIcon(icon("export", color, 15))
        for card in self._cards.values():
            card.refresh_icons(color)

    # ------------------------------------------------------------------ data
    def set_notes(self, notes: List[Annotation]) -> None:
        self._notes = list(notes)
        self.rebuild()

    def rebuild(self) -> None:
        """Rebuild the card list from the current model."""
        # remove widgets that are no longer present
        wanted = {note.id for note in self._notes}
        for card_id in list(self._cards):
            if card_id not in wanted:
                card = self._cards.pop(card_id)
                card.setParent(None)
                card.deleteLater()

        # refresh existing cards in place
        for note in self._notes:
            card = self._cards.get(note.id)
            if card is not None:
                card.update_annotation(note)
                card.setParent(None)

        # (re)insert in order
        ordered: List[QWidget] = []
        groups: List[Tuple[object, List[NoteCard]]] = []
        last_page: Optional[int] = None
        header_widget = None
        group_cards: List[NoteCard] = []
        for note in self._notes:
            if note.page != last_page:
                if header_widget is not None:
                    groups.append((header_widget, group_cards))
                last_page = note.page
                page_notes = [n for n in self._notes if n.page == note.page]
                header_widget = PageHeader(note.page, len(page_notes))
                group_cards = []
                ordered.append(header_widget)
            card = self._cards.get(note.id)
            if card is None:
                card = NoteCard(note, self._theme)
                self._cards[note.id] = card
                self._wire_card(card)
            group_cards.append(card)
            ordered.append(card)
        if header_widget is not None:
            groups.append((header_widget, group_cards))
        self._groups = groups

        # rebuild the layout
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.empty_label:
                widget.setParent(None)
        self.container_layout.insertWidget(0, self.empty_label)
        for widget in ordered:
            self.container_layout.addWidget(widget)
        self.container_layout.addStretch(1)

        self._apply_filter(self.filter_field.text())
        self._apply_highlight()

    def _wire_card(self, card: NoteCard) -> None:
        card.bodyChanged.connect(self.bodyChanged.emit)
        card.deleteRequested.connect(self.deleteRequested.emit)
        card.colorRequested.connect(self.colorRequested.emit)
        card.jumpRequested.connect(self.jumpRequested.emit)
        card.collapseToggled.connect(self.collapseToggled.emit)
        card.focusRequested.connect(self.focusRequested.emit)

    def refresh_card(self, annotation: Annotation) -> None:
        card = self._cards.get(annotation.id)
        if card is not None:
            card.update_annotation(annotation)

    # ---------------------------------------------------------------- actions
    def focus_note(self, annotation_id: str, select: bool = True) -> None:
        card = self._cards.get(annotation_id)
        if card is None:
            return
        self.scroll.ensureWidgetVisible(card, 0, 40)
        if select:
            card.focus_editor()

    def highlight_note(self, annotation_id: Optional[str]) -> None:
        self._highlighted = annotation_id
        self._apply_highlight()

    def _apply_highlight(self) -> None:
        theme = self._theme
        for card_id, card in self._cards.items():
            active = card_id == self._highlighted
            if theme is None:
                continue
            if active:
                border = theme.accent
                background = theme.accent_soft
            else:
                border = theme.border
                background = theme.surface_alt
            card.setStyleSheet(
                f"QFrame#NoteCard {{"
                f"  background: {background};"
                f"  border: 1px solid {border};"
                f"  border-radius: 9px;"
                f"}}"
            )

    def _apply_filter(self, text: str) -> None:
        """Hide the cards that do not match the filter text."""
        query = (text or "").strip().lower()
        shown = 0
        for header, cards in self._groups:
            visible_cards = []
            for card in cards:
                haystack = (
                    card.annotation.body + " " + card.annotation.capture
                ).lower()
                match = (not query) or (query in haystack)
                card.setVisible(match)
                if match:
                    visible_cards.append(card)
            shown += len(visible_cards)
            header.setVisible(bool(visible_cards))
            header.set_count(len(visible_cards))

        total = len(self._notes)
        if query:
            self.count_badge.setText(f"{shown}/{total}")
        else:
            self.count_badge.setText(str(total))

        if total == 0:
            self.empty_label.setText(
                "Select text in the document to capture it here.\n"
                "The quoted passage is attached automatically."
            )
            self.empty_label.setVisible(True)
        elif shown == 0:
            self.empty_label.setText(f"No notes match \u201c{(text or '').strip()}\u201d.")
            self.empty_label.setVisible(True)
        else:
            self.empty_label.setVisible(False)

    def ordered_cards(self) -> List[NoteCard]:
        """The note cards in the order they appear in the column."""
        cards: List[NoteCard] = []
        for index in range(self.container_layout.count()):
            widget = self.container_layout.itemAt(index).widget()
            if isinstance(widget, NoteCard):
                cards.append(widget)
        return cards

    def _handle_order_change(self, note_id: str, before_id: str) -> None:
        """Move a note card to a new position inside its own page group."""
        note = next((n for n in self._notes if n.id == note_id), None)
        if note is None:
            return

        before = next((n for n in self._notes if n.id == before_id), None)
        if before is not None and before.page != note.page:
            # A card belongs to the page its quote lives on, so it cannot be
            # dropped into a different page group.
            self.reorderRejected.emit(note_id)
            return

        page_notes = [n for n in self._notes if n.page == note.page]
        page_notes.remove(note)
        if before is None:
            page_notes.append(note)
        else:
            page_notes.insert(page_notes.index(before), note)

        for index, item in enumerate(page_notes):
            item.order = float(index)

        self._notes.sort(key=lambda a: (a.page, a.order))
        self.rebuild()
        self.orderChanged.emit(note_id, before_id)

    def note_count(self) -> int:
        return len(self._notes)

    @property
    def highlighted_id(self) -> Optional[str]:
        return self._highlighted
