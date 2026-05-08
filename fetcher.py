"""
数据采集模块
- Sina API: A股全量代码分页获取
- Tencent API (qt.gtimg.cn): 实时行情批量查询
- Tencent Kline API: 日K线数据
"""

import json
import re
import time
import urllib.request
from datetime import datetime
from typing import Optional

from config import BATCH_SIZE, SINA_PAGE_SIZE

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _request(url: str, timeout: int = 15) -> Optional[str]:
    """发送HTTP GET请求，返回文本内容"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return None


def _request_gbk(url: str, timeout: int = 15) -> Optional[str]:
    """发送HTTP GET请求（GBK编码）"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return raw.decode("gbk")
            except Exception:
                return raw.decode("utf-8", errors="replace")
    except Exception as e:
        return None


# ── 全市场股票列表 ────────────────────────────────────────────────────────

def fetch_stock_list() -> list[dict]:
    """
    从 Sina Market Center 分页获取全量A股代码
    返回 [{code, name, exchange}, ...]
    """
    stocks = []
    nodes = [
        ("sh", "sh_a"),      # 上海A股
        ("sz", "sz_a"),      # 深圳A股
    ]

    for exchange, node in nodes:
        page = 1
        while True:
            url = (
                f"https://vip.stock.finance.sina.com.cn/quotes_service/"
                f"api/json_v2.php/Market_Center.getHQNodeData"
                f"?page={page}&num={SINA_PAGE_SIZE}&sort=symbol&asc=1"
                f"&node={node}&symbol=&_s_r_a=init"
            )
            text = _request(url)
            if not text:
                break
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                break
            if not isinstance(data, list) or len(data) == 0:
                break
            for item in data:
                if isinstance(item, dict):
                    code = str(item.get("code", "")).strip()
                    name = str(item.get("name", "")).strip()
                    if code and name:
                        stocks.append({
                            "code": f"{exchange}{code}",
                            "name": name,
                            "exchange": exchange,
                            "short_code": code,
                        })
            if len(data) < SINA_PAGE_SIZE:
                break
            page += 1
            time.sleep(0.3)  # 避免频率过高

    return stocks


# ── 实时行情 ──────────────────────────────────────────────────────────────

def build_tencent_codes(stock_list: list[dict]) -> list[str]:
    """从stock_list提取腾讯格式的代码列表"""
    result = []
    for s in stock_list:
        code = s.get("code", "")
        if code.startswith("sh") or code.startswith("sz"):
            result.append(code)
        elif "short_code" in s:
            # 根据前缀猜测
            short_code = s["short_code"]
            exchange = s.get("exchange", "")
            if exchange:
                result.append(f"{exchange}{short_code}")
    return result


def fetch_quotes_batch(codes: list[str]) -> dict:
    """
    从腾讯财经批量获取实时行情
    返回 {tencent_code: {name, price, change_pct, volume, amount, ...}}
    """
    if not codes:
        return {}

    codes_str = ",".join(codes)
    url = f"https://qt.gtimg.cn/q={codes_str}"
    text = _request_gbk(url)

    if not text:
        return {}

    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        match = re.match(r'v_(\w+)="(.+)"', line)
        if not match:
            continue
        code = match.group(1)
        fields = match.group(2).split("~")
        if len(fields) < 40:
            continue

        try:
            name = fields[1]
            price = float(fields[3]) if fields[3] else 0
            pre_close = float(fields[4]) if fields[4] else 0
            change_pct = ((price - pre_close) / pre_close * 100) if pre_close > 0 else 0
            high = float(fields[33]) if fields[33] else 0
            low = float(fields[34]) if fields[34] else 0
            open_p = float(fields[5]) if fields[5] else 0
            volume = float(fields[6]) if fields[6] else 0  # 手
            amount_str = fields[37] if len(fields) > 37 and fields[37] else fields.get(7, "0")
            amount = float(amount_str) if amount_str else 0  # 万元

            # 流通市值 -> 换手率估算
            float_mv = float(fields[44]) if len(fields) > 44 and fields[44] else 0
            turnover_rate = 0
            if float_mv > 0 and price > 0:
                shares = float_mv * 10000 / price  # 流通股本（股）
                if shares > 0:
                    turnover_rate = round(volume * 100 / shares * 100, 2)

            result[code] = {
                "name": name,
                "code": code,
                "price": price,
                "pre_close": pre_close,
                "change_pct": round(change_pct, 2),
                "high": high,
                "low": low,
                "open": open_p,
                "volume": volume,
                "amount": amount,
                "turnover_rate": round(turnover_rate, 2),
                "time": fields[30] if len(fields) > 30 else "",
            }
        except (ValueError, IndexError):
            continue

    return result


def fetch_all_quotes(codes: list[str], batch_size: int = BATCH_SIZE) -> dict:
    """
    分批获取全市场行情
    返回 {tencent_code: {...}}
    """
    all_quotes = {}
    total = len(codes)
    for i in range(0, total, batch_size):
        batch = codes[i:i + batch_size]
        quotes = fetch_quotes_batch(batch)
        all_quotes.update(quotes)
        if i + batch_size < total:
            time.sleep(0.5)  # 限速
    return all_quotes


# ── 历史K线数据 ──────────────────────────────────────────────────────────

def fetch_kline(code: str, days: int = 5) -> list[float]:
    """获取日K线成交量序列"""
    # code 格式为 "sz300750"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq"
    text = _request(url)
    if not text:
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    # 导航到kline数据
    d = data
    for key in ("data", code, "day", "qfqday"):
        if isinstance(d, dict):
            d = d.get(key, d)

    if isinstance(d, list):
        volumes = []
        for item in d:
            if isinstance(item, list) and len(item) >= 6:
                try:
                    volumes.append(float(item[5]))
                except (ValueError, IndexError):
                    continue
        return volumes
    return []


def fetch_avg_volume(code: str, days: int = 5) -> float:
    """获取5日均量"""
    volumes = fetch_kline(code, days)
    if not volumes:
        return 0
    return sum(volumes) / len(volumes)
