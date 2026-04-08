# ==================== 阶段一：构建阶段 (Builder) ====================
FROM debian:12-slim AS builder

# 修改构建参数名，使其语义更清晰
ARG OLIVOS_RAW_VERSION
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

# 复制并执行下载脚本（传递原始版本号）
COPY download_source.sh .
RUN chmod +x download_source.sh && \
    ./download_source.sh "${OLIVOS_RAW_VERSION}" && \
    rm download_source.sh

# ... (后续安装依赖、插件等步骤与之前完全相同，此处省略以保持简洁) ...

# 确保后续步骤也使用正确的环境
COPY requirements.txt .
RUN python3 -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel
RUN /app/venv/bin/pip install --no-cache-dir -r requirements.txt

# 下载插件等操作...
COPY opk.txt download_plugins.py ./
RUN /app/venv/bin/python download_plugins.py && rm download_plugins.py opk.txt

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
