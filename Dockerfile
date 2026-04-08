FROM debian:12-slim AS builder

ARG OLIVOS_VERSION
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev \
        gcc libffi-dev libssl-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN VER="${OLIVOS_VERSION#v}" && \
    curl -fsSL "https://github.com/OlivOS-Team/OlivOS/archive/refs/tags/${OLIVOS_VERSION}.tar.gz" \
        -o src.tar.gz && \
    tar -xzf src.tar.gz && \
    mv "OlivOS-${VER}" OlivOS && \
    rm src.tar.gz

# 打印 requirements.txt 内容，确认文件存在且内容正确
RUN echo "=== requirements.txt ===" && cat OlivOS/requirements.txt

RUN python3 -m venv /app/venv

# 升级 pip（单独一步）
RUN /app/venv/bin/pip install --no-cache-dir -v --upgrade pip

# 安装依赖（单独一步，-v 输出完整错误）
RUN /app/venv/bin/pip install --no-cache-dir -v -r OlivOS/requirements.txt

COPY opk.txt download_plugins.py ./
RUN /app/venv/bin/python download_plugins.py && rm download_plugins.py opk.txt

RUN find /app/venv -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/venv -type f -name '*.pyi' -delete 2>/dev/null || true && \
    find /app/venv -type d -name 'tests' -exec rm -rf {} + 2>/dev/null || true

FROM debian:12-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 ca-certificates \
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
