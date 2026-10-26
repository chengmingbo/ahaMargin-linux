"""Lightweight geometry primitives shared by the core model and the UI.

Everything in the core layer works in *PDF user space* (points, origin at the
top-left of the page, y growing downwards), which is exactly what PyMuPDF uses.
The UI converts to widget coordinates at paint time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence


@dataclass(frozen=True)
class Point:
    x: float = 0.0
    y: float = 0.0

    def __iter__(self) -> Iterator[float]:
        yield self.x
        yield self.y

    def __getitem__(self, index: int) -> float:
        return (self.x, self.y)[index]

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    def distance_to(self, other: "Point") -> float:
        dx = other.x - self.x
        dy = other.y - self.y
        return (dx * dx + dy * dy) ** 0.5


@dataclass(frozen=True)
class Rect:
    """An axis aligned rectangle in PDF user space."""

    x0: float
    y0: float
    x1: float
    y1: float

    # ------------------------------------------------------------------ ctors
    @staticmethod
    def from_list(data: Sequence[float]) -> "Rect":
        return Rect(float(data[0]), float(data[1]), float(data[2]), float(data[3]))

    @staticmethod
    def from_points(points: Iterable[Point]) -> "Rect":
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        if not xs:
            return Rect(0, 0, 0, 0)
        return Rect(min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def from_xywh(x: float, y: float, w: float, h: float) -> "Rect":
        return Rect(x, y, x + w, y + h)

    # -------------------------------------------------------------- accessors
    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def is_empty(self) -> bool:
        return self.width <= 0.0 or self.height <= 0.0

    @property
    def center(self) -> Point:
        return Point((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    @property
    def top_left(self) -> Point:
        return Point(self.x0, self.y0)

    # --------------------------------------------------------------- geometry
    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]

    def union(self, other: "Rect") -> "Rect":
        return Rect(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def intersects(self, other: "Rect") -> bool:
        return not (
            other.x0 > self.x1
            or other.x1 < self.x0
            or other.y0 > self.y1
            or other.y1 < self.y0
        )

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def translated(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    def expanded(self, amount: float) -> "Rect":
        return Rect(
            self.x0 - amount,
            self.y0 - amount,
            self.x1 + amount,
            self.y1 + amount,
        )

    def clipped_to(self, bounds: "Rect") -> "Rect":
        return self.union(bounds) if self.intersects(bounds) else Rect(
            min(max(self.x0, bounds.x0), bounds.x1),
            min(max(self.y0, bounds.y0), bounds.y1),
            min(max(self.x1, bounds.x0), bounds.x1),
            min(max(self.y1, bounds.y0), bounds.y1),
        )

    def normalized(self) -> "Rect":
        return Rect(
            min(self.x0, self.x1),
            min(self.y0, self.y1),
            max(self.x0, self.x1),
            max(self.y0, self.y1),
        )


def rects_bounds(rects: Sequence[Rect]) -> Rect:
    """Union of a sequence of rectangles (zero rect when empty)."""
    if not rects:
        return Rect(0, 0, 0, 0)
    result = rects[0]
    for r in rects[1:]:
        result = result.union(r)
    return result
