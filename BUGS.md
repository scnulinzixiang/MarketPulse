# MarketPulse 已知问题清单

## 严重 Bug（必须修复）

### ~~1. 换手率数值错误（10000倍放大）~~ ✅ 已修复
- **文件**: `fetcher.py` 第 226 行
- ~~**现状**: `turnover_rate = round(volume * 100 / shares * 100, 2)` — 乘了两次 100~~
- **修复**: `turnover_rate = round(volume * 100 / shares, 2)` （已应用）

### ~~2. 成交额数据静默丢失~~ ✅ 已修复
- **文件**: `fetcher.py` 第 217 行
- ~~**现状**: `fields.get(7, "0")` — fields 是列表，不能调用 `.get()`，触发 AttributeError 后整行数据被丢弃~~
- **修复**: `fields[7] if len(fields) > 7 else "0"` （已应用）

### 3. 资金流向单位待验证 / 注释已修正
- **文件**: `fetcher.py` 第 339-343 行
- **现状**: 东方财富 f62/f66/f72/f78 字段除以 1e8 转亿元，单位是否正确待确认
- ~~**现状**: f72 注释写"大单"但实际是中单~~ ✅ 注释已修正为"中单净流入(亿)"
- **待办**: 对照东方财富网页实际数字验证各字段单位

## 设计问题（影响数据质量）

### 4. 板块分类系统混乱
- 存在 4 套并行分类方案，不同模块使用不同方案，导致同一股票分类结果不一致
- 涉及函数：`fetch_all_stock_industries()`、`_fetch_industries_fallback()`、`fetch_stock_industries_v2()`、`populate_stock_industries_locally()`
- 需要重写分类模块，统一使用一套规则

## 性能问题

### 5. 全量扫描缓慢
- 5000+ 只股票轮询，并发度不足导致刷新慢
- 用户反馈：数据更新明显滞后
