#!/bin/sh
set -e

# 接收版本号（可能带 v 也可能不带）
INPUT_VERSION="$1"

# 确保用于下载的 tag 带 v 前缀
if echo "$INPUT_VERSION" | grep -q '^v'; then
    TAG="$INPUT_VERSION"
else
    TAG="v$INPUT_VERSION"
fi

# 提取不带 v 的版本号用于目录名
VERSION="${TAG#v}"

echo "Downloading OlivOS version: $TAG"

# 下载并解压
curl -fsSL "https://github.com/OlivOS-Team/OlivOS/archive/refs/tags/${TAG}.tar.gz" -o src.tar.gz
tar -xzf src.tar.gz
mv "OlivOS-${VERSION}" OlivOS
rm src.tar.gz

echo "Successfully extracted to OlivOS/"
