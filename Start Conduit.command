#!/bin/bash
#
# Start Conduit.command — Double-click to start the Conduit server
#
# This launches the AI server that powers the Conduit M4L device.
# Keep this window open while using Conduit in Ableton.
#

# Resolve script directory (works even when double-clicked from Finder)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server"

clear
echo "================================================"
echo "   Conduit — AI MIDI Server"
echo "================================================"
echo ""

# ── Check Python ────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 is not installed."
    echo ""
    echo "Install it from: https://www.python.org/downloads/"
    echo "Or via Homebrew:  brew install python3"
    echo ""
    echo "Press any key to close..."
    read -n 1
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1)
echo "  Python:  $PYTHON_VERSION"

# ── Check Ollama ────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    echo ""
    echo "WARNING: Ollama is not installed."
    echo "  Conduit needs Ollama for local AI model inference."
    echo "  Install from: https://ollama.com"
    echo ""
else
    echo "  Ollama:  $(ollama --version 2>&1 | head -1)"

    # Check if llama3.2 model is available
    if ! ollama list 2>/dev/null | grep -q "llama3.2"; then
        echo ""
        echo "  Pulling llama3.2 model (first time only)..."
        ollama pull llama3.2
        echo ""
    else
        echo "  Model:   llama3.2 ready"
    fi
fi

# ── Install Python dependencies if needed ───────────────────
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo ""
    echo "  Installing Python dependencies..."
    pip3 install -q fastapi uvicorn httpx 2>/dev/null || {
        echo "  ERROR: Failed to install dependencies."
        echo "  Try manually: pip3 install fastapi uvicorn httpx"
        echo ""
        echo "Press any key to close..."
        read -n 1
        exit 1
    }
    echo "  Dependencies installed."
fi

# ── Check if server is already running ──────────────────────
if lsof -i :9321 &>/dev/null; then
    echo ""
    echo "  Server already running on port 9321."
    echo "  Stopping existing server..."
    lsof -ti :9321 | xargs kill 2>/dev/null
    sleep 1
fi

# ── Start server ────────────────────────────────────────────
echo ""
echo "================================================"
echo "  Starting Conduit server on http://localhost:9321"
echo "  Keep this window open while using Ableton."
echo "  Press Ctrl+C to stop."
echo "================================================"
echo ""

cd "$SERVER_DIR"
python3 main.py
STATUS=$?

echo ""
if [ $STATUS -ne 0 ]; then
    echo "Server stopped with error (code $STATUS)."
else
    echo "Server stopped."
fi
echo ""
echo "Press any key to close..."
read -n 1
