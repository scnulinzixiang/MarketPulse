"""
趋势分析引擎
- 板块聚合计算
- 多周期趋势评分
- 信号检测（轮动、热度变化）
"""

from datetime import datetime, timedelta
import time
from typing import Optional

from config import SECTOR_MAP, KNOWN_STOCKS, SECTOR_KEYWORDS
from config import MARKET_BREADTH_CONFIG, TREND_CONFIG


# ── 东财行业分类缓存 ──────────────────────────────────────────────────────

_stock_sector_cache = None
_stock_sector_cache_time = 0  # 缓存时间戳

def _load_stock_sectors() -> dict:
    """从数据库加载东方行业分类映射，惰性加载 + 缓存时效检查"""
    global _stock_sector_cache, _stock_sector_cache_time
    now = time.time()
    # 缓存有效期为30秒，避免重复查询
    if _stock_sector_cache is not None and (now - _stock_sector_cache_time) < 30:
        return _stock_sector_cache
    try:
        import store as _s
        conn = _s.get_conn()
        rows = conn.execute("SELECT code, sector_name FROM stock_industries").fetchall()
        conn.close()
        _stock_sector_cache = {r["code"]: r["sector_name"] for r in rows}
        _stock_sector_cache_time = now
    except Exception:
        if _stock_sector_cache is None:
            _stock_sector_cache = {}
        _stock_sector_cache_time = now
    return _stock_sector_cache


def reload_stock_sectors():
    """强制重新加载行业分类映射"""
    global _stock_sector_cache, _stock_sector_cache_time
    _stock_sector_cache = None
    _stock_sector_cache_time = 0
    return _load_stock_sectors()


# ── 板块分类 ──────────────────────────────────────────────────────────────

def classify_stock(code: str, name: str) -> str:
    """
    将股票归类到行业板块
    优先级: KNOWN_STOCKS > stock_industries(东方行业) > SECTOR_MAP(代码范围) > SECTOR_KEYWORDS(名称)
    """
    # 1. 已知股票精确匹配
    short_code = _extract_short_code(code)
    if short_code in KNOWN_STOCKS:
        return KNOWN_STOCKS[short_code]

    # 2. 东方行业分类（最准确，覆盖 ~3200 只）
    sector_map = _load_stock_sectors()
    if len(short_code) == 6 and short_code in sector_map:
        return sector_map[short_code]

    # 3. 代码范围匹配
    # code 格式为 sh600519 或 sz300750
    exchange = code[:2] if len(code) >= 2 else ""
    num_part = _extract_num(code)

    for sector, ranges in SECTOR_MAP.items():
        for r in ranges:
            r_exchange = r[0]
            if exchange != r_exchange:
                continue
            if len(r) == 3:
                _, start, end = r
                if start <= num_part <= end:
                    return sector

    # 3. 关键词匹配
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in name:
                return sector

    return "其他"


def _extract_short_code(code: str) -> str:
    """从 sh600519 提取 600519"""
    return code[-6:] if len(code) >= 6 else code


def _extract_num(code: str) -> int:
    """从 sh600519 提取 600519 为整数"""
    try:
        return int(code[-6:])
    except (ValueError, IndexError):
        return 0


def get_board_type(code: str) -> str:
    """根据股票代码判断所属板块
    返回: 'main_sh' 上证主板, 'main_sz' 深证主板, 'gem' 创业板, 'star' 科创板, 'bj' 北交所
    """
    short = _extract_short_code(code)
    if not short:
        return "other"
    if short.startswith("68"):
        return "star"       # 科创板 20%
    if short.startswith("30"):
        return "gem"        # 创业板 20%
    if short.startswith("8") or short.startswith("4"):
        return "bj"         # 北交所 30%
    if short.startswith("60"):
        return "main_sh"    # 上证主板 10%
    if short.startswith("00"):
        return "main_sz"    # 深证主板 10%
    return "other"


def get_limit_pct(code: str) -> float:
    """获取个股的涨跌停限制百分比"""
    board = get_board_type(code)
    limits = {
        "main_sh": 9.98,   # 主板 10%
        "main_sz": 9.98,
        "gem": 19.98,      # 创业板 20%
        "star": 19.98,     # 科创板 20%
        "bj": 29.98,       # 北交所 30%
    }
    return limits.get(board, 9.98)


# ── 板块聚合 ──────────────────────────────────────────────────────────────

def compute_sector_aggregates(quotes: dict, market_total: Optional[dict] = None) -> list[dict]:
    """
    将全市场行情聚合到板块级别
    quotes: {code: {name, price, change_pct, volume, amount, ...}}
    返回 [{
        sector_name, stock_count, avg_change, up_count, down_count,
        total_volume, total_amount, max_change, min_change
    }, ...]
    """
    # 按板块分组
    sector_groups: dict[str, list[dict]] = {}
    for code, snap in quotes.items():
        sector = classify_stock(code, snap.get("name", ""))
        if sector not in sector_groups:
            sector_groups[sector] = []
        sector_groups[sector].append(snap)

    results = []
    for sector_name, stocks in sector_groups.items():
        changes = [s.get("change_pct", 0) for s in stocks if s.get("change_pct") is not None]
        volumes = [s.get("volume", 0) for s in stocks]
        amounts = [s.get("amount", 0) for s in stocks]

        if not changes:
            continue

        avg_change = sum(changes) / len(changes)
        up_count = sum(1 for c in changes if c > 0)
        down_count = sum(1 for c in changes if c < 0)

        results.append({
            "sector_name": sector_name,
            "stock_count": len(stocks),
            "avg_change": round(avg_change, 2),
            "up_count": up_count,
            "down_count": down_count,
            "total_volume": sum(volumes),
            "total_amount": sum(amounts),
            "max_change": max(changes) if changes else 0,
            "min_change": min(changes) if changes else 0,
        })

    # 按平均涨跌幅排序
    results.sort(key=lambda x: x["avg_change"], reverse=True)
    return results


# ── 市场广度分析 ──────────────────────────────────────────────────────────

def compute_market_breadth(quotes: dict) -> dict:
    """计算市场整体指标，按板块区分涨跌停"""
    all_changes = [(code, s.get("change_pct", 0)) for code, s in quotes.items()
                   if s.get("change_pct") is not None]
    all_amounts = [s.get("amount", 0) for s in quotes.values()]

    if not all_changes:
        return {"total": 0, "up": 0, "down": 0, "flat": 0, "total_amount": 0}

    up = sum(1 for _, c in all_changes if c > 0)
    down = sum(1 for _, c in all_changes if c < 0)
    flat = sum(1 for _, c in all_changes if c == 0)
    total_amount = sum(all_amounts)

    # 按板块区分涨跌停判断
    limit_up = 0
    limit_down = 0
    for code, cp in all_changes:
        limit_pct = get_limit_pct(code)
        if cp >= limit_pct:
            limit_up += 1
        elif cp <= -limit_pct:
            limit_down += 1

    up_ratio = up / len(all_changes) if all_changes else 0

    return {
        "total": len(all_changes),
        "up": up,
        "down": down,
        "flat": flat,
        "up_ratio": round(up_ratio, 4),
        "total_amount": round(total_amount / 10000, 2),  # 万元->亿
        "limit_up": limit_up,
        "limit_down": limit_down,
    }


# ── 板块趋势分析 ──────────────────────────────────────────────────────────

def analyze_sector_trends(current_sectors: list[dict],
                           prev_sectors: Optional[list[dict]] = None) -> dict:
    """
    分析板块趋势
    返回 {signals, hot_sectors, cold_sectors, rotation}
    """
    if not current_sectors:
        return {"signals": [], "hot_sectors": [], "cold_sectors": [], "rotation": None}

    prev_map = {}
    if prev_sectors:
        prev_map = {s["sector_name"]: s for s in prev_sectors}

    signals = []
    hot_sectors = []
    cold_sectors = []

    for sector in current_sectors:
        name = sector["sector_name"]
        change = sector["avg_change"]
        stock_count = sector["stock_count"]

        if stock_count < 3:
            continue  # 太小的板块忽略

        # 热点板块
        if change >= 2.0:
            hot_sectors.append(sector)
        elif change <= -2.0:
            cold_sectors.append(sector)

        # 趋势变化信号
        prev = prev_map.get(name)
        if prev:
            change_delta = change - prev["avg_change"]
            if change_delta >= TREND_CONFIG["rotation_threshold"]:
                signals.append(f"{name} 加速{(change_delta):.1f}%")
            elif change_delta <= -TREND_CONFIG["rotation_threshold"]:
                signals.append(f"{name} 减速{(change_delta):.1f}%")

    hot_sectors.sort(key=lambda x: x["avg_change"], reverse=True)
    cold_sectors.sort(key=lambda x: x["avg_change"])

    # 轮动检测：最强板块 - 前值反差
    rotation = None
    if hot_sectors and prev_sectors:
        top = hot_sectors[0]
        prev_top = prev_map.get(top["sector_name"])
        if prev_top and prev_top["avg_change"] < -1.0:
            rotation = f"{top['sector_name']} 从弱势反转至领涨"

    return {
        "signals": signals[:10],
        "hot_sectors": hot_sectors[:10],
        "cold_sectors": cold_sectors[:5],
        "rotation": rotation,
    }


# ── 趋势强度评分 ──────────────────────────────────────────────────────────

def score_trend_strength(daily_data: list[dict]) -> float:
    """
    根据板块日线数据计算趋势强度评分
    正分 = 持续上涨趋势, 负分 = 持续下跌趋势
    """
    if not daily_data or len(daily_data) < 2:
        return 0

    sorted_data = sorted(daily_data, key=lambda x: x.get("date", ""))
    scores = 0

    # 最近N天的平均涨跌幅
    changes = [d.get("avg_change", 0) for d in sorted_data[-5:]]
    avg_change = sum(changes) / len(changes) if changes else 0
    scores += avg_change * 0.5

    # 上涨/下跌天数比例
    up_days = sum(1 for c in changes if c > 0)
    down_days = sum(1 for c in changes if c < 0)
    if up_days + down_days > 0:
        scores += (up_days - down_days) * 0.3

    # 近期趋势加速
    if len(changes) >= 3:
        recent_avg = sum(changes[-2:]) / 2
        early_avg = sum(changes[:3]) / 3 if changes[:3] else 0
        scores += (recent_avg - early_avg) * 0.2

    return round(scores, 2)
