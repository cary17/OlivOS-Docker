# ==================== 阶段一：构建阶段 (Builder) ====================
FROM debian:12-slim AS builder

ARG OLIVOS_VERSION
ARG DEBIAN_FRONTEND=noninteractive

# 安装编译依赖和工具
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev \
        gcc g++ libffi-dev libssl-dev libev-dev \
        libxml2-dev libxslt1-dev \
        libjpeg-dev zlib1g-dev \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制并执行下载脚本
COPY download_source.sh .
RUN chmod +x download_source.sh && \
    ./download_source.sh "${OLIVOS_VERSION}" && \
    rm download_source.sh

# 复制 requirements.txt 并安装 Python 依赖
COPY requirements.txt .

RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel

RUN /app/venv/bin/pip install --no-cache-dir -r requirements.txt

# 下载插件
COPY opk.txt download_plugins.py ./
RUN /app/venv/bin/python download_plugins.py && rm download_plugins.py opk.txt

# 复制本地 OPK 插件并安装
COPY opk/ ./opk_local/
RUN find ./opk_local -name '*.opk' -exec cp {} OlivOS/plugin/app/ \; && \
    rm -rf ./opk_local

# 清理虚拟环境中的缓存和测试文件，减小体积
RUN find /app/venv -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/venv -type f -name '*.pyi' -delete 2>/dev/null || true && \
    find /app/venv -type d -name 'tests' -exec rm -rf {} + 2>/dev/null || true

# ==================== 阶段二：最终运行阶段 ====================
FROM debian:12-slim

ARG DEBIAN_FRONTEND=noninteractive

# 仅安装运行时必需的系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 libev4 ca-certificates \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

# 从构建阶段复制虚拟环境和 OlivOS 源码
COPY --from=builder /app/venv ./venv
COPY --from=builder /app/OlivOS ./OlivOS

# 设置环境变量
ENV PATH="/app/venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/venv"
ENV PYTHONUNBUFFERED="1"
ENV PYTHONDONTWRITEBYTECODE="1"

# 复制并设置入口点脚本
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
