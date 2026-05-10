"""
AI 智能点评模块 — DeepSeek 原生 API (OpenAI 兼容)
API Key 从环境变量读取，不硬编码
"""
import json
import os
import urllib.request
from typing import Optional

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _get_api_key() -> Optional[str]:
    return os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")


def _call_deepseek(messages: list[dict], max_tokens: int = 1024, temperature: float = 0.7) -> Optional[str]:
    api_key = _get_api_key()
    if not api_key:
        return None
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    data = json.dumps({
        "model": "deepseek-chat",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("User-Agent", "MarketPulse/1.0")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
    except Exception:
        return None


def _fmt_sectors(items, prefix="", suffix=""):
    """格式化板块列表为字符串，避免嵌套f-string兼容性问题"""
    parts = []
    for s in items:
        name = s.get("sector_name", "")
        change = s.get("avg_change", 0)
        inflow = s.get("main_net_inflow", 0)
        if suffix == "%":
            parts.append(f"{name}{change:+.1f}%")
        elif suffix == "亿":
            parts.append(f"{name}{inflow:+.1f}亿")
        else:
            parts.append(f"{name}")
    return " ".join(parts)


def generate_market_commentary(
    breadth: dict,
    sectors: list[dict],
    moneyflow_sectors: list[dict],
    moneyflow_stocks: list[dict],
) -> Optional[str]:
    sorted_sectors = sorted(sectors, key=lambda x: x.get("avg_change", 0), reverse=True)
    top_gainers = sorted_sectors[:5]
    top_losers = sorted_sectors[-5:] if len(sorted_sectors) >= 5 else sorted_sectors
    top_losers.reverse()

    sorted_mf = sorted(moneyflow_sectors, key=lambda x: x.get("main_net_inflow", 0), reverse=True)
    most_inflow = [s for s in sorted_mf if s.get("main_net_inflow", 0) > 0][:3]
    most_outflow = [s for s in sorted_mf if s.get("main_net_inflow", 0) < 0]
    most_outflow = most_outflow[:3] if most_outflow else []

    gainer_str = " ".join(f'{s.get("sector_name","")}{s.get("avg_change",0):+.1f}%' for s in top_gainers)
    loser_str = " ".join(f'{s.get("sector_name","")}{s.get("avg_change",0):+.1f}%' for s in top_losers)
    inflow_str = " ".join(f'{s.get("sector_name","")}{s.get("main_net_inflow",0):+.1f}亿' for s in most_inflow) if most_inflow else "无"
    outflow_str = " ".join(f'{s.get("sector_name","")}{s.get("main_net_inflow",0):+.1f}亿' for s in most_outflow) if most_outflow else "无"

    up = breadth.get("up", 0)
    down = breadth.get("down", 0)
    limit_up = breadth.get("limit_up", 0)
    limit_down = breadth.get("limit_down", 0)
    amount = breadth.get("total_amount", 0)

    prompt = (
        f"大盘概况：上涨{up}/下跌{down}，涨停{limit_up}/跌停{limit_down}，成交额{amount:.0f}亿\n"
        f"领涨板块：{gainer_str}\n"
        f"弱势板块：{loser_str}\n"
        f"主力净流入：{inflow_str}\n"
        f"主力净流出：{outflow_str}\n\n"
        f"请给出：1)市场整体判断 2)资金动向解读 3)关注方向建议"
    )

    messages = [
        {"role": "system", "content": "你是一名专业的A股市场分析师，回复简洁精准，200字内。"},
        {"role": "user", "content": prompt},
    ]
    return _call_deepseek(messages)
