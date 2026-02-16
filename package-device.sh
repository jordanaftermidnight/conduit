#!/bin/bash
#
# package-device.sh — Build and package Conduit M4L device for Ableton Live
#
# Creates a distributable "Conduit" folder containing:
#   - Conduit.amxd       (Max for Live device, with [js] files embedded in AMPF)
#   - conduit-bridge.js  (node.script — also installed to Max Packages for search path)
#
# Usage:
#   ./package-device.sh           # Build + package to dist/Conduit/
#   ./package-device.sh --install # Build + install to Ableton User Library + Max Packages

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
M4L_DIR="$SCRIPT_DIR/m4l"
DIST_DIR="$SCRIPT_DIR/dist/Conduit"

# Ableton User Library paths (macOS) — check both common locations
ABLETON_MIDI_FX_ALT="$HOME/Documents/User Library/Presets/MIDI Effects/Max MIDI Effect"
ABLETON_MIDI_FX="$HOME/Music/Ableton/User Library/Presets/MIDI Effects/Max MIDI Effect"
ABLETON_M4L="$HOME/Music/Ableton/User Library/Presets/Max for Live"

# Max Packages — node.script finds files here via Max's search path
MAX8_PKG="$HOME/Documents/Max 8/Packages/Conduit/javascript"
MAX9_PKG="$HOME/Documents/Max 9/Packages/Conduit/javascript"

echo "╔═══════════════════════════════════════╗"
echo "║   Conduit — M4L Device Packager       ║"
echo "╚═══════════════════════════════════════╝"
echo

# ── Step 1: Build the patcher ──
echo "→ Building patcher..."
cd "$M4L_DIR"
python3 build-device.py
echo

# ── Step 2: Package into dist/ ──
echo "→ Packaging device..."
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

# Copy the device file (.amxd has [js] files embedded in AMPF)
cp "$M4L_DIR/Conduit.amxd" "$DIST_DIR/"
echo "  ✓ Conduit.amxd"

# Copy conduit-bridge.js alongside .amxd (node.script needs it on disk)
cp "$M4L_DIR/conduit-bridge.js" "$DIST_DIR/"
echo "  ✓ conduit-bridge.js"

echo
echo "✓ Device packaged: $DIST_DIR/"
echo

# ── Step 3: Optional install ──
if [ "${1:-}" = "--install" ]; then
    echo "→ Installing to Ableton User Library..."

    # Try ~/Documents/User Library first (common), then ~/Music/Ableton/
    INSTALL_DIR=""
    if [ -d "$ABLETON_MIDI_FX_ALT" ]; then
        INSTALL_DIR="$ABLETON_MIDI_FX_ALT/Conduit"
    elif [ -d "$ABLETON_MIDI_FX" ]; then
        INSTALL_DIR="$ABLETON_MIDI_FX/Conduit"
    elif [ -d "$ABLETON_M4L" ]; then
        INSTALL_DIR="$ABLETON_M4L/Conduit"
    elif [ -d "$HOME/Documents/User Library/Presets/MIDI Effects" ]; then
        mkdir -p "$ABLETON_MIDI_FX_ALT"
        INSTALL_DIR="$ABLETON_MIDI_FX_ALT/Conduit"
    elif [ -d "$HOME/Music/Ableton/User Library" ]; then
        mkdir -p "$ABLETON_MIDI_FX"
        INSTALL_DIR="$ABLETON_MIDI_FX/Conduit"
    else
        echo "  ✗ Ableton User Library not found"
        echo "    Checked: ~/Documents/User Library/ and ~/Music/Ableton/"
        echo "    Copy $DIST_DIR/ manually to your Ableton User Library"
        exit 0
    fi

    rm -rf "$INSTALL_DIR"
    cp -R "$DIST_DIR" "$INSTALL_DIR"
    echo "  ✓ Device: $INSTALL_DIR"

    # ── Install node.script dependency to Max Packages search path ──
    echo
    echo "→ Installing conduit-bridge.js to Max Packages..."
    INSTALLED_PKG=0
    for PKG_DIR in "$MAX8_PKG" "$MAX9_PKG"; do
        PARENT="$(dirname "$PKG_DIR")"
        GRANDPARENT="$(dirname "$PARENT")"
        if [ -d "$GRANDPARENT" ]; then
            mkdir -p "$PKG_DIR"
            cp "$M4L_DIR/conduit-bridge.js" "$PKG_DIR/"
            echo "  ✓ $PKG_DIR/conduit-bridge.js"
            INSTALLED_PKG=1
        fi
    done

    if [ "$INSTALLED_PKG" -eq 0 ]; then
        echo "  ⚠ No Max Packages directory found"
        echo "    node.script may not find conduit-bridge.js"
        echo "    Manually copy it to ~/Documents/Max 8/Packages/Conduit/javascript/"
    fi

    echo
    echo "  In Ableton: Browser → User Library → MIDI Effects → Conduit"
else
    echo "To install to Ableton User Library:"
    echo "  ./package-device.sh --install"
    echo
    echo "Or manually copy the folder:"
    echo "  cp -R $DIST_DIR ~/Music/Ableton/User\\ Library/Presets/MIDI\\ Effects/Max\\ MIDI\\ Effect/"
fi

echo
echo "╔═══════════════════════════════════════╗"
echo "║  Before using in Ableton:             ║"
echo "║  1. Double-click 'Start Conduit'      ║"
echo "║     to launch the AI server           ║"
echo "║  2. Drag Conduit.amxd onto a          ║"
echo "║     MIDI track in Ableton             ║"
echo "║  3. Wait a few seconds for it to load ║"
echo "║  4. Type a prompt and press Enter!    ║"
echo "╚═══════════════════════════════════════╝"
