FROM debian:12-slim AS builder

ARG OLIVOS_VERSION
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev \
        gcc libffi-dev libssl-dev libev-dev \
        libxml2-dev libxslt1-dev \
        libjpeg-dev zlib1g-dev \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN VER="${OLIVOS_VERSION#v}" && \
    curl -fsSL "https://github.com/OlivOS-Team/OlivOS/archive/refs/tags/${OLIVOS_VERSION}.tar.gz" \
        -o src.tar.gz && \
    tar -xzf src.tar.gz && \
    mv "OlivOS-${VER}" OlivOS && \
    rm src.tar.gz

# 使用本仓库根目录的 requirements.txt 安装依赖
COPY requirements.txt .
RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /app/venv/bin/pip install --no-cache-dir -r requirements.txt

# 下载预装插件
COPY opk.txt download_plugins.py ./
RUN /app/venv/bin/python download_plugins.py && rm download_plugins.py opk.txt

# 清理无用文件
RUN find /app/venv -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/venv -type f -name '*.pyi' -delete 2>/dev/null || true && \
    find /app/venv -type d -name 'tests' -exec rm -rf {} + 2>/dev/null || true

# ---- 最终镜像 ----
FROM debian:12-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 libev4 ca-certificates \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

COPY --from=builder /app/venv ./venv
COPY --from=builder /app/OlivOS ./OlivOS

ENV PATH="/app/venv/bin:$PATH"
ENV VIRTUAL_ENV=/app/venv
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
