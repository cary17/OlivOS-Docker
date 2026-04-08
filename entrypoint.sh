#!/bin/sh
set -e

if [ -n "$EXTRA_PACKAGES" ]; then
    pip install --no-cache-dir $EXTRA_PACKAGES
fi

cd /app/OlivOS
exec python main.py "$@"
