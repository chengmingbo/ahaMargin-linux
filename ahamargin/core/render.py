"""Drawing annotations into PDF page content.

Two features need the application's annotations rendered as ordinary page
artwork: the *flattened* export (marks burned into the page, no annotation
objects left) and the *print layout* (document on the left, notes in the
margin on the right).

Both must produce exactly the same picture, so the drawing code lives here
instead of being written twice.  Everything in this module works in PDF user
space with the origin in the top-left corner and y growing downwards, which
is the coordinate system PyMuPDF uses for its drawing calls.
"""

from __future__ import annotations

import math
from typing import Tuple

from .annotations import AnnKind, Annotation
from .geometry import Rect

# Default highlight colour used when a colour string cannot be parsed.
FALLBACK_COLOR = (1.0, 0.84, 0.31)


def hex_to_rgb01(value: str) -> Tuple[float, float, float]:
    """Convert ``#rrggbb`` into the ``(r, g, b)`` floats PyMuPDF expects."""
    try:
        text = str(value).lstrip("#")
        if len(text) == 3:
            text = "".join(c * 2 for c in text)
        r = int(text[0:2], 16) / 255.0
        g = int(text[2:4], 16) / 255.0
        b = int(text[4:6], 16) / 255.0
        return (r, g, b)
    except Exception:
        return FALLBACK_COLOR


def draw_annotation(page, annotation: Annotation) -> None:
    """Draw one annotation into a page's content stream.

    The annotation's geometry is interpreted in the coordinate system of
    ``page``, so the caller is responsible for transforming it first if the
    page is placed somewhere else (see the print layout).
    """
    color = hex_to_rgb01(annotation.color)
    kind = annotation.kind

    for rect in annotation.rects:
        box = _rect(rect)
        if kind is AnnKind.HIGHLIGHT:
            page.draw_rect(box, color=None, fill=color, fill_opacity=0.35, width=0)
        elif kind is AnnKind.MARGIN:
            # Margin notes render as a tinted anchor over the quoted text.
            page.draw_rect(box, color=None, fill=color, fill_opacity=0.25, width=0)
            page.draw_line(
                (rect.x0, rect.y1), (rect.x1, rect.y1), color=color, width=1.2
            )
        elif kind is AnnKind.UNDERLINE:
            page.draw_line(
                (rect.x0, rect.y1 - 1), (rect.x1, rect.y1 - 1), color=color, width=1.4
            )
        elif kind is AnnKind.STRIKEOUT:
            middle = (rect.y0 + rect.y1) / 2
            page.draw_line((rect.x0, middle), (rect.x1, middle), color=color, width=1.4)
        elif kind is AnnKind.SQUIGGLY:
            step = 4.0
            x = rect.x0
            up = True
            while x < rect.x1:
                nxt = min(x + step, rect.x1)
                page.draw_line(
                    (x, rect.y1 - 1),
                    (nxt, rect.y1 - 4 if up else rect.y1 - 1),
                    color=color, width=1.2,
                )
                x = nxt
                up = not up
        elif kind is AnnKind.RECT:
            page.draw_rect(box, color=color, width=1.4)
        elif kind is AnnKind.ELLIPSE:
            page.draw_oval(box, color=color, width=1.4)
        elif kind is AnnKind.LINE:
            page.draw_line((rect.x0, rect.y0), (rect.x1, rect.y1), color=color, width=1.4)
        elif kind is AnnKind.ARROW:
            _draw_arrow(page, rect, color)
        elif kind is AnnKind.TEXT:
            page.insert_textbox(
                box, annotation.text or " ", fontsize=11, fontname="helv", color=color
            )
        elif kind is AnnKind.NOTE:
            page.insert_textbox(
                box, annotation.text or " ", fontsize=9, fontname="helv", color=color
            )

    for stroke in annotation.paths:
        if len(stroke) < 2:
            continue
        page.draw_polyline(
            [(p.x, p.y) for p in stroke], color=color, width=2, lineCap=1, lineJoin=1
        )


def _draw_arrow(page, rect: Rect, color) -> None:
    start = (rect.x0, rect.y0)
    end = (rect.x1, rect.y1)
    page.draw_line(start, end, color=color, width=1.4)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 9.0
    for offset in (2.6, -2.6):
        page.draw_line(
            end,
            (
                end[0] - size * math.cos(angle - offset),
                end[1] - size * math.sin(angle - offset),
            ),
            color=color, width=1.4,
        )


def _rect(rect: Rect):
    import pymupdf

    return pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1)
