# ==================== 构建阶段 ====================
FROM python:3.11-slim AS builder

ARG OLIVOS_RAW_VERSION

# 安装编译依赖（构建后删除）
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
        libffi-dev libssl-dev \
        libxml2-dev libxslt1-dev \
        libjpeg-dev zlib1g-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 下载源码
COPY download_source.sh .
RUN chmod +x download_source.sh && \
    ./download_source.sh "${OLIVOS_RAW_VERSION}" && \
    rm download_source.sh

# 安装 Python 依赖（只安装必要包）
COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile \
        --upgrade pip setuptools wheel && \
    pip install --no-cache-dir --no-compile -r requirements.txt

# 下载插件
COPY opk.txt download_plugins.py ./
RUN python download_plugins.py && \
    rm download_plugins.py opk.txt

# 激进清理：删除所有缓存、测试文件、文档
RUN find /usr/local/lib/python3.11 -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11 -type d -name 'tests' -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11 -type d -name 'test' -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11 -type f -name '*.pyc' -delete 2>/dev/null || true && \
    find /usr/local/lib/python3.11 -type f -name '*.pyo' -delete 2>/dev/null || true && \
    find /usr/local/lib/python3.11 -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    rm -rf /root/.cache/pip /tmp/*

# ==================== 运行阶段 ====================
FROM python:3.11-slim

# 只安装最必要的运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

# 只复制 site-packages（不复制 /usr/local/bin）
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# 复制源码
COPY --from=builder /app/OlivOS /app/OlivOS

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# 入口点
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && \
    chmod +x /app/OlivOS/main.py

ENTRYPOINT ["/entrypoint.sh"]
