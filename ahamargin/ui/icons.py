"""Vector icons.

Every icon is a small 24x24 stroke SVG rendered through ``QSvgRenderer`` so it
stays crisp at any DPI and can be re-tinted for the active theme.
"""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

COLOR_TOKEN = "%%COLOR%%"

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" '
    'fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

ICONS: Dict[str, str] = {
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "save": '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M8 3v6h8V3"/><path d="M8 21v-7h8v7"/>',
    "export": '<path d="M12 3v12"/><path d="M8 7l4-4 4 4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4.2-4.2"/>',
    "zoom-in": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4.2-4.2"/><path d="M11 8.5v5"/><path d="M8.5 11h5"/>',
    "zoom-out": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4.2-4.2"/><path d="M8.5 11h5"/>',
    "fit-width": '<path d="M3 12h18"/><path d="M7 8l-4 4 4 4"/><path d="M17 8l4 4-4 4"/>',
    "fit-page": '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M16 3h3a2 2 0 0 1 2 2v3"/><path d="M8 21H5a2 2 0 0 1-2-2v-3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/><rect x="8" y="8" width="8" height="8" rx="1"/>',
    "actual-size": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 9h6v6H9z"/>',
    "highlight": '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M14.5 6.5l3 3"/>',
    "underline": '<path d="M7 4v7a5 5 0 0 0 10 0V4"/><path d="M5 20h14"/>',
    "strikeout": '<path d="M4 12h16"/><path d="M8 8.5a4 4 0 0 1 8 0"/><path d="M8 15.5a4 4 0 0 0 8 0"/>',
    "squiggly": '<path d="M3 14c1.5-3 3-3 4.5 0s3 3 4.5 0 3-3 4.5 0 3 3 4.5 0"/>',
    "pen": '<path d="M12 19l7-7-4-4-7 7-1 5z"/><path d="M15 8l2-2a2.8 2.8 0 0 1 4 4l-2 2"/>',
    "rect": '<rect x="4" y="6" width="16" height="12" rx="2"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="8" ry="6"/>',
    "arrow": '<path d="M5 19L18 6"/><path d="M12 6h6v6"/>',
    "line": '<path d="M5 19L19 5"/>',
    "textbox": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M8 10h8"/><path d="M12 10v6"/>',
    "sticky": '<path d="M5 4h14v10l-5 5H5z"/><path d="M14 19v-5h5"/>',
    "eraser": '<path d="M8 20h12"/><path d="M15.5 4.5l4 4L10 18H6l-2.5-2.5z"/><path d="M9 9.5l5 5"/>',
    "pointer": '<path d="M6 3l12 9-5.2 1.1L15.8 19l-2.4 1.2-3-5.8L7 18z"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6.5 7l1 13h9l1-13"/><path d="M10.5 11v5M13.5 11v5"/>',
    "page": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M9 12h6M9 16h4"/>',
    "outline": '<path d="M4 6h3.5M4 12h3.5M4 18h3.5"/><path d="M11 6h9M11 12h9M11 18h9"/>',
    "mindmap": '<circle cx="4.5" cy="12" r="2.4"/><circle cx="18.5" cy="6" r="2.4"/><circle cx="18.5" cy="18" r="2.4"/><path d="M6.8 10.9l9.4-3.8M6.8 13.1l9.4 3.8"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4l1.4-1.4M18 6l1.4-1.4"/>',
    "moon": '<path d="M20.5 14.8A8.7 8.7 0 0 1 9.2 3.5a8.7 8.7 0 1 0 11.3 11.3z"/>',
    "chevron-down": '<path d="M6 9.5l6 6 6-6"/>',
    "chevron-right": '<path d="M9.5 6l6 6-6 6"/>',
    "prev-page": '<path d="M15 6l-6 6 6 6"/>',
    "next-page": '<path d="M9 6l6 6-6 6"/>',
    "first-page": '<path d="M18 6l-6 6 6 6"/><path d="M7 6v12"/>',
    "last-page": '<path d="M6 6l6 6-6 6"/><path d="M17 6v12"/>',
    "note": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14.5 4v16"/><path d="M6 9h5M6 13h5"/>',
    "palette": '<circle cx="12" cy="12" r="9"/><circle cx="9" cy="9.5" r="1.3"/><circle cx="14.8" cy="9.5" r="1.3"/><circle cx="9" cy="15" r="1.3"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5.5 15H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v.5"/>',
    "anchor": '<path d="M10 13.5a4.5 4.5 0 0 0 6.4 0l2-2a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10.5a4.5 4.5 0 0 0-6.4 0l-2 2a4.5 4.5 0 0 0 6.4 6.4l1-1"/>',
    "panel-right": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    "panel-left": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    "bookmark": '<path d="M6 3.5h12v17l-6-5-6 5z"/>',
    "goto": '<path d="M14 4h5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-5"/><path d="M10 8l-4 4 4 4"/><path d="M6 12h9"/>',
    "sparkle": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "sync": '<path d="M20 11a8 8 0 0 0-13.7-5.2L3 9"/><path d="M4 13a8 8 0 0 0 13.7 5.2L21 15"/><path d="M3 4v5h5"/><path d="M21 20v-5h-5"/>',
}

_cache: Dict[tuple, QIcon] = {}


def color_dot(color: str, size: int = 12) -> QIcon:
    """A small filled circle in ``color`` (used as a list item marker)."""
    key = ("__dot__", color, size)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(0, 0, 0, 60), 1))
    painter.setBrush(QColor(color))
    painter.drawEllipse(1, 1, size * ratio - 2, size * ratio - 2)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    result = QIcon(pixmap)
    _cache[key] = result
    return result


def svg_source(name: str, color: str = "#000000") -> str:
    body = ICONS.get(name)
    if body is None:
        body = ICONS["page"]
    return _SVG_TEMPLATE.format(color=color, body=body)


def icon(name: str, color: str = "#4b5563", size: int = 20) -> QIcon:
    """Return a themed icon (cached)."""
    key = (name, color, size)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    source = svg_source(name, color)
    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    ratio = 2  # render at 2x for crispness
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size * ratio, size * ratio))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    result = QIcon(pixmap)
    _cache[key] = result
    return result
