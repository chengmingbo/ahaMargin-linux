#!/usr/bin/env bash
# Install the system libraries Qt 6 needs for the xcb (X11) platform plugin.
#
# Without these the application still runs, but only with the "offscreen"
# platform plugin, which cannot open a window.
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "This script needs root.  Try: sudo $0" >&2
    exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
    echo "Installing Debian/Ubuntu packages..."
    apt-get update
    apt-get install -y --no-install-recommends \
        libgl1 \
        libegl1 \
        libxkbcommon-x11-0 \
        libxcb-cursor0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-randr0 \
        libxcb-render-util0 \
        libxcb-shape0 \
        libxcb-xinerama0 \
        libxcb-xkb1 \
        libdbus-1-3 \
        libfontconfig1
elif command -v dnf >/dev/null 2>&1; then
    echo "Installing Fedora packages..."
    dnf install -y \
        mesa-libGL \
        libxkbcommon-x11 \
        libxcb-cursor \
        libxcb-icccm \
        libxcb-image \
        libxcb-keysyms \
        libxcb-randr \
        libxcb-render-util \
        libxcb-shape \
        libxcb-xinerama \
        dbus-libs \
        fontconfig
else
    echo "Unsupported distribution.  Install the Qt 6 xcb runtime libraries" >&2
    echo "for your system and try again." >&2
    exit 1
fi

echo "Done.  Now run:  pip3 install --user -r requirements.txt"
