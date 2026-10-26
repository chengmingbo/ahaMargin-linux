"""Persistence: keeps the application model and the PDF file in sync.

Design
------
The PDF file is the single source of truth for stored annotations.  Annotations
that belong to this application are tagged with a ``/Subject`` of the form
``mnote:<id>|<payload>``; annotations that were already in the document when it
was opened are left completely untouched.

``Store.save`` performs a *reconcile*: every annotation we own is removed from
the PDF and re-created from the in-memory model.  That is simple, predictable
and immune to the many small state changes an annotation can go through
(moving, recolouring, editing text) that the PDF annotation API does not
support incrementally.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import pymupdf

from .annotations import (
    AnnKind,
    Annotation,
    decode_payload,
    subject_for,
)
from .geometry import Point, Rect
from .render import draw_annotation, hex_to_rgb01 as _hex_to_rgb01


class Store:
    """Owns the open PDF and the list of annotations belonging to the app."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.doc = pymupdf.open(self.path)
        self.annotations: List[Annotation] = []
        self.dirty = False
        self.export_errors: List[str] = []
        self._load()

    # ------------------------------------------------------------------ load
    def _load(self) -> None:
        self.annotations = []
        for page in range(self.doc.page_count):
            try:
                annots = list(self.doc[page].annots() or [])
            except Exception:
                continue
            for annot in annots:
                try:
                    info = annot.info or {}
                except Exception:
                    continue
                subject = info.get("subject") or ""
                payload = decode_payload(subject)
                if payload is None:
                    continue  # not ours - leave it alone
                annotation = Annotation.from_payload(payload, fallback_page=page)
                # Stored geometry is in PDF user space; the application works
                # in display space, so rotate it into the display space.
                annotation.rects = _to_display_space(
                    self.doc[page], annotation.rects)
                annotation.paths = [
                    [_display_point(self.doc[page], p) for p in stroke]
                    for stroke in annotation.paths
                ]
                content = info.get("content") or ""
                if content:
                    annotation.text = content
                author = info.get("title") or ""
                if author:
                    annotation.author = author
                self.annotations.append(annotation)
        self.annotations.sort(key=lambda a: (a.page, a.order, a.created))
        self._normalise_order()

    def _normalise_order(self) -> None:
        """Give every annotation a stable, sortable order value."""
        for index, annotation in enumerate(self.annotations):
            annotation.order = float(index)

    # ------------------------------------------------------------- accessors
    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def by_id(self, annotation_id: str) -> Optional[Annotation]:
        for annotation in self.annotations:
            if annotation.id == annotation_id:
                return annotation
        return None

    def on_page(self, page: int) -> List[Annotation]:
        return [a for a in self.annotations if a.page == page]

    def margin_notes(self) -> List[Annotation]:
        """Margin notes ordered by page, then by their position in that page."""
        notes = [a for a in self.annotations if a.is_margin]
        notes.sort(key=lambda a: (a.page, a.order, a.created))
        return notes

    def add(self, annotation: Annotation) -> Annotation:
        annotation.order = float(len(self.annotations))
        self.annotations.append(annotation)
        self.dirty = True
        return annotation

    def renumber(self) -> None:
        """Reassign sequential order values, preserving the current sequence."""
        self.annotations.sort(key=lambda a: (a.page, a.order, a.created))
        for index, annotation in enumerate(self.annotations):
            annotation.order = float(index)

    def remove(self, annotation: Annotation) -> None:
        if annotation in self.annotations:
            self.annotations.remove(annotation)
            self.dirty = True

    def clear(self) -> None:
        self.annotations = []
        self.dirty = True

    # ------------------------------------------------------------------ save
    def save(self, out_path: Optional[str] = None) -> str:
        """Write the document (with our annotations) back to disk."""
        target = os.path.abspath(out_path) if out_path else self.path
        self._reconcile_into(self.doc)

        if target == self.path:
            directory = os.path.dirname(target) or "."
            handle, temp_path = tempfile.mkstemp(prefix=".mnote-", suffix=".pdf", dir=directory)
            os.close(handle)
            try:
                self.doc.save(
                    temp_path,
                    garbage=4,
                    deflate=True,
                    clean=True,
                    pretty=False,
                )
                os.replace(temp_path, target)
            except Exception:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise
        else:
            self.doc.save(target, garbage=4, deflate=True, clean=True)
        self.dirty = False
        return target

    def export_copy(self, out_path: str) -> str:
        """Write an annotated copy without touching the working file."""
        out = pymupdf.open(self.path)
        try:
            self._reconcile_into(out)
            out.save(out_path, garbage=4, deflate=True)
        finally:
            out.close()
        return out_path

    def export_flattened(self, out_path: str) -> str:
        """Export a PDF with the annotations burned into the page content.

        The result looks exactly like the marked-up document but contains no
        annotation objects at all, which is handy for sharing, printing or
        feeding to a tool that ignores annotations.
        """
        out = pymupdf.open(self.path)
        try:
            self.export_errors = []
            for annotation in self.annotations:
                try:
                    draw_annotation(out[annotation.page], annotation)
                except Exception as exc:
                    # Keep going, but remember the failure so the UI can
                    # report it instead of silently producing a bare page.
                    self.export_errors.append(
                        f"{annotation.kind.label} on page {annotation.page + 1}: {exc}"
                    )
                    continue

            # Drop our annotation objects; the artwork now lives in the page.
            for page_index in range(out.page_count):
                page = out[page_index]
                try:
                    annots = list(page.annots() or [])
                except Exception:
                    continue
                for annot in annots:
                    try:
                        subject = (annot.info or {}).get("subject") or ""
                    except Exception:
                        subject = ""
                    if subject.startswith("mnote:"):
                        try:
                            page.delete_annot(annot)
                        except Exception:
                            pass

            out.save(out_path, garbage=4, deflate=True)
        finally:
            out.close()
        return out_path

    def export_with_outline(self, out_path: str) -> str:
        """Export a copy whose bookmarks mirror the margin notes.

        The document's own outline is preserved and a "Margin notes" section
        is appended, with one entry per note pointing at its page.  This turns
        a pile of annotations into a navigable table of contents.
        """
        out = pymupdf.open(self.path)
        try:
            self._reconcile_into(out)

            notes = self.margin_notes()
            if notes:
                toc = list(out.get_toc(simple=True) or [])
                level = 1
                if toc:
                    level = max(int(entry[0]) for entry in toc) + 1

                toc.append([level, "Margin notes", 1])
                current_page = None
                for note in notes:
                    if note.page != current_page:
                        current_page = note.page
                        toc.append([level + 1, f"Page {note.page + 1}", note.page + 1])
                    title = (note.body or note.capture or "Note").strip()
                    title = " ".join(title.split())
                    if not title:
                        title = "Note"
                    toc.append([level + 2, title[:100], note.page + 1])
                out.set_toc(toc)

            out.save(out_path, garbage=4, deflate=True)
        finally:
            out.close()
        return out_path

    # ------------------------------------------------------------- internals
    def _reconcile_into(self, doc: pymupdf.Document) -> None:
        """Rewrite every annotation we own inside ``doc``."""
        for page_index in range(doc.page_count):
            page = doc[page_index]
            try:
                annots = list(page.annots() or [])
            except Exception:
                continue
            for annot in annots:
                try:
                    subject = (annot.info or {}).get("subject") or ""
                except Exception:
                    subject = ""
                if subject.startswith("mnote:"):
                    try:
                        page.delete_annot(annot)
                    except Exception:
                        try:
                            page.delete_annot(annot.xref)
                        except Exception:
                            pass

        for annotation in self.annotations:
            self._create_annot(doc, annotation)

    def _create_annot(self, doc: pymupdf.Document, annotation: Annotation) -> None:
        page = doc[annotation.page]
        color = _hex_to_rgb01(annotation.color)
        # The model works in display space; the PDF needs user space.
        rects = [_to_user_space(page, r) for r in annotation.rects]
        paths = [
            [(p.x, p.y) for p in [_to_user_point(page, point) for point in stroke]]
            for stroke in annotation.paths
        ]
        payload = annotation.to_payload()
        subject = subject_for(annotation.id, payload)

        annot = None
        kind = annotation.kind

        if kind is AnnKind.HIGHLIGHT:
            annot = page.add_highlight_annot([_rect(r) for r in rects])
        elif kind is AnnKind.MARGIN:
            # Margin notes render as a tinted anchor over the quoted text.
            annot = page.add_highlight_annot([_rect(r) for r in rects])
        elif kind is AnnKind.UNDERLINE:
            annot = page.add_underline_annot([_rect(r) for r in rects])
        elif kind is AnnKind.STRIKEOUT:
            annot = page.add_strikeout_annot([_rect(r) for r in rects])
        elif kind is AnnKind.SQUIGGLY:
            annot = page.add_squiggly_annot([_rect(r) for r in rects])
        elif kind is AnnKind.INK:
            strokes = [stroke for stroke in paths if stroke]
            if not strokes:
                return
            annot = page.add_ink_annot(strokes)
        elif kind is AnnKind.RECT:
            if not rects:
                return
            annot = page.add_rect_annot(_rect(rects[0]))
        elif kind is AnnKind.ELLIPSE:
            if not rects:
                return
            annot = page.add_circle_annot(_rect(rects[0]))
        elif kind is AnnKind.LINE:
            if not rects:
                return
            annot = page.add_line_annot(
                pymupdf.Point(rects[0].x0, rects[0].y0),
                pymupdf.Point(rects[0].x1, rects[0].y1),
            )
        elif kind is AnnKind.ARROW:
            if not rects:
                return
            annot = page.add_line_annot(
                pymupdf.Point(rects[0].x0, rects[0].y0),
                pymupdf.Point(rects[0].x1, rects[0].y1),
            )
        elif kind is AnnKind.TEXT:
            if not rects:
                return
            annot = page.add_freetext_annot(
                _rect(rects[0]),
                text=annotation.text or " ",
                fontsize=11,
                fontname="helv",
            )
        elif kind is AnnKind.NOTE:
            if not rects:
                return
            annot = page.add_text_annot(_rect(rects[0]), annotation.text or "Note")
        else:
            return

        if annot is None:
            return

        try:
            annot.set_info(
                subject=subject,
                content=annotation.text or "",
                title=annotation.author or "",
            )
        except Exception:
            pass

        try:
            annot.set_colors(stroke=color)
        except Exception:
            pass

        if kind in (AnnKind.RECT, AnnKind.ELLIPSE, AnnKind.LINE, AnnKind.ARROW,
                    AnnKind.INK, AnnKind.TEXT, AnnKind.NOTE):
            try:
                annot.set_border(width=0)
            except Exception:
                pass

        try:
            annot.set_opacity(annotation.opacity)
        except Exception:
            pass

        if kind in (AnnKind.INK, AnnKind.LINE, AnnKind.ARROW):
            try:
                annot.set_colors(stroke=color)
            except Exception:
                pass

        try:
            annot.update()
        except Exception:
            pass

    # ------------------------------------------------------------- sidecar IO
    def sidecar_path(self) -> Path:
        return Path(self.path).with_suffix(Path(self.path).suffix + ".mnote.json")

    def write_sidecar(self, extra: Optional[Dict] = None) -> None:
        """Store view state that does not belong in the PDF (collapsed, order)."""
        data = {
            "version": 1,
            "source": os.path.basename(self.path),
            "annotations": [
                {
                    "id": a.id,
                    "page": a.page,
                    "order": a.order,
                    "collapsed": a.collapsed,
                    "color": a.color,
                }
                for a in self.annotations
            ],
        }
        if extra:
            data.update(extra)
        try:
            with open(self.sidecar_path(), "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def read_sidecar(self) -> Dict:
        try:
            with open(self.sidecar_path(), "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}


def _rect(rect: Rect) -> pymupdf.Rect:
    return pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1)


def _to_user_space(page, rect: Rect) -> Rect:
    """Map a rectangle from display space into PDF user space."""
    try:
        if not page.rotation:
            return rect
        box = pymupdf.Rect(rect.as_tuple()) * page.derotation_matrix
        return Rect(box.x0, box.y0, box.x1, box.y1)
    except Exception:
        return rect


def _to_display_space(page, rect: Rect) -> Rect:
    """Map a rectangle from PDF user space into display space."""
    try:
        if not page.rotation:
            return rect
        box = pymupdf.Rect(rect.as_tuple()) * page.rotation_matrix
        return Rect(box.x0, box.y0, box.x1, box.y1)
    except Exception:
        return rect


def _to_user_point(page, point: Point) -> Point:
    try:
        if not page.rotation:
            return point
        mapped = pymupdf.Point(point.x, point.y) * page.derotation_matrix
        return Point(mapped.x, mapped.y)
    except Exception:
        return point


def _display_point(page, point: Point) -> Point:
    try:
        if not page.rotation:
            return point
        mapped = pymupdf.Point(point.x, point.y) * page.rotation_matrix
        return Point(mapped.x, mapped.y)
    except Exception:
        return point
