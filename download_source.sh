#!/bin/sh
set -e

# 接收原始版本号（例如 0.11.81）
RAW_VERSION="$1"

# 直接使用原始版本号构造 GitHub 下载 URL（项目 tag 不带 v）
TAG="$RAW_VERSION"

echo "Downloading OlivOS source for tag: $TAG"

# 下载并解压
curl -fsSL "https://github.com/OlivOS-Team/OlivOS/archive/refs/tags/${TAG}.tar.gz" -o src.tar.gz
tar -xzf src.tar.gz
mv "OlivOS-${RAW_VERSION}" OlivOS
rm src.tar.gz

# 复制 pyproject.toml 到根目录（方便 Dockerfile 引用）
if [ -f "OlivOS/pyproject.toml" ]; then
    cp OlivOS/pyproject.toml ./
    echo "Copied pyproject.toml to root directory"
fi

# 复制 requirements.txt 到根目录（如果存在）
if [ -f "OlivOS/requirements.txt" ]; then
    cp OlivOS/requirements.txt ./
    echo "Copied requirements.txt to root directory"
fi

echo "Successfully extracted to OlivOS/"
