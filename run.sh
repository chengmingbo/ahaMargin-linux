#!/usr/bin/env bash
# Launch ahaMargin-linux.
#
# Usage:
#   ./run.sh                 open with no document
#   ./run.sh file.pdf        open a document
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c "import PySide6" >/dev/null 2>&1; then
    echo "PySide6 is not installed.  Run:" >&2
    echo "    pip3 install --user -r requirements.txt" >&2
    exit 1
fi

if ! "$PYTHON" -c "import pymupdf" >/dev/null 2>&1; then
    echo "PyMuPDF is not installed.  Run:" >&2
    echo "    pip3 install --user -r requirements.txt" >&2
    exit 1
fi

exec "$PYTHON" -m ahamargin "$@"
