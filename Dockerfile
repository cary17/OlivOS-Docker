FROM debian:12-slim

ARG OLIVOS_VERSION
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 下载并解压 OlivOS 源码，重命名为 OlivOS
RUN VER="${OLIVOS_VERSION#v}" && \
    curl -fsSL "https://github.com/OlivOS-Team/OlivOS/archive/refs/tags/${OLIVOS_VERSION}.tar.gz" \
        -o src.tar.gz && \
    tar -xzf src.tar.gz && \
    mv "OlivOS-${VER}" OlivOS && \
    rm src.tar.gz

# 安装 Python 依赖
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# 下载预装插件
COPY opk.txt download_plugins.py ./
RUN python3 download_plugins.py && rm download_plugins.py opk.txt

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
