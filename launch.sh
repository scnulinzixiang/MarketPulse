#!/bin/bash
# 全市场资金趋势监控 — 一键启动
# 用法: ./launch.sh [--web|--once|--daily|--monitor|--dual] [--fast N] [--slow N] [--port PORT]

cd "$(dirname "$0")"

MODE="${1:---web}"
PORT=9999
FAST=60
SLOW=300

# Parse arguments (simple)
if [ "$1" = "--port" ] || [ "$2" = "--port" ]; then
    PORT="${3:-9999}"
fi

case "$MODE" in
    --web)
        echo "启动 Web 仪表盘 → http://localhost:$PORT"
        open "http://localhost:$PORT" 2>/dev/null &
        exec python3 webui.py --port "$PORT"
        ;;
    --once)
        echo "单次扫描 + AI 点评..."
        exec python3 main.py --once
        ;;
    --daily)
        echo "收盘总结..."
        exec python3 main.py --daily
        ;;
    --monitor)
        echo "盘中监控（旧版单循环）..."
        exec python3 main.py
        ;;
    --dual)
        echo "双循环监控启动 | 快循环 ${FAST}s | 慢循环 ${SLOW}s"
        echo "仪表盘: http://localhost:$PORT"
        open "http://localhost:$PORT" 2>/dev/null &
        exec python3 webui.py --port "$PORT" &
        exec python3 main.py --fast "$FAST" --slow "$SLOW"
        ;;
    *)
        echo "用法: ./launch.sh [--web|--once|--daily|--monitor|--dual]"
        echo ""
        echo "  --web      启动 Web 仪表盘（默认）"
        echo "  --once     单次扫描 + AI 点评"
        echo "  --daily    收盘总结"
        echo "  --monitor  盘中监控（旧版单循环）"
        echo "  --dual     双循环监控 + 仪表盘"
        exit 1
        ;;
esac
