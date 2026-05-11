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

from config import BATCH_SIZE, BATCH_DELAY, SINA_PAGE_SIZE

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


def _request_with_retry(url: str, timeout: int = 20, max_retries: int = 3) -> Optional[str]:
    """带重试和指数退避的HTTP GET请求（用于东方财富 push2 接口，限流时加倍等待）"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    delay = 2
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 429:
                    raise Exception("HTTP 429 Too Many Requests")
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt < max_retries - 1:
                # 指数退避：限流时加倍等待，最多60秒
                err_str = str(e).lower()
                if "429" in err_str or "refused" in err_str or "timeout" in err_str or "reset" in err_str:
                    delay = min(delay * 2, 60)
                else:
                    delay = min(delay + 1, 10)
                time.sleep(delay)
            else:
                return None
    return None


# ── 全市场股票列表 ────────────────────────────────────────────────────────

def fetch_stock_list(max_retries: int = 3) -> list[dict]:
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
            text = None
            for attempt in range(max_retries):
                text = _request(url, timeout=20)
                if text:
                    break
                time.sleep(2)
            if not text:
                print(f"  [fetcher] {exchange} 第{page}页获取失败（重试{max_retries}次后放弃）", flush=True)
                break
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                print(f"  [fetcher] {exchange} 第{page}页JSON解析失败", flush=True)
                break
            if not isinstance(data, list) or len(data) == 0:
                print(f"  [fetcher] {exchange} 第{page}页为空，列表获取完成", flush=True)
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
            print(f"  [fetcher] {exchange} 第{page}页: {len(data)}只 (累计{len(stocks)})", flush=True)
            if len(data) < SINA_PAGE_SIZE:
                break
            page += 1
            time.sleep(0.5)  # 避免频率过高

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
    顺序分批获取全市场行情
    返回 {tencent_code: {...}}
    """
    all_quotes = {}
    total = len(codes)
    batches = (total + batch_size - 1) // batch_size
    for i in range(0, total, batch_size):
        batch = codes[i:i + batch_size]
        quotes = fetch_quotes_batch(batch)
        all_quotes.update(quotes)
        batch_num = i // batch_size + 1
        if batch_num % 10 == 0 or batch_num == batches:
            print(f"  [fetcher] 批次 {batch_num}/{batches}, 本批{len(quotes)}只, 累计{len(all_quotes)}只", flush=True)
        if i + batch_size < total:
            time.sleep(BATCH_DELAY)
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


# ── 东方财富资金流向 ──────────────────────────────────────────────────────

def fetch_sector_moneyflow() -> list[dict]:
    """
    东方财富行业板块资金流向
    返回 [{sector_name, main_net_inflow(亿), main_net_ratio(%), ...}]
    """
    url = ("https://push2.eastmoney.com/api/qt/clist/get"
           "?fields=f12,f14,f62,f184,f66,f69,f72,f75,f78,f81"
           "&fltt=2&pn=1&pz=200&fs=m:90+t:2&fid=f62"
           "&ut=b2884a393a59ad64002ef1a68bbbdc4e")
    text = _request_with_retry(url, timeout=15)
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    diff = data.get("data", {}).get("diff", {})
    if not isinstance(diff, dict):
        return []

    result = []
    for item in diff.values():
        if not isinstance(item, dict):
            continue
        try:
            result.append({
                "sector_name": str(item.get("f14", "")),
                "main_net_inflow": float(item.get("f62", 0) or 0) / 1e8,
                "main_net_ratio": float(item.get("f184", 0) or 0),
                "big_net_inflow": float(item.get("f66", 0) or 0) / 1e8,
                "mid_net_inflow": float(item.get("f72", 0) or 0) / 1e8,
                "small_net_inflow": float(item.get("f78", 0) or 0) / 1e8,
            })
        except (ValueError, TypeError):
            continue

    return sorted(result, key=lambda x: x["main_net_inflow"], reverse=True)


def fetch_stock_moneyflow_top(top_n: int = 30) -> list[dict]:
    """
    东方财富个股资金流向榜 Top N
    返回 [{code, name, price, main_net_inflow(亿), main_net_ratio(%)}]
    """
    url = ("https://push2.eastmoney.com/api/qt/clist/get"
           "?fields=f12,f14,f2,f62,f184&fltt=2"
           f"&pn=1&pz={top_n}"
           "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
           "&fid=f62&ut=b2884a393a59ad64002ef1a68bbbdc4e")
    text = _request_with_retry(url, timeout=15)
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    diff = data.get("data", {}).get("diff", {})
    if not isinstance(diff, dict):
        return []

    result = []
    for item in diff.values():
        if not isinstance(item, dict):
            continue
        try:
            result.append({
                "code": str(item.get("f12", "")),
                "name": str(item.get("f14", "")),
                "price": float(item.get("f2", 0) or 0),
                "main_net_inflow": float(item.get("f62", 0) or 0) / 1e8,
                "main_net_ratio": float(item.get("f184", 0) or 0),
            })
        except (ValueError, TypeError):
            continue

    return sorted(result, key=lambda x: x["main_net_inflow"], reverse=True)


# ── 热点池（成交额排名）─────────────────────────────────────────────────

def fetch_top_stocks_by_amount(top_n: int = 300) -> list[dict]:
    """
    从东方财富获取成交额排名前N只股票
    用于快循环监控池
    """
    url = ("https://push2.eastmoney.com/api/qt/clist/get"
           "?fields=f12,f14,f2,f3,f4,f20"
           "&fltt=2"
           f"&pn=1&pz={top_n}"
           "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
           "&fid=f20&ut=b2884a393a59ad64002ef1a68bbbdc4e")
    text = _request_with_retry(url, timeout=15)
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    diff = data.get("data", {}).get("diff", {})
    if not isinstance(diff, dict):
        return []

    result = []
    for item in diff.values():
        if not isinstance(item, dict):
            continue
        try:
            result.append({
                "code": str(item.get("f12", "")),
                "name": str(item.get("f14", "")),
                "price": float(item.get("f2", 0) or 0),
                "change_pct": float(item.get("f3", 0) or 0),
                "amount": float(item.get("f20", 0) or 0) / 1e8,
            })
        except (ValueError, TypeError):
            continue
    return result
