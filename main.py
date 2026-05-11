"""
全市场资金趋势监控系统 — CLI入口 + 双循环主控

双循环架构:
- 快循环(60s): 热点池300只 → 异动检测 → macOS通知
- 慢循环(5min): 全市场5000只 → 资金流向 → AI点评 → macOS通知
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import POLL_INTERVAL, REPORT_INTERVAL, STOCK_REFRESH_DAYS, TRADING_START, TRADING_END, MORNING_END, AFTERNOON_START
import fetcher
import store
from analysis import compute_sector_aggregates, compute_market_breadth, analyze_sector_trends
from reporter import generate_market_report, generate_daily_summary, print_report, notify_report, send_notification, fmt_time


def log(msg: str):
    t = fmt_time()
    print(f"[{t}] {msg}", flush=True)


def is_trading_time() -> bool:
    now = datetime.now(tz=timezone(timedelta(hours=8)))
    t = (now.hour, now.minute)
    if t < TRADING_START or t >= TRADING_END:
        return False
    am_end = MORNING_END[0] * 60 + MORNING_END[1]
    pm_start = AFTERNOON_START[0] * 60 + AFTERNOON_START[1]
    t_min = t[0] * 60 + t[1]
    if am_end <= t_min < pm_start:
        return False
    return now.weekday() < 5


def should_refresh_stocks() -> bool:
    last = store.get_stock_last_update()
    if last is None:
        return True
    return (datetime.now(tz=timezone(timedelta(hours=8))) - last) > timedelta(days=STOCK_REFRESH_DAYS)


def ensure_stock_list():
    cached_count = store.get_stock_count()
    if not should_refresh_stocks() and cached_count > 0:
        log(f"股票池已就绪（{cached_count} 只）")
        return
    log("正在获取全市场股票列表...")
    stocks = fetcher.fetch_stock_list()
    if stocks and len(stocks) >= max(cached_count * 0.8, 1000):
        store.save_stocks(stocks)
        log(f"股票列表已更新（{len(stocks)} 只）")
    elif cached_count > 0:
        log("新获取股票数过少，保留缓存")
    elif stocks:
        store.save_stocks(stocks)
        log(f"股票列表已保存（{len(stocks)} 只）")
    else:
        log("警告：获取股票列表失败，使用缓存数据")


def detect_anomalies(quotes: dict) -> list[str]:
    alerts = []
    for code, q in quotes.items():
        cp = q.get("change_pct", 0) or 0
        name = q.get("name", "")
        # 涨跌幅异动
        if cp >= 5:
            msg = f"{name} 急涨+{cp:.1f}%"
        elif cp <= -5:
            msg = f"{name} 急跌{cp:.1f}%"
        else:
            continue
        # 放量检测：当日量 > 前5日均量 * 2
        volume = q.get("volume", 0) or 0
        if volume > 0:
            avg_vol = fetcher.fetch_avg_volume(code, days=5)
            if avg_vol > 0:
                ratio = volume / avg_vol
                if ratio >= 2:
                    msg = f"{msg}(放量{ratio:.1f}倍)"
        alerts.append(msg)
    return alerts[:10]


def run_scan_and_report(no_notify: bool = False, skip_moneyflow: bool = False):
    stocks = store.get_all_stocks()
    if not stocks:
        log("错误：股票池为空")
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

    cached_count = store.get_latest_snapshot_count()
    if cached_count > 0 and len(quotes) < cached_count * 0.8:
        log(f"本次扫描不完整，跳过保存")
        return None

    store.save_snapshots_batch(list(quotes.values()))
    sectors = compute_sector_aggregates(quotes)
    log(f"覆盖 {len(sectors)} 个板块")
    store.save_sector_snapshots_batch(sectors)

    breadth = compute_market_breadth(quotes)
    prev_data = store.get_previous_sector_snapshots(limit=1)
    prev_sectors = prev_data[0]["sectors"] if prev_data else None
    sector_analysis = analyze_sector_trends(sectors, prev_sectors)

    moneyflow_sectors = []
    moneyflow_stocks = []
    if not skip_moneyflow:
        moneyflow_sectors = fetcher.fetch_sector_moneyflow()
        moneyflow_stocks = fetcher.fetch_stock_moneyflow_top(30)
        log(f"资金流向：{len(moneyflow_sectors)} 个板块")

    report = generate_market_report(breadth, sector_analysis, sectors, prev_sectors)
    print_report(report)
    if not no_notify:
        notify_report(report)

    return {
        "quotes": quotes, "sectors": sectors,
        "breadth": breadth, "analysis": sector_analysis,
        "moneyflow_sectors": moneyflow_sectors,
        "moneyflow_stocks": moneyflow_stocks,
    }


def main_loop(fast_interval: int = 60, slow_interval: int = 300, no_notify: bool = False):
    log(f"双循环监控启动 | 快循环{fast_interval}s 慢循环{slow_interval}s")
    if not no_notify:
        send_notification("市场监控启动", "双循环监控已就绪")

    last_full_scan = 0
    while True:
        if not is_trading_time():
            now = datetime.now(tz=timezone(timedelta(hours=8)))
            if now.weekday() >= 5 or (now.hour, now.minute) >= TRADING_END:
                log("收盘了，监控停止。")
                send_notification("市场监控", "收盘了，明天继续")
                break
            time.sleep(30)
            continue

        now_ts = time.time()

        # 快循环：混合策略热点池（成交额前200 + 近期异动前100）
        hot_start = time.time()
        try:
            hot_stocks = fetcher.fetch_top_stocks_by_amount(200)
            hot_map = {}
            if hot_stocks:
                for s in hot_stocks:
                    code = s.get("code", "")
                    if code:
                        hot_map[code] = s
            # 补充近期异动股票
            try:
                anomaly_codes = store.get_recent_anomaly_stocks(100)
                for sc in anomaly_codes:
                    if sc not in hot_map:
                        # 尝试通过全量quotes获取数据（如果已有扫描记录）
                        hot_map[sc] = {"code": sc, "name": "", "change_pct": 0}
            except Exception:
                pass
            if hot_map:
                hot_quotes = {}
                for code, s in hot_map.items():
                    hot_quotes[code] = {"name": s.get("name", ""), "change_pct": s.get("change_pct", 0)}
                alerts = detect_anomalies(hot_quotes)
                if alerts and not no_notify:
                    msg = " | ".join(alerts[:3])
                    send_notification(f"异动预警 ({fmt_time()})", msg, sound=True)
                log(f"[快] {len(hot_stocks)}只, {len(alerts)}条异动 ({time.time()-hot_start:.0f}s)")
        except Exception as e:
            log(f"[快] 异常: {e}")

        # 慢循环：全量 + 资金流向 + AI
        if now_ts - last_full_scan >= slow_interval:
            last_full_scan = now_ts
            slow_start = time.time()
            try:
                result = run_scan_and_report(no_notify=no_notify)
                if result:
                    try:
                        from ai_advisor import generate_market_commentary
                        commentary = generate_market_commentary(
                            breadth=result["breadth"],
                            sectors=result["sectors"],
                            moneyflow_sectors=result.get("moneyflow_sectors", []),
                            moneyflow_stocks=result.get("moneyflow_stocks", []),
                        )
                        if commentary:
                            print(f"\n[AI 点评]\n{commentary}\n")
                            if not no_notify:
                                short = commentary[:80] + "..." if len(commentary) > 80 else commentary
                                send_notification(f"AI 市场研判 ({fmt_time()})", short)
                    except Exception as e:
                        log(f"AI 点评异常: {e}")
                log(f"[慢] 全量扫描完成 ({time.time()-slow_start:.0f}s)")
            except Exception as e:
                log(f"[慢] 异常: {e}")

        time.sleep(fast_interval)


def run_once():
    ensure_stock_list()
    result = run_scan_and_report(no_notify=True)
    if result:
        try:
            from ai_advisor import generate_market_commentary
            commentary = generate_market_commentary(
                breadth=result["breadth"], sectors=result["sectors"],
                moneyflow_sectors=result.get("moneyflow_sectors", []),
                moneyflow_stocks=result.get("moneyflow_stocks", []),
            )
            if commentary:
                print(f"\n[AI 点评]\n{commentary}\n")
        except Exception as e:
            log(f"AI 点评异常: {e}")


def run_daily_summary(no_notify: bool = False):
    result = run_scan_and_report(no_notify=True)
    if not result:
        return
    sectors, breadth = result["sectors"], result["breadth"]
    today = datetime.now(tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    for s in sectors:
        store.save_sector_daily({
            "sector_name": s["sector_name"], "date": today,
            "avg_change": s["avg_change"], "total_amount": s["total_amount"],
            "up_count": s["up_count"], "down_count": s["down_count"],
        })
    summary = generate_daily_summary(sectors, breadth)
    print(f"\n{summary}\n")
    if not no_notify:
        top3 = sorted(sectors, key=lambda x: x["avg_change"], reverse=True)[:3]
        msg = f"涨{breadth['up']}/跌{breadth['down']} | 成交{breadth['total_amount']:.0f}亿"
        if top3:
            msg += f" | 领涨:{top3[0]['sector_name']}+{top3[0]['avg_change']:.1f}%"
        send_notification(f"收盘总结 {today}", msg)
    log("收盘总结完成")


def main():
    parser = argparse.ArgumentParser(description="全市场资金趋势监控系统")
    parser.add_argument("--once", action="store_true", help="单次扫描+AI点评")
    parser.add_argument("--daily", action="store_true", help="收盘总结")
    parser.add_argument("--report", action="store_true", help="基于已有快照生成报告")
    parser.add_argument("--fast", type=int, default=60, help="快循环间隔(秒)")
    parser.add_argument("--slow", type=int, default=300, help="慢循环间隔(秒)")
    parser.add_argument("--no-notify", action="store_true", help="不推送通知")
    args = parser.parse_args()

    store.init_db()
    ensure_stock_list()
    if args.daily:
        run_daily_summary(no_notify=args.no_notify)
    elif args.once:
        run_once()
    elif args.report:
        prev_data = store.get_previous_sector_snapshots(limit=1)
        if not prev_data:
            log("错误：没有历史快照")
            return
        sectors = prev_data[0]["sectors"]
        breadth = {"total": sum(s["stock_count"] for s in sectors), "up": sum(s["up_count"] for s in sectors),
                   "down": sum(s["down_count"] for s in sectors), "flat": 0, "up_ratio": 0,
                   "total_amount": sum(s["total_amount"] for s in sectors), "limit_up": 0, "limit_down": 0}
        sector_analysis = analyze_sector_trends(sectors)
        report = generate_market_report(breadth, sector_analysis, sectors)
        print_report(report)
    else:
        main_loop(fast_interval=args.fast, slow_interval=args.slow, no_notify=args.no_notify)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n监控已停止")
        sys.exit(0)
