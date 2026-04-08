#!/bin/bash
set -e

# 支持通过 EXTRA_PACKAGES 环境变量在启动时安装额外依赖
if [ -n "$EXTRA_PACKAGES" ]; then
    pip3 install --no-cache-dir --break-system-packages $EXTRA_PACKAGES
fi

cd /app/OlivOS
exec python3 main.py "$@"
