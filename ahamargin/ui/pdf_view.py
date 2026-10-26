"""The PDF canvas.

A custom ``QAbstractScrollArea`` that renders pages with PyMuPDF, paints the
annotation overlay, handles text selection (the heart of the ahaMargin
workflow) and implements the drawing tools.

Coordinates
-----------
The widget works in *widget pixels*.  PDF user space is used everywhere else in
the application.  ``_to_pdf`` and ``_to_widget`` convert between the two; page
origins are honoured so that PDFs with a non-zero media box behave.
"""

from __future__ import annotations

import math
import time
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QAbstractScrollArea, QInputDialog, QLineEdit, QWidget

from ..core.annotations import AnnKind, Annotation
from ..core.document import PdfDocument, Word
from ..core.geometry import Point, Rect
from .theme import Theme
from .tools import Tool

PAGE_MARGIN = 26
PAGE_GAP = 20
FLASH_DURATION_MS = 1400
MIN_ZOOM = 0.15
MAX_ZOOM = 8.0


class PdfView(QAbstractScrollArea):
    """Scrollable, zoomable PDF canvas with annotation authoring."""

    selectionMade = Signal(int, str, object)
    annotationCreated = Signal(object)
    annotationRemoved = Signal(object)
    annotationPicked = Signal(object)
    annotationEditRequested = Signal(object)
    pageChanged = Signal(int)
    zoomChanged = Signal(float)
    hintChanged = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setViewportMargins(0, 0, 0, 0)

        self._doc: Optional[PdfDocument] = None
        self._annotations: List[Annotation] = []
        self._theme: Optional[Theme] = None

        self._zoom = 1.0
        self._fit_width = True
        self._fit_page = False

        self._pages: List[Tuple[int, float, float, float, float]] = []
        self._content_height = 0.0
        self._content_width = 0.0

        self._tool = Tool.POINTER
        self._tool_color = "#ffd54f"
        self._tool_opacity = 1.0

        # text selection state
        self._sel_words: List[Word] = []
        self._sel_rects: List[Rect] = []
        self._sel_text = ""
        self._sel_page = -1
        self._sel_anchor: Optional[Word] = None
        self._sel_head: Optional[Word] = None

        # drawing state
        self._draw_start: Optional[QPointF] = None
        self._draw_points: List[Point] = []
        self._draw_rect: Optional[Rect] = None
        self._pressing_annotation: Optional[Annotation] = None
        self._press_pos: Optional[QPoint] = None
        self._selected_annotation: Optional[Annotation] = None

        self._current_page = 0

        # "flash" highlight used to draw the eye to a passage after a jump
        self._flash: Optional[Tuple[int, Rect, float]] = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(30)
        self._flash_timer.timeout.connect(self._on_flash_tick)

    # ------------------------------------------------------------------ setup
    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.viewport().update()

    def set_document(self, doc: Optional[PdfDocument]) -> None:
        self._doc = doc
        self._sel_words = []
        self._sel_rects = []
        self._sel_text = ""
        self._sel_page = -1
        self._sel_anchor = None
        self._sel_head = None
        self._rebuild_layout()
        self.scroll_to_top()

    def set_annotations(self, annotations: Sequence[Annotation]) -> None:
        self._annotations = list(annotations)
        self.viewport().update()

    def set_tool(self, tool: Tool) -> None:
        self._tool = tool
        self._clear_selection()
        self.viewport().update()
        self.hintChanged.emit(self._tool.hint)

    def set_tool_color(self, color: str) -> None:
        self._tool_color = color
        self.viewport().update()

    def set_tool_opacity(self, opacity: float) -> None:
        self._tool_opacity = opacity
        self.viewport().update()

    @property
    def tool(self) -> Tool:
        return self._tool

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def document(self) -> Optional[PdfDocument]:
        return self._doc

    @property
    def selected_annotation(self) -> Optional[Annotation]:
        """The annotation the user last clicked on the canvas."""
        return self._selected_annotation

    def set_selected_annotation(self, annotation: Optional[Annotation]) -> None:
        self._selected_annotation = annotation
        self.viewport().update()

    # ----------------------------------------------------------------- layout
    def _rebuild_layout(self) -> None:
        if not self._doc:
            self._pages = []
            self._content_height = 0.0
            self._content_width = 0.0
            self._update_scrollbars()
            return

        entries: List[Tuple[int, float, float, float, float]] = []
        widths: List[float] = []
        y = PAGE_MARGIN
        for page in range(self._doc.page_count):
            width, height = self._doc.page_size(page)
            widths.append(width * self._zoom)
            entries.append((page, y, height * self._zoom, width * self._zoom, 0.0))
            y += height * self._zoom + PAGE_GAP
        max_width = max(widths) if widths else 0.0
        rebuilt: List[Tuple[int, float, float, float, float]] = []
        for entry, page_width in zip(entries, widths):
            x = PAGE_MARGIN + (max_width - page_width) / 2.0
            rebuilt.append((entry[0], entry[1], entry[2], entry[3], x))
        self._pages = rebuilt
        self._content_height = y
        self._content_width = max_width + 2 * PAGE_MARGIN
        self._update_scrollbars()

    def _update_scrollbars(self) -> None:
        viewport = self.viewport()
        vbar = self.verticalScrollBar()
        hbar = self.horizontalScrollBar()
        vbar.setRange(0, max(0, int(self._content_height - viewport.height())))
        hbar.setRange(0, max(0, int(self._content_width - viewport.width())))
        vbar.setPageStep(max(1, viewport.height() - 20))
        hbar.setPageStep(max(1, viewport.width() - 20))
        vbar.setSingleStep(24)
        hbar.setSingleStep(24)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        if self._fit_width or self._fit_page:
            self._apply_fit()
        else:
            self._rebuild_layout()

    # ------------------------------------------------------------------- zoom
    def set_zoom(self, zoom: float, anchor: Optional[QPoint] = None) -> None:
        if not self._doc:
            return
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, float(zoom)))
        if abs(zoom - self._zoom) < 1e-4:
            return
        old_zoom = self._zoom
        old_scroll_y = self.verticalScrollBar().value()
        old_scroll_x = self.horizontalScrollBar().value()

        self._zoom = zoom
        self._fit_width = False
        self._fit_page = False
        self._rebuild_layout()

        if anchor is not None:
            ratio = zoom / old_zoom if old_zoom else 1.0
            self.verticalScrollBar().setValue(int(old_scroll_y * ratio))
            self.horizontalScrollBar().setValue(int(old_scroll_x * ratio))
        else:
            self.verticalScrollBar().setValue(int(old_scroll_y * (zoom / old_zoom if old_zoom else 1.0)))
        self.zoomChanged.emit(self._zoom)
        self.viewport().update()

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / 1.25)

    def fit_width(self) -> None:
        self._fit_width = True
        self._fit_page = False
        self._apply_fit()

    def fit_page(self) -> None:
        self._fit_page = True
        self._fit_width = False
        self._apply_fit()

    def actual_size(self) -> None:
        self._fit_width = False
        self._fit_page = False
        self.set_zoom(1.0)

    def _apply_fit(self) -> None:
        if not self._doc or self._doc.page_count == 0:
            return
        viewport = self.viewport()
        usable_width = max(50, viewport.width() - 2 * PAGE_MARGIN)
        usable_height = max(50, viewport.height() - 2 * PAGE_MARGIN)
        width, height = self._doc.page_size(0)
        if self._fit_page:
            zoom = min(usable_width / max(width, 1.0), usable_height / max(height, 1.0))
        else:
            zoom = usable_width / max(width, 1.0)
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        self._zoom = zoom
        self._rebuild_layout()
        self.zoomChanged.emit(self._zoom)
        self.viewport().update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta == 0:
                return
            factor = 1.15 if delta > 0 else 1 / 1.15
            self.set_zoom(self._zoom * factor, event.position().toPoint())
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------ navigation
    def scroll_to_top(self) -> None:
        self.verticalScrollBar().setValue(0)
        self._emit_page(0)

    def goto_page(self, page: int, top: bool = True) -> None:
        if not self._doc:
            return
        page = max(0, min(page, self._doc.page_count - 1))
        for entry in self._pages:
            if entry[0] == page:
                if top:
                    self.verticalScrollBar().setValue(max(0, int(entry[1] - PAGE_MARGIN)))
                self._emit_page(page)
                self.viewport().update()
                return

    def scroll_to_rect(self, page: int, rect: Rect, padding: float = 60.0) -> None:
        """Scroll so that a PDF-space rect on ``page`` is visible."""
        if not self._doc:
            return
        for entry in self._pages:
            if entry[0] != page:
                continue
            _, page_y, _, _, page_x = entry
            target = page_y + (rect.y0 - padding) * self._zoom
            self.verticalScrollBar().setValue(max(0, int(target)))
            self._emit_page(page)
            self.viewport().update()
            return

    def _emit_page(self, page: int) -> None:
        if page != self._current_page:
            self._current_page = page
            self.pageChanged.emit(page)

    def current_visible_page(self) -> int:
        if not self._pages:
            return 0
        scroll_y = self.verticalScrollBar().value()
        middle = scroll_y + self.viewport().height() / 2
        for page, y, height, _, _ in self._pages:
            if y <= middle <= y + height:
                return page
        return self._pages[-1][0] if middle > self._pages[-1][1] else self._pages[0][0]

    # ----------------------------------------------------------- coordinates
    def _to_pdf(self, pos: QPoint) -> Optional[Tuple[int, float, float]]:
        if not self._doc:
            return None
        scroll_y = self.verticalScrollBar().value()
        scroll_x = self.horizontalScrollBar().value()
        wy = pos.y() + scroll_y
        wx = pos.x() + scroll_x
        for page, page_y, page_h, _, page_x in self._pages:
            if page_y <= wy < page_y + page_h:
                origin_x, origin_y = self._doc.page_origin(page)
                x = (wx - page_x) / self._zoom + origin_x
                y = (wy - page_y) / self._zoom + origin_y
                return page, x, y
        return None

    def _page_origin(self, page: int) -> Tuple[float, float]:
        if not self._doc:
            return (0.0, 0.0)
        return self._doc.page_origin(page)

    # -------------------------------------------------------------- painting
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        theme = self._theme
        bg = QColor(theme.window if theme else "#eceae6")
        painter.fillRect(self.viewport().rect(), bg)

        if not self._doc:
            painter.end()
            return

        scroll_y = self.verticalScrollBar().value()
        scroll_x = self.horizontalScrollBar().value()
        viewport_rect = self.viewport().rect()
        dpr = self.viewport().devicePixelRatioF()

        for page, page_y, page_h, page_w, page_x in self._pages:
            top = page_y - scroll_y
            if top > viewport_rect.height() or top + page_h < 0:
                continue
            left = page_x - scroll_x
            target = QRectF(left, top, page_w, page_h)

            # paper shadow
            painter.setPen(Qt.PenStyle.NoPen)
            shadow = QColor(theme.paper_shadow if theme else "#c9c4ba")
            shadow.setAlpha(70)
            painter.setBrush(shadow)
            painter.drawRoundedRect(target.adjusted(3, 4, 3, 5), 3, 3)

            image = self._doc.render(page, self._zoom, dpr)
            painter.drawImage(target, image)

            self._paint_annotations(painter, page, target)

            # page separator
            painter.setPen(QPen(QColor(theme.border if theme else "#dcd8d1"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(target)

        self._paint_selection(painter)
        self._paint_flash(painter)
        self._paint_pending_draw(painter)
        painter.end()

    # ---------------------------------------------------- annotation overlay
    def _paint_annotations(self, painter: QPainter, page: int, target: QRectF) -> None:
        origin_x, origin_y = self._page_origin(page)
        zoom = self._zoom

        painter.save()
        painter.translate(target.left(), target.top())
        painter.scale(zoom, zoom)
        painter.translate(-origin_x, -origin_y)

        for annotation in self._annotations:
            if annotation.page != page:
                continue
            color = QColor(annotation.color)
            color.setAlphaF(max(0.05, min(1.0, annotation.opacity)))
            pen = QPen(color)
            pen.setWidthF(max(0.6, 1.6 / zoom))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

            kind = annotation.kind
            if kind is AnnKind.HIGHLIGHT:
                for rect in annotation.rects:
                    fill = QColor(annotation.color)
                    fill.setAlphaF(max(0.05, min(1.0, annotation.opacity)) * 0.36)
                    painter.fillRect(QRectF(rect.x0, rect.y0, rect.width, rect.height), fill)
            elif kind is AnnKind.MARGIN:
                for rect in annotation.rects:
                    fill = QColor(annotation.color)
                    fill.setAlphaF(max(0.05, min(1.0, annotation.opacity)) * 0.28)
                    painter.fillRect(QRectF(rect.x0, rect.y0, rect.width, rect.height), fill)
                    painter.setPen(QPen(QColor(annotation.color), max(0.8, 2.2 / zoom)))
                    painter.drawLine(
                        QPointF(rect.x0, rect.y1), QPointF(rect.x1, rect.y1)
                    )
            elif kind is AnnKind.UNDERLINE:
                for rect in annotation.rects:
                    painter.setPen(QPen(QColor(annotation.color), max(1.0, 1.8 / zoom)))
                    y = rect.y1 - 1.0
                    painter.drawLine(QPointF(rect.x0, y), QPointF(rect.x1, y))
            elif kind is AnnKind.STRIKEOUT:
                for rect in annotation.rects:
                    painter.setPen(QPen(QColor(annotation.color), max(1.0, 1.8 / zoom)))
                    y = (rect.y0 + rect.y1) / 2
                    painter.drawLine(QPointF(rect.x0, y), QPointF(rect.x1, y))
            elif kind is AnnKind.SQUIGGLY:
                for rect in annotation.rects:
                    painter.setPen(QPen(QColor(annotation.color), max(1.0, 1.6 / zoom)))
                    path = QPainterPath()
                    y = rect.y1 - 1.0
                    step = max(3.0, 4.0 / zoom)
                    x = rect.x0
                    up = True
                    path.moveTo(x, y)
                    while x < rect.x1:
                        x += step
                        path.lineTo(min(x, rect.x1), y - (3.0 if up else 0.0))
                        up = not up
                    painter.drawPath(path)
            elif kind is AnnKind.INK:
                painter.setPen(pen)
                for stroke in annotation.paths:
                    if len(stroke) < 2:
                        if len(stroke) == 1:
                            painter.drawPoint(QPointF(stroke[0].x, stroke[0].y))
                        continue
                    path = QPainterPath()
                    path.moveTo(stroke[0].x, stroke[0].y)
                    for point in stroke[1:]:
                        path.lineTo(point.x, point.y)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(path)
            elif kind is AnnKind.RECT:
                for rect in annotation.rects:
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(QRectF(rect.x0, rect.y0, rect.width, rect.height))
            elif kind is AnnKind.ELLIPSE:
                for rect in annotation.rects:
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(QRectF(rect.x0, rect.y0, rect.width, rect.height))
            elif kind is AnnKind.LINE:
                for rect in annotation.rects:
                    painter.setPen(pen)
                    painter.drawLine(QPointF(rect.x0, rect.y0), QPointF(rect.x1, rect.y1))
            elif kind is AnnKind.ARROW:
                for rect in annotation.rects:
                    painter.setPen(pen)
                    start = QPointF(rect.x0, rect.y0)
                    end = QPointF(rect.x1, rect.y1)
                    painter.drawLine(start, end)
                    self._draw_arrow_head(painter, start, end, color, zoom)
            elif kind is AnnKind.TEXT:
                for rect in annotation.rects:
                    self._draw_text_box(painter, rect, annotation.text, color, zoom)
            elif kind is AnnKind.NOTE:
                for rect in annotation.rects:
                    self._draw_sticky(painter, rect, color, zoom)

            if annotation is self._selected_annotation:
                self._draw_selection_box(painter, annotation, zoom)

        painter.restore()

    def _draw_selection_box(self, painter: QPainter, annotation: Annotation,
                            zoom: float) -> None:
        """Outline the annotation the user has selected."""
        box = annotation.bounds
        if box.is_empty:
            return
        theme = self._theme
        accent = QColor(theme.accent if theme else "#2f6fed")
        pen = QPen(accent)
        pen.setWidthF(max(1.0, 1.8 / zoom))
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(
            QRectF(box.x0 - 2, box.y0 - 2, box.width + 4, box.height + 4)
        )

    def _draw_arrow_head(self, painter: QPainter, start: QPointF, end: QPointF,
                         color: QColor, zoom: float) -> None:
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        size = max(6.0, 9.0 / zoom)
        for offset in (2.6, -2.6):
            point = QPointF(
                end.x() - size * math.cos(angle - offset),
                end.y() - size * math.sin(angle - offset),
            )
            painter.drawLine(end, point)

    def _draw_text_box(self, painter: QPainter, rect: Rect, text: str,
                       color: QColor, zoom: float) -> None:
        box = QRectF(rect.x0, rect.y0, max(rect.width, 40.0), max(rect.height, 24.0))
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 120),
                            max(0.6, 1.0 / zoom)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(box)
        font = QFont()
        font.setPointSizeF(11.0)
        painter.setFont(font)
        painter.setPen(color)
        inner = box.adjusted(3, 2, -3, -2)
        painter.drawText(inner, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                         | int(Qt.TextFlag.TextWordWrap), text or "")

    def _draw_sticky(self, painter: QPainter, rect: Rect, color: QColor, zoom: float) -> None:
        box = QRectF(rect.x0, rect.y0, max(rect.width, 22.0), max(rect.height, 22.0))
        painter.setPen(QPen(color, max(0.6, 1.0 / zoom)))
        painter.setBrush(color)
        painter.drawRoundedRect(box, 2, 2)
        painter.setPen(QPen(QColor("#ffffff"), max(0.4, 0.8 / zoom)))
        for i in range(1, 4):
            y = box.top() + box.height() * (i + 1) / 5.0
            painter.drawLine(QPointF(box.left() + 4, y), QPointF(box.right() - 4, y))

    # ----------------------------------------------------------- selection UI
    def pdf_to_widget(self, page: int, x: float, y: float) -> QPoint:
        """Convert a PDF user space point to a viewport position."""
        scroll_y = self.verticalScrollBar().value()
        scroll_x = self.horizontalScrollBar().value()
        for entry_page, page_y, _, _, page_x in self._pages:
            if entry_page != page:
                continue
            origin_x, origin_y = self._page_origin(page)
            left = page_x - scroll_x + (x - origin_x) * self._zoom
            top = page_y - scroll_y + (y - origin_y) * self._zoom
            return QPoint(int(left), int(top))
        return QPoint()

    # ----------------------------------------------------------- flash target
    def flash_rect(self, page: int, rect: Rect, duration_ms: int = FLASH_DURATION_MS) -> None:
        """Briefly outline a region to draw attention to it after a jump."""
        if rect is None or rect.is_empty:
            return
        self._flash = (page, rect, time.monotonic())
        self._flash_timer.start()
        self.viewport().update()

    def _on_flash_tick(self) -> None:
        if self._flash is None:
            self._flash_timer.stop()
            return
        _, _, started = self._flash
        if (time.monotonic() - started) * 1000 > FLASH_DURATION_MS:
            self._flash = None
            self._flash_timer.stop()
        self.viewport().update()

    def _paint_flash(self, painter: QPainter) -> None:
        if self._flash is None:
            return
        page, rect, started = self._flash
        elapsed = (time.monotonic() - started) * 1000
        if elapsed > FLASH_DURATION_MS:
            return
        # Fade out over the last third of the animation.
        ratio = elapsed / FLASH_DURATION_MS
        alpha = int(235 * (1.0 if ratio < 0.65 else max(0.0, (1.0 - ratio) / 0.35)))
        if alpha <= 0:
            return
        theme = self._theme
        base = QColor(theme.accent if theme else "#2f6fed")
        base.setAlpha(alpha)
        pen = QPen(base)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        target = self._rect_to_widget(page, rect)
        if target.isNull():
            return
        painter.drawRect(target.adjusted(-3, -3, 3, 3))

    def _paint_selection(self, painter: QPainter) -> None:
        if not self._sel_rects or self._sel_page < 0:
            return
        theme = self._theme
        accent = QColor(theme.accent if theme else "#2f6fed")
        accent.setAlpha(90)
        painter.setPen(QPen(QColor(theme.accent if theme else "#2f6fed"), 1))
        painter.setBrush(accent)
        for rect in self._sel_rects:
            target = self._rect_to_widget(self._sel_page, rect)
            painter.drawRect(target)

    def _rect_to_widget(self, page: int, rect: Rect) -> QRectF:
        scroll_y = self.verticalScrollBar().value()
        scroll_x = self.horizontalScrollBar().value()
        for entry_page, page_y, _, page_w, page_x in self._pages:
            if entry_page != page:
                continue
            origin_x, origin_y = self._page_origin(page)
            left = page_x - scroll_x + (rect.x0 - origin_x) * self._zoom
            top = page_y - scroll_y + (rect.y0 - origin_y) * self._zoom
            return QRectF(left, top, rect.width * self._zoom, rect.height * self._zoom)
        return QRectF()

    def _paint_pending_draw(self, painter: QPainter) -> None:
        if self._sel_page < 0 or not (self._draw_points or self._draw_rect):
            return

        # Pending geometry uses PDF coordinates, just like saved annotations.
        # Map the PDF origin to the viewport before painting the live preview.
        origin = self._rect_to_widget(self._sel_page, Rect(0, 0, 0, 0)).topLeft()
        painter.save()
        painter.translate(origin)
        painter.scale(self._zoom, self._zoom)
        if self._tool is Tool.INK and self._draw_points:
            color = QColor(self._tool_color)
            color.setAlphaF(self._tool_opacity)
            pen = QPen(color, 2.2 / self._zoom)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            path = QPainterPath()
            first = self._draw_points[0]
            path.moveTo(first.x, first.y)
            for point in self._draw_points[1:]:
                path.lineTo(point.x, point.y)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        elif self._draw_rect is not None:
            color = QColor(self._tool_color)
            color.setAlphaF(self._tool_opacity)
            pen = QPen(color, 1.6 / self._zoom)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rect = self._draw_rect
            if self._tool is Tool.RECT:
                painter.drawRect(QRectF(rect.x0, rect.y0, rect.width, rect.height))
            elif self._tool is Tool.ELLIPSE:
                painter.drawEllipse(QRectF(rect.x0, rect.y0, rect.width, rect.height))
            else:
                painter.drawLine(QPointF(rect.x0, rect.y0), QPointF(rect.x1, rect.y1))
        painter.restore()

    # ------------------------------------------------------------ mouse input
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._doc:
            return
        self.setFocus()
        self._press_pos = event.position().toPoint()
        location = self._to_pdf(event.position().toPoint())
        if location is None:
            return
        page, x, y = location

        if event.button() == Qt.MouseButton.LeftButton:
            if self._tool is Tool.ERASER:
                target = self._annotation_at(page, x, y)
                if target is not None:
                    self.annotationRemoved.emit(target)
                return

            hit = self._annotation_at(page, x, y)
            if hit is not None and self._tool is Tool.POINTER:
                self._pressing_annotation = hit
                self._draw_start = QPointF(x, y)
                return

            if self._tool in (Tool.TEXT, Tool.NOTE):
                self._create_text_tool_annotation(page, x, y)
                return

            if self._tool is Tool.INK:
                self._draw_points = [Point(x, y)]
                self._sel_page = page
                return

            if self._tool.is_drawing:
                self._draw_start = QPointF(x, y)
                self._sel_page = page
                return

            # text selection tools
            self._sel_page = page
            word = self._doc.word_at(page, x, y)
            if word is None:
                self._clear_selection()
                return
            self._sel_anchor = word
            self._sel_head = word
            self._update_selection()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._doc:
            return
        location = self._to_pdf(event.position().toPoint())
        if location is None:
            return
        page, x, y = location

        if self._tool is Tool.INK and self._draw_points:
            self._draw_points.append(Point(x, y))
            self.viewport().update()
            return

        if self._tool.is_drawing and self._draw_start is not None:
            self._draw_rect = Rect(
                min(self._draw_start.x(), x), min(self._draw_start.y(), y),
                max(self._draw_start.x(), x), max(self._draw_start.y(), y),
            )
            self.viewport().update()
            return

        if self._sel_anchor is not None and self._tool in (
            Tool.POINTER, Tool.HIGHLIGHT, Tool.UNDERLINE,
            Tool.STRIKEOUT, Tool.SQUIGGLY,
        ):
            word = self._doc.word_at(page, x, y)
            if word is not None and word is not self._sel_head:
                self._sel_head = word
                self._update_selection()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._doc:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        location = self._to_pdf(event.position().toPoint())

        if self._tool is Tool.INK and self._draw_points:
            if location is not None:
                self._draw_points.append(Point(location[1], location[2]))
            if len(self._draw_points) >= 2:
                annotation = Annotation(
                    kind=AnnKind.INK,
                    page=self._sel_page,
                    color=self._tool_color,
                    opacity=self._tool_opacity,
                    paths=[list(self._draw_points)],
                )
                self.annotationCreated.emit(annotation)
            self._draw_points = []
            self.viewport().update()
            return

        if self._tool.is_drawing and self._draw_rect is not None:
            rect = self._draw_rect
            kind = self._tool.kind
            if kind in (AnnKind.ARROW, AnnKind.LINE):
                if abs(rect.x1 - rect.x0) < 2 and abs(rect.y1 - rect.y0) < 2:
                    rect = None
            elif rect.width < 2 and rect.height < 2:
                # A plain click with a shape tool is not a shape.
                rect = None
            if rect is not None:
                annotation = Annotation(
                    kind=kind,
                    page=self._sel_page,
                    rects=[rect],
                    color=self._tool_color,
                    opacity=self._tool_opacity,
                )
                self.annotationCreated.emit(annotation)
            self._draw_start = None
            self._draw_rect = None
            self.viewport().update()
            return

        # Click (without drag) on an existing annotation selects it.
        if self._pressing_annotation is not None:
            moved = False
            if self._press_pos is not None:
                moved = (event.position().toPoint() - self._press_pos).manhattanLength() > 4
            if not moved:
                self._selected_annotation = self._pressing_annotation
                self.annotationPicked.emit(self._pressing_annotation)
            self._pressing_annotation = None
            self._draw_start = None
            return

        if self._sel_anchor is not None and self._sel_words:
            text = self._sel_text
            rects = list(self._sel_rects)
            page = self._sel_page
            self._clear_selection()
            if text.strip() and rects:
                self.selectionMade.emit(page, text, rects)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Double clicking a text box or sticky note opens it for editing."""
        location = self._to_pdf(event.position().toPoint())
        if location is not None:
            page, x, y = location
            hit = self._annotation_at(page, x, y)
            if hit is not None and hit.kind in (AnnKind.TEXT, AnnKind.NOTE):
                self._selected_annotation = hit
                self.annotationEditRequested.emit(hit)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------ text select
    def _update_selection(self) -> None:
        if not self._doc or self._sel_anchor is None or self._sel_head is None:
            self._clear_selection()
            return
        page = self._sel_page
        lines = self._doc.lines(page)
        words = [word for line in lines for word in line.words]
        if not words:
            self._clear_selection()
            return

        low, high = self._sel_anchor, self._sel_head
        if low.index > high.index:
            low, high = high, low
        selected = [w for w in words if low.index <= w.index <= high.index]
        if not selected:
            self._clear_selection()
            return

        self._sel_words = selected
        self._sel_rects = [w.rect for w in selected]

        pieces: List[str] = []
        current_line = None
        for word in selected:
            if current_line is not None and word.line != current_line:
                pieces.append("\n")
            elif pieces:
                pieces.append(" ")
            pieces.append(word.text)
            current_line = word.line
        self._sel_text = "".join(pieces)
        self.viewport().update()

    def _clear_selection(self) -> None:
        self._sel_words = []
        self._sel_rects = []
        self._sel_text = ""
        self._sel_anchor = None
        self._sel_head = None
        self.viewport().update()

    def selected_text(self) -> str:
        return self._sel_text

    # ------------------------------------------------------------- hit testing
    def _annotation_at(self, page: int, x: float, y: float) -> Optional[Annotation]:
        best: Optional[Annotation] = None
        best_distance = 1e9
        for annotation in self._annotations:
            if annotation.page != page:
                continue
            if annotation.kind is AnnKind.INK:
                distance = self._distance_to_strokes(annotation, x, y)
                if distance is not None and distance < best_distance:
                    best_distance = distance
                    best = annotation
                continue
            for rect in annotation.rects:
                inflated = rect.expanded(3.0)
                if inflated.contains_point(x, y):
                    return annotation
                distance = self._distance_to_rect(inflated, x, y)
                if distance < best_distance:
                    best_distance = distance
                    best = annotation
        if best is not None and best_distance <= 6.0:
            return best
        return None

    @staticmethod
    def _distance_to_rect(rect: Rect, x: float, y: float) -> float:
        dx = max(rect.x0 - x, 0.0, x - rect.x1)
        dy = max(rect.y0 - y, 0.0, y - rect.y1)
        return math.hypot(dx, dy)

    @staticmethod
    def _distance_to_strokes(annotation: Annotation, x: float, y: float) -> Optional[float]:
        best = None
        for stroke in annotation.paths:
            for index in range(len(stroke) - 1):
                distance = PdfView._distance_to_segment(
                    stroke[index], stroke[index + 1], x, y
                )
                if best is None or distance < best:
                    best = distance
        return best

    @staticmethod
    def _distance_to_segment(a: Point, b: Point, x: float, y: float) -> float:
        vx, vy = b.x - a.x, b.y - a.y
        length_sq = vx * vx + vy * vy
        if length_sq == 0:
            return math.hypot(x - a.x, y - a.y)
        t = max(0.0, min(1.0, ((x - a.x) * vx + (y - a.y) * vy) / length_sq))
        px, py = a.x + t * vx, a.y + t * vy
        return math.hypot(x - px, y - py)

    # ---------------------------------------------------------- text / sticky
    def _create_text_tool_annotation(self, page: int, x: float, y: float) -> None:
        kind = AnnKind.TEXT if self._tool is Tool.TEXT else AnnKind.NOTE
        title = "Text" if kind is AnnKind.TEXT else "Note"
        text, accepted = QInputDialog.getText(
            self, f"Insert {title.lower()}", f"{title} text:", QLineEdit.EchoMode.Normal, ""
        )
        if not accepted:
            return
        if kind is AnnKind.TEXT:
            rect = Rect(x, y, x + 220, y + 70)
        else:
            rect = Rect(x, y, x + 24, y + 24)
        annotation = Annotation(
            kind=kind,
            page=page,
            rects=[rect],
            color=self._tool_color,
            opacity=self._tool_opacity,
            text=text,
        )
        self.annotationCreated.emit(annotation)

    # ------------------------------------------------------------------ keys
    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self._scroll_by_page(1)
            return
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_Backspace):
            self._scroll_by_page(-1)
            return
        if key == Qt.Key.Key_Home:
            self.scroll_to_top()
            return
        if key == Qt.Key.Key_End:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
            return
        if key == Qt.Key.Key_Delete and self._selected_annotation is not None:
            target = self._selected_annotation
            self._selected_annotation = None
            self.annotationRemoved.emit(target)
            self.viewport().update()
            return
        if key == Qt.Key.Key_Escape:
            self._selected_annotation = None
            self._clear_selection()
            self._draw_points = []
            self._draw_rect = None
            self.viewport().update()
            return
        super().keyPressEvent(event)

    def _scroll_by_page(self, direction: int) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + direction * bar.pageStep())

