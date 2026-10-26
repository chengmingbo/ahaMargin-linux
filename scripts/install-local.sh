#!/usr/bin/env bash
# Install for the current user; PREFIX defaults to ~/.local.
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
APP_HOME="$PREFIX/lib/ahaMargin-linux"
PYTHON="${PYTHON:-python3}"

"$PYTHON" -m venv --copies --system-site-packages "$APP_HOME/venv"
"$APP_HOME/venv/bin/python" -m pip install --no-build-isolation "$SOURCE_DIR"

# Retain any Qt runtime libraries supplied with the previous local install.
if [ -d "$PREFIX/lib/marginnote/native" ]; then
    mkdir -p "$APP_HOME/native"
    cp -a "$PREFIX/lib/marginnote/native/." "$APP_HOME/native/"
fi

"$APP_HOME/venv/bin/python" - "$PREFIX" "$SOURCE_DIR" <<'PY'
from pathlib import Path
import shlex
import sys

prefix, source = map(Path, sys.argv[1:])
name = "ahaMargin-linux"
app_home = prefix / "lib" / name
launcher = prefix / "bin" / name
launcher.parent.mkdir(parents=True, exist_ok=True)
launcher.write_text(
    "#!/bin/sh\nexport LD_LIBRARY_PATH=" + shlex.quote(str(app_home / "native"))
    + '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\nexec '
    + shlex.quote(str(app_home / "venv/bin" / name)) + ' "$@"\n'
)
launcher.chmod(0o755)

applications = prefix / "share/applications"
applications.mkdir(parents=True, exist_ok=True)
desktop_name = "io.github.chengmingbo.ahaMargin-linux.desktop"
desktop = (source / "packaging" / desktop_name).read_text()
exec_path = str(launcher).replace("\\", "\\\\").replace('"', '\\"')
(applications / desktop_name).write_text(
    desktop.replace("Exec=ahaMargin-linux %f", f'Exec="{exec_path}" %f')
)

# Keep old terminal commands working, with a single entry in the app menu.
legacy_launcher = prefix / "bin/marginnote"
if legacy_launcher.exists():
    legacy_launcher.write_text("#!/bin/sh\nexec " + shlex.quote(str(launcher)) + ' "$@"\n')
    legacy_launcher.chmod(0o755)
legacy_desktop = applications / "io.github.chengmingbo.marginnote-linux.desktop"
if legacy_desktop.exists():
    legacy_desktop.unlink()
print(f"Installed {name}: {launcher}")
PY

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$PREFIX/share/applications"
fi
