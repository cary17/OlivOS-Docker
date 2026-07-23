#!/bin/sh
set -e

cleanup() {
    if [ -n "${MAIN_PID:-}" ]; then
        kill -TERM "$MAIN_PID" 2>/dev/null || true
        wait "$MAIN_PID" 2>/dev/null || true
    fi
}

trap cleanup TERM INT

cd /app/OlivOS
python main.py "$@" &
MAIN_PID=$!
STATUS=0
wait "$MAIN_PID" || STATUS=$?
exit "$STATUS"
