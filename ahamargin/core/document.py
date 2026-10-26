"""PDF engine wrapper.

Wraps PyMuPDF with the things the application needs: cached page rendering,
word/line level text extraction for selection, full text search and outline
reading.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pymupdf
from PySide6.QtGui import QImage

from .geometry import Rect

_PIXMAP_CACHE_LIMIT = 48
_LINE_CACHE_LIMIT = 16


@dataclass
class Word:
    """A single extracted word with its page rectangle."""

    rect: Rect
    text: str
    block: int = 0
    line: int = 0
    index: int = 0


@dataclass
class TextLine:
    """A visual line of text, holding its words in reading order."""

    words: List[Word] = field(default_factory=list)
    block: int = 0
    line: int = 0
    index: int = 0

    @property
    def rect(self) -> Rect:
        if not self.words:
            return Rect(0, 0, 0, 0)
        box = self.words[0].rect
        for word in self.words[1:]:
            box = box.union(word.rect)
        return box

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class SearchHit:
    """One search result."""

    page: int
    rect: Rect
    context: str = ""
    snippet: str = ""


class PdfDocument:
    """Read side of the PDF engine plus rendering helpers."""

    def __init__(self, path: str, password: Optional[str] = None):
        self.path = os.path.abspath(path)
        self.doc = pymupdf.open(self.path)
        if password and self.doc.needs_pass:
            self.doc.authenticate(password)
        self._pixmaps: "OrderedDict[Tuple[int, float, int], QImage]" = OrderedDict()
        self._lines: Dict[int, List[TextLine]] = {}

    # ------------------------------------------------------------------ info
    @property
    def page_count(self) -> int:
        return self.doc.page_count

    @property
    def is_encrypted(self) -> bool:
        return bool(self.doc.is_encrypted)

    @property
    def needs_pass(self) -> bool:
        return bool(self.doc.needs_pass)

    def metadata(self) -> Dict[str, str]:
        meta = dict(self.doc.metadata or {})
        meta.pop("encryption", None)
        return meta

    @property
    def title(self) -> str:
        meta = self.metadata()
        return meta.get("title") or os.path.splitext(os.path.basename(self.path))[0]

    def outline(self) -> List[Tuple[int, str, int]]:
        """PDF bookmarks as ``(level, title, page)`` triples (page is 1-based)."""
        try:
            toc = self.doc.get_toc(simple=True)
        except Exception:
            return []
        return [(int(level), str(title), int(page)) for level, title, page in toc]

    def page_size(self, page: int) -> Tuple[float, float]:
        rect = self.doc[page].rect
        return (rect.width, rect.height)

    def page_origin(self, page: int) -> Tuple[float, float]:
        """Top-left corner of the page in PDF user space (usually 0, 0)."""
        rect = self.doc[page].rect
        return (float(rect.x0), float(rect.y0))

    def page_rotation(self, page: int) -> int:
        return self.doc[page].rotation

    def close(self) -> None:
        try:
            self.doc.close()
        except Exception:
            pass

    # -------------------------------------------------------------- rendering
    def render(self, page: int, zoom: float, dpr: float = 1.0) -> QImage:
        """Render a page to a QImage, cached by (page, zoom, dpr)."""
        dpr = max(1.0, min(float(dpr), 3.0))
        key = (page, round(float(zoom), 3), int(round(dpr * 4)))
        cached = self._pixmaps.get(key)
        if cached is not None:
            self._pixmaps.move_to_end(key)
            return cached

        matrix = pymupdf.Matrix(zoom * dpr, zoom * dpr)
        pix = self.doc[page].get_pixmap(matrix=matrix, alpha=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
        # QImage does not copy the buffer, so make an owning copy.
        image = image.copy()
        self._pixmaps[key] = image
        while len(self._pixmaps) > _PIXMAP_CACHE_LIMIT:
            self._pixmaps.popitem(last=False)
        return image

    def thumbnail(self, page: int, max_width: int = 150) -> QImage:
        width, height = self.page_size(page)
        zoom = max_width / max(width, 1.0)
        return self.render(page, zoom, dpr=1.0)

    # ------------------------------------------------------------------ text
    def lines(self, page: int) -> List[TextLine]:
        """Extracted text lines for a page, in reading order."""
        cached = self._lines.get(page)
        if cached is not None:
            return cached

        try:
            raw = self.doc[page].get_text("words")
        except Exception:
            raw = []

        # Text extraction reports coordinates in the unrotated page space,
        # while the page is rendered in its rotated (display) space.  Rotate
        # the rectangles so that both agree, otherwise selection and
        # highlighting land in the wrong place on rotated pages.
        matrix = None
        try:
            if self.doc[page].rotation:
                matrix = self.doc[page].rotation_matrix
        except Exception:
            matrix = None

        grouped: "OrderedDict[Tuple[int, int], List[Word]]" = OrderedDict()
        for entry in raw:
            try:
                x0, y0, x1, y1, text, block, line = entry[0], entry[1], entry[2], entry[3], entry[4], entry[5], entry[6]
            except Exception:
                continue
            if not text or not text.strip():
                continue
            rect = Rect(float(x0), float(y0), float(x1), float(y1))
            if matrix is not None:
                box = pymupdf.Rect(rect.as_tuple()) * matrix
                rect = Rect(box.x0, box.y0, box.x1, box.y1)
            grouped.setdefault((int(block), int(line)), []).append(
                Word(rect=rect, text=text, block=int(block), line=int(line))
            )

        result: List[TextLine] = []
        flat_index = 0
        for index, ((block, line), words) in enumerate(grouped.items()):
            words.sort(key=lambda w: w.rect.x0)
            for word in words:
                word.index = flat_index
                flat_index += 1
            result.append(TextLine(words=words, block=block, line=line, index=index))

        self._lines[page] = result
        if len(self._lines) > _LINE_CACHE_LIMIT:
            self._lines.pop(next(iter(self._lines)))
        return result

    def text_in_rect(self, page: int, rect: Rect) -> str:
        """The text covered by a rectangle, used when a mark becomes a note.

        The rectangle is given in display space, so it is mapped back to the
        unrotated page space that ``get_textbox`` expects.
        """
        try:
            box = pymupdf.Rect(rect.as_tuple())
            if self.doc[page].rotation:
                box = box * self.doc[page].derotation_matrix
            text = self.doc[page].get_textbox(box)
        except Exception:
            return ""
        return " ".join(text.split())

    def page_text(self, page: int) -> str:
        try:
            return self.doc[page].get_text("text")
        except Exception:
            return ""

    def word_at(self, page: int, x: float, y: float) -> Optional[Word]:
        """The word under a PDF-space point, if any."""
        best: Optional[Word] = None
        best_area = None
        for line in self.lines(page):
            for word in line.words:
                rect = word.rect
                if rect.contains_point(x, y):
                    area = rect.width * rect.height
                    if best_area is None or area < best_area:
                        best = word
                        best_area = area
        if best is not None:
            return best
        # Fall back to the closest word on the same vertical band.
        candidates = [
            w for line in self.lines(page) for w in line.words
            if min(w.rect.y1, y) - max(w.rect.y0, y) >= -2.0
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda w: (abs(w.rect.center.x - x) + abs(w.rect.center.y - y) * 2))
        return candidates[0]

    # ---------------------------------------------------------------- search
    def search(self, query: str, limit: int = 800) -> List[SearchHit]:
        """Full text search across the document."""
        hits: List[SearchHit] = []
        if not query.strip():
            return hits
        for page in range(self.page_count):
            try:
                rects = self.doc[page].search_for(query)
            except Exception:
                rects = []
            for rect in rects:
                if len(hits) >= limit:
                    return hits
                pdf_rect = Rect(rect.x0, rect.y0, rect.x1, rect.y1)
                hits.append(
                    SearchHit(
                        page=page,
                        rect=pdf_rect,
                        context=self._context_for(page, pdf_rect),
                    )
                )
        return hits

    def _context_for(self, page: int, rect: Rect) -> str:
        """A readable snippet of the line containing a hit."""
        page_rect = self.doc[page].rect
        band = Rect(
            max(page_rect.x0, rect.x0 - 260),
            max(page_rect.y0, rect.y0 - 14),
            min(page_rect.x1, rect.x1 + 260),
            min(page_rect.y1, rect.y1 + 14),
        )
        try:
            text = self.doc[page].get_textbox(band).strip()
        except Exception:
            text = ""
        if not text:
            for line in self.lines(page):
                if line.rect.intersects(rect):
                    text = line.text
                    break
        return " ".join(text.split())
