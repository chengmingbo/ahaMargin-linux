"""Tools available on the PDF canvas."""

from __future__ import annotations

from enum import Enum
from typing import Dict, Optional

from ..core.annotations import AnnKind


class Tool(Enum):
    POINTER = "pointer"
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
    ERASER = "eraser"

    @property
    def label(self) -> str:
        return _LABELS.get(self, self.value.title())

    @property
    def icon(self) -> str:
        return _ICONS.get(self, "pointer")

    @property
    def shortcut(self) -> str:
        return _SHORTCUTS.get(self, "")

    @property
    def kind(self) -> Optional[AnnKind]:
        """The annotation kind this tool creates (``None`` for pointer/eraser)."""
        if self is Tool.POINTER or self is Tool.ERASER:
            return None
        return AnnKind(self.value)

    @property
    def is_drawing(self) -> bool:
        return self in (Tool.INK, Tool.RECT, Tool.ELLIPSE, Tool.ARROW, Tool.LINE)

    @property
    def is_text_markup(self) -> bool:
        return self in (Tool.HIGHLIGHT, Tool.UNDERLINE, Tool.STRIKEOUT, Tool.SQUIGGLY)

    @property
    def default_color(self) -> str:
        return _COLORS.get(self, "#ffd54f")

    @property
    def hint(self) -> str:
        return _HINTS.get(self, "")


_LABELS: Dict[Tool, str] = {
    Tool.POINTER: "Note (select text to capture)",
    Tool.HIGHLIGHT: "Highlight",
    Tool.UNDERLINE: "Underline",
    Tool.STRIKEOUT: "Strikethrough",
    Tool.SQUIGGLY: "Squiggly",
    Tool.INK: "Freehand pen",
    Tool.RECT: "Rectangle",
    Tool.ELLIPSE: "Ellipse",
    Tool.ARROW: "Arrow",
    Tool.LINE: "Line",
    Tool.TEXT: "Text box",
    Tool.NOTE: "Sticky note",
    Tool.ERASER: "Eraser",
}

_ICONS: Dict[Tool, str] = {
    Tool.POINTER: "pointer",
    Tool.HIGHLIGHT: "highlight",
    Tool.UNDERLINE: "underline",
    Tool.STRIKEOUT: "strikeout",
    Tool.SQUIGGLY: "squiggly",
    Tool.INK: "pen",
    Tool.RECT: "rect",
    Tool.ELLIPSE: "ellipse",
    Tool.ARROW: "arrow",
    Tool.LINE: "line",
    Tool.TEXT: "textbox",
    Tool.NOTE: "sticky",
    Tool.ERASER: "eraser",
}

_SHORTCUTS: Dict[Tool, str] = {
    Tool.POINTER: "N",
    Tool.HIGHLIGHT: "H",
    Tool.UNDERLINE: "U",
    Tool.STRIKEOUT: "X",
    Tool.SQUIGGLY: "S",
    Tool.INK: "P",
    Tool.RECT: "R",
    Tool.ELLIPSE: "O",
    Tool.ARROW: "A",
    Tool.LINE: "L",
    Tool.TEXT: "T",
    Tool.NOTE: "Y",
    Tool.ERASER: "E",
}

_COLORS: Dict[Tool, str] = {
    Tool.HIGHLIGHT: "#ffd54f",
    Tool.UNDERLINE: "#4caf50",
    Tool.STRIKEOUT: "#ef5350",
    Tool.SQUIGGLY: "#ab47bc",
    Tool.INK: "#e53935",
    Tool.RECT: "#2f6fed",
    Tool.ELLIPSE: "#26a69a",
    Tool.ARROW: "#5e35b1",
    Tool.LINE: "#455a64",
    Tool.TEXT: "#2f6fed",
    Tool.NOTE: "#ffca28",
}


_HINTS: Dict[Tool, str] = {
    Tool.POINTER: "Select text to capture it as a margin note",
    Tool.HIGHLIGHT: "Drag across text to highlight it",
    Tool.UNDERLINE: "Drag across text to underline it",
    Tool.STRIKEOUT: "Drag across text to strike it through",
    Tool.SQUIGGLY: "Drag across text to mark it with a squiggle",
    Tool.INK: "Draw freehand with the pen",
    Tool.RECT: "Drag to draw a rectangle",
    Tool.ELLIPSE: "Drag to draw an ellipse",
    Tool.ARROW: "Drag to draw an arrow",
    Tool.LINE: "Drag to draw a line",
    Tool.TEXT: "Click to place a text box",
    Tool.NOTE: "Click to place a sticky note",
    Tool.ERASER: "Click an annotation to remove it",
}
