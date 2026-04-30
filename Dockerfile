# ==================== 构建阶段 ====================
FROM python:3.11-slim AS builder

ARG OLIVOS_RAW_VERSION
ARG BUILD_TYPE=full  # full 或 dev

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

# 复制 pyproject.toml 和 requirements.txt（作为备用）
COPY pyproject.toml* requirements.txt* ./

# 安装 Python 依赖 - 优先使用 pyproject.toml
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    if [ -f "pyproject.toml" ]; then \
        echo "Installing from pyproject.toml..."; \
        if [ "$BUILD_TYPE" = "dev" ]; then \
            echo "Dev build: installing core + extend + dev dependencies..."; \
            pip install --no-cache-dir ./OlivOS[extend,dev] || \
            pip install --no-cache-dir ./OlivOS && \
            pip install --no-cache-dir "lxml" "pyyaml" "openpyxl" "APScheduler==3.10.1" "js2py" "certifi" "httpx" "prompt-toolkit" "regex" "rich" && \
            pip install --no-cache-dir "pytest" "black" "flake8" "ruff"; \
        else \
            echo "Full build: installing core + extend dependencies..."; \
            pip install --no-cache-dir ./OlivOS[extend] || \
            pip install --no-cache-dir ./OlivOS && \
            pip install --no-cache-dir "lxml" "pyyaml" "openpyxl" "APScheduler==3.10.1" "js2py" "certifi" "httpx" "prompt-toolkit" "regex" "rich"; \
        fi; \
    elif [ -f "requirements.txt" ]; then \
        echo "pyproject.toml not found, using requirements.txt..."; \
        pip install --no-cache-dir -r requirements.txt; \
        if [ "$BUILD_TYPE" = "dev" ]; then \
            echo "Dev build: installing dev tools..."; \
            pip install --no-cache-dir "pytest" "black" "flake8" "ruff"; \
        fi; \
    else \
        echo "ERROR: No pyproject.toml or requirements.txt found!" && exit 1; \
    fi

# 仅在 full 模式下下载和安装 OPK 插件
RUN if [ "$BUILD_TYPE" = "full" ]; then \
        echo "Full build: downloading OPK plugins..."; \
        COPY opk.txt download_plugins.py ./ ; \
        python download_plugins.py && \
        rm download_plugins.py opk.txt; \
        # 复制本地 opk 文件夹中的插件
        COPY opk/ ./opk_local/ 2>/dev/null || true; \
        find ./opk_local -name '*.opk' -exec cp {} OlivOS/plugin/app/ \; 2>/dev/null || true; \
        rm -rf ./opk_local; \
    else \
        echo "Dev build: skipping OPK plugins installation"; \
    fi

# 清理：删除缓存、__pycache__、tests、test 目录
RUN rm -rf /root/.cache/pip && \
    find /usr/local/lib/python3.11 -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11 -type d -name 'tests' -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11 -type d -name 'test' -exec rm -rf {} + 2>/dev/null || true && \
    # 清理源码中不必要的文件
    find /app/OlivOS -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/OlivOS -type d -name '.git' -exec rm -rf {} + 2>/dev/null || true && \
    find /app/OlivOS -type f -name '*.pyc' -delete 2>/dev/null || true && \
    find /app/OlivOS -type f -name '*.pyo' -delete 2>/dev/null || true

# ==================== 运行阶段 ====================
FROM python:3.11-slim

ARG BUILD_TYPE=full

# 安装运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        # 开发版额外安装调试工具
        $(if [ "$BUILD_TYPE" = "dev" ]; then echo "vim curl procps"; fi) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制 site-packages
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# 复制源码
COPY --from=builder /app/OlivOS /app/OlivOS

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 入口点
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
