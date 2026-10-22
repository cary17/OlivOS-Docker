#!/bin/sh
set -e

cleanup() {
    if [ -z "${MAIN_PID:-}" ]; then
        PENDING_SIGNAL=$1
        return
    fi
    trap '' TERM INT
    STATUS=0
    # A missing group means startup is not ready; SIGINT may still be ignored.
    kill -"$1" "-$MAIN_PID" 2>/dev/null || kill -TERM "$MAIN_PID" 2>/dev/null || true
    wait "$MAIN_PID" 2>/dev/null || STATUS=$?
    exit "$STATUS"
}

trap 'cleanup TERM' TERM
trap 'cleanup INT' INT

cd /app/OlivOS
# 将镜像内置 OPK 移入挂载目录，覆盖同名文件或符号链接。
if [ -d /opt/olivos/plugins ]; then
    mkdir -p plugin/app
    for plugin in /opt/olivos/plugins/*.opk; do
        [ -f "$plugin" ] || continue
        name=${plugin##*/}
        mv -fT "$plugin" "plugin/app/$name"
    done
fi
# Keep the process tree in a separate group without extra system packages.
python -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.setsid(); os.execv(sys.executable, [sys.executable, "main.py", *sys.argv[1:]])' "$@" &
MAIN_PID=$!
if [ -n "${PENDING_SIGNAL:-}" ]; then
    cleanup "$PENDING_SIGNAL"
fi
STATUS=0
wait "$MAIN_PID" || STATUS=$?
exit "$STATUS"
