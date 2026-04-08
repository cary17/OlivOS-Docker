#!/bin/bash
set -e

if [ -n "$EXTRA_PACKAGES" ]; then
    pip3 install --no-cache-dir --break-system-packages \
        --target=/app/deps $EXTRA_PACKAGES
fi

cd /app/OlivOS
exec python3 main.py "$@"
