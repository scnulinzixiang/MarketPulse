"""
SQLite持久化模块
"""

import os
import sqlite3
from datetime import datetime, date, timezone, timedelta
from typing import Optional

from config import DB_PATH


def _now_cn() -> str:
    """返回北京时间字符串 yyyy-mm-dd HH:MM:SS"""
    return datetime.now(tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def get_db_path() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, DB_PATH)


def get_conn() -> sqlite3.Connection:
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表结构"""
    conn = get_conn()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            exchange TEXT,
            short_code TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS stock_industries (
            code TEXT PRIMARY KEY,
            sector_name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            price REAL,
            change_pct REAL,
            volume REAL,
            amount REAL,
            turnover_rate REAL,
            high REAL,
            low REAL,
            open REAL,
            pre_close REAL,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sector_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sector_name TEXT NOT NULL,
            stock_count INTEGER,
            avg_change REAL,
            up_count INTEGER,
            down_count INTEGER,
            total_volume REAL,
            total_amount REAL,
            max_change REAL,
            min_change REAL,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sector_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sector_name TEXT NOT NULL,
            date TEXT NOT NULL,
            avg_change REAL,
            total_amount REAL,
            up_count INTEGER,
            down_count INTEGER,
            UNIQUE(sector_name, date)
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);
        CREATE INDEX IF NOT EXISTS idx_sector_snapshots_ts ON sector_snapshots(ts);
        CREATE INDEX IF NOT EXISTS idx_sector_daily_date ON sector_daily(date);
    """)

    conn.commit()
    conn.close()


# ── 股票池操作 ────────────────────────────────────────────────────────────

def save_stocks(stocks: list[dict]):
    """批量保存/更新股票列表"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT OR REPLACE INTO stocks (code, name, exchange, short_code, added_at) VALUES (?, ?, ?, ?, ?)",
        [(s["code"], s["name"], s.get("exchange", ""), s.get("short_code", ""), _now_cn()) for s in stocks]
    )
    conn.commit()
    conn.close()


def get_stock_count() -> int:
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    conn.close()
    return count


def get_all_stocks() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM stocks ORDER BY code").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stock_last_update() -> Optional[datetime]:
    conn = get_conn()
    row = conn.execute("SELECT MAX(added_at) as last FROM stocks").fetchone()
    conn.close()
    if row and row["last"]:
        return datetime.fromisoformat(row["last"])
    return None


# ── 行情快照 ──────────────────────────────────────────────────────────────

def save_snapshot(snap: dict):
    """保存单条行情快照"""
    conn = get_conn()
    conn.execute(
        """INSERT INTO snapshots (code, name, price, change_pct, volume, amount,
           turnover_rate, high, low, open, pre_close, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (snap["code"], snap.get("name"), snap.get("price"),
         snap.get("change_pct"), snap.get("volume"), snap.get("amount"),
         snap.get("turnover_rate"), snap.get("high"), snap.get("low"),
         snap.get("open"), snap.get("pre_close"), _now_cn())
    )
    conn.commit()
    conn.close()


def save_snapshots_batch(snapshots: list[dict]):
    """批量保存行情快照"""
    conn = get_conn()
    ts = _now_cn()
    conn.executemany(
        """INSERT INTO snapshots (code, name, price, change_pct, volume, amount,
           turnover_rate, high, low, open, pre_close, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(s["code"], s.get("name"), s.get("price"),
          s.get("change_pct"), s.get("volume"), s.get("amount"),
          s.get("turnover_rate"), s.get("high"), s.get("low"),
          s.get("open"), s.get("pre_close"), ts)
         for s in snapshots]
    )
    conn.commit()
    conn.close()


def get_latest_snapshot_ts() -> Optional[datetime]:
    conn = get_conn()
    row = conn.execute("SELECT MAX(ts) as ts FROM snapshots").fetchone()
    conn.close()
    if row and row["ts"]:
        ts_str = row["ts"]
        if isinstance(ts_str, str):
            return datetime.fromisoformat(ts_str)
        return ts_str
    return None


def get_latest_snapshot_count() -> int:
    """获取最新快照的股票数量"""
    conn = get_conn()
    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM snapshots
        WHERE ts = (SELECT MAX(ts) FROM snapshots)
    """).fetchone()
    conn.close()
    return row[0] if row else 0


# ── 板块快照 ──────────────────────────────────────────────────────────────

def save_sector_snapshot(data: dict):
    """保存单条板块快照"""
    conn = get_conn()
    conn.execute(
        """INSERT INTO sector_snapshots (sector_name, stock_count, avg_change,
           up_count, down_count, total_volume, total_amount, max_change, min_change, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["sector_name"], data.get("stock_count", 0),
         data.get("avg_change", 0), data.get("up_count", 0),
         data.get("down_count", 0), data.get("total_volume", 0),
         data.get("total_amount", 0), data.get("max_change", 0),
         data.get("min_change", 0), _now_cn())
    )
    conn.commit()
    conn.close()


def save_sector_snapshots_batch(sectors: list[dict]):
    """批量保存板块快照"""
    conn = get_conn()
    ts = _now_cn()
    conn.executemany(
        """INSERT INTO sector_snapshots (sector_name, stock_count, avg_change,
           up_count, down_count, total_volume, total_amount, max_change, min_change, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(s["sector_name"], s.get("stock_count", 0), s.get("avg_change", 0),
          s.get("up_count", 0), s.get("down_count", 0), s.get("total_volume", 0),
          s.get("total_amount", 0), s.get("max_change", 0), s.get("min_change", 0), ts)
         for s in sectors]
    )
    conn.commit()
    conn.close()


def get_previous_sector_snapshots(limit: int = 1) -> list[dict]:
    """获取最近的N次板块快照"""
    conn = get_conn()
    # 获取最新的N个时间戳
    ts_rows = conn.execute(
        "SELECT DISTINCT ts FROM sector_snapshots ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    if not ts_rows:
        return []

    # 获取这些时间戳对应的所有板块数据
    timestamps = [r["ts"] for r in ts_rows]
    all_data = []
    for ts in timestamps:
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM sector_snapshots WHERE ts = ? ORDER BY sector_name", (ts,)
        ).fetchall()
        conn.close()
        all_data.append({"ts": ts, "sectors": [dict(r) for r in rows]})
    return all_data


# ── 板块日线 ──────────────────────────────────────────────────────────────

def save_sector_daily(data: dict):
    """保存板块日线数据"""
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO sector_daily (sector_name, date, avg_change,
           total_amount, up_count, down_count)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (data["sector_name"], data["date"], data.get("avg_change", 0),
         data.get("total_amount", 0), data.get("up_count", 0),
         data.get("down_count", 0))
    )
    conn.commit()
    conn.close()


def get_sector_daily(sector_name: str, days_back: int = 5) -> list[dict]:
    """获取最近N天的板块日线数据"""
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM sector_daily
           WHERE sector_name = ?
           ORDER BY date DESC
           LIMIT ?""",
        (sector_name, days_back)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_sector_daily(days_back: int = 5) -> dict[str, list[dict]]:
    """获取所有板块最近N天的日线数据"""
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM sector_daily
           ORDER BY date DESC
           LIMIT ?""",
        (days_back * 50,)  # 足够大的限制
    ).fetchall()
    conn.close()

    result = {}
    for r in rows:
        d = dict(r)
        name = d["sector_name"]
        if name not in result:
            result[name] = []
        result[name].append(d)
    return result


# ── 异动股票接口 ──────────────────────────────────────────────────────────

def get_recent_anomaly_stocks(limit: int = 100) -> list[str]:
    """获取近期被标记为异动的股票代码列表（供快循环热点池混合策略使用）"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT DISTINCT code FROM snapshots
        WHERE ts = (SELECT MAX(ts) FROM snapshots)
          AND (change_pct >= 3 OR change_pct <= -3)
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [r[0] for r in rows]


# ── 行业分类数据操作 ──────────────────────────────────────────────────────

def save_stock_industries(industries: dict[str, str]):
    """批量保存股票行业分类映射 {code: sector_name}"""
    conn = get_conn()
    conn.executemany(
        "INSERT OR REPLACE INTO stock_industries (code, sector_name) VALUES (?, ?)",
        [(code, sector) for code, sector in industries.items()]
    )
    conn.commit()
    conn.close()


def get_stock_industries_count() -> int:
    """获取行业分类表中的记录数"""
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) FROM stock_industries").fetchone()
    conn.close()
    return row[0] if row else 0


def get_all_stock_industries() -> dict[str, str]:
    """获取所有行业分类映射"""
    conn = get_conn()
    rows = conn.execute("SELECT code, sector_name FROM stock_industries").fetchall()
    conn.close()
    return {r["code"]: r["sector_name"] for r in rows}


# ── 本地规则填充行业分类 ────────────────────────────────────────────────

def populate_stock_industries_locally(force: bool = False) -> int:
    """
    使用本地规则（SECTOR_KEYWORDS + SECTOR_MAP + KNOWN_STOCKS）
    对全部股票做一次本地分类，写入 stock_industries 表。

    如果表中已有数据且覆盖率>3000只，跳过（除非 force=True）。
    返回本次写入的股票数量。
    """
    from analysis import classify_stock, _extract_short_code

    if not force:
        cnt = get_stock_industries_count()
        if cnt > 3000:
            print(f"  [store] 行业分类表已有 {cnt} 条记录（覆盖率充足），跳过本地填充")
            return 0

    stocks = get_all_stocks()
    if not stocks:
        print("  [store] 股票池为空，无法填充行业分类")
        return 0

    industries = {}
    total = len(stocks)
    seen = set()
    for i, s in enumerate(stocks):
        code = s.get("code", "")
        name = s.get("name", "")
        if not code or not name:
            continue
        short_code = _extract_short_code(code)
        if short_code in seen:
            continue
        seen.add(short_code)
        sector = classify_stock(code, name)
        if sector != "其他":
            industries[short_code] = sector
        if (i + 1) % 1000 == 0:
            print(f"  [store] 分类进度: {i+1}/{total}, 已归类: {len(industries)}", flush=True)

    if industries:
        save_stock_industries(industries)
        # 刷新 analysis 缓存
        from analysis import reload_stock_sectors
        reload_stock_sectors()
        print(f"  [store] 本地规则填充完成: {len(industries)} 只股票已归类, 跳过 {total - len(industries)} 只", flush=True)
    else:
        print("  [store] 本地规则填充结果为空", flush=True)

    return len(industries)
