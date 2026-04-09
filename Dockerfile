# ==================== 阶段一：构建阶段 (Builder) ====================
FROM python:3.11-slim AS builder

ARG OLIVOS_RAW_VERSION
ARG DEBIAN_FRONTEND=noninteractive

# 安装编译依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
        libffi-dev libssl-dev \
        libxml2-dev libxslt1-dev \
        libjpeg-dev zlib1g-dev \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 下载 OlivOS 源码
COPY download_source.sh .
RUN chmod +x download_source.sh && \
    ./download_source.sh "${OLIVOS_RAW_VERSION}" && \
    rm download_source.sh

# 直接安装到系统 Python（不需要虚拟环境）
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# 下载插件
COPY opk.txt download_plugins.py ./
RUN python download_plugins.py && rm download_plugins.py opk.txt

# 清理缓存
RUN rm -rf /root/.cache/pip && \
    find /usr/local/lib/python3.11 -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# ==================== 阶段二：最终运行阶段 ====================
FROM python:3.11-slim

ARG DEBIAN_FRONTEND=noninteractive

# 运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 从构建阶段复制已安装的 Python 包和源码
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/OlivOS /app/OlivOS

# 设置环境变量（不需要 VIRTUAL_ENV 和 PATH 修改）
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 入口点
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
