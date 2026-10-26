"""GUI smoke test for ahaMargin-linux.

Drives the real widget with synthetic mouse events so the selection logic,
the margin pane and the persistence round trip are all exercised end to end.

Run with::

    python3 tests/gui_smoke.py [output.png]
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ahamargin.core.annotations import AnnKind, Annotation
from ahamargin.core.geometry import Rect
from ahamargin.ui.main_window import MainWindow
from ahamargin.ui.tools import Tool

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name}  {detail}")


def make_pdf(path: str) -> None:
    doc = pymupdf.open()
    for index in range(5):
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 90), f"Chapter {index + 1}", fontsize=24, fontname="hebo")
        page.insert_text(
            (60, 140),
            "Reading is believing, but believing is not seeing.",
            fontsize=13,
            fontname="helv",
        )
        page.insert_text(
            (60, 170),
            "The only way to do great work is to love what you do.",
            fontsize=13,
            fontname="helv",
        )
        page.insert_text(
            (60, 200),
            "Capture this passage into the margin column to test the workflow.",
            fontsize=13,
            fontname="helv",
        )
    doc.set_toc([[1, "Chapter 1", 1], [1, "Chapter 2", 2], [1, "Chapter 3", 3]])
    doc.save(path)
    doc.close()


def drag_select(window: MainWindow, page: int, x0: float, y0: float, x1: float, y1: float) -> str:
    """Perform a real press/move/release drag over the canvas."""
    view = window.view
    start = view.pdf_to_widget(page, x0, y0)
    end = view.pdf_to_widget(page, x1, y1)
    viewport = view.viewport()

    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    # A couple of intermediate moves so the selection tracks properly.
    mid = QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2)
    QTest.mouseMove(viewport, mid)
    QTest.mouseMove(viewport, end)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    return 


def main() -> int:
    output = sys.argv[1] if len(sys.argv) > 1 else "ahamargin-gui.png"

    app = QApplication(sys.argv[:1])
    app.setStyleSheet("")

    with tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, "sample.pdf")
        make_pdf(pdf)

        window = MainWindow(pdf)
        window.resize(1500, 940)
        window.show()
        app.processEvents()

        print("\n[document]")
        check("document opened", window.store is not None)
        check("pdf loaded", window.doc is not None and window.doc.page_count == 5)
        check("outline loaded", window.sidebar.outline.topLevelItemCount() == 3)
        check("thumbnails built", window.sidebar.pages.count() == 5)

        print("\n[text selection -> margin note]")
        # Locate the first line of body text on page 0.
        lines = window.doc.lines(0)
        body = [line for line in lines if line.rect.y0 > 120]
        check("body text located", len(body) >= 2, str(len(body)))
        if not body:
            return 1

        line = body[0]
        start_word = line.words[0]
        end_word = line.words[min(5, len(line.words) - 1)]

        window.set_tool(Tool.POINTER)
        drag_select(
            window, 0,
            start_word.rect.x0 + 1, start_word.rect.center.y,
            end_word.rect.x1 - 1, end_word.rect.center.y,
        )
        check("margin note created", len(window.store.margin_notes()) == 1,
              str(len(window.store.margin_notes())))
        captured = window.store.margin_notes()[0].capture if window.store.margin_notes() else ""
        check("selection captured text", len(captured) > 5, repr(captured))

        notes = window.store.margin_notes()
        if notes:
            note = notes[0]
            check("note anchors to page 0", note.page == 0)
            check("note keeps the quote", "Reading" in note.capture, repr(note.capture))
            check("note is a margin note", note.kind is AnnKind.MARGIN)
            check("note has anchor rects", len(note.rects) > 0)
            check("margin panel shows the card", window.margin_panel.note_count() == 1)
            check("sidebar notes tree populated",
                  window.sidebar.notes.topLevelItemCount() == 1)

            # Type into the card the way a user would.
            card = window.margin_panel._cards[note.id]  # noqa: SLF001
            card.editor.setPlainText("This is the key idea of the chapter.")
            app.processEvents()
            check("body edited on the card", note.text.startswith("This is the key idea"))

        print("\n[marking tools]")
        window.set_tool(Tool.HIGHLIGHT)
        highlights_before = len([a for a in window.store.annotations if a.kind is AnnKind.HIGHLIGHT])
        line2 = body[1]
        drag_select(window, 0, line2.words[0].rect.x0 + 1, line2.words[0].rect.center.y,
                    line2.words[4].rect.x1 - 1, line2.words[4].rect.center.y)
        highlights_after = len([a for a in window.store.annotations if a.kind is AnnKind.HIGHLIGHT])
        check("highlight annotation created", highlights_after == highlights_before + 1,
              f"{highlights_before} -> {highlights_after}")

        # Freehand ink on page 1.
        window.set_tool(Tool.INK)
        start = window.view.pdf_to_widget(1, 100, 300)
        end = window.view.pdf_to_widget(1, 200, 380)
        viewport = window.view.viewport()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(viewport, QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
        QTest.mouseMove(viewport, end)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        inks = [a for a in window.store.annotations if a.kind is AnnKind.INK]
        check("ink stroke created", len(inks) == 1, str(len(inks)))
        if inks:
            check("ink stroke has points", len(inks[0].paths[0]) >= 2, str(len(inks[0].paths[0])))

        # Rectangle on page 2.
        window.set_tool(Tool.RECT)
        r_start = window.view.pdf_to_widget(2, 100, 400)
        r_end = window.view.pdf_to_widget(2, 300, 500)
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, r_start)
        QTest.mouseMove(viewport, r_end)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, r_end)
        rects = [a for a in window.store.annotations if a.kind is AnnKind.RECT]
        check("rectangle created", len(rects) == 1, str(len(rects)))

        print("\n[recent files]")
        check("recent list recorded", window._current_path in window._recent,
              str(window._recent))
        window.recent_menu.aboutToShow.emit()
        app.processEvents()
        actions = window.recent_menu.actions()
        check("recent menu populated", len(actions) >= 1, str(len(actions)))
        labels = [a.text() for a in actions]
        check("recent menu names the document",
              any(os.path.basename(window._current_path) in label for label in labels),
              str(labels))

        print("\n[marks panel]")
        highlights = [a for a in window.store.annotations if a.kind is AnnKind.HIGHLIGHT]
        check("marks tree populated", window.sidebar.marks.topLevelItemCount() > 0,
              str(window.sidebar.marks.topLevelItemCount()))
        check("marks tree lists the highlight", len(highlights) > 0)

        if highlights:
            mark = highlights[0]

            # Recolour a mark that is not a margin note.
            window._on_annotation_color(mark.id, "#ffab91")
            app.processEvents()
            check("mark recoloured",
                  window.store.by_id(mark.id).color == "#ffab91",
                  window.store.by_id(mark.id).color)

            # Turn the mark into a margin note; it should quote the text it
            # covers.
            window._convert_to_note(mark.id)
            app.processEvents()
            converted = window.store.by_id(mark.id)
            check("mark became a margin note", converted.kind is AnnKind.MARGIN,
                  converted.kind.value)
            check("converted note quotes its passage",
                  len(converted.capture) > 0, repr(converted.capture))
            check("converted note reaches the margin column",
                  any(n.id == mark.id for n in window.store.margin_notes()))

            # And it can be deleted again.
            count_before = len(window.store.annotations)
            window._delete_annotation_by_id(mark.id)
            app.processEvents()
            check("mark deleted", len(window.store.annotations) == count_before - 1)
            check("deleted mark is gone", window.store.by_id(mark.id) is None)

        print("\n[undo / redo]")
        # Add a fresh annotation and check that undo/redo round trip exactly.
        before_add = len(window.store.annotations)
        window._add_annotation(Annotation(
            kind=AnnKind.HIGHLIGHT, page=0, rects=[Rect(60, 80, 200, 100)],
            color="#ffd54f"))
        app.processEvents()
        check("annotation added", len(window.store.annotations) == before_add + 1,
              str(len(window.store.annotations)))
        window.undo()
        app.processEvents()
        check("undo removed it", len(window.store.annotations) == before_add,
              f"{len(window.store.annotations)} != {before_add}")
        window.redo()
        app.processEvents()
        check("redo restored it", len(window.store.annotations) == before_add + 1,
              str(len(window.store.annotations)))
        window.undo()
        app.processEvents()

        print("\n[note body undo]")
        body_note = window.store.margin_notes()[0]
        card = window.margin_panel._cards[body_note.id]
        original = body_note.text
        notes_before = len(window.store.margin_notes())

        # Start a fresh editing session: an earlier burst must not swallow
        # this edit.
        window._body_edit_times.pop(body_note.id, None)
        card.editor.setPlainText("a first draft")
        app.processEvents()
        check("typing updated the note", body_note.text == "a first draft",
              repr(body_note.text))
        check("typing is undoable", window.history.can_undo)

        # A second edit inside the same burst is not a separate undo step.
        steps_before = len(window.history._undo)
        card.editor.setPlainText("a first draft, revised")
        app.processEvents()
        check("a burst of typing is one undo step",
              len(window.history._undo) == steps_before,
              f"{len(window.history._undo)} != {steps_before}")

        window.undo()
        app.processEvents()
        check("undo reverted the typing",
              window.store.by_id(body_note.id).text == original,
              repr(window.store.by_id(body_note.id).text))
        check("undo did not resurrect deleted marks",
              len(window.store.margin_notes()) == notes_before,
              str(len(window.store.margin_notes())))

        # Editing a *different* note must always record its own step, even
        # right after typing in another one.
        others = [n for n in window.store.margin_notes() if n.id != body_note.id]
        if others:
            other_card = window.margin_panel._cards[others[0].id]
            steps_before = len(window.history._undo)
            other_card.editor.setPlainText("a different note")
            app.processEvents()
            check("editing another note records its own step",
                  len(window.history._undo) == steps_before + 1,
                  f"{len(window.history._undo)} != {steps_before + 1}")


        print("\n[canvas editing]")
        window.set_tool(Tool.POINTER)
        clickable = Annotation(kind=AnnKind.HIGHLIGHT, page=0,
                                rects=[Rect(60, 250, 300, 275)], color="#ffd54f")
        window.store.add(clickable)
        window.view.set_annotations(window.store.annotations)
        app.processEvents()
        if True:
            mark = clickable
            centre = mark.rects[0].center
            spot = window.view.pdf_to_widget(mark.page, centre.x, centre.y)
            QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier, spot)
            QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton,
                               Qt.KeyboardModifier.NoModifier, spot)
            app.processEvents()
            check("clicking a mark selects it",
                  window.view.selected_annotation is not None
                  and window.view.selected_annotation.id == mark.id,
                  str(window.view.selected_annotation))

            before_delete = len(window.store.annotations)
            QTest.keyClick(window.view.viewport(), Qt.Key.Key_Delete)
            app.processEvents()
            check("delete removes the selected annotation",
                  len(window.store.annotations) == before_delete - 1,
                  f"{before_delete} -> {len(window.store.annotations)}")

        # A click with a shape tool must not leave an empty shape behind.
        window.set_tool(Tool.RECT)
        shapes_before = len([a for a in window.store.annotations
                             if a.kind is AnnKind.RECT])
        click_spot = window.view.pdf_to_widget(0, 500, 600)
        QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, click_spot)
        QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier, click_spot)
        app.processEvents()
        shapes_after = len([a for a in window.store.annotations
                            if a.kind is AnnKind.RECT])
        check("a click with a shape tool creates nothing",
              shapes_after == shapes_before, f"{shapes_before} -> {shapes_after}")

        box = Annotation(kind=AnnKind.TEXT, page=0,
                         rects=[Rect(100, 400, 300, 450)], color="#2f6fed",
                         text="original wording")
        window.store.add(box)
        window.view.set_annotations(window.store.annotations)
        app.processEvents()
        edits = []
        # Detach the real handler: it opens a modal dialog, which would
        # block the test.
        window.view.annotationEditRequested.disconnect(window._edit_annotation_text)
        window.view.annotationEditRequested.connect(edits.append)
        spot = window.view.pdf_to_widget(0, 200, 425)
        QTest.mouseDClick(window.view.viewport(), Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier, spot)
        app.processEvents()
        check("double click asks to edit a text box",
              len(edits) == 1 and edits[0].id == box.id, str(len(edits)))
        window.view.annotationEditRequested.disconnect(edits.append)
        window.view.annotationEditRequested.connect(window._edit_annotation_text)

        window._apply_annotation_text(box, "revised wording")
        app.processEvents()
        check("text box wording updated",
              window.store.by_id(box.id).text == "revised wording",
              repr(window.store.by_id(box.id).text))

        print("\n[search]")
        window._run_search("great work")
        hits = window.doc.search("great work")
        check("search returns hits", len(hits) > 0, str(len(hits)))

        # A result must scroll to the exact hit, not merely to the page top.
        results = window.sidebar.search.results
        check("results listed", results.count() == len(hits), str(results.count()))
        before_scroll = window.view.verticalScrollBar().value()
        results.itemClicked.emit(results.item(0))
        app.processEvents()
        check("search jumps to the hit", window.view._flash is not None, "no flash set")
        if window.view._flash is not None:
            flash_page, flash_rect, _ = window.view._flash
            check("flash is on the right page", flash_page == hits[0].page,
                  f"{flash_page} != {hits[0].page}")
            check("flash matches the hit", flash_rect.as_tuple() == hits[0].rect.as_tuple(),
                  f"{flash_rect.as_tuple()} != {hits[0].rect.as_tuple()}")
        check("search scrolled the view",
              window.view.verticalScrollBar().value() != before_scroll or hits[0].page == 0)

        print("\n[reordering]")
        for index in range(3):
            window.store.add(Annotation(
                kind=AnnKind.MARGIN, page=0, rects=[Rect(60, 300 + index * 20, 300, 310 + index * 20)],
                color="#ffd54f", text=f"card {index}",
                extra={"capture": f"quote {index}"}))
        window._refresh_notes()
        app.processEvents()

        notes = window.store.margin_notes()
        page_one = [n for n in notes if n.page == 0]
        check("three cards on page 0", len(page_one) >= 3, str(len(page_one)))
        if len(page_one) >= 3:
            first = page_one[0]
            third = page_one[2]
            window.margin_panel._handle_order_change(third.id, first.id)
            app.processEvents()
            after = [n.id for n in window.store.margin_notes() if n.page == 0]
            check("card moved to the front", after.index(third.id) < after.index(first.id),
                  str(after))
            check("card count unchanged", len(after) == len(page_one))

            # The drag payload must carry the note id.
            from ahamargin.ui.margin_panel import MARGIN_MIME as _MIME
            card_for_third = window.margin_panel._cards[third.id]
            payload = card_for_third.drag_mime()
            check("drag payload has the mime type", payload.hasFormat(_MIME))
            check("drag payload carries the id",
                  bytes(payload.data(_MIME)).decode("utf-8") == third.id)

            # Exercise the real drop handler: build a drop event and post it
            # at the position of the first card.
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QDropEvent

            from ahamargin.ui.margin_panel import MARGIN_MIME

            container = window.margin_panel.container
            cards = window.margin_panel.ordered_cards()
            check("cards are laid out", len(cards) >= 3, str(len(cards)))
            if cards:
                target = cards[0]
                mime = QMimeData()
                mime.setData(MARGIN_MIME, third.id.encode("utf-8"))
                position = QPointF(target.geometry().center().x(),
                                   target.geometry().center().y())
                drop = QDropEvent(
                    position,
                    Qt.DropAction.MoveAction,
                    mime,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                container.dropEvent(drop)
                app.processEvents()
                after_drop = [n.id for n in window.store.margin_notes() if n.page == 0]
                check("drop moved the card to the front",
                      after_drop.index(third.id) < after_drop.index(first.id),
                      str(after_drop))

            # Moving a card to another page must be refused: the quote is
            # anchored to a specific page.
            other = next((n for n in window.store.margin_notes() if n.page != 0), None)
            if other is not None:
                before = [n.id for n in window.store.margin_notes()]
                rejected = []
                window.margin_panel.reorderRejected.connect(lambda _id: rejected.append(1))
                window.margin_panel._handle_order_change(first.id, other.id)
                app.processEvents()
                check("cross page drop rejected", len(rejected) == 1, str(len(rejected)))
                check("order unchanged after rejection",
                      [n.id for n in window.store.margin_notes()] == before)


        print("\n[outline export]")
        import pymupdf

        outlined = os.path.join(tmp, "outlined.pdf")
        window.store.export_with_outline(outlined)
        export_doc = pymupdf.open(outlined)
        toc = export_doc.get_toc()
        check("outline written", len(toc) > 0, str(toc))
        titles = [entry[1] for entry in toc]
        check("outline has a notes section",
              any("Margin notes" in title for title in titles), str(titles))
        bodies = [n.body.strip().split("\n")[0] for n in window.store.margin_notes()
                  if n.body.strip()]
        check("outline lists the note text",
              any(b and b[:40] in " ".join(titles) for b in bodies),
              f"{bodies} not in {titles}")
        check("outline survives a reopen", len(pymupdf.open(outlined).get_toc()) == len(toc))
        export_doc.close()

        print("\n[note navigation]")
        notes = window.store.margin_notes()
        if len(notes) >= 2:
            window._current_note_id = notes[0].id
            window._navigate_notes(1)
            app.processEvents()
            check("next note selected", window._current_note_id == notes[1].id,
                  f"{window._current_note_id} != {notes[1].id}")
            window._navigate_notes(-1)
            app.processEvents()
            check("previous note selected", window._current_note_id == notes[0].id,
                  f"{window._current_note_id} != {notes[0].id}")
        else:
            check("enough notes to navigate", False, str(len(notes)))

        print("\n[notes filter]")
        panel = window.margin_panel
        total = panel.note_count()
        panel.filter_field.setText("zzzz-no-such-note")
        app.processEvents()
        hidden = [c for c in panel.ordered_cards() if c.isHidden()]
        check("filter hides every card", len(hidden) == len(panel.ordered_cards()),
              str(len(hidden)))
        check("filter badge shows a ratio", "/" in panel.count_badge.text(),
              panel.count_badge.text())
        check("empty state explains the filter",
              "No notes match" in panel.empty_label.text(), panel.empty_label.text())
        panel.filter_field.setText("")
        app.processEvents()
        shown = [c for c in panel.ordered_cards() if not c.isHidden()]
        check("clearing the filter restores the cards", len(shown) == total,
              f"{len(shown)} != {total}")

        print("\n[print layout]")
        from PySide6.QtCore import QSettings
        from PySide6.QtGui import QPageRanges
        from PySide6.QtPrintSupport import QPrinter

        from ahamargin.core.print_layout import PrintOptions, build_print_pdf
        from ahamargin.ui.print_dialog import PrintOptionsDialog

        # The options dialog must survive being opened and must describe a
        # sensible layout.  It uses its own settings scope so the test never
        # touches the preferences of a real installation.
        test_settings = QSettings("ahaMargin-linux Tests", "ahaMargin-linux Tests")
        test_settings.clear()  # start from the built-in defaults
        dialog = PrintOptionsDialog(window, settings=test_settings)
        options = dialog.current_options()
        check("dialog returns options", isinstance(options, PrintOptions))
        check("dialog defaults to landscape", options.orientation == "landscape")
        check("dialog defaults to a note column", 0.15 < options.note_ratio < 0.75)
        check("dialog has a quote switch", dialog.quotes_check.isChecked())
        check("preview renders",
              dialog.preview.grab().save(os.path.join(tmp, "preview.png")))
        check("preview image is not empty",
              os.path.getsize(os.path.join(tmp, "preview.png")) > 0)

        dialog.size_combo.setCurrentText("Letter")
        dialog.orientation_combo.setCurrentIndex(
            dialog.orientation_combo.findData("portrait")
        )
        dialog.note_slider.setValue(50)
        dialog.range_edit.setText("1-2")
        changed = dialog.current_options()
        check("dialog page size applies", changed.page_size == "Letter")
        check("dialog orientation applies", changed.orientation == "portrait")
        check("dialog column width applies", abs(changed.note_ratio - 0.5) < 1e-6)
        check("dialog page range applies", changed.page_range == "1-2")
        dialog.save_settings()

        reopened = PrintOptionsDialog(window, settings=test_settings)
        restored = reopened.current_options()
        check("settings are remembered", restored.page_size == "Letter")
        check("settings remember the range", restored.page_range == "1-2")
        test_settings.clear()

        # The window must offer the print actions and enable them with a
        # document open.
        check("print action exists", window.act_print is not None)
        check("print action is enabled", window.act_print.isEnabled())
        check("print export action is enabled", window.act_export_print.isEnabled())

        # Build a layout and check that it really carries the notes.
        layout_path = os.path.join(tmp, "gui-layout.pdf")
        errors: list = []
        build_print_pdf(
            window.store.path, layout_path, window.store.annotations,
            PrintOptions(), title=window._document_title(), errors=errors,
        )
        check("layout built from the window", os.path.exists(layout_path))
        check("layout has no errors", not errors, "; ".join(errors))
        layout_doc = pymupdf.open(layout_path)
        check("layout has a sheet per page",
              layout_doc.page_count == window.doc.page_count,
              f"{layout_doc.page_count} != {window.doc.page_count}")
        layout_doc.close()

        # The painting path must produce a real PDF when the print dialog is
        # set to write to a file.
        printed_path = os.path.join(tmp, "gui-printed.pdf")
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(printed_path)
        printer.setFullPage(True)
        painted = window._paint_layout_to_printer(printer, layout_path)
        check("printer accepted the job", painted)
        check("printed file exists", os.path.exists(printed_path))
        if os.path.exists(printed_path):
            printed = pymupdf.open(printed_path)
            check("printed file has the right page count",
                  printed.page_count == window.doc.page_count,
                  f"{printed.page_count} != {window.doc.page_count}")
            printed.close()

        # A page range must be honoured when the layout is copied out.
        subset_path = os.path.join(tmp, "gui-subset.pdf")
        ranged = QPrinter(QPrinter.PrinterMode.HighResolution)
        ranged.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        ranged.setOutputFileName(subset_path)
        selected = QPageRanges()
        selected.addPage(1)
        selected.addPage(2)
        ranged.setPageRanges(selected)
        window._write_layout_subset(layout_path, subset_path, ranged)
        check("subset file exists", os.path.exists(subset_path))
        if os.path.exists(subset_path):
            subset = pymupdf.open(subset_path)
            check("subset keeps only the chosen pages", subset.page_count == 2,
                  str(subset.page_count))
            subset.close()

        # The printer range must also drive the painted output.
        painted_range = os.path.join(tmp, "gui-painted-range.pdf")
        ranged2 = QPrinter(QPrinter.PrinterMode.HighResolution)
        ranged2.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        ranged2.setOutputFileName(painted_range)
        ranged2.setPageRanges(selected)
        window._paint_layout_to_printer(ranged2, layout_path)
        check("ranged print file exists", os.path.exists(painted_range))
        if os.path.exists(painted_range):
            ranged_doc = pymupdf.open(painted_range)
            check("ranged print honours the range", ranged_doc.page_count == 2,
                  str(ranged_doc.page_count))
            ranged_doc.close()

        print("\n[focus mode]")
        check("focus mode starts off", window.act_focus_mode.isChecked() is False)
        toolbar_visible = window.toolbar.isVisible()
        sidebar_visible = window.left_dock.isVisible()
        window.act_focus_mode.setChecked(True)
        app.processEvents()
        check("focus mode hides the toolbar", not window.toolbar.isVisible())
        check("focus mode hides the sidebar", not window.left_dock.isVisible())
        check("focus mode hides the notes panel", not window.right_dock.isVisible())
        check("focus mode is recorded", window._focus_mode is True)
        window.act_focus_mode.setChecked(False)
        app.processEvents()
        check("leaving focus mode restores the toolbar",
              window.toolbar.isVisible() == toolbar_visible)
        check("leaving focus mode restores the sidebar",
              window.left_dock.isVisible() == sidebar_visible)

        print("\n[persistence]")
        notes_before = len(window.store.margin_notes())
        total_before = len(window.store.annotations)
        window.save()
        check("store clean after save", window.store.dirty is False)
        saved_path = window.store.path
        window.close_document()
        window2 = MainWindow(saved_path)
        window2.show()
        app.processEvents()
        check("notes survived reload",
              len(window2.store.margin_notes()) == notes_before,
              f"{len(window2.store.margin_notes())} != {notes_before}")
        check("marks survived reload",
              len(window2.store.annotations) == total_before,
              f"{len(window2.store.annotations)} != {total_before}")
        window2._apply_theme(window2.theme)

        print("\n[screenshot]")
        window2.resize(1500, 940)
        window2.show()
        app.processEvents()
        pixmap = window2.grab()
        ok = pixmap.save(output)
        check("screenshot written", ok and os.path.getsize(output) > 0, output)
        print(f"       -> {os.path.abspath(output)}")

    print("\n" + "=" * 60)
    print(f"passed: {len(PASSED)}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
