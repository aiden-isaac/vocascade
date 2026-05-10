#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- 1. Genie TTS Server ---
echo "Starting Genie TTS server (port 8000)..."
cd "$BASE_DIR/genie_model_reference"
source .venv/bin/activate
python server.py &
GENIE_PID=$!
deactivate
sleep 2

# --- 2. Voice Satellite Server ---
echo "Starting Voice Satellite server (port 8001)..."
cd "$BASE_DIR"
source venv/bin/activate
python server.py &
MAIN_PID=$!

echo ""
echo "Both servers started."
echo "  Genie TTS PID : $GENIE_PID"
echo "  Voice Sat PID : $MAIN_PID"
echo ""
echo "Stop with: ./stop_servers.sh"
