#!/usr/bin/env bash
#
# run_voice_stack.sh — Bring up the local voice stack on this machine.
#
# Starts, in order:
#   1. Genie TTS server   (genie_tts_env, 127.0.0.1:8000)
#   2. vocascade app      (.venv, host:port from .env, PORT defaults to 8000)
#
# Both servers' logs are streamed to this terminal, each line prefixed with a
# colour-coded [GENIE] / [ADAPTER] label so you can tell them apart. Press
# Ctrl-C once to shut both down cleanly.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- interpreters -----------------------------------------------------------
GENIE_PY="$REPO_ROOT/genie_tts_env/bin/python"   # Genie TTS's own venv
ADAPTER_PY="$REPO_ROOT/.venv/bin/python"         # vocascade's venv

GENIE_HOST="127.0.0.1"
GENIE_PORT="8000"

# --- colours ----------------------------------------------------------------
C_GENIE=$'\033[36m'    # cyan
C_ADAPTER=$'\033[35m'  # magenta
C_SYS=$'\033[32m'      # green
C_ERR=$'\033[31m'      # red
C_RST=$'\033[0m'

sys()  { printf '%s[run]%s %s\n'  "$C_SYS" "$C_RST" "$*"; }
err()  { printf '%s[run]%s %s\n'  "$C_ERR" "$C_RST" "$*" >&2; }

# Prefix every line of a stream with a coloured [LABEL].
prefix() {
    local label="$1" color="$2" line
    while IFS= read -r line; do
        printf '%s[%s]%s %s\n' "$color" "$label" "$C_RST" "$line"
    done
}

# --- sanity checks ----------------------------------------------------------
[[ -x "$GENIE_PY"   ]] || { err "Genie interpreter not found: $GENIE_PY (run scripts/setup_genie.sh)"; exit 1; }
[[ -x "$ADAPTER_PY" ]] || { err "Adapter interpreter not found: $ADAPTER_PY (create the .venv)"; exit 1; }

PIDS=()

cleanup() {
    trap '' INT TERM        # ignore further signals while we tear down
    echo
    sys "shutting down..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    wait 2>/dev/null
    sys "all servers stopped."
    exit 0
}
trap cleanup INT TERM

# --- 1. Genie TTS server ----------------------------------------------------
sys "starting Genie TTS server on ${GENIE_HOST}:${GENIE_PORT} ..."
"$GENIE_PY" -u -c "import genie_tts; genie_tts.start_server(host='${GENIE_HOST}', port=${GENIE_PORT})" \
    > >(prefix "GENIE  " "$C_GENIE") 2>&1 &
GENIE_PID=$!
PIDS+=("$GENIE_PID")

# Wait until Genie is accepting connections before launching the adapter.
sys "waiting for Genie TTS to come up ..."
for _ in $(seq 1 60); do
    if (exec 3<>"/dev/tcp/${GENIE_HOST}/${GENIE_PORT}") 2>/dev/null; then
        exec 3>&- 3<&-
        sys "Genie TTS is up."
        break
    fi
    if ! kill -0 "$GENIE_PID" 2>/dev/null; then
        err "Genie TTS exited before it was ready — see [GENIE] logs above."
        cleanup
    fi
    sleep 1
done

# --- 2. vocascade app ---------------------------------------------------
sys "starting vocascade ..."
"$ADAPTER_PY" -u -m vocascade \
    > >(prefix "ADAPTER" "$C_ADAPTER") 2>&1 &
ADAPTER_PID=$!
PIDS+=("$ADAPTER_PID")

sys "both servers running. Press Ctrl-C to stop."

# If either server dies on its own, tear the whole stack down.
wait -n "$GENIE_PID" "$ADAPTER_PID"
err "a server exited unexpectedly — see logs above."
cleanup
