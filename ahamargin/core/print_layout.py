"""Print layout: the document with its margin notes set in the margin.

The idea that defines ahaMargin is that a note lives *next to* the passage it
belongs to.  This module turns that into something you can put on paper: every
source page is placed on the left of a sheet and the margin notes that belong
to it are typeset in a column on the right.

The result is an ordinary PDF, so it can be printed, mailed or archived.  The
page content is embedded as vector data (``show_pdf_page``), which means the
text of the source document stays selectable and searchable in the output
instead of being flattened to a picture.

Notes that do not fit on the first sheet of a page continue on a following
sheet, so nothing is ever silently dropped.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pymupdf

from .annotations import Annotation
from .geometry import Point, Rect
from .render import draw_annotation, hex_to_rgb01

# Paper sizes in points (1/72 inch).
PAGE_SIZES: Dict[str, Tuple[float, float]] = {
    "A4": (595.276, 841.890),
    "A5": (419.528, 595.276),
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
    "Tabloid": (792.0, 1224.0),
}

# Real TrueType fonts are preferred over the base-14 PDF fonts because they
# carry the full Unicode range, so notes written in any language survive.
_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

_ITALIC_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Oblique.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Italic.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Italic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSans-Italic.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansOblique.ttf",
    "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
    "C:/Windows/Fonts/ariali.ttf",
)

_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)

# Colours used for the typeset notes.
INK_BODY = (0.13, 0.15, 0.18)
INK_QUOTE = (0.42, 0.45, 0.50)
INK_FOOTER = (0.55, 0.57, 0.60)
INK_RULE = (0.82, 0.84, 0.86)

# Vertical rhythm of the note column.
NOTE_BAR_WIDTH = 2.6
NOTE_TEXT_GAP = 6.0
NOTE_BLOCK_GAP = 9.0
QUOTE_GAP = 3.0
FOOTER_HEIGHT = 16.0


@dataclass
class PrintOptions:
    """Everything the print layout can be told to do."""

    page_size: str = "A4"
    orientation: str = "landscape"
    # Share of the printable width reserved for the note column.
    note_ratio: float = 0.36
    margin: float = 30.0
    gap: float = 16.0
    font_size: float = 9.0
    include_quotes: bool = True
    include_page_numbers: bool = True
    only_annotated_pages: bool = False
    # Something like "1-5, 9, 12-" - empty means every page.
    page_range: str = ""
    show_annotations: bool = True
    max_quote_lines: int = 8
    footer_title: str = ""


class LayoutError(RuntimeError):
    """Raised when a print layout cannot be produced at all."""


@dataclass
class _Face:
    """A text face: a real font file when one is available, base-14 otherwise."""

    name: str
    candidates: Sequence[str]
    fallback: str

    def __post_init__(self) -> None:
        self.path = self._find()
        self.metrics = self._load(self.path)

    def _find(self) -> Optional[str]:
        roots = [
            "",
            os.path.join(os.path.expanduser("~"), ".local", "share", "fonts"),
            os.path.join(os.path.expanduser("~"), ".fonts"),
            "/usr/share/fonts",
            "/usr/local/share/fonts",
        ]
        for candidate in self.candidates:
            if os.path.isabs(candidate):
                if os.path.isfile(candidate):
                    return candidate
                continue
            for root in roots:
                path = os.path.join(root, candidate) if root else candidate
                if os.path.isfile(path):
                    return path
        return None

    def _load(self, path: Optional[str]) -> pymupdf.Font:
        if path:
            try:
                return pymupdf.Font(fontfile=path)
            except Exception:
                pass
        try:
            return pymupdf.Font(self.fallback)
        except Exception:
            return pymupdf.Font("helv")

    @property
    def ascender(self) -> float:
        try:
            return float(self.metrics.ascender)
        except Exception:
            return 0.9

    def register(self, page) -> None:
        """Make the face available on ``page`` (base-14 fonts are always there)."""
        if not self.path:
            return
        try:
            page.insert_font(fontname=self.name, fontfile=self.path)
        except Exception:
            # Fall back to the base-14 font for the rest of the document.
            self.path = None
            self.name = self.fallback
            self.metrics = self._load(None)

    def text_length(self, text: str, size: float) -> float:
        try:
            return float(self.metrics.text_length(text, size))
        except Exception:
            return len(text) * size * 0.5


@dataclass
class _Faces:
    regular: _Face = field(default_factory=lambda: _Face("mnoteReg", _REGULAR_CANDIDATES, "helv"))
    italic: _Face = field(default_factory=lambda: _Face("mnoteIt", _ITALIC_CANDIDATES, "heit"))
    bold: _Face = field(default_factory=lambda: _Face("mnoteBd", _BOLD_CANDIDATES, "hebo"))

    def register_all(self, page) -> None:
        self.regular.register(page)
        self.italic.register(page)
        self.bold.register(page)


def parse_page_range(spec: str, page_count: int) -> List[int]:
    """Turn a range such as ``"1-3, 7"`` into a list of 0-based page indices.

    An empty specification means "every page".  Out-of-range values are
    clamped and nonsense is ignored, so a typo can never abort a print job.
    """
    if page_count <= 0:
        return []
    text = (spec or "").strip()
    if not text:
        return list(range(page_count))

    wanted: List[int] = []
    for chunk in text.replace(";", ",").split(","):
        token = chunk.strip().lower()
        if not token:
            continue
        if token in ("all", "*"):
            wanted.extend(range(page_count))
            continue
        if "-" in token:
            start_text, _, end_text = token.partition("-")
            start = _parse_int(start_text, 1)
            end = _parse_int(end_text, page_count)
            if start is None:
                start = 1
            if end is None:
                end = page_count
            if start > end:
                start, end = end, start
            wanted.extend(range(start - 1, end))
        else:
            value = _parse_int(token, None)
            if value is not None:
                wanted.append(value - 1)

    result: List[int] = []
    for index in wanted:
        if 0 <= index < page_count and index not in result:
            result.append(index)
    return result


def _parse_int(text: str, default: Optional[int]) -> Optional[int]:
    text = (text or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def group_notes(annotations: Iterable[Annotation]) -> Dict[int, List[Annotation]]:
    """Group the margin notes by the page they belong to."""
    grouped: Dict[int, List[Annotation]] = {}
    for annotation in annotations:
        if not annotation.is_margin:
            continue
        grouped.setdefault(annotation.page, []).append(annotation)
    for notes in grouped.values():
        notes.sort(key=lambda a: (a.order, a.created))
    return grouped


def sheet_size(options: PrintOptions) -> Tuple[float, float]:
    """The size of one printed sheet in points."""
    width, height = PAGE_SIZES.get(options.page_size, PAGE_SIZES["A4"])
    if options.orientation == "portrait":
        if width > height:
            width, height = height, width
    else:
        if width < height:
            width, height = height, width
    return width, height


def build_print_pdf(
    src_path: str,
    out_path: str,
    annotations: Sequence[Annotation] = (),
    options: Optional[PrintOptions] = None,
    title: str = "",
    errors: Optional[List[str]] = None,
) -> str:
    """Write a print-ready PDF with the margin notes set in the margin.

    Problems that affect a single page are collected in ``errors`` instead of
    aborting the whole job; only a completely broken document raises
    :class:`LayoutError`.  Returns the path that was written.
    """
    options = options or PrintOptions()
    problems: List[str] = errors if errors is not None else []
    src = pymupdf.open(src_path)
    out = pymupdf.open()
    try:
        faces = _Faces()
        notes_by_page = group_notes(annotations)
        pages = parse_page_range(options.page_range, src.page_count)
        if options.only_annotated_pages:
            pages = [p for p in pages if notes_by_page.get(p)]

        sheet_w, sheet_h = sheet_size(options)
        margin = max(8.0, float(options.margin))
        content_x = margin
        content_y = margin
        content_w = sheet_w - 2 * margin
        content_h = sheet_h - 2 * margin - FOOTER_HEIGHT

        sheets: List[Tuple[int, bool]] = []  # (source page, is continuation)

        for page_number in pages:
            notes = list(notes_by_page.get(page_number, []))
            index = 0
            continuation = 0
            while True:
                page = out.new_page(width=sheet_w, height=sheet_h)
                faces.register_all(page)

                has_notes = bool(notes)
                if has_notes:
                    note_w = content_w * max(0.15, min(0.75, options.note_ratio))
                    page_w = content_w - note_w - options.gap
                    note_col = Rect(
                        content_x + page_w + options.gap,
                        content_y,
                        content_x + content_w,
                        content_y + content_h,
                    )
                else:
                    page_w = content_w
                    note_col = Rect(0, 0, 0, 0)

                if continuation == 0:
                    source_rect = _place_source_page(
                        page, src, page_number,
                        Rect(content_x, content_y, content_x + page_w, content_y + content_h),
                        problems,
                    )
                    if source_rect is not None and options.show_annotations:
                        _draw_page_annotations(
                            page, src, page_number, source_rect, annotations, problems
                        )
                else:
                    header_h = _draw_continuation_header(
                        page, page_number, note_col, faces, options
                    )
                    note_col = Rect(
                        note_col.x0, note_col.y0 + header_h, note_col.x1, note_col.y1
                    )

                placed = _layout_notes(page, notes[index:], note_col, options, faces, problems)
                if placed == 0 and index < len(notes):
                    # Nothing could be placed at all - stop rather than loop.
                    break
                index += placed
                sheets.append((page_number, continuation > 0))

                if index >= len(notes):
                    break
                continuation += 1

        _draw_footers(out, sheets, faces, options, title)
        out.save(out_path, garbage=4, deflate=True)
    finally:
        out.close()
        src.close()
    return out_path


# --------------------------------------------------------------------- pieces
def _place_source_page(page, src, page_number: int, box: Rect, problems: List[str]):
    """Place the source page inside ``box`` and return the rect it occupies."""
    source = src[page_number]
    src_rect = source.rect
    src_w = max(1.0, float(src_rect.width))
    src_h = max(1.0, float(src_rect.height))
    scale = min(box.width / src_w, box.height / src_h)
    width = src_w * scale
    height = src_h * scale
    target = Rect(box.x0, box.y0, box.x0 + width, box.y0 + height)
    try:
        page.show_pdf_page(
            pymupdf.Rect(target.x0, target.y0, target.x1, target.y1), src, page_number
        )
    except Exception as exc:
        problems.append(f"Page {page_number + 1} could not be placed: {exc}")
        return None
    return target


def _draw_page_annotations(
    page, src, page_number: int, target: Rect,
    annotations: Sequence[Annotation], problems: List[str],
) -> None:
    """Redraw our annotations on top of the placed source page."""
    source = src[page_number]
    src_rect = source.rect
    src_w = max(1.0, float(src_rect.width))
    src_h = max(1.0, float(src_rect.height))
    if target.width <= 0 or target.height <= 0:
        return
    scale_x = target.width / src_w
    scale_y = target.height / src_h

    for annotation in annotations:
        if annotation.page != page_number:
            continue
        clone = annotation.clone()
        clone.rects = [
            Rect(
                target.x0 + (r.x0 - src_rect.x0) * scale_x,
                target.y0 + (r.y0 - src_rect.y0) * scale_y,
                target.x0 + (r.x1 - src_rect.x0) * scale_x,
                target.y0 + (r.y1 - src_rect.y0) * scale_y,
            )
            for r in annotation.rects
        ]
        clone.paths = [
            [
                Point(
                    target.x0 + (p.x - src_rect.x0) * scale_x,
                    target.y0 + (p.y - src_rect.y0) * scale_y,
                )
                for p in stroke
            ]
            for stroke in annotation.paths
        ]
        try:
            draw_annotation(page, clone)
        except Exception as exc:
            problems.append(
                f"{annotation.kind.label} on page {page_number + 1} could not be drawn: {exc}"
            )
            continue


def _layout_notes(
    page, notes: Sequence[Annotation], column: Rect, options: PrintOptions,
    faces: _Faces, problems: List[str],
) -> int:
    """Draw notes into the column; return how many of them fitted."""
    if not notes or column.width <= 0 or column.height <= 0:
        return 0

    body_size = max(6.0, float(options.font_size))
    quote_size = max(5.5, body_size - 1.0)
    body_leading = body_size * 1.32
    quote_leading = quote_size * 1.28

    text_x = column.x0 + NOTE_BAR_WIDTH + NOTE_TEXT_GAP
    text_w = max(20.0, column.width - NOTE_BAR_WIDTH - NOTE_TEXT_GAP)

    y = column.y0
    placed = 0

    for note in notes:
        quote = (note.capture or "").strip()
        body = (note.body or "").strip()
        if options.include_quotes and quote:
            quote_lines = _wrap(quote, faces.italic, quote_size, text_w)
            if len(quote_lines) > max(1, options.max_quote_lines):
                quote_lines = quote_lines[: max(1, options.max_quote_lines)]
                quote_lines[-1] = quote_lines[-1].rstrip(".,;") + "..."
        else:
            quote_lines = []
        body_lines = _wrap(body, faces.regular, body_size, text_w) if body else []
        if not quote_lines and not body_lines:
            body_lines = ["(empty note)"]

        content_h = (
            len(quote_lines) * quote_leading
            + (QUOTE_GAP if quote_lines else 0)
            + len(body_lines) * body_leading
        )
        if y + content_h > column.y1:
            if placed == 0:
                # A single note taller than the whole column: clip it so the
                # layout still makes progress instead of looping forever.
                _draw_clipped_note(page, note, column, y, options, faces, problems)
                placed = 1
            break  # the rest continues on the next sheet

        # The colour bar that ties the note to its colour in the margin pane.
        color = hex_to_rgb01(note.color)
        try:
            page.draw_rect(
                pymupdf.Rect(column.x0, y, column.x0 + NOTE_BAR_WIDTH, y + content_h),
                color=None,
                fill=color,
                fill_opacity=0.95,
                width=0,
            )
        except Exception:
            pass

        cursor = y
        if quote_lines:
            cursor = _draw_lines(
                page, quote_lines, text_x, cursor, faces.italic, quote_size,
                quote_leading, INK_QUOTE, problems,
            )
            cursor += QUOTE_GAP
        if body_lines:
            cursor = _draw_lines(
                page, body_lines, text_x, cursor, faces.regular, body_size,
                body_leading, INK_BODY, problems,
            )

        y += content_h + NOTE_BLOCK_GAP
        placed += 1

        # A hairline between two notes keeps a dense column readable.
        if placed < len(notes):
            try:
                page.draw_line(
                    (column.x0, y - NOTE_BLOCK_GAP / 2),
                    (column.x0 + column.width, y - NOTE_BLOCK_GAP / 2),
                    color=INK_RULE,
                    width=0.5,
                )
            except Exception:
                pass

    return placed


def _draw_clipped_note(
    page, note: Annotation, column: Rect, y: float, options: PrintOptions,
    faces: _Faces, problems: List[str],
) -> None:
    """Draw a note that is taller than the whole column, clipped to fit.

    A note this long is unusual, but it must not be able to stall the layout,
    so the text is cut short and marked as truncated.
    """
    body_size = max(6.0, float(options.font_size))
    quote_size = max(5.5, body_size - 1.0)
    body_leading = body_size * 1.32
    quote_leading = quote_size * 1.28
    text_x = column.x0 + NOTE_BAR_WIDTH + NOTE_TEXT_GAP
    text_w = max(20.0, column.width - NOTE_BAR_WIDTH - NOTE_TEXT_GAP)
    available = max(0.0, column.y1 - y)

    quote = (note.capture or "").strip()
    body = (note.body or "").strip()
    quote_lines = (
        _wrap(quote, faces.italic, quote_size, text_w)
        if options.include_quotes and quote
        else []
    )
    body_lines = _wrap(body, faces.regular, body_size, text_w) if body else []

    # The quote gives way first; the note body is what matters most.
    if quote_lines:
        keep = max(0, int((available * 0.5 - QUOTE_GAP) // quote_leading))
        if keep < len(quote_lines):
            quote_lines = quote_lines[:keep]
            if quote_lines:
                quote_lines[-1] = quote_lines[-1].rstrip(".,;") + "..."
    used = len(quote_lines) * quote_leading + (QUOTE_GAP if quote_lines else 0)
    room = max(0, int((available - used) // body_leading))
    if room < 1:
        body_lines = []
    elif len(body_lines) > room:
        body_lines = body_lines[:room]
        body_lines[-1] = body_lines[-1].rstrip() + " ..."

    content_h = used + len(body_lines) * body_leading
    color = hex_to_rgb01(note.color)
    try:
        page.draw_rect(
            pymupdf.Rect(column.x0, y, column.x0 + NOTE_BAR_WIDTH, y + content_h),
            color=None, fill=color, fill_opacity=0.95, width=0,
        )
    except Exception:
        pass

    cursor = y
    if quote_lines:
        cursor = _draw_lines(
            page, quote_lines, text_x, cursor, faces.italic, quote_size,
            quote_leading, INK_QUOTE, problems,
        )
        cursor += QUOTE_GAP
    if body_lines:
        _draw_lines(
            page, body_lines, text_x, cursor, faces.regular, body_size,
            body_leading, INK_BODY, problems,
        )


def _draw_lines(
    page, lines: Sequence[str], x: float, y: float, face: _Face, size: float,
    leading: float, color, problems: Optional[List[str]] = None,
) -> float:
    """Draw wrapped lines and return the y below the last one."""
    baseline = y + face.ascender * size
    for line in lines:
        if line:
            try:
                page.insert_text(
                    (x, baseline), line, fontname=face.name, fontsize=size, color=color
                )
            except Exception as exc:
                if problems is not None:
                    problems.append(f"Could not typeset a line of text: {exc}")
        baseline += leading
    return baseline - leading


def _wrap(text: str, face: _Face, size: float, width: float) -> List[str]:
    """Greedy word wrapping using the real font metrics."""
    lines: List[str] = []
    for paragraph in str(text).split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if face.text_length(candidate, size) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)

    # Hard-break words that are wider than the column on their own.
    result: List[str] = []
    for line in lines:
        if not line or face.text_length(line, size) <= width:
            result.append(line)
            continue
        chunk = ""
        for character in line:
            if chunk and face.text_length(chunk + character, size) > width:
                result.append(chunk)
                chunk = character
            else:
                chunk += character
        if chunk:
            result.append(chunk)
    return result


def _draw_continuation_header(
    page, page_number: int, column: Rect, faces: _Faces, options: PrintOptions
) -> float:
    """Label a continuation sheet; returns the height the header occupies."""
    label = f"Page {page_number + 1} - notes continued"
    size = max(6.0, float(options.font_size) - 1.0)
    baseline = column.y0 + faces.italic.ascender * size
    try:
        page.insert_text(
            (column.x0 + NOTE_BAR_WIDTH + NOTE_TEXT_GAP, baseline),
            label,
            fontname=faces.italic.name,
            fontsize=size,
            color=INK_QUOTE,
        )
    except Exception:
        pass
    return size * 1.6


def _draw_footers(
    out, sheets: Sequence[Tuple[int, bool]], faces: _Faces,
    options: PrintOptions, title: str,
) -> None:
    """Add the footer to every sheet once the total number is known."""
    if not options.include_page_numbers and not options.footer_title and not title:
        return
    total = len(sheets)
    for index, (source_page, continuation) in enumerate(sheets):
        page = out[index]
        sheet_w, sheet_h = page.rect.width, page.rect.height
        margin = max(8.0, float(options.margin))
        baseline = sheet_h - margin * 0.55
        size = max(5.5, float(options.font_size) - 2.0)
        left = options.footer_title or title
        if left:
            try:
                page.insert_text(
                    (margin, baseline), left, fontname=faces.regular.name,
                    fontsize=size, color=INK_FOOTER,
                )
            except Exception:
                pass
        if options.include_page_numbers:
            label = f"{index + 1} / {total}"
            try:
                width = faces.regular.text_length(label, size)
                page.insert_text(
                    (sheet_w - margin - width, baseline), label,
                    fontname=faces.regular.name, fontsize=size, color=INK_FOOTER,
                )
            except Exception:
                pass
