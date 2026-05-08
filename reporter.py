"""
报告生成和通知模块
"""

import os
from datetime import datetime
from typing import Optional


def send_notification(title: str, message: str, sound: bool = False):
    """发送macOS桌面通知"""
    sound_cmd = f'sound name "{"default" if sound else ""}"'
    safe_title = title.replace('"', '\\"')
    safe_msg = message.replace('"', '\\"')
    os.system(
        f'osascript -e \'display notification "{safe_msg}" with title "{safe_title}" {sound_cmd}\' '
        f'2>/dev/null'
    )


def fmt_time() -> str:
    return datetime.now().strftime("%H:%M")


def fmt_date() -> str:
    return datetime.now().strftime("%m-%d")


# ── 市场趋势报告 ──────────────────────────────────────────────────────────

def generate_market_report(breadth: dict,
                            sector_analysis: dict,
                            current_sectors: list[dict],
                            prev_sectors: Optional[list[dict]] = None) -> str:
    """
    生成全市场资金趋势报告
    """
    now = fmt_time()
    lines = [f"全市场资金趋势报告 {now}", "─" * 32]

    # 大盘概况
    up = breadth.get("up", 0)
    down = breadth.get("down", 0)
    total = breadth.get("total", 0)
    up_ratio = breadth.get("up_ratio", 0)
    amount = breadth.get("total_amount", 0)
    limit_up = breadth.get("limit_up", 0)
    limit_down = breadth.get("limit_down", 0)

    if up_ratio > 0.6:
        trend = "强势"
    elif up_ratio < 0.3:
        trend = "弱势"
    else:
        trend = "震荡"

    lines.append(f"大盘风向：{trend}（上涨{up}/下跌{down}）")
    lines.append(f"成交额：{amount:.0f}亿（涨停{limit_up}/跌停{limit_down}）")

    # 计算涨跌比变化
    if prev_sectors:
        lines.append("")

    # 热点板块
    hot = sector_analysis.get("hot_sectors", [])
    if hot:
        lines.append("")
        lines.append("热点板块（涨幅 Top 5）：")
        for i, s in enumerate(hot[:5], 1):
            tag = ""
            if prev_sectors:
                prev_map = {p["sector_name"]: p for p in prev_sectors}
                prev = prev_map.get(s["sector_name"])
                if prev:
                    delta = s["avg_change"] - prev["avg_change"]
                    if delta > 0.5:
                        tag = " ↑加速"
                    elif delta < -0.5:
                        tag = " ↓减速"
            lines.append(f" {i}. {s['sector_name']} +{s['avg_change']:.1f}%"
                         f"（{s['up_count']}/{s['stock_count']}涨）{tag}")

    # 弱势板块
    cold = sector_analysis.get("cold_sectors", [])
    if cold:
        lines.append("")
        lines.append("弱势板块：")
        for i, s in enumerate(cold[:3], 1):
            lines.append(f" {i}. {s['sector_name']} {s['avg_change']:.1f}%"
                         f"（{s['down_count']}/{s['stock_count']}跌）")

    # 趋势信号
    signals = sector_analysis.get("signals", [])
    rotation = sector_analysis.get("rotation")
    if signals or rotation:
        lines.append("")
        lines.append("趋势信号：")
        if rotation:
            lines.append(f" · {rotation}")
        for sig in signals[:5]:
            lines.append(f" · {sig}")

    # 资金流向 Top/Bottom
    lines.append("")
    lines.append(f"成交额 Top 板块：")
    sorted_by_amount = sorted(current_sectors, key=lambda x: x["total_amount"], reverse=True)
    for s in sorted_by_amount[:3]:
        lines.append(f" · {s['sector_name']} {s['total_amount']:.0f}万")

    return "\n".join(lines)


def generate_daily_summary(current_sectors: list[dict], breadth: dict) -> str:
    """生成收盘总结"""
    lines = [
        f"收盘总结 {fmt_date()}",
        "─" * 32,
        f"上涨{breadth['up']}/下跌{breadth['down']} | "
        f"成交{breadth['total_amount']:.0f}亿",
    ]

    if current_sectors:
        sorted_sectors = sorted(current_sectors,
                                key=lambda x: x["avg_change"], reverse=True)
        lines.append("")
        lines.append("板块涨幅榜：")
        for s in sorted_sectors[:5]:
            lines.append(f" · {s['sector_name']} +{s['avg_change']:.1f}%")
        lines.append("")
        lines.append("板块跌幅榜：")
        for s in sorted_sectors[-5:]:
            lines.append(f" · {s['sector_name']} {s['avg_change']:.1f}%")

    return "\n".join(lines)


def notify_report(report: str):
    """发送报告通知（仅推摘要）"""
    lines = report.split("\n")
    # 发送前3行作为通知
    title = lines[0] if lines else "市场报告"
    summary_lines = [l for l in lines[1:6] if l.strip() and "─" not in l]
    summary = " | ".join(summary_lines)[:200] if summary_lines else "见终端"
    send_notification(title, summary, sound=False)


def print_report(report: str):
    """打印报告到终端"""
    print()
    for line in report.split("\n"):
        print(line)
    print()
