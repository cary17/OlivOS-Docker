#!/bin/sh
set -e
VER="${1#v}"
curl -fsSL "https://github.com/OlivOS-Team/OlivOS/archive/refs/tags/${1}.tar.gz" -o src.tar.gz
tar -xzf src.tar.gz
mv "OlivOS-${VER}" OlivOS
rm src.tar.gz
