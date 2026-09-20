#!/bin/sh
set -e

cleanup() {
    if [ -n "${MAIN_PID:-}" ]; then
        kill -TERM "$MAIN_PID" 2>/dev/null || true
        wait "$MAIN_PID" 2>/dev/null || true
    fi
}

trap cleanup TERM INT

cd /app/OlivOS
# 挂载目录只补充缺失插件，保留用户修改和已解包的同名插件。
if [ -d /opt/olivos/plugins ]; then
    mkdir -p plugin/app
    for plugin in /opt/olivos/plugins/*.opk; do
        [ -f "$plugin" ] || continue
        name=${plugin##*/}
        if [ ! -e "plugin/app/$name" ] && [ ! -L "plugin/app/$name" ] && \
           [ ! -e "plugin/app/${name%.opk}" ] && [ ! -L "plugin/app/${name%.opk}" ]; then
            cp -n "$plugin" "plugin/app/$name"
        fi
    done
fi
python main.py "$@" &
MAIN_PID=$!
STATUS=0
wait "$MAIN_PID" || STATUS=$?
exit "$STATUS"
