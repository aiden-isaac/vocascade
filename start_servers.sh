#!/bin/bash

BASE_DIR="$HOME/voice-satellite"

# --- 1. Genie Model Reference Server ---
echo "🚀 Starting Genie Model Reference Server..."
cd "$BASE_DIR/genie_model_reference" || exit 1

# Activate virtual environment
source .venv/bin/activate

# Start server in background
python server.py &
GENIE_PID=$!

# Give it a moment to initialize
sleep 2

deactivate
cd "$BASE_DIR"

# --- 2. Main Voice Satellite Server ---
echo "🚀 Starting Voice Satellite Server..."
source venv/bin/activate

python server.py &
MAIN_PID=$!

echo ""
echo "✅ Both servers started successfully!"
echo "   Genie Model PID: $GENIE_PID"
echo "   Main Satellite PID: $MAIN_PID"
echo ""
echo "To stop them, run:"
echo "   kill $GENIE_PID $MAIN_PID"
