# 插件与 CPU 架构无关，多架构构建共享同一份下载产物。
# GitHub token 仅以 BuildKit secret 形式进入下载命令，不写入层。
FROM --platform=$BUILDPLATFORM python:3.11-slim AS plugins
RUN printf 'precedence ::ffff:0:0/96  100\n' >> /etc/gai.conf

ARG BUILD_TYPE=full
ARG OLIVOS_RAW_VERSION
# 插件渠道（stable/testing）由工作流按 matrix 显式传入。
ARG PLUGIN_CHANNEL=stable
WORKDIR /build
COPY opk.txt download_plugins.py ./
# 下载器复用 scripts/build_metadata.py 的 JSON 请求与版本比较实现。
COPY scripts/__init__.py scripts/build_metadata.py ./scripts/
COPY opk/ ./opk_local/
ENV PLUGIN_DIR=/opt/olivos/plugins
ENV PLUGIN_MANIFEST=/opt/olivos/plugins/manifest.json
RUN --mount=type=secret,id=github_token \
    set -eu; \
    case "$PLUGIN_CHANNEL" in \
        stable|testing) ;; \
        *) echo "Unknown plugin channel: $PLUGIN_CHANNEL" >&2; exit 1 ;; \
    esac; \
    echo "Preparing plugins for ${OLIVOS_RAW_VERSION} (${PLUGIN_CHANNEL})"; \
    mkdir -p "$PLUGIN_DIR"; \
    if [ -s /run/secrets/github_token ]; then \
        GITHUB_TOKEN="$(cat /run/secrets/github_token)"; \
        export GITHUB_TOKEN; \
    else \
        echo "No GitHub token secret provided; using anonymous API access (may hit rate limits)"; \
    fi; \
    if [ "$BUILD_TYPE" = "full" ]; then \
        python download_plugins.py; \
    else \
        printf '[]\n' > "$PLUGIN_MANIFEST"; \
    fi

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
# 按 BUILD_TYPE 一次性选择安装目标：
# full 额外安装上游 extend 可选依赖组，core 保持核心，dev 加调试工具。
# full 在安装前确认当前源码版本确实定义了 extend，不接受 pip 的
# “extra 不存在”警告后继续发布不完整镜像。
# ============================================
RUN if [ "$BUILD_TYPE" = "full" ]; then \
        python -c "import sys, tomllib; extras = tomllib.load(open('pyproject.toml', 'rb'))['project'].get('optional-dependencies') or {}; sys.exit('upstream pyproject.toml does not define the extend extra' if 'extend' not in extras else 0)" && \
        echo "=== Installing core + extend dependencies ===" && \
        pip install --no-cache-dir '.[extend]'; \
    elif [ "$BUILD_TYPE" = "dev" ]; then \
        echo "=== Installing dev tools ===" && \
        pip install --no-cache-dir '.[dev]'; \
    else \
        echo "=== Installing core dependencies ===" && \
        pip install --no-cache-dir .; \
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
COPY --from=plugins /opt/olivos/plugins /opt/olivos/plugins

# full 镜像在最终运行层做依赖自检（pip check + 必须模块导入），
# 检查失败让构建在 push 之前失败；仅 builder 可导入不足以证明 runtime 完整。
RUN if [ "$BUILD_TYPE" = "full" ]; then \
        python -m pip check && \
        python -c "import yaml, openpyxl, httpx, lxml.etree"; \
    fi

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1

# 入口点
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ==================== 非 root 运行 ====================
# 固定 UID/GID 10001，不新增任意 UID/GID 配置框架。
RUN groupadd --gid 10001 olivos && \
    useradd --uid 10001 --gid 10001 --create-home --home-dir /home/olivos --shell /usr/sbin/nologin olivos

# 运行时写入位置统一用镜像内目录属主解决，不放宽为全局 chmod 777：
# - /app/OlivOS：源码树与上游相对路径写入（./logfile 等）
# - /opt/olivos/plugins：内置 OPK 要被 entrypoint 的 mv -fT 移走，源目录必须可写
# - /home/olivos：可写 HOME（ENV HOME 覆盖基础镜像遗留的 /root）
# 挂载的宿主机 conf/plugin 目录由部署方授权（见 README），启动脚本不递归 chown。
RUN chown -R olivos:olivos /app/OlivOS /opt/olivos/plugins /home/olivos
ENV HOME=/home/olivos

# 最终运行身份：依赖安装、目录准备与依赖自检完成之后才切换。
USER olivos

STOPSIGNAL SIGTERM
ENTRYPOINT ["/entrypoint.sh"]
