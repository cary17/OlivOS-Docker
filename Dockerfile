# ==================== 构建阶段 ====================
FROM python:3.11-slim AS builder

ARG OLIVOS_RAW_VERSION
ARG BUILD_TYPE=full  # full, core, dev

# 安装编译依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
        libffi-dev libssl-dev \
        libxml2-dev libxslt1-dev \
        libjpeg-dev zlib1g-dev \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 下载源码
COPY download_source.sh .
RUN chmod +x download_source.sh && \
    ./download_source.sh "${OLIVOS_RAW_VERSION}" && \
    rm download_source.sh

# 复制依赖配置文件
COPY pyproject.toml requirements.txt ./

# 升级 pip 和安装构建工具
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# ============================================
# 安装 Python 依赖（分层安装，优化缓存）
# ============================================

# 第1层：优先使用 pyproject.toml 安装核心依赖
RUN if [ -f "pyproject.toml" ]; then \
        echo "=== Installing from pyproject.toml ===" && \
        cd OlivOS && \
        pip install --no-cache-dir .; \
    elif [ -f "requirements.txt" ]; then \
        echo "=== pyproject.toml not found, using requirements.txt ===" && \
        # 提取核心依赖（排除注释和开发工具）
        grep -v "^#" requirements.txt | grep -v "^$" | grep -v "^pytest" | grep -v "^black" | grep -v "^flake8" | grep -v "^ruff" | xargs pip install --no-cache-dir; \
    else \
        echo "ERROR: No dependency file found!" && exit 1; \
    fi

# 第2层：安装插件生态依赖 (extend) - 所有版本都需要
RUN if [ -f "pyproject.toml" ]; then \
        echo "=== Installing extend dependencies ===" && \
        cd OlivOS && \
        pip install --no-cache-dir .[extend] || \
        pip install --no-cache-dir lxml pyyaml openpyxl APScheduler==3.10.1 js2py certifi httpx "prompt-toolkit" regex rich; \
    else \
        echo "=== Installing extend dependencies from requirements.txt ===" && \
        pip install --no-cache-dir lxml pyyaml openpyxl APScheduler==3.10.1 js2py certifi httpx "prompt-toolkit" regex rich; \
    fi

# 第3层：安装开发工具 (dev) - 仅 dev 版本
RUN if [ "$BUILD_TYPE" = "dev" ]; then \
        echo "=== Installing dev tools ===" && \
        if [ -f "pyproject.toml" ]; then \
            cd OlivOS && pip install --no-cache-dir .[dev] || \
            pip install --no-cache-dir pytest black flake8 ruff; \
        else \
            pip install --no-cache-dir pytest black flake8 ruff; \
        fi \
    fi

# 第4层：下载 OPK 插件 - 仅 full 版本
RUN if [ "$BUILD_TYPE" = "full" ]; then \
        echo "=== Downloading OPK plugins ===" && \
        COPY opk.txt download_plugins.py ./ ; \
        python download_plugins.py && \
        rm download_plugins.py opk.txt; \
        # 复制本地 opk 文件夹中的插件
        COPY opk/ ./opk_local/ 2>/dev/null || true; \
        find ./opk_local -name '*.opk' -exec cp {} OlivOS/plugin/app/ \; 2>/dev/null || true; \
        rm -rf ./opk_local; \
    fi

# 清理不必要的文件，减小镜像体积
RUN rm -rf /root/.cache/pip && \
    # 清理 Python 缓存
    find /usr/local/lib/python3.11 -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11 -type d -name 'tests' -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11 -type d -name 'test' -exec rm -rf {} + 2>/dev/null || true && \
    # 清理源码中的缓存和测试文件
    find /app/OlivOS -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/OlivOS -type d -name '.git' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/OlivOS -type d -name 'tests' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/OlivOS -type d -name 'test' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/OlivOS -type f -name '*.pyc' -delete 2>/dev/null || true && \
    find /app/OlivOS -type f -name '*.pyo' -delete 2>/dev/null || true && \
    # 清理文档和示例文件
    find /app/OlivOS -type f -name '*.md' -delete 2>/dev/null || true && \
    find /app/OlivOS -type f -name '*.rst' -delete 2>/dev/null || true

# ==================== 运行阶段 ====================
FROM python:3.11-slim

ARG BUILD_TYPE=full

# 安装运行时依赖（开发版额外安装调试工具）
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        $(if [ "$BUILD_TYPE" = "dev" ]; then echo "vim curl procps htop"; fi) \
    && rm -rf /var/lib/apt/lists/*

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

ENTRYPOINT ["/entrypoint.sh"]
