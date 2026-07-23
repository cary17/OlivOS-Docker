#!/bin/sh
set -e

cleanup() {
    if [ -n "${MAIN_PID:-}" ]; then
        kill -TERM "$MAIN_PID" 2>/dev/null || true
        wait "$MAIN_PID" 2>/dev/null || true
    fi
}

trap cleanup TERM INT

# 使用 python -m pip 避免依赖 /usr/local/bin
if [ -n "${EXTRA_PACKAGES:-}" ]; then
    echo "Installing: $EXTRA_PACKAGES"
    # shellcheck disable=SC2086
    python -m pip install --no-cache-dir $EXTRA_PACKAGES
fi

cd /app/OlivOS
python main.py "$@" &
MAIN_PID=$!
STATUS=0
wait "$MAIN_PID" || STATUS=$?
exit "$STATUS"
