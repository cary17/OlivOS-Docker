FROM debian:12-slim

ARG OLIVOS_VERSION
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev \
        gcc curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 下载并解压 OlivOS 源码，重命名为 OlivOS
RUN VER="${OLIVOS_VERSION#v}" && \
    curl -fsSL "https://github.com/OlivOS-Team/OlivOS/archive/refs/tags/${OLIVOS_VERSION}.tar.gz" \
        -o src.tar.gz && \
    tar -xzf src.tar.gz && \
    mv "OlivOS-${VER}" OlivOS && \
    rm src.tar.gz

# 安装依赖：优先安装 OlivOS 自带的，再叠加本仓库的
COPY requirements.txt ./requirements.extra.txt
RUN pip3 install --no-cache-dir --break-system-packages \
        $([ -f OlivOS/requirements.txt ] && echo "-r OlivOS/requirements.txt") \
        -r requirements.extra.txt && \
    rm requirements.extra.txt

# 下载预装插件
COPY opk.txt download_plugins.py ./
RUN python3 download_plugins.py && rm download_plugins.py opk.txt

# 清理编译工具，减小镜像体积
RUN apt-get purge -y --auto-remove gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
