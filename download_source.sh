#!/bin/sh
set -e

# 接收原始版本号
RAW_VERSION="$1"

if [ -z "$RAW_VERSION" ] || ! printf '%s' "$RAW_VERSION" | grep -Eq '^[0-9A-Za-z][0-9A-Za-z._-]*$'; then
    echo "Invalid OlivOS version: $RAW_VERSION" >&2
    exit 2
fi

# 直接使用原始版本号构造 GitHub 下载 URL（项目 tag 不带 v）
TAG="$RAW_VERSION"

echo "Downloading OlivOS source for tag: $TAG"

# 下载并解压
curl -fsSL \
    --retry 3 \
    --retry-delay 2 \
    --retry-all-errors \
    --connect-timeout 15 \
    --max-time 180 \
    "https://github.com/OlivOS-Team/OlivOS/archive/refs/tags/${TAG}.tar.gz" \
    -o src.tar.gz
tar -xzf src.tar.gz
SOURCE_DIR="OlivOS-${RAW_VERSION}"
if [ ! -f "$SOURCE_DIR/main.py" ] || [ ! -f "$SOURCE_DIR/setup.py" ]; then
    echo "Downloaded archive does not contain the expected OlivOS source tree" >&2
    exit 3
fi
mv "$SOURCE_DIR" OlivOS
rm src.tar.gz

echo "Successfully extracted to OlivOS/"
