"""Annotation model.

The whole application shares a single annotation model.  Margin notes are not a
separate animal: they are annotations of kind ``MARGIN`` that additionally
carry a quoted *capture* and a note *body*.  Keeping one model means the
canvas, the margin pane, the outline and the exporters all agree on what
exists.

Persistence format
------------------
Our annotations are stored inside the PDF itself so a document stays a single
portable file.  Each annotation carries a ``/Subject`` string of the shape::

    mnote:<id>|<base64url(json payload)>

The ``/Subject`` field is used because PyMuPDF exposes arbitrary length text
there, while ``/Content`` is kept human readable so that other PDF readers
show a sensible popup.

The payload holds everything that is not the human readable body: kind, page,
colour, opacity, the geometry and, for margin notes, the quoted capture.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .geometry import Point, Rect

SUBJECT_PREFIX = "mnote:"
PAYLOAD_PREFIX = "MNOTE1:"
PAYLOAD_VERSION = 1


class AnnKind(str, Enum):
    """Every annotation type the application can create."""

    HIGHLIGHT = "highlight"
    UNDERLINE = "underline"
    STRIKEOUT = "strikeout"
    SQUIGGLY = "squiggly"
    INK = "ink"
    RECT = "rect"
    ELLIPSE = "ellipse"
    ARROW = "arrow"
    LINE = "line"
    TEXT = "text"
    NOTE = "note"
    MARGIN = "margin"

    @property
    def label(self) -> str:
        return _KIND_LABELS.get(self, self.value.title())

    @property
    def is_text_markup(self) -> bool:
        """Highlight/underline/strikeout/squiggly operate on text rects."""
        return self in _TEXT_MARKUP_KINDS

    @property
    def is_freehand(self) -> bool:
        return self is AnnKind.INK


_KIND_LABELS: Dict[AnnKind, str] = {
    AnnKind.HIGHLIGHT: "Highlight",
    AnnKind.UNDERLINE: "Underline",
    AnnKind.STRIKEOUT: "Strikethrough",
    AnnKind.SQUIGGLY: "Squiggly",
    AnnKind.INK: "Freehand ink",
    AnnKind.RECT: "Rectangle",
    AnnKind.ELLIPSE: "Ellipse",
    AnnKind.ARROW: "Arrow",
    AnnKind.LINE: "Line",
    AnnKind.TEXT: "Text box",
    AnnKind.NOTE: "Sticky note",
    AnnKind.MARGIN: "Margin note",
}

_TEXT_MARKUP_KINDS = {
    AnnKind.HIGHLIGHT,
    AnnKind.UNDERLINE,
    AnnKind.STRIKEOUT,
    AnnKind.SQUIGGLY,
}


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def encode_payload(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return PAYLOAD_PREFIX + base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_payload(encoded: str) -> Optional[Dict[str, Any]]:
    """Return the payload dict, or ``None`` when the string is not ours."""
    if not encoded:
        return None
    text = encoded.strip()
    if text.startswith(PAYLOAD_PREFIX):
        blob = text[len(PAYLOAD_PREFIX):]
    elif SUBJECT_PREFIX in text:
        # Tolerate the `mnote:<id>|<payload>` shape as well.
        blob = text.split("|", 1)[1] if "|" in text else ""
    else:
        return None
    if not blob:
        return None
    try:
        raw = base64.urlsafe_b64decode(blob.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def subject_for(annotation_id: str, payload: Dict[str, Any]) -> str:
    return f"{SUBJECT_PREFIX}{annotation_id}|{encode_payload(payload)[len(PAYLOAD_PREFIX):]}"


def subject_id(subject: str) -> Optional[str]:
    """Extract our annotation id from a ``/Subject`` string."""
    if not subject or not subject.startswith(SUBJECT_PREFIX):
        return None
    rest = subject[len(SUBJECT_PREFIX):]
    return rest.split("|", 1)[0] or None


@dataclass
class Annotation:
    """A single annotation owned by the application."""

    kind: AnnKind = AnnKind.HIGHLIGHT
    page: int = 0
    id: str = field(default_factory=new_id)
    rects: List[Rect] = field(default_factory=list)
    color: str = "#ffd54f"
    opacity: float = 1.0
    text: str = ""
    paths: List[List[Point]] = field(default_factory=list)
    order: float = 0.0
    created: float = field(default_factory=time.time)
    modified: float = field(default_factory=time.time)
    author: str = ""

    # Margin-note specific extras (kept in `extra` so the payload codec stays
    # uniform across kinds).
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Accept loose geometry (tuples/lists) and normalise it."""
        self.rects = [_as_rect(r) for r in self.rects]
        self.paths = [[_as_point(p) for p in stroke] for stroke in self.paths]
        if not isinstance(self.kind, AnnKind):
            try:
                self.kind = AnnKind(self.kind)
            except ValueError:
                self.kind = AnnKind.HIGHLIGHT

    # ---------------------------------------------------------------- helpers
    @property
    def is_margin(self) -> bool:
        return self.kind is AnnKind.MARGIN

    @property
    def capture(self) -> str:
        return str(self.extra.get("capture", ""))

    @capture.setter
    def capture(self, value: str) -> None:
        self.extra["capture"] = value

    @property
    def body(self) -> str:
        return self.text

    @body.setter
    def body(self, value: str) -> None:
        self.text = value

    @property
    def collapsed(self) -> bool:
        return bool(self.extra.get("collapsed", False))

    @collapsed.setter
    def collapsed(self, value: bool) -> None:
        self.extra["collapsed"] = bool(value)

    @property
    def bounds(self) -> Rect:
        """Bounding box across rects and ink paths."""
        boxes = list(self.rects)
        for stroke in self.paths:
            if stroke:
                boxes.append(Rect.from_points(stroke))
        if not boxes:
            return Rect(0, 0, 0, 0)
        result = boxes[0]
        for box in boxes[1:]:
            result = result.union(box)
        return result

    def touched(self) -> None:
        self.modified = time.time()

    # --------------------------------------------------------------- payload
    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "v": PAYLOAD_VERSION,
            "id": self.id,
            "kind": self.kind.value,
            "page": self.page,
            "color": self.color,
            "opacity": self.opacity,
            "order": self.order,
            "rects": [r.as_list() for r in self.rects],
            "author": self.author,
        }
        if self.paths:
            payload["paths"] = [[list(p) for p in stroke] for stroke in self.paths]
        for key in ("capture", "collapsed"):
            if key in self.extra:
                payload[key] = self.extra[key]
        return payload

    @staticmethod
    def from_payload(payload: Dict[str, Any], fallback_page: int = 0) -> "Annotation":
        kind_raw = str(payload.get("kind", AnnKind.HIGHLIGHT.value))
        try:
            kind = AnnKind(kind_raw)
        except ValueError:
            kind = AnnKind.HIGHLIGHT

        rects = [Rect.from_list(item) for item in payload.get("rects", [])]
        paths: List[List[Point]] = []
        for stroke in payload.get("paths", []) or []:
            paths.append([Point(float(p[0]), float(p[1])) for p in stroke])

        annotation = Annotation(
            kind=kind,
            page=int(payload.get("page", fallback_page)),
            id=str(payload.get("id") or new_id()),
            rects=rects,
            color=str(payload.get("color", "#ffd54f")),
            opacity=float(payload.get("opacity", 1.0)),
            paths=paths,
            order=float(payload.get("order", 0.0)),
            author=str(payload.get("author", "")),
        )
        if "capture" in payload:
            annotation.extra["capture"] = payload["capture"]
        if "collapsed" in payload:
            annotation.extra["collapsed"] = bool(payload["collapsed"])
        return annotation

    def clone(self) -> "Annotation":
        copy = Annotation(
            kind=self.kind,
            page=self.page,
            id=self.id,
            rects=list(self.rects),
            color=self.color,
            opacity=self.opacity,
            text=self.text,
            paths=[list(stroke) for stroke in self.paths],
            order=self.order,
            created=self.created,
            modified=self.modified,
            author=self.author,
            extra=dict(self.extra),
        )
        return copy


def _as_point(value) -> Point:
    """Coerce a tuple/list/dict into a :class:`Point`."""
    if isinstance(value, Point):
        return value
    if isinstance(value, dict):
        return Point(float(value.get("x", 0.0)), float(value.get("y", 0.0)))
    try:
        return Point(float(value[0]), float(value[1]))
    except Exception:
        return Point(0.0, 0.0)


def _as_rect(value) -> Rect:
    """Coerce a tuple/list/dict into a :class:`Rect`."""
    if isinstance(value, Rect):
        return value
    if isinstance(value, dict):
        return Rect(
            float(value.get("x0", 0.0)), float(value.get("y0", 0.0)),
            float(value.get("x1", 0.0)), float(value.get("y1", 0.0)),
        )
    try:
        return Rect(float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except Exception:
        return Rect(0.0, 0.0, 0.0, 0.0)
