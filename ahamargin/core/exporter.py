"""Exporters: turn notes and annotations into shareable artefacts."""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Dict, List, Sequence, Tuple

from .annotations import Annotation

Annotation


def _fmt_time(timestamp: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
    except Exception:
        return ""


def _page_title(title: str, page: int) -> str:
    return f"Page {page + 1}"


def _margin_notes(annotations: Sequence[Annotation]) -> List[Annotation]:
    notes = [a for a in annotations if a.is_margin]
    notes.sort(key=lambda a: (a.page, a.order, a.created))
    return notes


def notes_to_markdown(
    annotations: Sequence[Annotation],
    document_title: str = "Document",
    include_marks: bool = False,
) -> str:
    """Render the margin notes as a Markdown study sheet."""
    out = io.StringIO()
    out.write(f"# {document_title}\n\n")
    out.write(f"_Exported {time.strftime('%Y-%m-%d %H:%M')} from ahaMargin-linux_\n\n")

    notes = _margin_notes(annotations)
    if notes:
        by_page: Dict[int, List[Annotation]] = {}
        for note in notes:
            by_page.setdefault(note.page, []).append(note)
        for page in sorted(by_page):
            out.write(f"## {_page_title(document_title, page)}\n\n")
            for index, note in enumerate(by_page[page], start=1):
                out.write(f"### Note {index}  ·  {_fmt_time(note.created)}\n\n")
                if note.capture:
                    quoted = note.capture.replace("\n", " ")
                    out.write(f"> {quoted}\n>\n")
                body = note.body.strip()
                if body:
                    out.write(body + "\n\n")
                out.write(f"*tag: `{note.color}`*\n\n")
    else:
        out.write("_No margin notes yet._\n\n")

    if include_marks:
        marks = [a for a in annotations if not a.is_margin]
        if marks:
            out.write("## Highlights and marks\n\n")
            by_page = {}
            for mark in marks:
                by_page.setdefault(mark.page, []).append(mark)
            for page in sorted(by_page):
                out.write(f"### {_page_title(document_title, page)}\n\n")
                for mark in by_page[page]:
                    out.write(f"- **{mark.kind.label}** — `{mark.color}`\n")
            out.write("\n")

    return out.getvalue()


def notes_to_json(annotations: Sequence[Annotation], document_title: str = "Document") -> str:
    """Full fidelity JSON dump of every annotation."""
    payload = {
        "document": document_title,
        "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "annotations": [
            {
                "id": a.id,
                "kind": a.kind.value,
                "page": a.page,
                "color": a.color,
                "opacity": a.opacity,
                "order": a.order,
                "text": a.text,
                "capture": a.capture,
                "created": _fmt_time(a.created),
                "modified": _fmt_time(a.modified),
                "rects": [r.as_list() for r in a.rects],
                "paths": [[list(p) for p in stroke] for stroke in a.paths],
            }
            for a in annotations
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def notes_to_csv(annotations: Sequence[Annotation], document_title: str = "Document") -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["page", "kind", "color", "capture", "note", "created"])
    for a in sorted(annotations, key=lambda x: (x.page, x.order)):
        writer.writerow([
            a.page + 1,
            a.kind.label,
            a.color,
            a.capture.replace("\n", " "),
            a.body.replace("\n", " "),
            _fmt_time(a.created),
        ])
    return out.getvalue()


# --------------------------------------------------------------------- mind map
_MIND_NODE_W = 300
_MIND_PAGE_W = 170
_MIND_GAP = 16
_MIND_X_ROOT = 40
_MIND_X_PAGE = 330
_MIND_X_NOTE = 570


def notes_to_mindmap_svg(
    annotations: Sequence[Annotation],
    document_title: str = "Document",
) -> str:
    """Render the margin notes as a two level mind map (SVG).

    The root holds the document, the middle column holds the pages and the
    right column holds the individual notes.  The result is a static vector
    image that can be dropped into any notes application.
    """
    notes = _margin_notes(annotations)
    by_page: Dict[int, List[Annotation]] = {}
    for note in notes:
        by_page.setdefault(note.page, []).append(note)

    page_nodes: List[Dict] = []
    note_nodes: List[Dict] = []
    edges: List[Tuple[Dict, Dict]] = []

    y = 60.0
    root = {"x": _MIND_X_ROOT, "y": y, "h": 54, "w": 250, "title": document_title, "sub": "root"}
    page_tops: List[float] = []
    for page in sorted(by_page):
        page_notes = by_page[page]
        blocks: List[Dict] = []
        py = y
        for note in page_notes:
            text = (note.body or note.capture or "").strip()
            lines = _wrap(text, 34) or ["(empty note)"]
            height = 30 + 18 * len(lines)
            blocks.append({"x": _MIND_X_NOTE, "y": py, "h": height, "w": _MIND_NODE_W,
                           "note": note, "lines": lines, "parent": None})
            py += height + _MIND_GAP
        block_height = (py - _MIND_GAP) - y if page_notes else 46
        page_node = {"x": _MIND_X_PAGE, "y": y, "h": max(block_height, 46), "w": _MIND_PAGE_W,
                     "title": f"Page {page + 1}", "sub": f"{len(page_notes)} note(s)"}
        page_nodes.append(page_node)
        page_tops.append(y)
        for block in blocks:
            block["parent"] = page_node
            note_nodes.append(block)
            edges.append((page_node, block))
        y += max(block_height, 46) + 34

    total_height = max(y + 40, 220.0)
    total_width = _MIND_X_NOTE + _MIND_NODE_W + 60

    parts: List[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width:.0f}" '
        f'height="{total_height:.0f}" viewBox="0 0 {total_width:.0f} {total_height:.0f}" '
        f'font-family="Noto Sans, DejaVu Sans, Helvetica, Arial, sans-serif">'
    )
    parts.append(
        '<defs><style>'
        '.t{font-size:14px;font-weight:600;fill:#1f2430}'
        '.s{font-size:11px;fill:#6b7280}'
        '.n{font-size:12.5px;fill:#242a36}'
        '.edge{stroke:#9aa3b2;stroke-width:1.6;fill:none}'
        '</style></defs>'
    )
    parts.append('<rect width="100%" height="100%" fill="#ffffff"/>')

    root_y = 60.0
    parts.append(
        f'<rect x="{root["x"]}" y="{root_y}" rx="10" ry="10" width="{root["w"]}" height="54" '
        f'fill="#2f6fed"/>'
        f'<text x="{root["x"] + 16}" y="{root_y + 25}" class="t" style="fill:#ffffff">'
        f'{_escape(document_title)[:34]}</text>'
        f'<text x="{root["x"] + 16}" y="{root_y + 43}" class="s" style="fill:#d8e3ff">'
        f'{len(notes)} margin note(s)</text>'
    )

    # Edges from the root to each page node.
    root_right = root["x"] + root["w"]
    root_mid = root_y + 27
    for page_node in page_nodes:
        page_mid = page_node["y"] + page_node["h"] / 2
        parts.append(
            f'<path class="edge" d="M {root_right} {root_mid} '
            f'C {root_right + 60} {root_mid}, {page_node["x"] - 60} {page_mid}, '
            f'{page_node["x"]} {page_mid}"/>'
        )

    for page_node in page_nodes:
        parts.append(
            f'<rect x="{page_node["x"]}" y="{page_node["y"]}" rx="8" ry="8" '
            f'width="{page_node["w"]}" height="{page_node["h"]}" fill="#eef2fb" stroke="#c9d6f2"/>'
            f'<text x="{page_node["x"] + 12}" y="{page_node["y"] + 22}" class="t">'
            f'{_escape(page_node["title"])}</text>'
            f'<text x="{page_node["x"] + 12}" y="{page_node["y"] + 38}" class="s">'
            f'{_escape(page_node["sub"])}</text>'
        )

    for block in note_nodes:
        note = block["note"]
        page_node = block["parent"]
        page_mid = page_node["y"] + page_node["h"] / 2
        block_mid = block["y"] + block["h"] / 2
        parts.append(
            f'<path class="edge" d="M {page_node["x"] + page_node["w"]} {page_mid} '
            f'C {page_node["x"] + page_node["w"] + 50} {page_mid}, '
            f'{block["x"] - 50} {block_mid}, {block["x"]} {block_mid}"/>'
        )
        parts.append(
            f'<rect x="{block["x"]}" y="{block["y"]}" rx="7" ry="7" width="{block["w"]}" '
            f'height="{block["h"]}" fill="{_escape(note.color)}" fill-opacity="0.22" '
            f'stroke="{_escape(note.color)}"/>'
        )
        for index, line in enumerate(block["lines"]):
            parts.append(
                f'<text x="{block["x"] + 12}" y="{block["y"] + 22 + index * 18}" '
                f'class="n" style="fill:#242a36">'
                f'{_escape(line)}</text>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


def _wrap(text: str, width: int) -> List[str]:
    words = text.split()
    if not words:
        return []
    lines: List[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


_AMP = chr(38)
_LT = chr(60)
_GT = chr(62)
_QUOT = chr(34)


def _escape(text: str) -> str:
    """XML-escape text for safe embedding in SVG output."""
    text = text.replace(_AMP, _AMP + "amp;")
    text = text.replace(_LT, _AMP + "lt;")
    text = text.replace(_GT, _AMP + "gt;")
    text = text.replace(_QUOT, _AMP + "quot;")
    return text
