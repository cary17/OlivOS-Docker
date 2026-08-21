# ==================== 构建阶段 ====================
FROM python:3.11-slim AS builder

ARG OLIVOS_RAW_VERSION
# full, core, dev
ARG BUILD_TYPE=full

# 安装编译依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
        libffi-dev libssl-dev \
        libxml2-dev libxslt1-dev \
        libjpeg-dev zlib1g-dev \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# IPv4 优先（RFC 6724 地址选择策略）：避免阵发性 IPv6 黑洞拖慢源码与依赖下载
RUN printf 'precedence ::ffff:0:0/96  100\n' >> /etc/gai.conf

WORKDIR /app

# 下载源码
COPY download_source.sh .
RUN chmod +x download_source.sh && \
    ./download_source.sh "${OLIVOS_RAW_VERSION}" && \
    rm download_source.sh

# 切换到源码目录（pyproject.toml 在这里）
WORKDIR /app/OlivOS

# 升级 pip 和安装构建工具
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# ============================================
# 安装 Python 依赖（根据 BUILD_TYPE）
# ============================================

# 安装核心依赖
RUN echo "=== Installing core dependencies ===" && \
    pip install --no-cache-dir .

# 安装开发工具 dev（仅 dev 版本）
RUN if [ "$BUILD_TYPE" = "dev" ]; then \
        echo "=== Installing dev tools ===" && \
        pip install --no-cache-dir .[dev]; \
    fi

# 回到上级目录
WORKDIR /app

COPY opk.txt download_plugins.py ./
COPY opk/ ./opk_local/

# 下载 OPK 插件（仅 full 版本）
RUN if [ "$BUILD_TYPE" = "full" ]; then \
        echo "=== Downloading OPK plugins ===" && \
        python download_plugins.py && \
        rm download_plugins.py opk.txt && \
        if [ -d ./opk_local ]; then \
            find ./opk_local -name '*.opk' -exec cp {} OlivOS/plugin/app/ \; ; \
        fi; \
        rm -rf ./opk_local; \
    else \
        rm -f download_plugins.py opk.txt && \
        rm -rf ./opk_local; \
    fi

# 清理不必要的文件，减小镜像体积
RUN rm -rf /root/.cache/pip && \
    # 清理 Python 字节码和构建元数据，不递归删除第三方包的数据文件
    find /usr/local/lib/python3.11 -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/OlivOS -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/OlivOS -type d -name '.git' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/OlivOS -type f -name '*.pyc' -delete 2>/dev/null || true && \
    find /app/OlivOS -type f -name '*.pyo' -delete 2>/dev/null || true

# ==================== 运行阶段 ====================
FROM python:3.11-slim

ARG BUILD_TYPE=full

# 安装运行时依赖（开发版额外安装调试工具）
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        $(if [ "$BUILD_TYPE" = "dev" ]; then echo "vim curl procps htop"; fi) \
    && rm -rf /var/lib/apt/lists/*

# IPv4 优先（RFC 6724 地址选择策略）：避免阵发性 IPv6 黑洞拖慢平台 API 请求
RUN printf 'precedence ::ffff:0:0/96  100\n' >> /etc/gai.conf

WORKDIR /app

# 复制已安装的 Python 包
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# 复制源码
COPY --from=builder /app/OlivOS /app/OlivOS

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1

# 入口点
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

STOPSIGNAL SIGTERM
ENTRYPOINT ["/entrypoint.sh"]
