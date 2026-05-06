#!/bin/bash
echo "Stopping servers..."

# Find and kill by looking for Python processes running server.py in these directories
# Or simply kill by ports: 8000 and 8001
echo "Killing processes listening on port 8000 and 8001..."

pids_8000=$(lsof -t -i:8000 2>/dev/null)
if [ -n "$pids_8000" ]; then
    echo "Killing genie processes on 8000: $pids_8000"
    kill $pids_8000
    sleep 1
    kill -9 $pids_8000 2>/dev/null
else
    echo "No genie processes listening on 8000."
fi

pids_8001=$(lsof -t -i:8001 2>/dev/null)
if [ -n "$pids_8001" ]; then
    echo "Killing voice-satellite processes on 8001: $pids_8001"
    kill $pids_8001
    sleep 1
    kill -9 $pids_8001 2>/dev/null
else
    echo "No voice-satellite processes listening on 8001."
fi

# Fallback: kill any python process named server.py in our specific directories
# using awk/grep on /proc
for pid in $(pgrep -f "python server.py"); do
    cwd=$(pwdx /proc/$pid/cwd 2>/dev/null | grep -oE "voice-satellite.*" || true)
    if [ -n "$cwd" ]; then
        echo "Killing python server.py process (PID $pid) in $cwd"
        kill -9 $pid 2>/dev/null
    fi
done

echo "Done."
