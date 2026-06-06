#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# Micround v2 — Setup Script
#
# Run on BOTH the Desktop Mac (server) and the MacBook (client).
# This script installs Homebrew, Python 3, Git, creates a virtual environment,
# and installs all Python dependencies.
#
# Usage:
#   cd ~/codingprojects/micround
#   bash setup.sh
# ──────────────────────────────────────────────────────────────────────────────

set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
CYAN="\033[0;36m"
RESET="\033[0m"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║    🔬  Micround v2 — Setup                      ║${RESET}"
echo -e "${BOLD}║    Microscope Background Streamer                ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. Homebrew ───────────────────────────────────────────────────────────────
echo -e "${CYAN}[1/5]${RESET} Checking for Homebrew…"
if ! command -v brew &> /dev/null; then
    echo "  Homebrew not found. Installing…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add to PATH for Apple Silicon Macs
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
else
    echo -e "  ${GREEN}✅  Homebrew $(brew --version | head -1)${RESET}"
fi

# ── 2. Python 3 ──────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[2/5]${RESET} Checking for Python 3…"
if ! command -v python3 &> /dev/null; then
    echo "  Python 3 not found. Installing via Homebrew…"
    brew install python
else
    echo -e "  ${GREEN}✅  $(python3 --version)${RESET}"
fi

# ── 3. Git ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[3/5]${RESET} Checking for Git…"
if ! command -v git &> /dev/null; then
    echo "  Git not found. Installing via Homebrew…"
    brew install git
else
    echo -e "  ${GREEN}✅  $(git --version)${RESET}"
fi

# ── 4. Virtual Environment ───────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[4/5]${RESET} Setting up Python virtual environment…"
if [ ! -d "venv" ]; then
    echo "  Creating virtual environment…"
    python3 -m venv venv
    echo -e "  ${GREEN}✅  Virtual environment created${RESET}"
else
    echo -e "  ${GREEN}✅  Virtual environment already exists${RESET}"
fi

# ── 5. Python Packages ───────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[5/5]${RESET} Installing Python dependencies…"
source venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo -e "  ${GREEN}✅  All Python packages installed${RESET}"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║  ${GREEN}✅  Setup Complete!${RESET}${BOLD}                               ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "${YELLOW}If this is the SERVER Mac (microscope), you also need ngrok:${RESET}"
echo "  1. Install:    brew install ngrok"
echo "  2. Sign up:    https://dashboard.ngrok.com/signup  (free)"
echo "  3. Auth:       ngrok config add-authtoken YOUR_TOKEN"
echo ""
echo -e "${BOLD}To run the SERVER (Desktop Mac):${RESET}"
echo "  cd \"$(basename "$SCRIPT_DIR")\""
echo "  source venv/bin/activate"
echo "  python3 server.py"
echo ""
echo -e "${BOLD}To run the CLIENT (MacBook):${RESET}"
echo "  cd \"$(basename "$SCRIPT_DIR")\""
echo "  source venv/bin/activate"
echo "  python3 client.py"
echo ""
echo "  The client will auto-discover the server on the same Wi-Fi."
echo "  For remote use, copy the URL from the server menu bar and run:"
echo "  python3 client.py wss://YOUR_NGROK_URL"
echo ""
