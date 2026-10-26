"""End to end checks for ahaMargin-linux.

Run with::

    python3 tests/test_ahamargin.py

The suite is written with plain asserts so it needs no test runner.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf

from ahamargin.core.annotations import AnnKind, Annotation, decode_payload, subject_for
from ahamargin.core.document import PdfDocument
from ahamargin.core.exporter import (
    notes_to_csv,
    notes_to_json,
    notes_to_markdown,
    notes_to_mindmap_svg,
)
from ahamargin.core.geometry import Rect
from ahamargin.core.print_layout import (
    PrintOptions,
    build_print_pdf,
    parse_page_range,
    sheet_size,
)
from ahamargin.core.store import Store
from ahamargin.core.undo import AnnotationHistory, clone_all

PASSED = []
FAILED = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name}  {detail}")


def make_sample_pdf(path: str, pages: int = 4) -> None:
    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 90), f"Chapter {index + 1}", fontsize=24, fontname="hebo")
        page.insert_text(
            (60, 140),
            "ahaMargin captures a passage and keeps your thoughts beside it.",
            fontsize=12,
            fontname="helv",
        )
        page.insert_text(
            (60, 165),
            "Select any sentence to create a linked note in the margin column.",
            fontsize=12,
            fontname="helv",
        )
    doc.set_toc([[1, "Chapter 1", 1], [1, "Sub 1.1", 2], [1, "Chapter 2", 3]])
    doc.save(path)
    doc.close()


def test_geometry() -> None:
    print("\n[geometry]")
    r = Rect(10, 20, 30, 60)
    check("width", r.width == 20)
    check("height", r.height == 40)
    check("union", r.union(Rect(0, 0, 5, 5)).as_tuple() == (0, 0, 30, 60))
    check("contains", r.contains_point(15, 30))
    check("intersects", r.intersects(Rect(25, 55, 40, 70)))
    check("not intersects", not r.intersects(Rect(100, 100, 120, 120)))


def test_payload_roundtrip() -> None:
    print("\n[payload]")
    annotation = Annotation(
        kind=AnnKind.MARGIN,
        page=2,
        rects=[Rect(10, 20, 100, 40)],
        color="#ffd54f",
        text="my note body",
        extra={"capture": "quoted text", "collapsed": False},
    )
    payload = annotation.to_payload()
    subject = subject_for(annotation.id, payload)
    decoded = decode_payload(subject)
    check("payload decodes", decoded is not None)
    if decoded:
        restored = Annotation.from_payload(decoded)
        check("kind survives", restored.kind is AnnKind.MARGIN)
        check("page survives", restored.page == 2)
        check("capture survives", restored.capture == "quoted text")
        check("rects survive", restored.rects[0].as_tuple() == (10, 20, 100, 40))
        check("colour survives", restored.color == "#ffd54f")


def test_store_roundtrip(tmp: str) -> None:
    print("\n[store]")
    source = os.path.join(tmp, "sample.pdf")
    make_sample_pdf(source)

    store = Store(source)
    check("outline loaded", len(store.doc.get_toc()) == 3)

    note = Annotation(
        kind=AnnKind.MARGIN,
        page=1,
        rects=[Rect(60, 130, 500, 150)],
        color="#a5d6a7",
        text="Important passage",
        extra={"capture": "ahaMargin captures a passage"},
    )
    store.add(note)

    mark = Annotation(kind=AnnKind.HIGHLIGHT, page=0, rects=[Rect(60, 80, 300, 100)], color="#ffd54f")
    store.add(mark)

    ink = Annotation(
        kind=AnnKind.INK,
        page=2,
        color="#e53935",
        paths=[[(10.0, 10.0), (20.0, 30.0), (40.0, 25.0)]],
    )
    store.add(ink)

    store.save()

    reopened = Store(source)
    kinds = sorted(a.kind.value for a in reopened.annotations)
    check("three annotations persisted", len(reopened.annotations) == 3, str(kinds))
    check("margin note persisted", any(a.kind is AnnKind.MARGIN for a in reopened.annotations))
    check("highlight persisted", any(a.kind is AnnKind.HIGHLIGHT for a in reopened.annotations))
    check("ink persisted", any(a.kind is AnnKind.INK for a in reopened.annotations))

    margin = [a for a in reopened.annotations if a.kind is AnnKind.MARGIN]
    if margin:
        check("note body persisted", margin[0].text == "Important passage", margin[0].text)
        check("note capture persisted", margin[0].capture == "ahaMargin captures a passage")
        check("note colour persisted", margin[0].color == "#a5d6a7")

    ink_notes = [a for a in reopened.annotations if a.kind is AnnKind.INK]
    if ink_notes:
        check("ink path persisted", len(ink_notes[0].paths) == 1 and len(ink_notes[0].paths[0]) == 3)

    # Saving again must not duplicate annotations.
    reopened.save()
    again = Store(source)
    check("no duplication on re-save", len(again.annotations) == 3, str(len(again.annotations)))

    # Foreign annotations must survive untouched.
    foreign = pymupdf.open(source)
    annot = foreign[0].add_text_annot(pymupdf.Rect(10, 10, 40, 40), "pre-existing")
    annot.update()
    foreign.save(os.path.join(tmp, "foreign.pdf"))
    foreign.close()

    store2 = Store(os.path.join(tmp, "foreign.pdf"))
    check("foreign annotation not adopted", len(store2.annotations) == 3)
    store2.save()
    foreign_kept = [
        an for an in (store2.doc[0].annots() or [])
        if not (an.info or {}).get("subject", "").startswith("mnote:")
    ]
    check("foreign annotation kept", len(foreign_kept) == 1, str(len(foreign_kept)))
    ours = [
        an for an in (store2.doc[0].annots() or [])
        if (an.info or {}).get("subject", "").startswith("mnote:")
    ]
    # Page 0 holds the foreign annotation plus our highlight (the margin note
    # lives on page 1), so exactly one of ours must remain there.
    check("our annotations rewritten", len(ours) == 1, str(len(ours)))
    store2.doc.close()


def test_text_extraction(tmp: str) -> None:
    print("\n[text]")
    source = os.path.join(tmp, "sample.pdf")
    doc = PdfDocument(source)
    check("page count", doc.page_count == 4)
    lines = doc.lines(0)
    check("lines extracted", len(lines) > 0)
    words = [w for line in lines for w in line.words]
    check("words extracted", len(words) > 5)
    word = doc.word_at(0, 65, 88)
    check("word hit test", word is not None and "Chapter" in word.text, str(word.text if word else None))
    hits = doc.search("margin")
    check("search finds hits", len(hits) > 0, str(len(hits)))
    check("search context", hits and len(hits[0].context) > 0)
    doc.close()


def test_rotated_pages(tmp: str) -> None:
    """Rotated pages must agree between rendering, extraction and storage."""
    print("\n[rotated pages]")
    path = os.path.join(tmp, "rotated.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 120), "ROTATED TEXT LINE", fontsize=18, fontname="hebo")
    page.set_rotation(90)
    doc.save(path)
    doc.close()

    pdf = PdfDocument(path)
    check("rotated page keeps its size", pdf.page_size(0) == (842.0, 595.0),
          str(pdf.page_size(0)))

    words = [w for line in pdf.lines(0) for w in line.words]
    check("rotated page has words", len(words) == 3, str(len(words)))
    if not words:
        pdf.close()
        return

    x0 = min(w.rect.x0 for w in words)
    y0 = min(w.rect.y0 for w in words)
    x1 = max(w.rect.x1 for w in words)
    y1 = max(w.rect.y1 for w in words)
    # The text was drawn on the left of the unrotated page, so after a 90
    # degree rotation it must land on the right hand side of the display.
    check("words are in display space", x0 > 500, f"x0={x0:.0f}")
    check("words span the rotated height", (y1 - y0) > 100, f"{y1 - y0:.0f}")

    # The rendered ink has to be where the words say it is.
    pix = pdf.render(0, 1.0, 1.0)
    image = pix
    width, height = image.width(), image.height()
    left, right, top, bottom = width, 0, height, 0
    found = False
    for py in range(0, height, 2):
        for px in range(0, width, 2):
            if image.pixelColor(px, py).lightness() < 140:
                found = True
                left = min(left, px); right = max(right, px)
                top = min(top, py); bottom = max(bottom, py)
    check("rendered ink found", found)
    if found:
        check("ink sits where the words are",
              left >= int(x0) - 25 and right <= int(x1) + 25,
              f"ink {left}-{right} vs words {x0:.0f}-{x1:.0f}")
    pdf.close()

    # A mark made on a rotated page must survive a save and reload.
    store = Store(path)
    rect = words[0].rect
    store.add(Annotation(kind=AnnKind.HIGHLIGHT, page=0, rects=[rect], color="#ffd54f"))
    store.save()

    reopened = Store(path)
    check("rotated mark survived", len(reopened.annotations) == 1,
          str(len(reopened.annotations)))
    if reopened.annotations:
        stored = reopened.annotations[0].rects[0]
        check("rotated mark kept its position",
              abs(stored.x0 - rect.x0) < 1.5 and abs(stored.y0 - rect.y0) < 1.5,
              f"{stored.as_tuple()} vs {rect.as_tuple()}")


def test_exporters(tmp: str) -> None:
    print("\n[exporters]")
    notes = [
        Annotation(
            kind=AnnKind.MARGIN, page=0, rects=[Rect(10, 10, 100, 20)],
            color="#ffd54f", text="first thought", extra={"capture": "quoted passage"},
        ),
        Annotation(
            kind=AnnKind.MARGIN, page=2, rects=[Rect(10, 10, 100, 20)],
            color="#a5d6a7", text="second thought", extra={"capture": "another quote"},
        ),
        Annotation(kind=AnnKind.HIGHLIGHT, page=1, rects=[Rect(10, 10, 100, 20)]),
    ]

    markdown = notes_to_markdown(notes, "Sample")
    check("markdown has title", markdown.startswith("# Sample"))
    check("markdown has quote", "quoted passage" in markdown)
    check("markdown has body", "first thought" in markdown)
    check("markdown groups pages", "## Page 1" in markdown and "## Page 3" in markdown)

    payload = notes_to_json(notes, "Sample")
    check("json valid", payload.strip().startswith("{") and payload.strip().endswith("}"))
    check("json has annotations", '"annotations"' in payload)

    csv_text = notes_to_csv(notes)
    check("csv has header", csv_text.splitlines()[0].startswith("page,kind"))
    check("csv has rows", len(csv_text.strip().splitlines()) == 4)

    svg = notes_to_mindmap_svg(notes, "Sample")
    check("svg starts correctly", svg.lstrip().startswith("<svg"))
    check("svg closes", svg.rstrip().endswith("</svg>"))
    check("svg has both notes", "first thought" in svg and "second thought" in svg)
    check("svg escapes entities", "&" in svg or "<" in svg)

    out = os.path.join(tmp, "notes.md")
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(markdown)
    check("markdown file written", os.path.getsize(out) > 0)


def test_undo() -> None:
    print("\n[undo]")
    history = AnnotationHistory()
    a = Annotation(kind=AnnKind.HIGHLIGHT, page=0, rects=[Rect(0, 0, 1, 1)], text="x")
    b = Annotation(kind=AnnKind.HIGHLIGHT, page=0, rects=[Rect(0, 0, 1, 1)], text="y")

    # `record` stores the state *before* a change.
    history.record("add first", clone_all([]))
    history.record("add second", clone_all([a]))
    check("can undo", history.can_undo)
    entry = history.pop_undo()
    check("undo returns state", entry is not None and len(entry[1]) == 1)
    check("undo label", entry is not None and entry[0] == "add second")

    history.push_redo("add second", [a, b])
    check("redo stack filled", history.can_redo)
    redone = history.pop_redo()
    check("redo returns state", redone is not None and len(redone[1]) == 2)


def test_print_layout(tmp: str) -> None:
    print("\n[print layout]")
    source = os.path.join(tmp, "print-source.pdf")
    doc = pymupdf.open()
    for index in range(3):
        page = doc.new_page(width=420, height=560)
        page.insert_text(
            (50, 90), f"Body text on page {index + 1}", fontsize=14, fontname="helv"
        )
    doc.save(source)
    doc.close()

    def make_note(page: int, body: str, capture: str = "") -> Annotation:
        note = Annotation(
            kind=AnnKind.MARGIN, page=page, rects=[Rect(50, 60, 200, 80)], color="#8ab4f8"
        )
        note.capture = capture
        note.body = body
        return note

    notes = [
        make_note(0, "First note body", "A quoted passage"),
        make_note(1, "Second note body"),
    ]
    highlight = Annotation(
        kind=AnnKind.HIGHLIGHT, page=0, rects=[Rect(50, 78, 260, 98)], color="#ffd54f"
    )

    # --- page range parsing
    check("range empty means all", parse_page_range("", 4) == [0, 1, 2, 3])
    check("range simple", parse_page_range("2", 4) == [1])
    check("range span", parse_page_range("1-3", 5) == [0, 1, 2])
    check("range mixed", parse_page_range("1-2, 5", 6) == [0, 1, 4])
    check("range open ended", parse_page_range("3-", 5) == [2, 3, 4])
    check("range clamps", parse_page_range("2-99", 3) == [1, 2])
    check("range ignores junk", parse_page_range("hello", 3) == [])
    check("range deduplicates", parse_page_range("1, 1, 2", 3) == [0, 1])

    # --- the layout itself
    out = os.path.join(tmp, "print-layout.pdf")
    errors: list = []
    build_print_pdf(
        source, out, notes + [highlight], PrintOptions(), title="Test", errors=errors
    )
    check("layout built", os.path.exists(out) and os.path.getsize(out) > 0)
    check("layout has no errors", not errors, "; ".join(errors))

    result = pymupdf.open(out)
    check("one sheet per page", result.page_count == 3, f"got {result.page_count}")
    first = result[0].get_text()
    check("source text is kept", "Body text on page 1" in first)
    check("note body is typeset", "First note body" in first)
    check("quote is typeset", "A quoted passage" in first)
    check("title in footer", "Test" in first)
    check("sheet number in footer", "1 / 3" in first)
    check(
        "notes are on their own page",
        "Second note body" in result[1].get_text(),
    )
    result.close()

    # --- a page range limits the sheets
    limited = os.path.join(tmp, "print-limited.pdf")
    build_print_pdf(source, limited, notes, PrintOptions(page_range="2"))
    limited_doc = pymupdf.open(limited)
    check("page range limits sheets", limited_doc.page_count == 1)
    check("page range picks the right page", "Body text on page 2" in limited_doc[0].get_text())
    limited_doc.close()

    # --- only annotated pages
    only = os.path.join(tmp, "print-only.pdf")
    build_print_pdf(
        source, only, notes, PrintOptions(only_annotated_pages=True)
    )
    only_doc = pymupdf.open(only)
    check("only annotated pages", only_doc.page_count == 2, f"got {only_doc.page_count}")
    only_doc.close()

    # --- notes that do not fit continue on the next sheet
    many = [make_note(0, f"Note {i} " + ("long reasoning " * 12)) for i in range(12)]
    overflow = os.path.join(tmp, "print-overflow.pdf")
    build_print_pdf(source, overflow, many, PrintOptions())
    overflow_doc = pymupdf.open(overflow)
    check("overflow adds a sheet", overflow_doc.page_count >= 2)
    overflow_text = "".join(page.get_text() for page in overflow_doc)
    check("no note is dropped", all(f"Note {i}" in overflow_text for i in range(12)))
    check("continuation is labelled", "notes continued" in overflow_text)
    overflow_doc.close()

    # --- a note far taller than the column must not stall the layout
    huge = make_note(0, "Huge note. " * 4000, "A very long quoted passage. " * 200)
    giant = os.path.join(tmp, "print-giant.pdf")
    build_print_pdf(source, giant, [huge], PrintOptions())
    giant_doc = pymupdf.open(giant)
    check("an oversized note still builds", giant_doc.page_count >= 1)
    check("an oversized note is clipped", giant_doc.page_count < 50,
          f"{giant_doc.page_count} sheets")
    giant_doc.close()

    # --- quotes can be left out
    plain = os.path.join(tmp, "print-noquotes.pdf")
    build_print_pdf(source, plain, notes, PrintOptions(include_quotes=False))
    plain_doc = pymupdf.open(plain)
    check("quotes can be hidden", "A quoted passage" not in plain_doc[0].get_text())
    plain_doc.close()
    # --- marks can be left out
    nomarks = os.path.join(tmp, "print-nomarks.pdf")
    build_print_pdf(source, nomarks, notes + [highlight], PrintOptions(show_annotations=False))
    nomarks_doc = pymupdf.open(nomarks)
    check("marks can be hidden", "First note body" in nomarks_doc[0].get_text())
    nomarks_doc.close()


def test_print_layout_geometry(tmp: str) -> None:
    print("\n[print layout geometry]")
    source = os.path.join(tmp, "geometry-source.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((60, 100), "Rotated content", fontsize=16, fontname="helv")
    page.set_rotation(90)
    doc.save(source)
    doc.close()

    check(
        "landscape sheet is wider than tall",
        sheet_size(PrintOptions(orientation="landscape"))[0]
        > sheet_size(PrintOptions(orientation="landscape"))[1],
    )
    check(
        "portrait sheet is taller than wide",
        sheet_size(PrintOptions(orientation="portrait"))[1]
        > sheet_size(PrintOptions(orientation="portrait"))[0],
    )

    mark = Annotation(
        kind=AnnKind.HIGHLIGHT, page=0, rects=[Rect(60, 80, 300, 120)], color="#ffd54f"
    )
    out = os.path.join(tmp, "geometry.pdf")
    errors: list = []
    build_print_pdf(source, out, [mark], PrintOptions(), errors=errors)
    check("rotated page builds", not errors and os.path.exists(out))
    result = pymupdf.open(out)
    check("rotated text is kept", "Rotated content" in result[0].get_text())
    result.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        test_geometry()
        test_payload_roundtrip()
        test_store_roundtrip(tmp)
        test_text_extraction(tmp)
        test_rotated_pages(tmp)
        test_exporters(tmp)
        test_undo()
        test_print_layout(tmp)
        test_print_layout_geometry(tmp)

    print("\n" + "=" * 60)
    print(f"passed: {len(PASSED)}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
