"""Check rendered drawing positions during and after a mouse drag.

Run with: python3 tests/test_drawing_preview.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ahamargin.core.document import PdfDocument
from ahamargin.ui.pdf_view import PdfView
from ahamargin.ui.tools import Tool


def drawing_bounds(view: PdfView) -> tuple[int, int, int, int]:
    """Locate the magenta drawing in the actual viewport pixels."""
    image = view.viewport().grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = bytes(image.constBits())
    width = image.width()
    marked = [
        index // 4 for index in range(0, len(pixels), 4)
        if pixels[index] > 200 and pixels[index + 1] < 80 and pixels[index + 2] > 200
    ]
    if not marked:
        raise AssertionError("No drawing visible in the viewport")
    xs = [index % width for index in marked]
    ys = [index // width for index in marked]
    dpr = image.devicePixelRatio()
    return tuple(round(value / dpr) for value in (min(xs), min(ys), max(xs), max(ys)))


class DrawingPreviewTest(unittest.TestCase):
    def test_preview_and_finished_drawing_follow_drag(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "drawing.pdf")
            with pymupdf.open() as pdf:
                for _ in range(3):
                    pdf.new_page(width=595, height=842)
                pdf.save(path)
            doc = PdfDocument(path)
            try:
                for tool in (Tool.RECT, Tool.ELLIPSE, Tool.LINE, Tool.ARROW, Tool.INK):
                    for zoom, page, scroll_x, reverse in (
                        (0.75, 0, 0, False),
                        (1.75, 0, 90, False),
                        (1.25, 2, 55, False),
                        (1.25, 2, 55, True),
                    ):
                        with self.subTest(tool=tool, zoom=zoom, page=page, reverse=reverse):
                            view = PdfView()
                            try:
                                view.resize(640, 480)
                                view.set_document(doc)
                                view.show()
                                app.processEvents()
                                view.set_zoom(zoom)
                                page_y = view._pages[page][1]
                                view.verticalScrollBar().setValue(int(page_y + 35 * zoom))
                                view.horizontalScrollBar().setValue(scroll_x)
                                view.set_tool(tool)
                                view.set_tool_color("#ff00ff")
                                created = []

                                def on_created(annotation):
                                    created.append(annotation)
                                    view.set_annotations(created)

                                view.annotationCreated.connect(on_created)
                                app.processEvents()
                                start = view.pdf_to_widget(page, 120, 120)
                                end = view.pdf_to_widget(page, 260, 230)
                                expected = (start.x(), start.y(), end.x(), end.y())
                                if reverse:
                                    start, end = end, start
                                viewport = view.viewport()
                                QTest.mousePress(viewport, Qt.MouseButton.LeftButton,
                                                 Qt.KeyboardModifier.NoModifier, start)
                                QTest.mouseMove(viewport, QPoint(
                                    (start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
                                QTest.mouseMove(viewport, end)
                                app.processEvents()
                                preview = drawing_bounds(view)
                                QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton,
                                                   Qt.KeyboardModifier.NoModifier, end)
                                app.processEvents()
                                finished = drawing_bounds(view)
                                self.assertEqual(len(created), 1)
                                self.assertEqual(created[0].page, page)
                                for label, bounds in (("preview", preview), ("finished", finished)):
                                    for index, (actual, target) in enumerate(zip(bounds, expected)):
                                        # The saved arrowhead extends past the shaft endpoint.
                                        tolerance = 12 if tool is Tool.ARROW and label == "finished" and index >= 2 else 3
                                        self.assertAlmostEqual(actual, target, delta=tolerance,
                                                               msg=f"{label}: {bounds} != {expected}")
                            finally:
                                view.close()
            finally:
                doc.close()


if __name__ == "__main__":
    unittest.main()
