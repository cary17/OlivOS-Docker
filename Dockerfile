# ==================== 阶段一：构建阶段 (Builder) ====================
FROM python:3.11-slim AS builder

ARG OLIVOS_RAW_VERSION
ARG DEBIAN_FRONTEND=noninteractive

# 安装编译依赖（python:3.11-slim 已包含 python3-dev）
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

# 创建虚拟环境并安装依赖
COPY requirements.txt .
RUN python -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel && \
    /app/venv/bin/pip install --no-cache-dir -r requirements.txt

# 下载插件
COPY opk.txt download_plugins.py ./
RUN /app/venv/bin/python download_plugins.py && rm download_plugins.py opk.txt

# 复制本地 OPK 文件（如果有）
COPY opk/ ./opk_local/ 2>/dev/null || true
RUN if [ -d ./opk_local ]; then \
        find ./opk_local -name '*.opk' -exec cp {} OlivOS/plugin/app/ \; && \
        rm -rf ./opk_local; \
    fi

# 清理缓存
RUN rm -rf /root/.cache/pip && \
    find /app/venv -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# ==================== 阶段二：最终运行阶段 ====================
FROM python:3.11-slim

ARG DEBIAN_FRONTEND=noninteractive

# 运行阶段只需要基础运行时
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 从构建阶段复制虚拟环境和源码
COPY --from=builder /app/venv /app/venv
COPY --from=builder /app/OlivOS /app/OlivOS

# 设置环境变量，优先使用虚拟环境
ENV PATH="/app/venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/venv"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 验证关键模块是否安装成功
RUN /app/venv/bin/python -c "import psutil; print(f'✓ psutil {psutil.__version__} installed successfully')"

# 复制并设置入口点脚本
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
