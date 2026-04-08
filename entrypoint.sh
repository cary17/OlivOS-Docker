#!/bin/sh
set -e

# 信号处理函数
cleanup() {
    echo "$(date): Received SIGTERM, shutting down gracefully..."
    if [ -n "$MAIN_PID" ] && kill -0 "$MAIN_PID" 2>/dev/null; then
        kill -TERM "$MAIN_PID"
        # 等待进程退出
        wait "$MAIN_PID"
        echo "$(date): Process $MAIN_PID exited"
    fi
    exit 0
}

# 捕获信号
trap cleanup TERM INT

# 安装额外包
if [ -n "$EXTRA_PACKAGES" ]; then
    echo "$(date): Installing extra packages: $EXTRA_PACKAGES"
    pip install --no-cache-dir $EXTRA_PACKAGES
fi

cd /app/OlivOS
echo "$(date): Starting OlivOS..."

# 启动主进程
python main.py "$@" &
MAIN_PID=$!
echo "$(date): Main process started with PID: $MAIN_PID"

# 等待进程结束
wait $MAIN_PID
echo "$(date): Main process exited"
