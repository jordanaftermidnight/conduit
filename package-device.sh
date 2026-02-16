#!/bin/bash
#
# package-device.sh — Build and package Conduit M4L device for Ableton Live
#
# Creates a distributable "Conduit" folder containing:
#   - Conduit.amxd       (Max for Live device)
#   - conduit-bridge.js  (node.script — server communication)
#   - midi-applicator.js (clip writer)
#   - param-applicator.js (parameter applicator)
#   - session-context.js (Ableton session state)
#
# Usage:
#   ./package-device.sh           # Build + package to dist/Conduit/
#   ./package-device.sh --install # Build + install to Ableton User Library

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
M4L_DIR="$SCRIPT_DIR/m4l"
DIST_DIR="$SCRIPT_DIR/dist/Conduit"

# Ableton User Library paths (macOS) — check both common locations
ABLETON_MIDI_FX_ALT="$HOME/Documents/User Library/Presets/MIDI Effects/Max MIDI Effect"
ABLETON_MIDI_FX="$HOME/Music/Ableton/User Library/Presets/MIDI Effects/Max MIDI Effect"
ABLETON_M4L="$HOME/Music/Ableton/User Library/Presets/Max for Live"

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

# Copy the device file
cp "$M4L_DIR/Conduit.amxd" "$DIST_DIR/"

# Copy all JS dependencies (must be co-located with .amxd)
for js in conduit-bridge.js midi-applicator.js param-applicator.js session-context.js; do
    if [ -f "$M4L_DIR/$js" ]; then
        cp "$M4L_DIR/$js" "$DIST_DIR/"
        echo "  ✓ $js"
    else
        echo "  ✗ MISSING: $js"
        exit 1
    fi
done

echo
echo "✓ Device packaged: $DIST_DIR/"
echo "  Contents:"
ls -la "$DIST_DIR/" | tail -n +2
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
    echo "  ✓ Installed to: $INSTALL_DIR"
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
echo "║  3. Wait 5s for node.script to load   ║"
echo "║  4. Type a prompt and press Enter!    ║"
echo "╚═══════════════════════════════════════╝"
