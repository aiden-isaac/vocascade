#!/bin/bash
set -euo pipefail

echo "Stopping Voice Satellite servers..."

# Kill by port (primary method)
for port in 8000 8001; do
    pids=$(lsof -t -i:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "  Killing processes on port $port: $pids"
        kill $pids 2>/dev/null || true
        sleep 1
        kill -9 $pids 2>/dev/null || true
    else
        echo "  No process on port $port."
    fi
done

# Fallback: kill any python server.py processes in our directories
for pid in $(pgrep -f "python server.py" 2>/dev/null || true); do
    cwd=$(readlink -f /proc/$pid/cwd 2>/dev/null || true)
    if echo "$cwd" | grep -q "voice-satellite"; then
        echo "  Killing stray python server.py (PID $pid) in $cwd"
        kill -9 "$pid" 2>/dev/null || true
    fi
done

echo "Done."
