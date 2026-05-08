"""
全市场资金趋势监控系统 — CLI入口 + 主循环

用法:
    python3 main.py                  # 盘中监控模式
    python3 main.py --once           # 单次扫描并报告
    python3 main.py --daily          # 收盘总结
    python3 main.py --report         # 立即报告（基于已有快照）
    python3 main.py --interval 300   # 5分钟轮询
    python3 main.py --no-notify      # 不推送通知
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

# 确保模块可以正确导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import POLL_INTERVAL, REPORT_INTERVAL, STOCK_REFRESH_DAYS, TRADING_START, TRADING_END, MORNING_END, AFTERNOON_START
import fetcher
import store
from analysis import (
    compute_sector_aggregates, compute_market_breadth,
    analyze_sector_trends, classify_stock
)
from reporter import (
    generate_market_report, generate_daily_summary,
    print_report, notify_report, send_notification, fmt_time
)


# ── 工具函数 ──────────────────────────────────────────────────────────────

def log(msg: str):
    t = fmt_time()
    print(f"[{t}] {msg}", flush=True)


def is_trading_time() -> bool:
    now = datetime.now()
    t = (now.hour, now.minute)

    if t < TRADING_START or t >= TRADING_END:
        return False

    am_end = MORNING_END[0] * 60 + MORNING_END[1]
    pm_start = AFTERNOON_START[0] * 60 + AFTERNOON_START[1]
    t_min = t[0] * 60 + t[1]
    if am_end < t_min < pm_start:
        return False
    if now.weekday() >= 5:
        return False
    return True


def should_refresh_stocks() -> bool:
    """判断是否需要刷新股票列表"""
    last = store.get_stock_last_update()
    if last is None:
        return True
    return (datetime.now() - last) > timedelta(days=STOCK_REFRESH_DAYS)


# ── 股票池初始化 ──────────────────────────────────────────────────────────

def ensure_stock_list():
    """确保股票池已初始化"""
    cached_count = store.get_stock_count()

    if not should_refresh_stocks() and cached_count > 0:
        log(f"股票池已就绪（{cached_count} 只）")
        return

    log("正在获取全市场股票列表...")
    stocks = fetcher.fetch_stock_list()
    if stocks and len(stocks) >= max(cached_count * 0.8, 1000):
        store.save_stocks(stocks)
        log(f"股票列表已更新（{len(stocks)} 只，原{cached_count}只）")
    elif cached_count > 0:
        log(f"新获取股票数过少（{len(stocks) if stocks else 0}只），保留缓存（{cached_count}只）")
    elif stocks:
        store.save_stocks(stocks)
        log(f"股票列表已保存（{len(stocks)} 只）")
    else:
        log("警告：获取股票列表失败，使用缓存数据")


# ── 单次扫描 ──────────────────────────────────────────────────────────────

def run_scan_and_report(no_notify: bool = False):
    """执行一次全市场扫描并生成报告"""
    # 获取股票列表
    stocks = store.get_all_stocks()
    if not stocks:
        log("错误：股票池为空，请先运行一次初始化")
        return None

    codes = fetcher.build_tencent_codes(stocks)
    log(f"正在获取全市场行情（{len(codes)} 只）...")

    start = time.time()
    quotes = fetcher.fetch_all_quotes(codes)
    elapsed = time.time() - start

    if not quotes:
        log("错误：获取行情失败")
        return None

    log(f"获取完成（{len(quotes)} 只有效, {elapsed:.0f}s）")

    # 检查新数据是否比缓存更完整，避免不完整扫描覆盖好数据
    cached_count = store.get_latest_snapshot_count()
    if cached_count > 0 and len(quotes) < cached_count * 0.8:
        log(f"本次扫描结果不完整（{len(quotes)}只 < 缓存{cached_count}只*0.8），跳过保存")
        return None

    # 保存快照
    store.save_snapshots_batch(list(quotes.values()))

    # 板块聚合
    sectors = compute_sector_aggregates(quotes)
    log(f"覆盖 {len(sectors)} 个板块")

    # 保存板块快照
    store.save_sector_snapshots_batch(sectors)

    # 市场广度
    breadth = compute_market_breadth(quotes)

    # 获取上次板块快照做对比
    prev_data = store.get_previous_sector_snapshots(limit=1)
    prev_sectors = prev_data[0]["sectors"] if prev_data else None

    # 趋势分析
    sector_analysis = analyze_sector_trends(sectors, prev_sectors)

    # 生成报告
    report = generate_market_report(breadth, sector_analysis, sectors, prev_sectors)
    print_report(report)

    if not no_notify:
        notify_report(report)

    return {
        "quotes": quotes,
        "sectors": sectors,
        "breadth": breadth,
        "analysis": sector_analysis,
    }


# ── 主循环（盘中监控） ────────────────────────────────────────────────────

def main_loop(interval: int, no_notify: bool):
    """盘中监控主循环"""
    log(f"全市场资金趋势监控启动 | 轮询{interval}s")

    if not no_notify:
        send_notification("市场监控启动", "全市场资金趋势监控已就绪", sound=False)

    cycle = 0
    report_count = 0
    last_sectors = None

    while True:
        if not is_trading_time():
            now = datetime.now()
            if now.weekday() >= 5 or (now.hour, now.minute) >= TRADING_END:
                log("收盘了，监控停止。")
                if not no_notify:
                    send_notification("市场监控", "收盘了，明天继续")
                break

            # 中午休息或等待开盘
            time.sleep(30)
            continue

        cycle += 1
        report_cycle = (cycle % REPORT_INTERVAL == 0)

        result = run_scan_and_report(no_notify=(not report_cycle and no_notify))

        if result and report_cycle:
            report_count += 1
            last_sectors = result["sectors"]

        time.sleep(interval)


# ── 收盘总结 ──────────────────────────────────────────────────────────────

def run_daily_summary(no_notify: bool = False):
    """生成收盘总结"""
    result = run_scan_and_report(no_notify=True)
    if not result:
        return

    sectors = result["sectors"]
    breadth = result["breadth"]

    # 保存日线
    today = datetime.now().strftime("%Y-%m-%d")
    for s in sectors:
        store.save_sector_daily({
            "sector_name": s["sector_name"],
            "date": today,
            "avg_change": s["avg_change"],
            "total_amount": s["total_amount"],
            "up_count": s["up_count"],
            "down_count": s["down_count"],
        })

    # 生成并发送收盘总结
    summary = generate_daily_summary(sectors, breadth)
    print()
    print(summary)
    print()

    if not no_notify:
        # 发送精简版通知
        top_sectors = sorted(sectors, key=lambda x: x["avg_change"], reverse=True)[:3]
        bottom_sectors = sorted(sectors, key=lambda x: x["avg_change"])[:3]
        msg_parts = [
            f"涨{breadth['up']}/跌{breadth['down']}",
            f"成交{breadth['total_amount']:.0f}亿",
        ]
        if top_sectors:
            msg_parts.append(f"领涨:{top_sectors[0]['sector_name']}+{top_sectors[0]['avg_change']:.1f}%")
        send_notification(f"收盘总结 {today}", " | ".join(msg_parts))

    log("收盘总结完成")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="全市场资金趋势监控系统")
    parser.add_argument("--once", action="store_true", help="单次扫描并报告")
    parser.add_argument("--daily", action="store_true", help="生成收盘总结")
    parser.add_argument("--report", action="store_true", help="基于已有快照生成报告")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL, help="轮询间隔(秒)")
    parser.add_argument("--no-notify", action="store_true", help="不推送通知")
    args = parser.parse_args()

    # 初始化数据库
    store.init_db()

    # 确保股票池
    ensure_stock_list()

    if args.daily:
        run_daily_summary(no_notify=args.no_notify)
    elif args.once:
        run_scan_and_report(no_notify=args.no_notify)
    elif args.report:
        # 从已有快照生成报告
        prev_data = store.get_previous_sector_snapshots(limit=1)
        if not prev_data:
            log("错误：没有历史快照数据，请先运行 --once 或监控模式")
            return
        sectors = prev_data[0]["sectors"]
        # 获取行情快照以计算市场广度
        log(f"基于 {prev_data[0]['ts']} 的快照生成报告")
        breadth = {
            "total": sum(s["stock_count"] for s in sectors),
            "up": sum(s["up_count"] for s in sectors),
            "down": sum(s["down_count"] for s in sectors),
            "flat": 0,
            "up_ratio": 0,
            "total_amount": sum(s["total_amount"] for s in sectors),
            "limit_up": 0,
            "limit_down": 0,
        }
        if sectors:
            broadcast = compute_market_breadth({})
            sector_analysis = analyze_sector_trends(sectors)
            report = generate_market_report(breadth, sector_analysis, sectors)
            print_report(report)
    else:
        main_loop(interval=args.interval, no_notify=args.no_notify)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n监控已停止")
        sys.exit(0)
