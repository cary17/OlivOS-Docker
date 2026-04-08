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

echo "Successfully extracted to OlivOS/"
