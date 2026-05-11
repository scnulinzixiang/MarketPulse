"""
Web 仪表盘 — 用 Python 内置 http.server 启动
用法: python3 webui.py [--port 8080]
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store
import main as m
import fetcher
from analysis import compute_sector_aggregates, compute_market_breadth, analyze_sector_trends

_scan_lock = threading.Lock()
_scan_status = {"state": "idle", "msg": "", "pct": 0}


def set_status(state: str, msg: str = "", pct: int = 0):
    global _scan_status
    _scan_status = {"state": state, "msg": msg, "pct": pct}


def get_latest_snapshot():
    """获取最新一次扫描的完整数据"""
    prev_data = store.get_previous_sector_snapshots(limit=1)
    if not prev_data:
        return None
    return prev_data[0]


def get_prev_snapshots(limit=5):
    """获取最近N次扫描"""
    return store.get_previous_sector_snapshots(limit=limit)


def get_market_overview():
    """从 snapshots 表计算市场概览"""
    conn = store.get_conn()
    row = conn.execute("""
        SELECT ts, COUNT(*) as total,
               SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END) as up,
               SUM(CASE WHEN change_pct < 0 THEN 1 ELSE 0 END) as down,
               ROUND(SUM(amount)/10000, 0) as total_amount_yi
        FROM snapshots
        WHERE ts = (SELECT MAX(ts) FROM snapshots)
    """).fetchone()
    conn.close()

    if not row or not row["total"]:
        return None

    up_ratio = round(row["up"] / row["total"] * 100, 1) if row["total"] > 0 else 0
    if up_ratio > 60:
        sentiment = "强势"
    elif up_ratio < 30:
        sentiment = "弱势"
    else:
        sentiment = "震荡"

    # SQLite CURRENT_TIMESTAMP 存的是 UTC，转为北京时间 (UTC+8)
    ts = str(row["ts"])
    try:
        from datetime import datetime, timedelta
        dt = datetime.fromisoformat(ts) + timedelta(hours=8)
        ts = dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    return {
        "ts": ts,
        "total": row["total"],
        "up": row["up"],
        "down": row["down"],
        "up_ratio": up_ratio,
        "total_amount": row["total_amount_yi"],
        "sentiment": sentiment,
    }


def get_sector_history(days=30):
    """获取板块历史趋势"""
    conn = store.get_conn()
    rows = conn.execute("""
        SELECT ts, sector_name, avg_change, stock_count
        FROM sector_snapshots
        ORDER BY ts
    """).fetchall()
    conn.close()

    # 按时间分组
    timestamps = {}
    for r in rows:
        ts = str(r["ts"])
        if ts not in timestamps:
            timestamps[ts] = {}
        timestamps[ts][r["sector_name"]] = {
            "change": r["avg_change"],
            "count": r["stock_count"],
        }
    return timestamps


# ═══════════════════════════════════════════════════════════════════
# HTTP 处理器
# ═══════════════════════════════════════════════════════════════════

class DashboardHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/api/overview":
            self._json_response(get_market_overview())
        elif self.path == "/api/sectors":
            snap = get_latest_snapshot()
            self._json_response(snap["sectors"] if snap else [])
        elif self.path == "/api/history":
            self._json_response(get_sector_history())
        elif self.path.startswith("/api/scan"):
            self._run_scan()
        elif self.path == "/api/stocks":
            overview = get_market_overview()
            if overview:
                up_ratio = overview["up_ratio"]
                if up_ratio > 60:
                    bar_color = "#22c55e"
                elif up_ratio < 30:
                    bar_color = "#ef4444"
                else:
                    bar_color = "#f59e0b"
                overview["bar_color"] = bar_color
            self._json_response(overview)
        elif self.path == "/api/moneyflow/sectors":
            self._json_response(fetcher.fetch_sector_moneyflow())
        elif self.path == "/api/moneyflow/stocks":
            self._json_response(fetcher.fetch_stock_moneyflow_top(20))
        elif self.path == "/api/ai":
            self._generate_ai()
        elif self.path == "/api/status":
            self._json_response(_scan_status)
        else:
            self._serve_html()

    def _json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode())

    def _run_scan(self):
        """在后台线程中执行扫描"""
        def scan():
            set_status("scanning", "获取全市场股票列表...", 5)
            with _scan_lock:
                try:
                    if store.get_stock_count() == 0:
                        m.ensure_stock_list()
                    set_status("scanning", "获取全市场行情(5000只)...", 15)
                    m.run_scan_and_report(no_notify=True)
                    set_status("done", "扫描完成", 100)
                except Exception as e:
                    set_status("error", f"扫描异常: {e}", 0)
                    print(f"Scan error: {e}")
                # 5秒后自动回到idle
                def reset():
                    time.sleep(5)
                    if _scan_status["state"] == "done":
                        set_status("idle", "", 0)
                threading.Thread(target=reset, daemon=True).start()
        t = threading.Thread(target=scan, daemon=True)
        t.start()
        self._json_response({"status": "started"})

    def _generate_ai(self):
        """生成 AI 市场点评（服务器端调用 DeepSeek，key 不暴露给前端）"""
        try:
            snap = get_latest_snapshot()
            if not snap:
                self._json_response({"error": "no_data"})
                return
            sectors = snap["sectors"]
            conn = store.get_conn()
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END) as up,
                       SUM(CASE WHEN change_pct < 0 THEN 1 ELSE 0 END) as down,
                       ROUND(SUM(amount)/10000, 0) as total_amount_yi
                FROM snapshots WHERE ts = (SELECT MAX(ts) FROM snapshots)
            """).fetchone()
            conn.close()
            breadth = {
                "up": row["up"] or 0, "down": row["down"] or 0,
                "limit_up": 0, "limit_down": 0,
                "total_amount": row["total_amount_yi"] or 0,
            }
            moneyflow = fetcher.fetch_sector_moneyflow()
            from ai_advisor import generate_market_commentary
            commentary = generate_market_commentary(breadth, sectors, moneyflow, [])
            self._json_response({"text": commentary, "ts": str(datetime.now())})
        except Exception as e:
            self._json_response({"error": str(e)})

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode("utf-8"))

    def log_message(self, fmt, *args):
        print(f"[HTTP] {args[0]} {args[1]}", flush=True)


# ═══════════════════════════════════════════════════════════════════
# HTML 页面（内联所有 CSS/JS）
# ═══════════════════════════════════════════════════════════════════

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>全市场资金趋势监控</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:#0f0f13;color:#e0e0e0;min-height:100vh}
.container{max-width:1200px;margin:0 auto;padding:16px}

/* Header */
.header{display:flex;align-items:center;justify-content:space-between;padding:16px 0;
  border-bottom:1px solid #1e1e2a;margin-bottom:20px}
.header h1{font-size:20px;font-weight:600;color:#f0f0f0}
.header .ts{color:#888;font-size:13px}
.refresh-btn{background:#2a2a3a;border:1px solid #3a3a4a;color:#ccc;padding:8px 16px;
  border-radius:6px;cursor:pointer;font-size:13px;transition:.2s}
.refresh-btn:hover{background:#3a3a4a;color:#fff}
.refresh-btn.scanning{opacity:.6;pointer-events:none}

/* Cards */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:20px}
.card{background:#1a1a26;border:1px solid #26263a;border-radius:10px;padding:16px}
.card .label{font-size:12px;color:#888;margin-bottom:4px}
.card .value{font-size:26px;font-weight:700}
.card .sub{font-size:13px;color:#666;margin-top:2px}

/* Sector Table */
.sector-table{width:100%;border-collapse:collapse;font-size:13px}
.sector-table th{text-align:left;padding:10px 8px;border-bottom:1px solid #26263a;
  color:#888;font-weight:500;font-size:12px;cursor:pointer;user-select:none}
.sector-table th:hover{color:#ccc}
.sector-table td{padding:8px;border-bottom:1px solid #1a1a22}
.sector-table tr:hover{background:#1e1e2e}
.up{color:#22c55e}
.down{color:#ef4444}
.flat{color:#888}
.bar{display:inline-block;height:4px;border-radius:2px;vertical-align:middle;margin-right:6px}

/* Chart */
.chart-container{background:#1a1a26;border:1px solid #26263a;border-radius:10px;
  padding:16px;margin-bottom:20px;overflow-x:auto}
.chart-container h3{font-size:14px;color:#aaa;margin-bottom:12px}
canvas{display:block;width:100%;height:240px}

/* Status indicator */
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.dot-green{background:#22c55e}
.dot-red{background:#ef4444}
.dot-yellow{background:#f59e0b}

/* Scrollbar */
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:#0f0f13}
::-webkit-scrollbar-thumb{background:#2a2a3a;border-radius:3px}

/* Moneyflow Tables */
.mf-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}
@media(max-width:768px){.mf-grid{grid-template-columns:1fr}}
.mf-table{width:100%;border-collapse:collapse;font-size:12px}
.mf-table th{text-align:left;padding:6px 8px;border-bottom:1px solid #26263a;color:#888;font-weight:500;font-size:11px}
.mf-table td{padding:6px 8px;border-bottom:1px solid #1a1a22;font-size:12px}
.inflow{color:#22c55e}
.outflow{color:#ef4444}

/* Progress Bar */
.status-bar{display:flex;align-items:center;gap:12px;padding:8px 0;margin-bottom:12px;min-height:32px}
.status-msg{font-size:13px;color:#888;flex-shrink:0}
.progress-track{flex:1;height:4px;background:#1e1e2e;border-radius:2px;overflow:hidden}
.progress-fill{height:100%;background:linear-gradient(90deg,#6366f1,#818cf8);border-radius:2px;transition:width .5s ease}
.progress-fill.done{background:linear-gradient(90deg,#22c55e,#4ade80)}
.progress-fill.error{background:linear-gradient(90deg,#ef4444,#f87171)}

/* AI Commentary */
.ai-card{background:linear-gradient(135deg,#1a1a26,#1a1a2e);border:1px solid #2a2a4a;border-radius:10px;
  padding:16px;margin-bottom:20px}
.ai-card .ai-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.ai-card .ai-label{font-size:13px;color:#818cf8;font-weight:600}
.ai-card .ai-time{font-size:11px;color:#666}
.ai-card .ai-body{font-size:13px;line-height:1.7;color:#d0d0d0;white-space:pre-wrap}
.ai-card .ai-empty{color:#555;font-size:13px;font-style:italic}
.ai-card .ai-btn{background:#2a2a4a;border:1px solid #3a3a5a;color:#818cf8;padding:4px 12px;
  border-radius:4px;cursor:pointer;font-size:12px}
.ai-card .ai-btn:hover{background:#3a3a5a}
.ai-card .ai-btn.loading{opacity:.5;pointer-events:none}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1>📊 全市场资金趋势</h1>
      <div class="ts" id="tsLabel">加载中…</div>
    </div>
    <div>
      <button class="refresh-btn" id="scanBtn" onclick="triggerScan()">🔄 重新扫描</button>
    </div>
  </div>

  <!-- 状态进度条 -->
  <div class="status-bar" id="statusBar" style="display:none">
    <span class="status-msg" id="statusMsg">加载中…</span>
    <div class="progress-track"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
  </div>

  <!-- 概览卡片 -->
  <div class="grid" id="overviewGrid"></div>

  <!-- 板块趋势图 -->
  <div class="chart-container">
    <h3>板块涨幅分布</h3>
    <canvas id="sectorChart"></canvas>
  </div>

  <!-- 板块排名 -->
  <div class="chart-container">
    <h3>板块排名 <span style="font-weight:400;color:#666;font-size:12px">按涨跌幅排序</span></h3>
    <table class="sector-table" id="sectorTable">
      <thead>
        <tr>
          <th>#</th>
          <th>板块</th>
          <th style="text-align:right">涨跌幅</th>
          <th style="text-align:right">上涨/总数</th>
          <th style="text-align:right">成交额(万)</th>
          <th style="text-align:right">趋势</th>
        </tr>
      </thead>
      <tbody id="sectorBody"></tbody>
    </table>
  </div>

  <!-- 资金流向 -->
  <div class="chart-container">
    <h3>主力资金流向 <span style="font-weight:400;color:#666;font-size:12px">东方财富数据</span></h3>
    <div class="mf-grid">
      <div>
        <h4 style="color:#22c55e;font-size:13px;margin-bottom:8px">净流入 TOP</h4>
        <table class="mf-table" id="inflowTable">
          <thead><tr><th>板块</th><th style="text-align:right">主力净流入</th><th style="text-align:right">净占比</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
      <div>
        <h4 style="color:#ef4444;font-size:13px;margin-bottom:8px">净流出 TOP</h4>
        <table class="mf-table" id="outflowTable">
          <thead><tr><th>板块</th><th style="text-align:right">主力净流出</th><th style="text-align:right">净占比</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- AI 点评 -->
  <div class="ai-card">
    <div class="ai-header">
      <div class="ai-label">AI 市场研判</div>
      <div>
        <span class="ai-time" id="aiTime"></span>
        <button class="ai-btn" id="aiBtn" onclick="fetchAI()">生成点评</button>
      </div>
    </div>
    <div class="ai-empty" id="aiBody">点击"生成点评"获取 DeepSeek 市场分析</div>
  </div>
</div>

<script>
const API = '';

async function fetchJSON(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const r = await fetch(API + url, { signal: controller.signal });
    clearTimeout(timer);
    return r.json();
  } catch (e) {
    clearTimeout(timer);
    return null;
  }
}

// 渲染概览卡片
function renderOverview(data) {
  const grid = document.getElementById('overviewGrid');
  if (!data) {
    grid.innerHTML = '<div class="card"><div class="label">暂无数据</div><div class="value" style="font-size:14px;color:#888">首次扫描中，请稍候...</div></div>';
    return;
  }
  const s = data.sentiment;
  const dot = s === '强势' ? 'dot-green' : s === '弱势' ? 'dot-red' : 'dot-yellow';
  grid.innerHTML = `
    <div class="card">
      <div class="label">大盘风向</div>
      <div class="value"><span class="status-dot ${dot}"></span>${s}</div>
      <div class="sub">上涨${data.up} / 下跌${data.down}</div>
    </div>
    <div class="card">
      <div class="label">上涨占比</div>
      <div class="value ${data.up_ratio > 60 ? 'up' : data.up_ratio < 30 ? 'down' : 'flat'}">${data.up_ratio}%</div>
      <div class="sub">共 ${data.total} 只交易</div>
    </div>
    <div class="card">
      <div class="label">成交额</div>
      <div class="value" style="color:#60a5fa">${Number(data.total_amount).toLocaleString()}</div>
      <div class="sub">亿元</div>
    </div>
    <div class="card">
      <div class="label">上次扫描</div>
      <div class="value" style="font-size:16px;font-weight:400;color:#aaa">${data.ts ? data.ts.slice(11,16) : '--'}</div>
      <div class="sub">${data.ts ? data.ts.slice(0,10) : ''}</div>
    </div>
  `;
  document.getElementById('tsLabel').textContent = `最后更新: ${data.ts || '--'}`;
}

// 渲染板块排名
function renderSectors(sectors) {
  const tbody = document.getElementById('sectorBody');
  if (!sectors.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#666;padding:20px">暂无板块数据</td></tr>';
    return;
  }
  const sorted = [...sectors].sort((a,b) => b.avg_change - a.avg_change);
  tbody.innerHTML = sorted.map((s,i) => {
    const cls = s.avg_change > 0.5 ? 'up' : s.avg_change < -0.5 ? 'down' : 'flat';
    const arrow = s.avg_change > 0 ? '↑' : s.avg_change < 0 ? '↓' : '→';
    const pct = s.avg_change > 0 ? '+' + s.avg_change.toFixed(1) : s.avg_change.toFixed(1);
    const barW = Math.min(Math.abs(s.avg_change) * 8, 80);
    const barCol = s.avg_change > 0 ? '#22c55e' : s.avg_change < 0 ? '#ef4444' : '#555';
    const ratio = s.up_count + s.down_count > 0 ? (s.up_count / (s.up_count + s.down_count) * 100).toFixed(0) : '--';
    return `<tr>
      <td style="color:#555">${i+1}</td>
      <td><strong>${s.sector_name}</strong></td>
      <td class="${cls}" style="text-align:right">
        <span class="bar" style="width:${barW}px;background:${barCol}"></span>${arrow} ${pct}%
      </td>
      <td style="text-align:right;color:#888">${s.up_count}/${s.stock_count}</td>
      <td style="text-align:right;color:#aaa">${Number(s.total_amount).toLocaleString()}</td>
      <td style="text-align:right;color:#888">${ratio}%</td>
    </tr>`;
  }).join('');
}

// 绘制板块分布图
function renderChart(sectors) {
  const canvas = document.getElementById('sectorChart');
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = (rect.width - 32) * dpr;
  canvas.height = 240 * dpr;
  canvas.style.width = (rect.width - 32) + 'px';
  canvas.style.height = '240px';
  ctx.scale(dpr, dpr);
  const W = canvas.width / dpr, H = canvas.height / dpr;

  ctx.clearRect(0, 0, W, H);

  if (!sectors.length) {
    ctx.fillStyle = '#555';
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('暂无数据', W/2, H/2);
    return;
  }

  // 取前15个板块绘制水平柱状图
  const sorted = [...sectors].sort((a,b) => b.avg_change - a.avg_change);
  const top = sorted.slice(0, 15);
  const maxAbs = Math.max(Math.abs(top[0]?.avg_change || 1), Math.abs(top[top.length-1]?.avg_change || 1), 1);

  const barH = Math.min((H - 40) / top.length, 20);
  const padLeft = 80;
  const padRight = 20;
  const chartW = W - padLeft - padRight;
  const midX = padLeft + chartW / 2;
  const scale = (chartW / 2) / maxAbs * 0.9;

  top.forEach((s, i) => {
    const y = 20 + i * (barH + 6);
    const w = Math.abs(s.avg_change) * scale;
    const x0 = s.avg_change >= 0 ? midX : midX - w;

    ctx.fillStyle = s.avg_change >= 0 ? '#22c55e' : '#ef4444';
    ctx.fillRect(x0, y, w, barH);

    ctx.fillStyle = '#aaa';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(s.sector_name, padLeft - 8, y + barH/2 + 4);

    ctx.textAlign = 'left';
    const val = (s.avg_change > 0 ? '+' : '') + s.avg_change.toFixed(1) + '%';
    ctx.fillStyle = s.avg_change >= 0 ? '#22c55e' : '#ef4444';
    ctx.fillText(val, midX + (s.avg_change >= 0 ? w + 4 : -w - 50), y + barH/2 + 4);
  });

  // 中轴线
  ctx.strokeStyle = '#333';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(midX, 10);
  ctx.lineTo(midX, 20 + top.length * (barH + 6));
  ctx.stroke();
}


// 渲染资金流向
function renderMoneyflow(sectors) {
  if (!sectors || !sectors.length) return;
  const inflow = [...sectors].filter(s => s.main_net_inflow > 0).slice(0, 8);
  const outflow = [...sectors].filter(s => s.main_net_inflow < 0).slice(-8).reverse();
  document.querySelector('#inflowTable tbody').innerHTML = inflow.length
    ? inflow.map(s => '<tr><td>'+s.sector_name+'</td><td class=inflow style=text-align:right>+'+s.main_net_inflow.toFixed(1)+'亿</td><td style=text-align:right;color:#888>'+s.main_net_ratio.toFixed(1)+'%</td></tr>').join('')
    : '<tr><td colspan=3 style=text-align:center;color:#555>暂无净流入</td></tr>';
  document.querySelector('#outflowTable tbody').innerHTML = outflow.length
    ? outflow.map(s => '<tr><td>'+s.sector_name+'</td><td class=outflow style=text-align:right>'+s.main_net_inflow.toFixed(1)+'亿</td><td style=text-align:right;color:#888>'+s.main_net_ratio.toFixed(1)+'%</td></tr>').join('')
    : '<tr><td colspan=3 style=text-align:center;color:#555>暂无净流出</td></tr>';
}

// AI 点评（调用服务器端 /api/ai，key 不暴露）
let aiLoading = false;
async function fetchAI() {
  if (aiLoading) return;
  aiLoading = true;
  const btn = document.getElementById("aiBtn");
  const body = document.getElementById("aiBody");
  btn.textContent = "分析中…";
  btn.classList.add("loading");
  body.className = "ai-body";
  body.textContent = "正在分析市场数据…";
  const res = await fetchJSON("/api/ai");
  if (res && res.text) {
    body.textContent = res.text;
    document.getElementById("aiTime").textContent = res.ts ? res.ts.slice(11,19) : "";
  } else {
    body.textContent = "暂无数据，请先执行扫描。";
  }
  aiLoading = false;
  btn.textContent = "刷新点评";
  btn.classList.remove("loading");
}

// 触发扫描
let scanning = false;
async function triggerScan() {
  if (scanning) return;
  scanning = true;
  const btn = document.getElementById('scanBtn');
  btn.textContent = '⏳ 扫描中…';
  btn.classList.add('scanning');
  // 记录扫描前的时间戳
  const before = await fetchJSON('/api/overview');
  const beforeTs = before ? before.ts : null;
  await fetchJSON('/api/scan');
  // 轮询等待扫描完成（时间戳变化）
  let waited = 0;
  const poll = setInterval(async () => {
    waited += 5;
    const d = await fetchJSON('/api/overview');
    const done = d && d.ts && d.ts !== beforeTs;
    if (done || waited > 180) {
      clearInterval(poll);
      scanning = false;
      btn.textContent = '🔄 重新扫描';
      btn.classList.remove('scanning');
      if (done) refreshAll();
    }
  }, 5000);
}

// 主动刷新，不过度频繁
let autoTimer;
let sectorsCache = null;

async function refreshAll() {
  const [overview, sectors, moneyflow] = await Promise.all([
    fetchJSON('/api/overview'),
    fetchJSON('/api/sectors'),
    fetchJSON('/api/moneyflow/sectors')
  ]);
  sectorsCache = sectors;
  renderOverview(overview);
  renderSectors(sectors);
  renderChart(sectors);
  renderMoneyflow(moneyflow);
}

// 状态轮询
async function pollStatus() {
  const s = await fetchJSON('/api/status');
  if (!s) return;
  const bar = document.getElementById('statusBar');
  const msg = document.getElementById('statusMsg');
  const fill = document.getElementById('progressFill');
  if (s.state === 'idle') {
    bar.style.display = 'none';
    return;
  }
  bar.style.display = 'flex';
  msg.textContent = s.msg || '';
  fill.style.width = s.pct + '%';
  fill.className = 'progress-fill' + (s.state === 'done' ? ' done' : '') + (s.state === 'error' ? ' error' : '');
}

// 初始化 + 自动刷新
refreshAll();
autoTimer = setInterval(refreshAll, 10000);
setInterval(pollStatus, 2000);

// 窗口大小变化时重绘图表
window.addEventListener('resize', () => { if (sectorsCache) renderChart(sectorsCache); });
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="市场监控 Web 仪表盘")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--scan-interval", type=int, default=120, help="全量扫描间隔(秒)")
    args = parser.parse_args()

    store.init_db()

    # 确保股票池就绪
    m.ensure_stock_list()

    # 后台初始扫描（不阻塞服务器启动）
    def initial_scan():
        print("正在执行初始扫描...")
        try:
            m.run_scan_and_report(no_notify=True)
            print("初始扫描完成")
        except Exception as e:
            print(f"初始扫描异常: {e}")

    threading.Thread(target=initial_scan, daemon=True).start()

    # 双循环自动扫描
    def auto_scan_loop():
        last_full = 0
        while True:
            time.sleep(30)
            if not m.is_trading_time():
                continue
            now = time.time()
            try:
                # 快循环：热点池
                set_status("scanning", "更新热点池(300只)...", 10)
                with _scan_lock:
                    hot = fetcher.fetch_top_stocks_by_amount(300)
                    if hot:
                        print(f"[快] 热点池 {len(hot)}只 更新", flush=True)
                # 慢循环：全量
                if now - last_full >= args.scan_interval:
                    last_full = now
                    set_status("scanning", "全量扫描(5000只+资金流向)...", 30)
                    with _scan_lock:
                        m.run_scan_and_report(no_notify=True)
                    set_status("done", "数据已更新", 100)
                    def reset():
                        time.sleep(5)
                        if _scan_status["state"] == "done":
                            set_status("idle", "", 0)
                    threading.Thread(target=reset, daemon=True).start()
            except Exception as e:
                print(f"[Scan] {e}", flush=True)

    threading.Thread(target=auto_scan_loop, daemon=True).start()

    server = HTTPServer(("0.0.0.0", args.port), DashboardHandler)
    print(f"")
    print(f"  🌐 全市场资金趋势仪表盘")
    print(f"  ─────────────────────────")
    print(f"  地址: http://localhost:{args.port}")
    print(f"  快循环: 30秒(热点池) | 慢循环: 每{args.scan_interval}秒(全量)")
    print(f"  按 Ctrl+C 停止")
    print(f"")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")


if __name__ == "__main__":
    main()
