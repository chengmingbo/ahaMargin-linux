"""Dialog for the print layout options.

The print layout is the one feature where a few numbers make a big
difference - how wide the note column is, whether the quoted passage is
repeated, which pages are printed - so they get a dialog of their own,
complete with a small schematic preview of the resulting sheet.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt
from .settings import application_settings
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.print_layout import PAGE_SIZES, PrintOptions, sheet_size


class LayoutPreview(QWidget):
    """A small schematic drawing of the sheet the layout will produce."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(132)
        self.setMinimumWidth(280)
        self._options = PrintOptions()

    def set_options(self, options: PrintOptions) -> None:
        self._options = options
        self.update()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = QApplication.instance().palette()
        surface = palette.color(QPalette.ColorRole.Base)
        window = palette.color(QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.WindowText)
        mid = palette.color(QPalette.ColorRole.Mid)

        painter.fillRect(self.rect(), window)

        sheet_w, sheet_h = sheet_size(self._options)
        padding = 14.0
        available_w = self.width() - 2 * padding
        available_h = self.height() - 2 * padding
        scale = min(available_w / sheet_w, available_h / sheet_h)
        width = sheet_w * scale
        height = sheet_h * scale
        sheet = QRectF(
            (self.width() - width) / 2.0, (self.height() - height) / 2.0, width, height
        )

        painter.setBrush(surface)
        painter.setPen(QPen(mid, 1.0))
        painter.drawRoundedRect(sheet, 3, 3)

        # The printable area inside the sheet.
        margin = max(2.0, self._options.margin * scale)
        content = sheet.adjusted(margin, margin, -margin, -margin)
        content_w = content.width()
        ratio = max(0.15, min(0.75, self._options.note_ratio))
        gap = max(2.0, self._options.gap * scale)
        note_w = content_w * ratio
        page_w = content_w - note_w - gap

        # The page image on the left.
        page_block = QRectF(content.left(), content.top(), page_w, content.height())
        painter.setBrush(QColor(text.red(), text.green(), text.blue(), 28))
        painter.setPen(QPen(mid, 1.0))
        painter.drawRect(page_block)

        # A couple of "text lines" so the page reads as a page.
        line_y = page_block.top() + 8
        while line_y < page_block.bottom() - 6:
            painter.setPen(QPen(text, 1.0))
            painter.drawLine(
                QPointF(page_block.left() + 6, line_y),
                QPointF(page_block.right() - 6, line_y),
            )
            line_y += 8

        # The note column on the right.
        note_block = QRectF(content.left() + page_w + gap, content.top(), note_w, content.height())
        painter.setBrush(QColor(text.red(), text.green(), text.blue(), 18))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(note_block)

        note_y = note_block.top() + 6
        while note_y < note_block.bottom() - 10:
            painter.setPen(QPen(text, 1.0))
            painter.drawLine(
                QPointF(note_block.left() + 3, note_y),
                QPointF(note_block.right() - 6, note_y),
            )
            note_y += 11


class PrintOptionsDialog(QDialog):
    """Collects the options for the print layout."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        options: Optional[PrintOptions] = None,
        settings: Optional[QSettings] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Print layout")
        self.setModal(True)
        self._settings = settings if settings is not None else application_settings()

        self.options = options or PrintOptions()
        self._build_ui()
        self._apply_options(self.options)
        self._load_settings()
        self._refresh_preview()

    # ------------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(10)

        intro = QLabel(
            "Print the document with its margin notes set in the margin, "
            "next to the passage each note belongs to."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self.preview = LayoutPreview(self)
        outer.addWidget(self.preview)

        form = QFormLayout()
        form.setSpacing(8)

        self.size_combo = QComboBox()
        for name in PAGE_SIZES:
            self.size_combo.addItem(name)
        self.size_combo.currentTextChanged.connect(self._refresh_preview)
        form.addRow("Paper size", self.size_combo)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItem("Landscape", "landscape")
        self.orientation_combo.addItem("Portrait", "portrait")
        self.orientation_combo.currentIndexChanged.connect(self._refresh_preview)
        form.addRow("Orientation", self.orientation_combo)

        width_row = QHBoxLayout()
        self.note_slider = QSlider(Qt.Orientation.Horizontal)
        self.note_slider.setRange(20, 60)
        self.note_slider.setSingleStep(2)
        self.note_slider.valueChanged.connect(self._refresh_preview)
        self.note_value = QLabel("36%")
        self.note_value.setMinimumWidth(38)
        width_row.addWidget(self.note_slider)
        width_row.addWidget(self.note_value)
        form.addRow("Note column", width_row)

        self.font_spin = QSpinBox()
        self.font_spin.setRange(7, 16)
        self.font_spin.setSuffix(" pt")
        self.font_spin.valueChanged.connect(self._refresh_preview)
        form.addRow("Note text size", self.font_spin)

        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("all pages, e.g. 1-5, 9")
        self.range_edit.textChanged.connect(self._refresh_preview)
        form.addRow("Pages", self.range_edit)

        outer.addLayout(form)

        self.quotes_check = QCheckBox("Repeat the quoted passage above each note")
        self.quotes_check.toggled.connect(self._refresh_preview)
        outer.addWidget(self.quotes_check)

        self.numbers_check = QCheckBox("Print page numbers in the footer")
        self.numbers_check.toggled.connect(self._refresh_preview)
        outer.addWidget(self.numbers_check)

        self.annotations_check = QCheckBox("Print the highlights and other marks")
        self.annotations_check.toggled.connect(self._refresh_preview)
        outer.addWidget(self.annotations_check)

        self.annotated_only_check = QCheckBox("Only print pages that have notes")
        self.annotated_only_check.toggled.connect(self._refresh_preview)
        outer.addWidget(self.annotated_only_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ---------------------------------------------------------------- options
    def _apply_options(self, options: PrintOptions) -> None:
        """Show the given options in the widgets."""
        index = self.size_combo.findText(options.page_size)
        if index >= 0:
            self.size_combo.setCurrentIndex(index)
        index = self.orientation_combo.findData(options.orientation)
        if index >= 0:
            self.orientation_combo.setCurrentIndex(index)
        self.note_slider.setValue(int(round(options.note_ratio * 100)))
        self.font_spin.setValue(int(options.font_size))
        self.quotes_check.setChecked(options.include_quotes)
        self.numbers_check.setChecked(options.include_page_numbers)
        self.annotations_check.setChecked(options.show_annotations)
        self.annotated_only_check.setChecked(options.only_annotated_pages)
        self.range_edit.setText(options.page_range)

    def current_options(self) -> PrintOptions:
        """Read the widgets back into a :class:`PrintOptions`."""
        options = PrintOptions(
            page_size=self.size_combo.currentText(),
            orientation=self.orientation_combo.currentData(),
            note_ratio=self.note_slider.value() / 100.0,
            font_size=float(self.font_spin.value()),
            include_quotes=self.quotes_check.isChecked(),
            include_page_numbers=self.numbers_check.isChecked(),
            only_annotated_pages=self.annotated_only_check.isChecked(),
            page_range=self.range_edit.text().strip(),
            show_annotations=self.annotations_check.isChecked(),
        )
        return options

    def _refresh_preview(self) -> None:
        options = self.current_options()
        self.note_value.setText(f"{self.note_slider.value()}%")
        self.preview.set_options(options)

    # --------------------------------------------------------------- settings
    def _load_settings(self) -> None:
        settings = self._settings
        size = settings.value("print/page_size")
        if size in PAGE_SIZES:
            self.size_combo.setCurrentText(str(size))
        orientation = settings.value("print/orientation")
        if orientation in ("landscape", "portrait"):
            index = self.orientation_combo.findData(orientation)
            if index >= 0:
                self.orientation_combo.setCurrentIndex(index)
        ratio = settings.value("print/note_ratio")
        if ratio is not None:
            try:
                self.note_slider.setValue(int(round(float(ratio) * 100)))
            except (TypeError, ValueError):
                pass
        size_value = settings.value("print/font_size")
        if size_value is not None:
            try:
                self.font_spin.setValue(int(float(size_value)))
            except (TypeError, ValueError):
                pass
        for key, widget in (
            ("print/include_quotes", self.quotes_check),
            ("print/include_page_numbers", self.numbers_check),
            ("print/show_annotations", self.annotations_check),
            ("print/only_annotated_pages", self.annotated_only_check),
        ):
            value = settings.value(key)
            if value is not None:
                widget.setChecked(str(value).lower() in ("1", "true", "yes"))
        page_range = settings.value("print/page_range")
        if page_range:
            self.range_edit.setText(str(page_range))

    def save_settings(self) -> None:
        options = self.current_options()
        settings = self._settings
        settings.setValue("print/page_size", options.page_size)
        settings.setValue("print/orientation", options.orientation)
        settings.setValue("print/note_ratio", options.note_ratio)
        settings.setValue("print/font_size", options.font_size)
        settings.setValue("print/include_quotes", options.include_quotes)
        settings.setValue("print/include_page_numbers", options.include_page_numbers)
        settings.setValue("print/show_annotations", options.show_annotations)
        settings.setValue("print/only_annotated_pages", options.only_annotated_pages)
        settings.setValue("print/page_range", options.page_range)
