FROM debian:12-slim AS builder

ARG OLIVOS_VERSION
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev \
        gcc libffi-dev libssl-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 下载解压源码并重命名
RUN VER="${OLIVOS_VERSION#v}" && \
    curl -fsSL "https://github.com/OlivOS-Team/OlivOS/archive/refs/tags/${OLIVOS_VERSION}.tar.gz" \
        -o src.tar.gz && \
    tar -xzf src.tar.gz && \
    mv "OlivOS-${VER}" OlivOS && \
    rm src.tar.gz

# 安装依赖到独立目录
RUN pip3 install --no-cache-dir --break-system-packages \
        --target=/app/deps \
        -r OlivOS/requirements.txt

# 下载预装插件
COPY opk.txt download_plugins.py ./
RUN python3 download_plugins.py && rm download_plugins.py opk.txt

# 清理 pyc 和无用文件
RUN find /app/deps -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/deps -type f -name '*.pyi' -delete && \
    find /app/deps -type d -name 'tests' -exec rm -rf {} + 2>/dev/null || true

# ---- 最终镜像 ----
FROM debian:12-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

COPY --from=builder /app/OlivOS ./OlivOS
COPY --from=builder /app/deps ./deps

ENV PYTHONPATH=/app/deps
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
