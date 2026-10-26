"""Undo / redo.

Annotations are small, so the history keeps whole snapshots of the annotation
list.  That is far more reliable than command inversion, especially because
almost every edit can touch several annotations at once (delete a note and its
anchor, recolour a highlight, move a card).
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .annotations import Annotation


def clone_all(annotations: Sequence[Annotation]) -> List[Annotation]:
    return [a.clone() for a in annotations]


class AnnotationHistory:
    """Bounded undo/redo stack over snapshots of the annotation list."""

    def __init__(self, limit: int = 120):
        self._limit = limit
        self._undo: List[Tuple[str, List[Annotation]]] = []
        self._redo: List[Tuple[str, List[Annotation]]] = []

    # ---------------------------------------------------------------- record
    def record(self, label: str, state: Sequence[Annotation]) -> None:
        """Remember ``state`` (the state *before* a change) under ``label``."""
        self._undo.append((label, clone_all(state)))
        while len(self._undo) > self._limit:
            self._undo.pop(0)
        self._redo.clear()

    # ----------------------------------------------------------------- query
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    # ------------------------------------------------------------------ pops
    def pop_undo(self) -> Optional[Tuple[str, List[Annotation]]]:
        if not self._undo:
            return None
        label, state = self._undo.pop()
        return label, state

    def pop_redo(self) -> Optional[Tuple[str, List[Annotation]]]:
        if not self._redo:
            return None
        label, state = self._redo.pop()
        return label, state

    def push_undo(self, label: str, state: Sequence[Annotation]) -> None:
        """Restore an entry onto the undo stack (used by redo)."""
        self._undo.append((label, clone_all(state)))
        while len(self._undo) > self._limit:
            self._undo.pop(0)

    def push_redo(self, label: str, state: Sequence[Annotation]) -> None:
        self._redo.append((label, clone_all(state)))
        while len(self._redo) > self._limit:
            self._redo.pop(0)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
