#!/bin/bash
#
# Install Conduit.command — One-click installer for Conduit (macOS)
#
# Double-click this file to install Conduit. You only need to do this once.
# After installing, just open Ableton and drag Conduit onto a MIDI track.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_SRC="$SCRIPT_DIR/server"

# Install destinations
CONDUIT_HOME="$HOME/Documents/Conduit"
SERVER_DST="$CONDUIT_HOME/server"

# Ableton User Library paths (macOS) — check both common locations
ABLETON_MIDI_FX_ALT="$HOME/Documents/User Library/Presets/MIDI Effects/Max MIDI Effect"
ABLETON_MIDI_FX="$HOME/Music/Ableton/User Library/Presets/MIDI Effects/Max MIDI Effect"

# Max Packages — node.script finds files here via Max's search path
MAX8_PKG="$HOME/Documents/Max 8/Packages/Conduit/javascript"
MAX9_PKG="$HOME/Documents/Max 9/Packages/Conduit/javascript"

clear
echo "================================================"
echo "   Conduit — Installer"
echo "================================================"
echo ""

# ── [1/5] Check Python 3.9+ ───────────────────────────────────
echo "[1/5] Checking Python..."

if ! command -v python3 &>/dev/null; then
    echo ""
    echo "  ERROR: Python 3 is not installed."
    echo "  Install from: https://www.python.org/downloads/"
    echo "  Or via Homebrew: brew install python3"
    echo ""
    echo "Press any key to close..."
    read -n 1
    exit 1
fi

PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(echo "$PYTHON_VER" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VER" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]); then
    echo ""
    echo "  ERROR: Python $PYTHON_VER is too old. Conduit requires Python 3.9+."
    echo "  Install from: https://www.python.org/downloads/"
    echo ""
    echo "Press any key to close..."
    read -n 1
    exit 1
fi

echo "  Python $PYTHON_VER OK"

# ── [2/5] Check Ollama ────────────────────────────────────────
echo ""
echo "[2/5] Checking Ollama..."

if ! command -v ollama &>/dev/null; then
    echo ""
    echo "  ERROR: Ollama is not installed."
    echo "  Conduit needs Ollama for local AI model inference."
    echo ""
    echo "  Install from: https://ollama.com"
    echo "  Then re-run this installer."
    echo ""
    echo "Press any key to close..."
    read -n 1
    exit 1
fi

echo "  Ollama installed: $(ollama --version 2>&1 | head -1)"

# Check if Ollama server is reachable
if ! curl -s --connect-timeout 3 http://localhost:11434 >/dev/null 2>&1; then
    echo "  Ollama server not running — starting it..."
    ollama serve &>/dev/null &
    sleep 2
    if ! curl -s --connect-timeout 3 http://localhost:11434 >/dev/null 2>&1; then
        echo ""
        echo "  WARNING: Could not start Ollama server."
        echo "  Open the Ollama app manually, then re-run this installer."
        echo ""
        echo "Press any key to close..."
        read -n 1
        exit 1
    fi
fi
echo "  Ollama server reachable"

# ── [3/5] Pull llama3.2 model ─────────────────────────────────
echo ""
echo "[3/5] Checking model..."

if ollama list 2>/dev/null | grep -q "llama3.2"; then
    echo "  llama3.2 already downloaded"
else
    echo "  Downloading llama3.2 (~2GB, first time only)..."
    ollama pull llama3.2
    echo "  llama3.2 downloaded"
fi

# ── [4/5] Install Python dependencies ─────────────────────────
echo ""
echo "[4/5] Installing Python dependencies..."

if python3 -c "import fastapi" 2>/dev/null; then
    echo "  Dependencies already installed"
else
    if pip3 install -q -r "$SERVER_SRC/requirements.txt" 2>/dev/null; then
        echo "  Dependencies installed"
    elif pip3 install -q --user -r "$SERVER_SRC/requirements.txt" 2>/dev/null; then
        echo "  Dependencies installed (--user)"
    else
        echo ""
        echo "  ERROR: Failed to install Python dependencies."
        echo "  Try manually: pip3 install -r server/requirements.txt"
        echo ""
        echo "Press any key to close..."
        read -n 1
        exit 1
    fi
fi

# ── [5/5] Install files ───────────────────────────────────────
echo ""
echo "[5/5] Installing files..."

# Copy server files
mkdir -p "$SERVER_DST"
mkdir -p "$SERVER_DST/genres"
cp "$SERVER_SRC"/*.py "$SERVER_DST/"
cp "$SERVER_SRC/requirements.txt" "$SERVER_DST/"
if [ -d "$SERVER_SRC/genres" ]; then
    cp -R "$SERVER_SRC/genres/"* "$SERVER_DST/genres/" 2>/dev/null || true
fi
echo "  Server files: $SERVER_DST/"

# Find the .amxd device
AMXD_SRC=""
if [ -f "$SCRIPT_DIR/dist/Conduit/Conduit.amxd" ]; then
    AMXD_SRC="$SCRIPT_DIR/dist/Conduit/Conduit.amxd"
elif [ -f "$SCRIPT_DIR/m4l/Conduit.amxd" ]; then
    AMXD_SRC="$SCRIPT_DIR/m4l/Conduit.amxd"
else
    echo "  Building device..."
    if [ -f "$SCRIPT_DIR/package-device.sh" ]; then
        bash "$SCRIPT_DIR/package-device.sh" 2>/dev/null
        if [ -f "$SCRIPT_DIR/dist/Conduit/Conduit.amxd" ]; then
            AMXD_SRC="$SCRIPT_DIR/dist/Conduit/Conduit.amxd"
        fi
    fi
fi

BRIDGE_SRC="$SCRIPT_DIR/m4l/conduit-bridge.js"
if [ ! -f "$BRIDGE_SRC" ] && [ -f "$SCRIPT_DIR/dist/Conduit/conduit-bridge.js" ]; then
    BRIDGE_SRC="$SCRIPT_DIR/dist/Conduit/conduit-bridge.js"
fi

# Install to Ableton User Library
INSTALL_DIR=""
if [ -d "$ABLETON_MIDI_FX_ALT" ]; then
    INSTALL_DIR="$ABLETON_MIDI_FX_ALT/Conduit"
elif [ -d "$ABLETON_MIDI_FX" ]; then
    INSTALL_DIR="$ABLETON_MIDI_FX/Conduit"
elif [ -d "$HOME/Documents/User Library/Presets/MIDI Effects" ]; then
    mkdir -p "$ABLETON_MIDI_FX_ALT"
    INSTALL_DIR="$ABLETON_MIDI_FX_ALT/Conduit"
elif [ -d "$HOME/Music/Ableton/User Library" ]; then
    mkdir -p "$ABLETON_MIDI_FX"
    INSTALL_DIR="$ABLETON_MIDI_FX/Conduit"
else
    echo "  WARNING: Ableton User Library not found."
    echo "  Checked: ~/Documents/User Library/ and ~/Music/Ableton/"
    echo "  You'll need to copy the device manually."
fi

if [ -n "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR"
    if [ -n "$AMXD_SRC" ]; then
        cp "$AMXD_SRC" "$INSTALL_DIR/"
        echo "  Device: $INSTALL_DIR/Conduit.amxd"
    else
        echo "  WARNING: Conduit.amxd not found — device not installed."
        echo "  Run ./package-device.sh --install to build and install the device."
    fi
    if [ -f "$BRIDGE_SRC" ]; then
        cp "$BRIDGE_SRC" "$INSTALL_DIR/"
        echo "  Bridge: $INSTALL_DIR/conduit-bridge.js"
    fi
fi

# Install conduit-bridge.js to Max Packages
INSTALLED_PKG=0
for PKG_DIR in "$MAX8_PKG" "$MAX9_PKG"; do
    PARENT="$(dirname "$PKG_DIR")"
    GRANDPARENT="$(dirname "$PARENT")"
    if [ -d "$GRANDPARENT" ]; then
        mkdir -p "$PKG_DIR"
        if [ -f "$BRIDGE_SRC" ]; then
            cp "$BRIDGE_SRC" "$PKG_DIR/"
            echo "  Max Package: $PKG_DIR/conduit-bridge.js"
            INSTALLED_PKG=1
        fi
    fi
done

if [ "$INSTALLED_PKG" -eq 0 ]; then
    echo "  NOTE: No Max Packages directory found (Max 8 or Max 9)."
    echo "  node.script may not find conduit-bridge.js."
fi

# ── Done ──────────────────────────────────────────────────────
echo ""
echo "================================================"
echo "  Conduit installed successfully!"
echo "================================================"
echo ""
echo "  To use Conduit:"
echo "  1. Open Ableton Live"
echo "  2. Go to Browser > User Library > MIDI Effects > Conduit"
echo "  3. Drag Conduit onto a MIDI track"
echo "  4. The server starts automatically — no terminal needed"
echo ""
echo "  Server files: ~/Documents/Conduit/server/"
echo "  Server log:   ~/Documents/Conduit/server.log"
echo ""
echo "  NOTE: If macOS blocks this script, right-click it"
echo "  and choose Open instead of double-clicking."
echo ""
echo "Press any key to close..."
read -n 1
