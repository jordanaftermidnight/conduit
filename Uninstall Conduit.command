#!/bin/bash
#
# Uninstall Conduit.command — Remove all installed Conduit files (macOS)
#
# This removes the server, device, and bridge script installed by
# "Install Conduit.command". It does NOT remove Ollama itself,
# Python, or the downloaded Conduit source folder.
#

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

clear
echo "================================================"
echo "   Conduit — Uninstaller"
echo "================================================"
echo ""

REMOVED=""

# ── Remove server files ───────────────────────────────────────
CONDUIT_HOME="$HOME/Documents/Conduit"
if [ -d "$CONDUIT_HOME" ]; then
    rm -rf "$CONDUIT_HOME"
    echo "  Removed: $CONDUIT_HOME/"
    REMOVED="$REMOVED server"
else
    echo "  Not found: $CONDUIT_HOME/ (already removed)"
fi

# ── Remove device from Ableton User Library ───────────────────
for LIB_PATH in \
    "$HOME/Documents/User Library/Presets/MIDI Effects/Max MIDI Effect/Conduit" \
    "$HOME/Music/Ableton/User Library/Presets/MIDI Effects/Max MIDI Effect/Conduit" \
    "$HOME/Music/Ableton/User Library/Presets/Max for Live/Conduit"; do
    if [ -d "$LIB_PATH" ]; then
        rm -rf "$LIB_PATH"
        echo "  Removed: $LIB_PATH/"
        REMOVED="$REMOVED device"
    fi
done

# ── Remove bridge from Max Packages ───────────────────────────
for PKG_PATH in \
    "$HOME/Documents/Max 8/Packages/Conduit" \
    "$HOME/Documents/Max 9/Packages/Conduit"; do
    if [ -d "$PKG_PATH" ]; then
        rm -rf "$PKG_PATH"
        echo "  Removed: $PKG_PATH/"
        REMOVED="$REMOVED bridge"
    fi
done

# ── Ask about Ollama model ────────────────────────────────────
echo ""
if command -v ollama &>/dev/null; then
    if ollama list 2>/dev/null | grep -q "llama3.2"; then
        echo -n "  Remove llama3.2 model? (~2GB) (y/n): "
        read -n 1 REMOVE_MODEL
        echo ""
        if [ "$REMOVE_MODEL" = "y" ] || [ "$REMOVE_MODEL" = "Y" ]; then
            ollama rm llama3.2
            echo "  Removed: llama3.2 model"
            REMOVED="$REMOVED model"
        else
            echo "  Kept: llama3.2 model"
        fi
    fi
fi

# ── Summary ───────────────────────────────────────────────────
echo ""
echo "================================================"
if [ -n "$REMOVED" ]; then
    echo "  Conduit uninstalled."
else
    echo "  Nothing to remove — Conduit was not installed."
fi
echo "================================================"
echo ""
echo "  NOT removed:"
echo "  - Ollama (system app)"
echo "  - Python and pip packages"
echo "  - This Conduit source folder"
echo ""
echo "Press any key to close..."
read -n 1
