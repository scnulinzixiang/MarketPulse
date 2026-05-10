# MarketPulse

全市场A股资金趋势监控系统。盘中自动扫描5000+只股票，识别板块资金流向和市场趋势，通过 Web 仪表盘呈现。

## 功能
- **全市场扫描** — 通过 Sina + Tencent API 定时拉取全量A股实时行情
- **板块分类** — 名称关键词 + 已知股票映射，覆盖 31 个行业板块
- **趋势分析** — 板块涨跌幅排名、热点/弱势板块识别、轮动信号检测
- **市场广度** — 涨跌比、成交额、涨停/跌停计数
- **资金流向** — 东方财富板块+个股主力资金净流入/流出追踪
- **数据持久化** — SQLite 存储历史快照，支持日线对比
- **Web 仪表盘** — 深色主题网页，含概览卡片、板块排名表、分布柱状图、30秒自动刷新
- **macOS 桌面启动** — 启动台 `.app` + 桌面 `.command` 一键启动

## 数据源
| 接口 | 用途 |
|------|------|
| Sina Market Center | A股代码全量列表（分页） |
| Tencent qt.gtimg.cn | 实时行情（批量查询） |
| Tencent ifzq | 日K线成交量 |
| 东方财富 push2 | 板块/个股主力资金流向 |

## 启动方式
```bash
./launch.sh --web      # Web 仪表盘（默认）
./launch.sh --once     # 单次扫描
./launch.sh --daily    # 收盘总结
./launch.sh --monitor  # 盘中持续监控
```

或双击桌面 `MarketPulse.command` / 启动台 `MarketPulse.app`。

## 数据字段

### 板块资金流向（`fetch_sector_moneyflow`）
| 字段 | 说明 | 来源 | 单位 |
|------|------|------|------|
| sector_name | 行业板块名称 | f14 | -- |
| main_net_inflow | 主力净流入 | f62 | 亿元 |
| main_net_ratio | 主力净占比 | f184 | % |
| big_net_inflow | 大单净流入 | f66 | 亿元 |
| mid_net_inflow | 中单净流入 | f72 | 亿元 |
| small_net_inflow | 小单净流入 | f78 | 亿元 |

主力=超大单+大单。正值为净流入(看多)，负值为净流出(看空)。

### 个股资金流向（`fetch_stock_moneyflow_top`）
| 字段 | 说明 | 来源 | 单位 |
|------|------|------|------|
| code | 股票代码 | f12 | -- |
| name | 股票名称 | f14 | -- |
| price | 最新价 | f2 | 元 |
| main_net_inflow | 主力净流入 | f62 | 亿元 |
| main_net_ratio | 主力净占比 | f184 | % |

## 规划中
- AI 智能点评 -- 集成 DeepSeek / Claude API，自动生成板块异动解读
- 个股异动报警 -- 短线急涨急跌、放量异动检测
- 趋势强度评分 -- 多周期（1日/3日/5日/20日）综合打分
- 风格切换检测 -- 大盘/小盘、成长/价值轮动识别
- 资金流向看板 -- Web 仪表盘集成资金流向热力图和排行榜
- WebUI 交互式对话 -- 在仪表盘内提问，AI 回答市场问题
- K线/走势图 -- 板块历史走势折线图
- 自定义自选股组 -- 在页面上添加自定义监控组
- 推送通知 -- 板块异动时主动推送到桌面/手机
- 扫描性能优化 -- 从 ~3 分钟缩短至 ~30 秒
- Docker 化部署支持

## 更新记录

### v0.1.1 -- 2026-05-10
新增东方财富资金流向追踪：
- fetcher.py: 新增 _request_with_retry、fetch_sector_moneyflow()、fetch_stock_moneyflow_top()
- main.py: run_scan_and_report 集成资金流向采集，结果存入 moneyflow_sectors / moneyflow_stocks
- README.md: 补充资金流向说明和数据字段文档

### v0.1 -- 2026-05-08
- 初始版本发布
- 基于 Sina + Tencent API 的全市场实时扫描
- 板块分类、趋势分析、市场广度计算
- SQLite 持久化、Web 仪表盘、macOS 桌面启动
