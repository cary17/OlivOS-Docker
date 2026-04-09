#!/bin/sh
set -e

cleanup() {
    kill -TERM "$MAIN_PID" 2>/dev/null
    wait "$MAIN_PID" 2>/dev/null
    exit 0
}

trap cleanup TERM INT

# 使用 python -m pip 避免依赖 /usr/local/bin
if [ -n "$EXTRA_PACKAGES" ]; then
    echo "Installing: $EXTRA_PACKAGES"
    python -m pip install --no-cache-dir $EXTRA_PACKAGES
fi

cd /app/OlivOS
python main.py "$@" &
MAIN_PID=$!
wait $MAIN_PID
