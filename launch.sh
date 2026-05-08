#!/bin/bash
# 全市场资金趋势监控 — 一键启动
# 用法: ./launch.sh [--once] [--daily] [--web] [--port PORT]

cd "$(dirname "$0")"

MODE="${1:---web}"
PORT=9999

case "$MODE" in
    --web)
        echo "启动 Web 仪表盘 → http://localhost:$PORT"
        open "http://localhost:$PORT" 2>/dev/null &
        exec /Users/apple/.hermes/hermes-agent/venv/bin/python3 webui.py --port "$PORT"
        ;;
    --once|"")
        echo "单次扫描..."
        exec /Users/apple/.hermes/hermes-agent/venv/bin/python3 main.py --once
        ;;
    --daily)
        echo "收盘总结..."
        exec /Users/apple/.hermes/hermes-agent/venv/bin/python3 main.py --daily
        ;;
    --monitor)
        echo "盘中监控..."
        exec /Users/apple/.hermes/hermes-agent/venv/bin/python3 main.py
        ;;
    *)
        echo "用法: ./launch.sh [--web|--once|--daily|--monitor]"
        exit 1
        ;;
esac
