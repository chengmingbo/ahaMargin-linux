"""Application entry point for ahaMargin-linux."""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from PySide6.QtWidgets import QApplication

from . import APP_ID, APP_NAME, ORGANIZATION_NAME
from .ui.main_window import MainWindow
from .ui.theme import LIGHT, apply_theme


def _existing_pdf(path: str) -> bool:
    return os.path.isfile(path) and path.lower().endswith(".pdf")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ahaMargin-linux",
        description=f"{APP_NAME} — a MarginNote style PDF marker for Linux.",
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        help="PDF file to open on startup.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the version and exit.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from . import __version__

        print(f"{APP_NAME} {__version__}")
        return 0

    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationDisplayName(APP_NAME)
    QApplication.setOrganizationName(ORGANIZATION_NAME)
    QApplication.setDesktopFileName(APP_ID)

    app = QApplication(sys.argv[:1] + argv)
    apply_theme(app, LIGHT)

    path = args.pdf
    if path and not _existing_pdf(path):
        print(f"warning: {path} is not a readable PDF file", file=sys.stderr)
        path = None

    window = MainWindow(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
