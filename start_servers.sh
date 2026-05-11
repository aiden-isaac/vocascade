#!/bin/bash
set -euo pipefail

# Resolve the project root regardless of where this script is called from.
PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load .env (safe parser — never executes values as shell) ──────────────────
if [ -f "$PROJ_DIR/.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        [[ "$line" =~ ^[[:space:]]*$ ]] && continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            export "$line"
        fi
    done < "$PROJ_DIR/.env"
fi

# ── Genie TTS Server (port 8000) ─────────────────────────────────────────────
#
# The Genie TTS launcher is always genie/server.py from this repo.
# The virtualenv lives separately (usually inside genie_model_reference/).
#
# GENIE_VENV_DIR: directory that contains the .venv/ or venv/ for genie_tts.
#   Auto-detected in order:
#     1. $GENIE_VENV_DIR  (explicit override via .env or environment)
#     2. $PROJ_DIR/genie_model_reference/   (sibling/subdir with full install)
#     3. $PROJ_DIR/genie/                   (fallback, dev/minimal)
#
GENIE_SERVER="$PROJ_DIR/genie/server.py"
if [ ! -f "$GENIE_SERVER" ]; then
    echo "ERROR: Genie TTS launcher not found: $GENIE_SERVER"
    exit 1
fi

if [ -z "${GENIE_VENV_DIR:-}" ]; then
    if [ -d "$PROJ_DIR/genie_model_reference" ]; then
        GENIE_VENV_DIR="$PROJ_DIR/genie_model_reference"
    else
        GENIE_VENV_DIR="$PROJ_DIR/genie"
    fi
fi

echo "Starting Genie TTS server (port 8000)..."
echo "  Script : $GENIE_SERVER"
echo "  Venv   : $GENIE_VENV_DIR"

# genie_tts resolves GenieData/ at import time. Point it at the absolute path
# so it works regardless of working directory.
export GENIE_DATA_DIR="${GENIE_DATA_DIR:-$GENIE_VENV_DIR/GenieData}"

if [ ! -d "$GENIE_DATA_DIR" ]; then
    echo "ERROR: GenieData not found at $GENIE_DATA_DIR"
    echo "Set GENIE_DATA_DIR in .env to the directory containing speaker_encoder.onnx etc."
    exit 1
fi

echo "  Data   : $GENIE_DATA_DIR"

if [ -d "$GENIE_VENV_DIR/.venv/bin" ]; then
    source "$GENIE_VENV_DIR/.venv/bin/activate"
    python "$GENIE_SERVER" &
    GENIE_PID=$!
    deactivate
elif [ -d "$GENIE_VENV_DIR/venv/bin" ]; then
    source "$GENIE_VENV_DIR/venv/bin/activate"
    python "$GENIE_SERVER" &
    GENIE_PID=$!
    deactivate
else
    echo "ERROR: No .venv/ or venv/ found in $GENIE_VENV_DIR"
    echo "Set GENIE_VENV_DIR in .env to the directory containing the genie_tts venv."
    exit 1
fi

sleep 2

# ── Voice Satellite Server (port 8001) ────────────────────────────────────────
echo "Starting Voice Satellite server (port 8001)..."
cd "$PROJ_DIR"
source venv/bin/activate
python server.py &
MAIN_PID=$!

echo ""
echo "Both servers started."
echo "  Genie TTS PID : $GENIE_PID"
echo "  Voice Sat PID : $MAIN_PID"
echo ""
echo "Stop with: ./stop_servers.sh"
