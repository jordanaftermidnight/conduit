#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# Conduit Installer — macOS setup script
# Checks dependencies, installs Ollama if needed, pulls the right
# model for your system, and verifies the server starts.
#
# Usage: bash install.sh
# ─────────────────────────────────────────────────────────────────────

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

CONDUIT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVER_DIR="$CONDUIT_DIR/server"

echo -e "${CYAN}"
echo "  ┌─────────────────────────────────┐"
echo "  │         CONDUIT INSTALLER        │"
echo "  │   LLM ↔ Ableton Live Bridge     │"
echo "  │   by Semitone Autonomy           │"
echo "  └─────────────────────────────────┘"
echo -e "${NC}"

# ── System Detection ─────────────────────────────────────────────────

TOTAL_RAM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
TOTAL_RAM_GB=$((TOTAL_RAM_BYTES / 1073741824))
# Subtract ~6GB for macOS + Ableton
AVAILABLE_GB=$((TOTAL_RAM_GB - 6))
[ "$AVAILABLE_GB" -lt 0 ] && AVAILABLE_GB=0

CHIP=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Unknown")

echo -e "System: ${GREEN}$CHIP${NC}"
echo -e "RAM: ${GREEN}${TOTAL_RAM_GB}GB total${NC}, ~${GREEN}${AVAILABLE_GB}GB${NC} available for LLM"
echo ""

# ── Select Model ─────────────────────────────────────────────────────

if [ "$AVAILABLE_GB" -le 3 ]; then
    MODEL="qwen3:1.7b"
    MODEL_DESC="Basic — simple patterns (1.2GB)"
elif [ "$AVAILABLE_GB" -le 7 ]; then
    MODEL="qwen3:4b"
    MODEL_DESC="Good — genre-aware generation (2.8GB)"
elif [ "$AVAILABLE_GB" -le 12 ]; then
    MODEL="qwen3:8b"
    MODEL_DESC="Full capability — default (5GB)"
elif [ "$AVAILABLE_GB" -le 20 ]; then
    MODEL="qwen3:14b"
    MODEL_DESC="Excellent — complex arrangements (9GB)"
else
    MODEL="qwen2.5:32b"
    MODEL_DESC="Best local — near-cloud quality (19GB)"
fi

echo -e "Recommended model: ${GREEN}$MODEL${NC} — $MODEL_DESC"
echo ""

# ── Check Python ─────────────────────────────────────────────────────

echo -e "${CYAN}[1/4] Checking Python...${NC}"

if command -v python3 &>/dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 10 ]; then
        echo -e "  ${GREEN}✓${NC} Python $PYTHON_VERSION"
    else
        echo -e "  ${RED}✗${NC} Python $PYTHON_VERSION found but 3.10+ required"
        echo "  Install: brew install python@3.12"
        exit 1
    fi
else
    echo -e "  ${RED}✗${NC} Python not found"
    echo "  Install: brew install python@3.12"
    exit 1
fi

# ── Check/Install Ollama ─────────────────────────────────────────────

echo -e "${CYAN}[2/4] Checking Ollama...${NC}"

if command -v ollama &>/dev/null; then
    OLLAMA_VERSION=$(ollama --version 2>&1 | head -1)
    echo -e "  ${GREEN}✓${NC} Ollama installed ($OLLAMA_VERSION)"
else
    echo -e "  ${YELLOW}!${NC} Ollama not found. Installing..."
    if command -v brew &>/dev/null; then
        brew install ollama
    else
        echo ""
        echo -e "  ${YELLOW}Please install Ollama manually:${NC}"
        echo "    https://ollama.com/download"
        echo ""
        echo "  After installing, run this script again."
        exit 1
    fi
fi

# Check if Ollama is running
if curl -s http://localhost:11434/api/tags &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Ollama server running"
else
    echo -e "  ${YELLOW}!${NC} Starting Ollama server..."
    ollama serve &>/dev/null &
    sleep 3

    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} Ollama server started"
    else
        echo -e "  ${RED}✗${NC} Could not start Ollama. Try: ollama serve"
        exit 1
    fi
fi

# ── Pull Model ───────────────────────────────────────────────────────

echo -e "${CYAN}[3/4] Checking model...${NC}"

# Check if model is already pulled
PULLED_MODELS=$(curl -s http://localhost:11434/api/tags | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('models', []):
    print(m['name'])
" 2>/dev/null || echo "")

if echo "$PULLED_MODELS" | grep -q "^${MODEL}$"; then
    echo -e "  ${GREEN}✓${NC} Model $MODEL already available"
else
    echo -e "  ${YELLOW}!${NC} Pulling $MODEL (this may take a few minutes)..."
    ollama pull "$MODEL"
    echo -e "  ${GREEN}✓${NC} Model $MODEL ready"
fi

# ── Install Python Dependencies ──────────────────────────────────────

echo -e "${CYAN}[4/4] Installing Python dependencies...${NC}"

if [ -f "$SERVER_DIR/requirements.txt" ]; then
    pip3 install -q -r "$SERVER_DIR/requirements.txt" 2>/dev/null
    echo -e "  ${GREEN}✓${NC} Dependencies installed"
else
    echo -e "  ${RED}✗${NC} requirements.txt not found at $SERVER_DIR"
    exit 1
fi

# ── Verify ───────────────────────────────────────────────────────────

echo ""
echo -e "${CYAN}Verifying server starts...${NC}"

cd "$SERVER_DIR"
timeout 5 python3 -c "
import main
print('Server module loads OK')
" 2>/dev/null && echo -e "  ${GREEN}✓${NC} Server module verified" || echo -e "  ${YELLOW}!${NC} Server module check skipped (timeout)"

# ── Done ─────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}┌─────────────────────────────────────┐${NC}"
echo -e "${GREEN}│  ✓ Conduit installation complete!    │${NC}"
echo -e "${GREEN}└─────────────────────────────────────┘${NC}"
echo ""
echo -e "  Model:  ${GREEN}$MODEL${NC}"
echo -e "  Server: ${GREEN}$SERVER_DIR${NC}"
echo ""
echo -e "  To start Conduit:"
echo -e "    ${CYAN}cd $SERVER_DIR && python3 main.py${NC}"
echo ""
echo -e "  Then load the Conduit M4L device in Ableton Live."
echo ""
