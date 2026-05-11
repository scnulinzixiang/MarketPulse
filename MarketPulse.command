#!/bin/bash
cd "$(dirname "$0")"
echo ""
echo "  📊 MarketPulse v0.1.1"
echo "  ───────────────────────"
echo "  仪表盘: http://localhost:9999"
echo "  按 Ctrl+C 停止"
echo ""
sleep 1 && open "http://localhost:9999" &
exec /Users/apple/.hermes/hermes-agent/venv/bin/python3 webui.py --port 9999
