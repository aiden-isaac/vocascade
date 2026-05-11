#!/bin/bash
set -euo pipefail

# Resolve the project root regardless of where this script is called from.
PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Genie TTS Server (port 8000) ─────────────────────────────────────────────
#
# GENIE_DIR can be overridden in .env or the environment. It should point to a
# directory containing server.py and a .venv/ virtual environment.
#
# Default: <project>/genie/   (the minimal launcher committed to this repo)
# Override example in .env:
#   GENIE_DIR=/path/to/genie_model_reference
#
if [ -f "$PROJ_DIR/.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip blank lines and comments
        [[ "$line" =~ ^[[:space:]]*$ ]] && continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        # Only export lines that look like KEY=VALUE
        if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            export "$line"
        fi
    done < "$PROJ_DIR/.env"
fi

GENIE_DIR="${GENIE_DIR:-$PROJ_DIR/genie}"

if [ ! -d "$GENIE_DIR" ]; then
    echo "ERROR: Genie TTS directory not found: $GENIE_DIR"
    echo "Set GENIE_DIR in .env or the environment to the correct path."
    exit 1
fi

if [ ! -f "$GENIE_DIR/server.py" ]; then
    echo "ERROR: No server.py found in $GENIE_DIR"
    exit 1
fi

echo "Starting Genie TTS server (port 8000) from $GENIE_DIR ..."
cd "$GENIE_DIR"

if [ -d ".venv/bin" ]; then
    source .venv/bin/activate
    python server.py &
    GENIE_PID=$!
    deactivate
elif [ -d "venv/bin" ]; then
    source venv/bin/activate
    python server.py &
    GENIE_PID=$!
    deactivate
else
    echo "WARNING: No .venv/ or venv/ found in $GENIE_DIR — using system python"
    python3 server.py &
    GENIE_PID=$!
fi

sleep 2

# ── Voice Satellite Server (port 8001) ────────────────────────────────────────
echo "Starting Voice Satellite server (port 8001) from $PROJ_DIR ..."
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
