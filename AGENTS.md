# AGENTS.md — dragon-quant

> 龙头战法量化筛选系统 · AI Agent 操作手册

---

## 这是什么

一套纯 Python 3 的 A 股龙头筛选系统。从当日涨停榜出发，评估涨停股的龙头质量并加权排名输出；同时支持日志查询、SQLite 持久化、龙头回测与 Web UI 可视化。

系统当前使用**五维「识别真龙」评分体系**，由 `scan` 命令触发：

- **五维「识别真龙」**：带动性 30% / 领涨性 25% / 抗跌性 15% / 流动性 20% / 资金承接 10%，**门槛+加权两段式聚合**（四大特征任一低于门槛即一票否决，资金承接不否决仅加权贡献）。详见 [评分规范](dragon_quant/scorers/评分器Refactor.md)。

> 为兼容历史数据，SQLite 物理表继续沿用 `*_v2`（如 `dragons_v2` / `scans_v2`），`scan_id` 继续使用 `v2_YYYYMMDD_topN`。`scan_v2` 命令保留为隐藏兼容别名，行为等同 `scan`；旧 `*_v1` 表不再由主流程写入，仅可通过显式 `--source v1` 查询历史记录。

> **v1 已废弃**：所有龙头/扫描/候选池相关逻辑只读 `dragons_v2`。`--source v1` 仅保留用于查询历史数据，**不再新增任何 v1 写入、分支或功能**；新命令（如 `auction`）一律固定 `source="v2"`。

数据源：**通达信 TDX（首选，`tmdx` 包，无 Cookie 无反爬）+ 同花顺（板块，回退）+ 雪球（个股 K 线/分时，回退）+ 腾讯（批量行情/收盘盘口，回退）**，不依赖任何付费行情接口。tdx 链路失败时自动回退老链路。

> **板块口径为「行业 + 概念 + 地区」三维融合总榜**（TDX `HY`/`GN`/`DQ`，按涨幅排序取前 8；THS 回退同样支持三类），**领跌 Top20 仍按行业**（资金承接「对手盘」语义不变）。板块身份为 `(source, category, code)`，缓存键按 `source:category:code` 命名空间隔离。

---

## 快速使用

```bash
cd ~/repo/dragon-quant

# 查看 Linux 风格帮助提示
python -m dragon_quant -h
python -m dragon_quant scan -h

# 批量扫描（五维「识别真龙」评分器）
python -m dragon_quant
python -m dragon_quant scan --top 25 --candidates 5 --workers 2

# 强制执行（跳过交易时段拦截 + DB 缓存）
python -m dragon_quant scan --force

# 板块黑名单管理（拉取领涨/领跌板块时过滤）
python -m dragon_quant blacklist list
python -m dragon_quant blacklist add "次新股"
python -m dragon_quant blacklist remove "次新股"

# 持久化数据管理
python -m dragon_quant storage status        # 查看存储状态
python -m dragon_quant storage size          # 磁盘占用
python -m dragon_quant storage clear --all   # 清理全部

# 回测 / 查看 UI（默认读取 dragons_v2）
python -m dragon_quant review --date 20260519
python -m dragon_quant review --ui-only

# 导出扫描物化的龙头（JSON，默认最新有数据的交易日）
python -m dragon_quant dragons
python -m dragon_quant dragons --date 20260519 --true-only

# 盘后复盘：采集原始数据资产(assets.db) + 计算主线/情绪温度/候选池快照（review-account 消费）
python -m dragon_quant prep
python -m dragon_quant prep --date 20260519
python -m dragon_quant prep --date 20260519 --recompute   # 用已存资产重算（算法升级重放）
python -m dragon_quant prep --date 20260519 --force       # 强制重采全部可采资产（不绕过 16:40 门禁）
python -m dragon_quant prep --json                        # 输出完整复盘 JSON（默认 4 行市场摘要 + 标的清单）

# 采集 T-1 prep 推荐标的（strong/light）的当日集合竞价盘口 + 行情，落原始资产并计算快照
python -m dragon_quant auction
# 推荐集为空/快照缺失时自动回退「最近 3 个有龙头数据的交易日」并集；--days 仅回退路径生效
python -m dragon_quant auction --days 3
# 强制旧口径（最近 N 日 dragons 并集）
python -m dragon_quant auction --pool legacy
# 输出完整 JSON（默认只展示参与竞价判断的标的强弱与汇总）
python -m dragon_quant auction --json
# 用已存原始资产重算某日竞价快照（不触网）
python -m dragon_quant auction --recompute 20260519

# 数据资产云端同步（门槛 A：R2/B2 连通性与元数据回环）
python -m dragon_quant assets doctor --profile r2 --samples 50
```

### 前置条件

板块数据用**同花顺**，**无需 Cookie**（HTTP GET + GBK 直取，无 Playwright/反爬）。个股数据依赖雪球，需配雪球 Cookie：

```bash
# 查看状态
python -c "from dragon_quant.providers.cookie import get_xq; print(f'雪球: {bool(get_xq())}')"

# 手动设置雪球 Cookie（推荐）
python -m dragon_quant.providers.cookie set --cookie "xq_a_token=...; xq_is_login=1; u=..." --source xq

# 自动获取（需要 playwright；首次运行自动下载 chromium 内核约 150MB，走国内镜像）
python -m dragon_quant.providers.cookie fetch --source xq
```

Cookie 文件位置（随平台而定，可用 `DQ_DATA_DIR` 覆盖）：
- macOS：`~/Library/Application Support/dragon-quant/cookies/xueqiu`
- Windows：`%APPDATA%\dragon-quant\cookies\xueqiu`

---

## 架构总览

```
dragon_quant/
├── __init__.py / __main__.py    # 入口
├── cli.py                       # argparse CLI 门面（解析树 + main() + 共享 helper；scan_v2 为隐藏兼容别名）
├── cli_commands.py              # 各子命令 `_cmd_*` 处理函数（经转发包装引用 cli 的 patch 目标）
├── orchestrator.py              # 编排主流程 (Phase A→F)，固定五维评分
├── data.py                      # 原子数据查询 API
├── rate_limit.py                # 分组并发调度器
│
├── providers/                   # 数据源适配层
│   ├── base.py                  # StockProvider ABC + 板块 K 线方法
│   ├── tdx.py                   # 通达信 TDX（首选）— 板块/成分股/K线/分时/五档行情，无 Cookie 无反爬
│   ├── ths.py                   # 同花顺（回退）— 行业/概念/地区排行(HTTP)/成分股(HTML)/板块1分K/历史5分K
│   ├── xueqiu.py                # 雪球（回退）— 个股日K/分时，需 Cookie
│   ├── tencent.py               # 腾讯（回退）— 零认证，批量行情 + 收盘盘口(bid1)；宏观 quote 方法保留（当前规格未路由）
│   ├── macro.py                 # 宏观扰动门面 — MACRO_SPECS/collect_macro + TDX 扩展行情主链路 + Yahoo 回退（仅美债/黄金）
│   └── cookie.py                # Cookie 管理 + CLI
│
├── scorers/                  # 五维「识别真龙」评分器
│   ├── base.py                  # DragonVerdict + 1分K对齐/归一化涨幅/排名分位工具
│   ├── registry.py              # 全部权重/门槛/阈值常量（集中调参）
│   ├── drive.py                 # 带动性 30%（封板最早/带动板块脉冲检测/板块共鸣）
│   ├── leadership.py            # 领涨性 25%（连板最多/5日涨幅板块内分位）
│   ├── anti_drop.py             # 抗跌性 15%（大盘+板块双基准横盘稳住/率先起飞）
│   ├── liquidity.py             # 流动性 20%（换手充沛度/封板质量，一字不罚）
│   ├── absorption.py            # 资金承接 10%（跨板块虹吸，回看10交易日）
│   └── aggregator.py            # 门槛+加权聚合 → DragonVerdict
│
├── auction/                     # 集合竞价强度评分器（纯函数、无 I/O；四维加权 + 否决闸门）
│   ├── registry.py              # 权重/阈值/窗口常量（集中调参）
│   ├── strength.py              # evaluate / rank_auctions
│   ├── serialize.py             # Quote/AuctionVerdict → JSON 原生 dict（NaN/inf 清洗）
│   └── types.py                 # AuctionVerdict
│
├── vpa/                         # 量价分析（独立模块，插件式因子 FACTORS）
│   ├── engine.py / types.py / report.py
│   └── factors/                 # vol_amount / trend_verify / breakout / divergence
│
├── cache/data_cache.py          # 内存+本地双重缓存
├── logging/                     # 结构化日志 + 自然语言报告
│   ├── logger.py                # ScanLogger
│   └── reporter.py              # ReportBuilder（五维报告）
├── storage/                     # 统一持久化
│   ├── paths.py / manager.py
│   ├── db.py                    # SQLite 门面 + 基础设施（_connect/_ensure_schema/_tables/init_db）
│   ├── _base.py                 # 转发包装，令 `db._connect` 的 patch 传递到领域模块
│   ├── blacklist.py / scans.py / dragons.py / logs.py
│   ├── assets_db.py             # 数据资产独立库 assets.db（prep/auction 原始资产 + 算法快照）
│   └── review_account.py / signals.py
├── utils/trading.py            # 交易日历 + 涨停判断 + 买入日定位
├── review.py                    # 龙头回测验证
├── review_account/              # 共享策略/事件引擎/成交/数据/评估/盘后复盘（strategy/engine/execution/data/market/prep/evaluation）
│   ├── prep.py / advice.py      # 两段式复盘 + 4 行市场摘要/逐只清单
│   └── verdicts.py              # 逐只参与判定规则链（facts → strong/light/ignore/unrated）
├── live_trade/                  # 实盘辅助交易（buy/sell；复用 review_account 策略，纯信号记账）
│   ├── signal_engine.py         # SignalEngine：无账户/资金/仓位限制，buy-all + 峰值跟踪 + 影子风控
│   ├── shadow.py                # 影子状态：从 buy_sell_signals as-of 重建 AccountState（复用 risk_governor）
│   ├── trader.py                # LiveTrader.buy/sell（回放当日事件流 → 信号入库）
│   └── service.py               # run_buy/run_sell（策略装配 + 中文输出）
├── assets_sync/                 # 数据资产云端同步（原始资产备份/恢复）
│   ├── codec.py / models.py     # 规范化 JSON / gzip / 哈希 / manifest
│   ├── credentials.py           # profile + 凭据（环境变量 → 0600 文件）
│   ├── doctor.py                # 门槛 A：连通性与元数据回环
│   └── backends/                # S3Backend（R2/B2，SigV4）+ LocalDirBackend（测试）
├── web_ui/                      # 回测结果 Web UI（Vite+React+TS+Mantine / stdlib HTTPServer）
└── models/types.py             # dataclass 数据模型
```

---

## 执行流程

编排器 `orchestrator.scan()` 分 6 个阶段：

| Phase | 做什么 |
|-------|--------|
| **A** 板块排行 | TDX/THS 三维板块榜（行业/概念/地区）融合取 Top8 + **行业**领跌 Top20，过滤 DB 黑名单；逐维度 TDX→THS 回退，单维度失败标 `degraded`、不中断扫描 |
| **B** 候选筛选 | 每个入选板块取当日所有涨停股(pct≥9.9)，过滤 ST+双创+北交所；跨板块同 code 去重，`primary_sector` = 总榜排名最高板块 |
| **C** 连板+排序 | 雪球日K 算连板天数，写 `Candidate.fived_pct`，按(连板,概念数)降序，候选池全部评分 |
| **D** 并发加载 | 板块历史10日5分K + 板块当日1分K + 大盘1分K + 全候选1分K + 腾讯批量行情 |
| **E** 打分 | `_score_one` 调 `scorers.aggregator.evaluate()`，五维门槛+加权 → DragonVerdict |
| **F** 输出+持久化 | 所有候选保留诊断分与明细，仅通过者排名；Top N 真龙报告/入库 + 5日去重，保留原始排名 |

总耗时约 40-80 秒（取决于网络、并发数、v2 拉取量更大）。

---

## 数据模型

核心类型全部在 `models/types.py`：

- **KBar** — 一根 K 线（timestamp, OHLCV, 涨跌幅, 换手率, 成交额）
- **StockInfo** — 股票基本信息 + 当日快照（含 `five_day_return`）
- **Quote** — 实时行情快照（现价/涨跌幅/换手率/市值/PE/量比…）+ **收盘盘口 `bid1_price`/`bid1_volume`/`ask1_volume`**（gtimg f[9]/f[10]/f[20]，单位手）
- **SectorPerformance** — 板块行情（代码/名称/涨跌幅/振幅）
- **Candidate** — 候选股（code, concepts, board_count, **fived_pct**, primary_sector, score）
- **ScoreResult** — 单维度评分结果（dim, score 0-100, weight, details）
- **DragonVerdict**（`scorers/base.py`）— 五维聚合产物（is_true_dragon, composite, rank, dims, reject_reason）

---

## 并发模型

`RateLimiter` 核心规则：
```
同一 provider（按 provider 名分组）→ 串行排队 + 随机延迟（防封 IP）
不同 provider 之间 → 自由并发
```
用法：`limiter.submit("ths", "ths", fn)` 之后 `limiter.wait_all()`。

---

## 反爬要点

### 同花顺（板块主数据源，无需 Cookie）
- **板块排行**：行业 `data.10jqka.com.cn/funds/hyzjl/...`、概念 `.../gnzjl/...`（同表结构），地区 `q.10jqka.com.cn/dy/`（单页，td[2]=涨跌幅）；HTTP GET + GBK 直取。
  - **铁律**：字段必须用 `zdf`（涨跌幅）。旧 `tradezdf` 是资金流字段，**无视 order/page**，永远返回固定 50 行资金流入板块（曾导致领跌榜全是正值的 bug）。
  - 单页 DOM **非严格有序**，必须抓多页后本地按 pct 排序。网关有 **403 频控**，已加退避重试 + 页间延迟。
- **成分股**：`q.10jqka.com.cn/thshy/detail/code/{881xxx}/`（GBK HTML 表格，列 td[1]=code/td[2]=name/td[4]=涨跌幅），翻页走非 ajax `/thshy/detail/order/desc/page/{p}/code/{code}/`。
- **板块当日1分K**：`d.10jqka.com.cn/v6/time/48_{inner}/last.js`（JSONP，原始1分，不聚合）。
- **板块历史5分K**：`d.10jqka.com.cn/v6/line/48_{inner}/30/last1000.js`（JSONP，**周期码 30=5分**，真实 OHLC）。
- innerCode：行业板块 `clid` 即 code 本身（881xxx）；概念板块为 885xxx 映射；地区板块 `clid` 即 code（882xxx）。统一解析对应维度详情页 `<input id="clid">`，进程内缓存键含维度。

### 雪球（个股，需 Cookie）
- `minute.json`（当日1分K）/ `kline.json`（日K）/ `quote.json`。Referer：`https://xueqiu.com/S/{SH/SZ}{code}`。
- 注意：`pankou.json` 盘后返回空体，**已弃用**，封单改用腾讯 gtimg 收盘盘口。

### 腾讯（零认证）
- `qt.gtimg.cn/q=` 批量行情（GBK）。封单量取 `f[10]`（买一量，手），与成交量 `f[36]`（手）同源同单位。

当前 Chrome UA：同花顺 120 / 雪球·腾讯 147。大面积失效时更新版本号即可。

---

## SQLite 表结构（`storage/db.py`）

| 表 | 用途 | 关键点 |
|----|------|--------|
| `scans_v2` | 每轮扫描元信息 | 含 `raw_output` 完整结果 JSON；`scan` 固定读写 v2 |
| `scan_stocks_v2` | 每轮全部评分结果 | scan_id 关联；填充 `dim_liquidity` / `is_true_dragon` / `reject_reason` |
| `dragons_v2` | 入选龙头（最终物化）| `UNIQUE(trade_date, code)`；`version`=包版本号；review 字段写回此表 |
| `scan_logs_v2` | 结构化日志 | `logs` 默认查询 v2 |
| `*_v1` | 历史旧表 | 不再由主流程写入，仅显式 `--source v1` 查询 |
| `vpa_analysis` | 量价分析 | 独立表，不复用 dragons |
| `sector_blacklist` | 板块黑名单 | 行业切换后默认种子为空 |
| `review_account_runs` | 账户级 review 批次 | 保存 UI 记录名称、策略参数、区间、初始资金、最终权益、收益率、最大回撤 |
| `review_account_snapshots` | 账户每日快照 | 保存现金、市值、总权益、收益曲线、当前持仓 |
| `review_account_trades` | 账户交割单 | 每笔买卖含 `reason_code` / `reason_text` / `signal_json` |
| `review_account_positions` | 已平仓持仓 | 保存买入/卖出价、退出原因、持有天数、实现收益；`post_exit_max_return` / `post_exit_max_drawdown` / `post_exit_window_days` 为清仓后 5 个交易日的窗口指标（事后评估，不参与交易决策；窗口可越出回测区间；旧记录三列为 NULL，不回溯重算） |
| `review_account_events` | 账户决策时间线 | 保存买入、卖出、持仓和空仓原因，供 UI 解释每日决策 |
| `buy_sell_signals` | 实盘辅助买卖信号 | buy 记录买入、sell 按规则平仓；未平仓 code 唯一（部分唯一索引 `idx_buy_sell_open`），含最高浮盈/价、`qty`（买入日冻结）、半仓腿（`half_exit_date/price/qty/reason_*/signal_json`）、平仓原因与持有天数 |
| **`assets.db`（独立库）** | 数据资产仓（prep + auction） | 与 dragon.db 物理隔离，防止不可再生资产被 `storage clear --all` 连带清理；`storage status` 展示 dragon.db / assets.db / 旧 prep.db 体积 |
| `prep_assets`（assets.db） | 复盘原始数据资产 | `prep` 命令采集；`UNIQUE(trade_date, asset_type)`，单事务按键 UPSERT；资产类型 `dragons`/`ths_limit_up_pool`/`ths_block_top`/`market_breadth`/`fg_boards`/`fund_flow`/`cninfo_announcements`/`macro_global`/`collection_report`；`collection_report` 按条合并，语义为「该日期资产当前最佳状态」（未执行的键沿用存量，失败补采不覆盖健康状态）；每日落库=时间换历史 |
| `prep_snapshots`（assets.db） | 盘后复盘计算结果 | 单列 `result_json` 存完整复盘结果（`sentiment` 情绪/建议仓位 + `main_boards` 主线 + `macro` 宏观扰动 + `candidates` 候选池（含 `facts`/`verdict`/`dimensions`/`ignore_reason`） + `dragon_source` 血缘），`UNIQUE(trade_date, source)`，`source` 固定 `v2`；当前 `version=0.5`；加字段=加 JSON key，无需改表 |
| `auction_assets`（assets.db） | 集合竞价原始资产 | `auction` 命令产出；`items_json` 存当日竞价盘口明细（`time`/`price`/`matched`/`unmatched` 数组），`quote_json`（可空）存 `Quote` 行情快照（昨收/跌停价/流通市值等，旧行为 NULL），`UNIQUE(trade_date, code)`；`trade_date` 为竞价所属交易日（=当日）。仅当日 9:25 后可用、盘后仍可拉取、无历史回看 |
| `auction_snapshots`（assets.db） | 集合竞价强度计算结果 | `auction` 采集/`auction --recompute DATE` 写入；单列 `analysis_json` 存 `AuctionVerdict` 序列化结果（`version`/`code`/`is_weak`/`strength`/`direction`/`level`/`dims`/`reject_reason`/`unverified`/`rank`），`UNIQUE(trade_date, code, source)`、`source` 默认 `v2`；同日重跑整日原子替换，空结果不删旧快照。读接口 `assets_db.list_auction_snapshots` / `get_auction_snapshot`（逐行 JSON 损坏隔离）供候选池 A 维度消费 |

> **存储层重构（2026-10-20）**：旧 `prep.db`（`prep_assets`/`prep_snapshots`）与 dragon.db 的遗留 `auction_snapshots` 表保留但**不再读写、不自动迁移、不 DROP**。需要保留历史时按 README「持久化」段的手工迁移片段操作。

### v2 物理分表兼容
- 新扫描的缓存、扫描明细、日志、龙头物化全部读写 `*_v2` 表。
- `scan_id` 继续使用 `v2_YYYYMMDD_topN` 格式；不要假设 `scan_id[:8]` 是日期。
- 5 日去重只看 `dragons_v2`，兼容历史 v2 数据。
- 运行时不创建旧无后缀 `scans` / `scan_stocks` / `scan_logs` / `dragons` 表；`source` 仍是版本路由字段，默认 `v2`。

---

## 开发注意事项

### 龙头回测
```bash
python -m dragon_quant review                        # 自动筛 5~20 交易日内 pending 票全回测
python -m dragon_quant review --date 20260519 --top 5
python -m dragon_quant review --date 20260519
python -m dragon_quant review --ui                    # 回测后启动 Web UI，默认展示 v2
python -m dragon_quant review --ui-only               # 仅看结果
```
回测逻辑：默认从 `dragons_v2` 读 pending → 找入选后第一个非一字板日（`high != low`）最低价买入 → 算 `max_return_5d` / `max_return_hold_days` → 按买入日至峰值窗口算 `max_drawdown_5d` → 写回 `dragons_v2`。`--source v1` 仅用于历史旧表。

### 账户级模拟交易
```bash
python -m dragon_quant review-account --from 20260501 --to 20260601
python -m dragon_quant review-account --from 20260501 --to 20260601 --capital 200000 --ui
python -m dragon_quant review-account --ui-only --source v2
```
`review-account` 不替代 `review`。统一实现位于 `review_account/`：`market.py` 负责候选与时间/指标切片，`data.py` 校验日K和完整5分钟K，`strategy.py` 产生信号，`engine.py` 顺序推进账户，`execution.py` 计算撮合、仓位和费用。`simulator.py` 与 `live_trade/signal_engine.py` 必须复用同一 `evaluate_buy`/`evaluate_sell`/`fill_price`/`fee`，不得复制买卖/成交规则。

- 候选池优先读**盘后复盘快照**（`prep_snapshots`，`prep` 命令产出：主线龙头 + 最高标 + 晋级者 + 情绪温度/建议仓位）；快照消费仅对 `source=="v2"` 生效。默认 `prep_verdict_filter_enabled=True`：`verdict=ignore` 出池，`strong/light/unrated` 保留（unrated 仅 P=False 排序垫底）；推荐集（strong/light）为空/快照缺失/读取异常/无资格时回退旧逻辑——取之前 3 个真实交易日的**全部真龙**并集（`get_dragons_by_date(top_n=None)`，共享层过滤否决/低于50分），按 code 去重保留综合分更高者。回退池**不再应用 ignore 过滤**，P 按快照 verdict 如实标注（ignore→False、无该 code→Unknown），输出 `fallback_reason`。回测额外加载区间前交易日，首日可开仓。
- Asia/Shanghai 时序：开盘MA5承接仅用上日指标和量能；突破前高改为**首根5分钟K完成后确认**，要求首根5分钟K换手≥`turn_strong_bar_turnover_min`（默认0.5%）带量，日K兜底日不触发；分歧买龙要求连续缩量一字板后完整6根5分钟K未破昨收且企稳；不允许用未来信号倒选。
- 择优（P/A 三值）：**tier(A,P) → 五维综合分降序 → 买点得分(priority)降序 → 代码**；三值序为 True < Unknown < False。排序键由 `review_account/priority.rank_key` 单一实现，`TradingEngine` 与 `SignalEngine` 共用。`buy` 保持 buy-all，排序只影响 pending 顺序。仅 `auction_ranking_enabled=True` 时 A 参与排序；`auction_veto_enabled=True` 时 verified ∧ is_weak 否决（按 `auction_veto_exempt_signals` 豁免）。两者皆默认 False；关闭时零竞价读取、零否决、A 恒 Unknown（AC8'）。
- 信号待下一可执行事件成交，实时需更晚的新鲜报价；封死涨跌停不假定成交，跳空不能补填止损或成本线。T+1不卖当日仓，但跟踪买入后峰值；同一根K的高点不能反向激活该根K内的保护。
- 卖出为 10 条 `SellRule` 责任链（`strategy.py` `_SELL_RULES`，顺序即优先级、命中短路，`late_only` 承载「phase!=late 即跳过」阶段门）：**首日止损 `first_day_stop`（默认关）** → 首个可卖日-3.5%/以后-5%止损 → 已有峰值8%后回撤3.5%移动止盈（可选分级/ATR 化）→ 已有峰值6%后完整成本保护 → 高开7%/5%的5/30分钟窗口 → 14:55弱势/MA5 → 次日涨停半仓 → 放量；新增 `soft_time_stop`（N日未创新高）与 `hard_time_stop`（持有超 `max_hold_days` 封板豁免）两条时间止损，默认关。`sell_rule_toggles` 逐条开关（空=存量开/新增关）供消融实验。15:00只估值与更新峰值，不以最终收盘价补成交。
- **首日止损 `first_day_stop`（D3 首日浮亏减半，默认关）**：`sell_rule_toggles={"first_day_stop": true}` 开启；仅 `hold_days==1` 生效，盘中 `bar_low` 触及 `first_day_stop_loss_pct`（-3.5%）先卖**一半**（`first_day_stop_half`），剩余半仓 T+2 起回归 -5% 硬止损；`first_day_disaster_stop_pct`（默认 `0`=关闭，须 `<= first_day_stop_loss_pct`）为灾难线，**灾难段优先且不带半仓守卫**（首次击穿即整仓，`first_day_disaster_stop`）。开启时 `hard_stop` 让位、只处理 `hold_days>=2`。**同一信号仅一条半仓腿**：`first_day_stop_half` 与 `next_day_limit_up_half` 共用 `took_profit_half`（首日已减半→次日封板不再减半）。`risk_per_trade_pct>0` 时首日风险距离改用 `StrategyConfig.first_day_risk_distance_pct()` = `(half + (disaster or stop_loss_pct))/2` 保守上界。兜底日只有 open/late 两帧，灾难线无法提前退出；跨日 pending 按成交日行重算价与量。**运维契约：开启后 T+1 必须至少运行一次 `sell`**（`buy` 不评估卖出），否则首日窗口静默错过。方案见 `archived_md/review_account/2026-10-22-first-day-stoploss-redesign.md`，规则细节与状态机见 `review_account/STRATEGY.md` §1.1。
- 当天卖出后不买入；不因下午卖出而取消早上交易。默认每日最多买一次，仓位基线仍使用可用现金；共享配置可开启单票25%/风险1%、MA5距离/趋势、ATR移动止盈实验，未验证参数不自动推广。
- `--config` JSON 原样传给共享 StrategyConfig；滑点0.2%、佣金0.03%、卖出印花税0.05%、每笔最低佣金5元。`evaluation.compare_strategies` 按60/20/20时间划分、最少交易数与双倍滑点验证，不自动换默认参数。
- 择时与仓位改造（除龙回头默认开启外，其余 default-off，向后兼容）：`sentiment_sizing_enabled`/`sentiment_ban_enabled` 消费盘后复盘快照 `sentiment.position_pct`（情绪→仓位→禁买；历史快照为补跑时点近似，以前向验证为主）；`risk.py` 组合风控（日级熔断/净值回撤降仓/连续亏损降仓）；`entry_veto` 为**当前无调用方的通用前置买入否决钩子**（默认放行）；竞价否决实现在 `evaluate_buy` 信号后置闸门（`reason_code=auction_veto`，可按买点豁免、detail 带 `vetoed_signal` 供影子统计）；龙回头买点 `dragon_comeback_enabled`（**默认开启**；显式 `false` 可复现旧基线，默认开启后历史回测结果不可直接与旧基线比较）；验证基建 `plateau_scan`/`ablation`/`drawdown_decomposition`/`trial_count`。分叉声明见 `review_account/STRATEGY.md`。
- 数据分级：历史交易日按 code 三态判定——有完整48根5分钟K → 严格盘中撮合；无分时但当日日K有效 → 整日**日K兜底**（开盘价买、当日OHLC判卖，分时相关规则自然跳过）；当日日K缺行或 `volume`/`amount` 双零且无分时 → **停牌**，不进 `rows`、不评估买卖、按最近可得收盘价估值。剔除停牌 code 后再判断整日兜底；分时可用但当日日K缺行的标的在兜底日记 `daily_unavailable`（不建行、不静默消失）。日K完全无历史/预热不足/无效OHLC仍报 DataCoverageError，不持久化部分结果。当日（盘中与收盘后）不做停牌分类（日K缓存按写入日命名、不校验新鲜度），保持旧契约硬报错。兜底成交价保守近似，结果标 `data_quality`（strict_5min/mixed/daily_fallback）、`fallback_days` 与 `suspended_days`/`suspended_codes`/`suspended_days_count`/`unavailable_days`/`data_degraded`；停牌候选仍在 `details` 中带 `reason_code=suspended`。行情按日期/周期/不复权口径隔离缓存；SH000001用于交易日历，个股000001仍是平安银行。
- `/account` 生成/删除和历史批次查询保持不变；API可传 `strategy_params`，快照增量补 `positions_json` 存全部持仓（查询返回 `positions`），旧数据仍可读取；已平仓表保持原语义。完整规则见 `review_account/STRATEGY.md`。
- **清仓后窗口指标**：回测结束后由 `service.run_review_account` 调用 `simulator.annotate_post_exit`（**不在 `AccountSimulator.run()` 内**，回测核心保持纯 as-of），对每条已平仓记录按 code 取一次日K计算 `post_exit_max_return` / `post_exit_max_drawdown` / `post_exit_window_days`，失败逐条降级为 NULL/0、不中断回测。窗口为清仓日**之后**的 5 根有效日K（停牌占位行不计入、不足按实际、无 bar 为 NULL/0 天），允许越出 `date_to`（事后评估）；最高盈利 =（窗口最高−卖出）/卖出×100，最大回撤 = min(0,（窗口最低−卖出）/卖出×100)。旧 run 不回溯，三列为 NULL。
- **买点统计**：`evaluation.entry_reason_stats(positions, initial_cash)` 按 `BUY_SIGNAL_CODES` 四类（开盘回踩MA5/弱转强/分歧买龙/龙回头，0 笔也输出）+ 未知 code + 合计行，输出笔数/盈利笔数/胜率/平均每笔收益/累计贡献；胜率与 `_result()["win_rate"]` 同源（`realized_return > 0`）。累计贡献为近似值 `Σ(realized_return/100×entry_price×qty)/initial_cash×100`（忽略买入佣金）。`GET /api/account/positions` 返回 `{"data": [...], "stats": {...}}`（只增键），旧 run 也可实时聚合。
- `/account` 前端：`AccountStats` 之后新增「买点统计」卡片；「单票收益汇总」新增「清仓5日内最高盈利」「清仓5日内最大回撤」两列（NULL 显示 `—`，窗口不足 5 根标注实际天数，表头提示可能含回测区间外数据）。

### Web UI 前端构建
源码 `web_ui/frontend/`（Vite+React+TS+Mantine），产物 `web_ui/dist/`（已入库随包分发）。运行期仅靠 Python stdlib 托管，**不需要 Node**；改前端时才需 `npm run build`。

### 实盘辅助交易（buy / sell）
```bash
python -m dragon_quant buy --date 20260907 --at 10:00
python -m dragon_quant buy --json        # 完整 JSON（details[].checks 含四类买点诊断）
python -m dragon_quant sell --date 20260908 --at 14:55
python -m dragon_quant buy --force      # 非交易时段强制跑当日回放（09:30 前仍拒绝）
python -m dragon_quant sell --force
```
`buy/sell` 底层与 `review-account` 完全复用同一 `review_account` 策略与成交函数（`evaluate_buy`/`evaluate_sell`/`fill_price`/`fee`/`apply_sell_fill`/`track_peak` 等），只做信号记账、**不维护真实账户**：只要候选池标的触发买点就记一条买入（buy-all，不再择优取第一只）；`sell` 从 `buy_sell_signals` 筛出「买入日之前且 as-of 未平仓」的信号，回放当日行情判定卖出。策略调参只需改 `review_account` + 回测，`buy/sell` 自动跟随。`buy` 与 `review-account` 共用 P/A 三值排序与否决；输出 `ranking_mode`（`auction`/`auction_unverified`/`prep_only`/`legacy`）、`fallback_reason`，被否决但本可买入的标的在 `details` 中带 `vetoed_signal`。

- **四类买点诊断（checks）**：`buy` 对每个候选（买入与未买入）逐条输出 ① 开盘回踩MA5 / ② 弱转强 / ③ 分歧买龙 / ④ 龙回头 的状态与带数值原因；同一 code 只输出一次，取最后一条带 checks 的 detail（买入标的的命中买点用 `buys[].reason_code` 回填）。状态枚举 `passed`/`failed`/`pending`/`window_closed`/`unavailable`/`skipped`：09:30 的 ②③ 为 `pending`，窗口已过未通过为 `window_closed`，日K兜底 ②③ 为 `unavailable`，情绪禁买/综合分不足/非真龙为 `skipped`（不伪造失败）。决策与诊断共用 `strategy._rule_*`（阈值只有一份）；`checks` 为 detail 的只增字段，不写入 `buy_sell_signals`。`buy --json` 的 `details[].checks` 每项含 `rule`/`label`/`window`/`status`/`selected`/`reason`，detail 另带 `observed_at`/`phase`。风控/已卖出导致买点未评估时，输出 `buy_eval_skipped` + 四条 `skipped`；买点命中但未成交（不可成交价/超时/预算不足/回放结束）仍展示四类并标 `selected`。
- `SignalEngine`（`live_trade/signal_engine.py`）复刻 `TradingEngine.step` 的「决策 → 下一可执行事件成交」两段式与峰值跟踪，去掉资金/仓位/次数限制；卖出数量用 `notional_quantity`（`initial_cash × max_position_pct`，与回测首仓同口径）落库冻结，保证 `break_even_price` 与回测精确一致；买入不足一手时与回测一样不建仓。卖出/半仓的仓位记账经共享 `apply_sell_fill`，峰值经共享 `track_peak`，两引擎不复制规则。
- **半仓记账**：`next_day_limit_up_half` 或 `first_day_stop_half` 触发时 `half_close_signal` 冻结 `half_exit_qty/price/reason_*`（`half_exit_reason_code` 区分止盈/止损减半）且保持 `status='open'`，剩余半仓继续跟踪；后续规则可再次触发 `close_signal` 全清。`took_profit_half` 是「该信号已完成一条半仓腿（不分盈亏）」的唯一性守卫，两条半仓原因码共用 → 同一信号不会出现两条半仓腿。
- **影子风控**：`live_trade/shadow.py` 从 `buy_sell_signals` 全量记录按回放时点（`as_of=target`）重建 `AccountState`，逐事件调用同一 `risk_governor`；日级熔断/连亏熔断跟随，`equity<=0` 额外触发破产闸；delevel 降仓在无 sizing 场景不适用。`build_shadow_state` 的 `cash` 是净值轧差项（不是现金主张），run 内买入同步扣减。
- 数据分级：buy/sell 的 `codes` 均为「候选 ∪ as-of 持仓」，任一分时缺失即整日走日K兜底；结果带 `data_quality` 与 `degraded` 提示。历史交易日停牌标的（当日日K缺行或双零量且无分时）按 code 跳过：停牌持仓冻结（不评估卖出、按最近可得收盘价保持净值），结果带 `suspended_codes`，`degraded` 增加停牌说明；当日不做停牌分类。
- 历史日期必须同时传 `--date YYYYMMDD --at HH:MM`（重放当日截至该时点的5分钟K，as-of 过滤也以该时点为准）；实时今日回放截至当前已完成的5分钟K。绝不使用当前腾讯报价。
- **非交易时段 `--force`**：`buy`/`sell` 均支持，放行「午间休市（11:30–13:00）」与「已收盘（≥15:06）」两个时段闸门；**09:30 前仍拒绝**（无完整5分钟K，强制无意义，保留原「当前尚未开盘」报错）。15:00–15:06 属既有收盘宽限窗口（本来就允许运行），`--force` 在该窗口不生效，日K兜底仍按收盘价成交。`--force` 不解除任何数据/日期校验（非交易日、历史日期缺 `--at`、无完整5分钟K 照常报错）；与 `--at` 同给时 `--at` 优先、force 静默无效。强制模式的事件流截断到最后一个已完成5分钟K（午休=11:30、收盘=15:00），影子状态 as-of 仍取真实当前时点，**不伪造收盘价成交**（日K兜底日的 15:00:00.001 收盘成交腿被截断，15:00 的 late 决策事件仍执行但不成交）。强制运行照常写 `buy_sell_signals`：`signal_json` 带 `"forced": true` 便于事后辨认，人读输出打印一行强制模式提示（交易时段内加 `--force` 不打印、不标记）。
- 信号表 `buy_sell_signals`：`insert_signal` 对同一未平仓 code 幂等（部分唯一索引 `idx_buy_sell_open`），返回 `(id, created)`；`list_open_signals(before_date=...)` 只取买入日早于目标日的持仓，`list_all_signals` 供影子重建；峰值经 `update_signal_peaks` 跨日写回供移动止盈/保本判定。
- 运维契约：峰值新鲜度依赖每个交易日至少运行一次 `buy` 或 `sell`（buy 也会写回既有持仓峰值）；历史重放会写库，幂等命中时输出标注「未落库」。
- 同输入/参数/信号状态下买卖决策与回测一致；实时延迟与成交条件仍可能导致结果不同，不保证收益。
- `buy` 输出首行显示**建议仓位**（来自盘后复盘快照 `sentiment.position_pct`，`load_market_sentiment` 读取，缺失时显示 `—`）。该值**仅供人工参考，不参与下单**：`SignalEngine` 不维护真实账户（仅有影子状态），只把它用于禁买判定（`sentiment_ban_enabled` 开启且 `position_pct==0` 时否决买入）。`sell` 不显示仓位。

### 盘后复盘 `prep`（两段式 + 增量采集 + 独立资产库）
```bash
python -m dragon_quant prep                              # 最新有龙头数据的交易日
python -m dragon_quant prep --date 20260519
python -m dragon_quant prep --date 20260519 --recompute   # 用已存资产重算快照
python -m dragon_quant prep --date 20260519 --force       # 强制重采全部可采资产
python -m dragon_quant prep --json                        # 完整复盘 JSON（默认 4 行摘要）
```
- **两段式 + 默认增量**：采集段按 `ASSET_COLLECTORS` 注册表逐类采集 → 单事务写 `assets.db/prep_assets`；计算段只从 `prep_assets` 读回（快照=资产纯函数）→ 写 `prep_snapshots`。默认先读存量资产 + `collection_report` 做健康判定：**全部健康则零采集**（含 dragons 本地读取）直接重算；否则只补采不健康且可采的 asset_type。`dragons` 重采且候选 **code 集合变化**时级联补采 `fund_flow`/`cninfo_announcements`；`fund_flow`/`cninfo` 另做 code 覆盖 diff（`quotes`/`items` 未覆盖当前龙头并集 codes → 补采）。新增因子 = 注册一行 + 一个 collector，不动流水线骨架。
- **collection_report 语义**：「该日期资产当前最佳已知状态」，**不是**本次运行的遭遇。`save_prep_assets` 只按键合并本次实际执行的 collector 条目（`BEGIN IMMEDIATE` 单事务），零采集时 report 完全不动。补采失败**不覆盖**既有健康状态（保留原条目并写 `last_attempt`）；采集成功但 `empty_suspicious` 仍按新状态覆盖（payload 与状态自洽）。
- **可采性矩阵**：`same_day_only` 资产（`market_breadth`/`fg_boards`/`fund_flow`）仅 `trade_date == today` 可采；历史日缺失 = 永久降级，**不尝试采集、不写 report**，compute 按缺失降级。旧版本遗留的 `unavailable` 若同 payload 的 `as_of==trade_date` 且自检为 `ok`，在下一次普通运行中窄口径修复为 `ok`（不采集、不扩散到 `error`/`empty_suspicious`）。`macro_global` 不在 `same_day_only` 之列（日 K canonical 可历史重建）。
- **`--force`**：重采全部可采资产（历史日 `same_day_only` 仍不采，但窄口径修复照常生效）；**不绕过** 16:40 当日门禁与交易日校验（与 `scan --force` 语义不同）。`--recompute` 零检测零采集（现状不变）。
- **复盘窗口**：含 T 的最近 3 个交易日 `[T-2, T-1, T]`（按交易日历）。窗口日期在**采集段**解析并写入 `dragons` 资产（`window_days` / `window_trade_dates` / `days` 逐日 scan_id/version/count/status），compute 段仍是资产纯函数、`--recompute` 可重放。窗口内缺日跳过并标 `dragons_partial`；日历不足三日标 `dragons_window_short`；**三日并集为空**才算 `empty`；并集无行且存在 error 日 → collector 抛错（不把取数故障伪装成无龙头）。窗口降级只触发 dragons 本地重读（`collection_health`），**不把 dragons 从 usable 中剔除**（`asset_health` 仍判可用，candidates 正常产出）。
- **候选池**：三日真龙并集，同 code 按 `(-composite_score, rank)` 去重、完全同分取较新入选日；每只保留真实 `trade_date`（下游 `candidate_trade_date` 血缘）。`fund_flow` / `cninfo_announcements` codes 取并集（按 code 去重）。THS 归因字段只对 T 日入选候选填充，跨日候选为 `null`；主力净额/公告是 T 日时点信息，对所有候选附加。
- **数据源**（tmdx 优先、THS 次之、**排除东财**）：`dragons`（dragons_v2 按窗口逐日冻结）/ `ths_limit_up_pool`（涨停原因/板型/炸板次数/连板高度，THS dataapi，T 日）/ `ths_block_top`（题材主线，THS dataapi，T 日）/ `market_breadth`（全 A 扫描五合一：红盘率/涨停/炸板/跌停/成交额）/ `fg_boards`（昨日涨停溢价/晋级交叉/龙虎榜名单）/ `fund_flow`（并集候选股 `main_net_amount`）/ `cninfo_announcements`（并集候选公告标记）/ `macro_global`（宏观扰动：日经225期指/A50期指/美债 10Y/黄金）。
- **宏观扰动 `macro_global`**：品种与口径由 `MACRO_SPECS` 声明，逐品种字段 `same_day_close` 决定 canonical：
  - `jp_nikkei`（日经225期指，`INTL_INDEX/NK0Y`）与 `cn_a50`（富时A50期指，`INTL_INDEX/CNY0`）：`same_day_close=True`，取日 K 中**日期 `<= trade_date` 的最后一根**（同日收盘）；priority 只有 `tdx`，**无回退**。
  - `ust10y`（`MACRO_INDICATOR/8_ATY`）与 `gold`（`COMEX_FUTURES/GC00W` → `SH_GOLD/Au99.99`）：保持 `< trade_date`（上一已完成 bar）；回退 Yahoo chart 日 K（best-effort，可能 429）。
  - 夜盘归属下一交易日 bar，故 T 日 bar 一旦发布即定型、T+1 bar 被 `day > trade_date` 排除；发布延迟由 `macro_same_day_missing` 显式标记。逐品种记录 `caliber`（`same_day_daily_bar`/`previous_daily_bar`），**无顶层 caliber**。任一外围指数 `session_kind != "same_day"` 时标 `macro_same_day_missing`（只影响复盘展示，不触发 buy/auction 的补跑提示）。
  - `get_macro_quotes` transport 失败抛异常 → `source_errors`；单品种缺失只降级。宏观**不设** `same_day_only`（日 K 可确定性历史重建），`as_of=trade_date`，不做 `_AS_OF_CHECKED`。期指连续存在换月 roll 跳变，label 固定「期指」，涨跌幅在换月日可能失真；美债为两位小数日频快照，bp 变动标「近似」且只展示、不进仓位公式。腾讯/雪球 provider 的宏观方法保留历史契约但当前规格未路由。
- **逐只参与判定**：`compute_candidates` 为每只候选写 `facts` 块（`in_pool_today` 与入选日解耦、`t_pct`/`t_turnover`/`t_amount`/`open_num`/`high_days`/`limit_up_type`/`limit_up_suc_rate`/`flow_available`/`announcements_available`/`risk_announcement`）；`review_account/verdicts.py` 按有序规则链产出 `strong`（强烈建议）/`light`（轻仓博弈）/`ignore`（忽略）/`unrated`（未评级）与 2-3 行维度明细。忽略原因优先级：数据不足 → HARD 公告 → 高位炸板 → 非涨停+主力流出 → 退出涨停梯队 → 综合分不足 → 兜底；SOFT 公告（减持/质押/担保等）只降档不忽略。verdict **仅展示**，不过滤 review-account 消费的 candidates。
- **快照口径**：`version=0.5`；顶层 `macro`（`available`/`session`/`instruments`/`degraded`/`sources`，`session` ∈ `same_day`/`previous_session`/`mixed`/`null`（外围指数组不可用时为 null），口径血缘在逐品种 `caliber`）；`candidates[].facts/verdict/verdict_label/dimensions/ignore_reason`。`dragon_source` 含 `window_days` / `dates`（逐日血缘）/ `version_mixed`。`window_days` 语义：`0`=dragons 资产不可用（血缘未知）、`1`=旧版单日口径重算、`3`=三日窗口。旧资产 `--recompute` 按单日口径重算（`window_days=1`），不产生 `dragons_*` 降级、不报错；旧宏观资产中的 `us_*` 会被过滤（degraded/sources 同步），缺失的新 key 进 degraded，无 `macro_global` 时标 `macro_global` 降级。
- **CLI 输出**：默认 4 行市场摘要（是否建议买入/仓位/等级/理由，理由内含外围/美债/黄金片段）+ 空行 + 逐只标的清单（✅ strong/light 在前，组内综合分降序；忽略在后每只一句原因；未评级最后）；候选为空显示「无参与复盘标的」。等级 0.8→强烈买入、0.5→一般、0.3→轻仓博弈、0.0→不建议买入、None→不建议买入（数据不足）；降级信息并入理由。`--json` 输出完整 JSON（`--verbose` 已删除，传旧参数由 argparse 以 exit 2 拒绝）；默认模式错误写 stderr 并以非零码退出，`--json` 模式错误保持原 JSON error 到 stdout。
- **时点门禁**：`same_day_only` 资产（`market_breadth`/`fg_boards`/`fund_flow`）仅当日采集，补跑历史日**不采集、不写 report**（compute 按缺失降级）；`macro_global` 不在此列（日 K canonical 可历史重建）。当日运行须 ≥16:40（A50 期指日盘 16:30 收盘 + 缓冲；15:30–16:40 时段硬拒绝）。日期校验用 TDX 指数日K（不依赖雪球 Cookie），「非交易日」与「日历数据源失败」报错严格区分。`_collect_all` 入口对不可采类型直接 `ValueError`，防止误把 T 日数据写到历史日。
- **错误契约**：新增采集 provider 方法在 transport 失败时**抛异常**（存量方法「返回空」契约不变，如 `get_sector_ranking` 用 `_fetch_with_retry(raise_on_exhaust=False)`）；采集段逐 collector 隔离、计算段逐因子隔离，**任何单点失败不中断 prep**，失败原因落 `collection_report` 与 `sentiment.factor_errors`/`degraded`。collector 失败/返回非 dict/分类异常时不写 payload；若存量资产健康则保留其 report 状态并写 `last_attempt`，不因一次补采失败降级快照。依赖 dragons codes 的采集器在 dragons 未就绪时不执行（记 error，避免空 payload 覆盖存量）。
- **情绪口径**：`limit_up_cnt` 主口径为 THS T 日精确值（`limit_up_cnt_ths`），FG T-1 交叉值并列保存（`limit_up_cnt_t1_fg`）且不进公式；THS 缺失时显式回退 FG 并标 `degraded`+`position_input` 血缘。`position_advice` 阈值（80/5/30/10）不变。
- **只观察不进公式**：`broken_rate`/`yesterday_premium_pct`/`promotion_rate`/`max_board_height`/`lhb_cnt` 等 v0.2 因子先落 JSON 供观察与回测，**不改仓位阈值**（未验证参数不推广）。
- `prep --source` 已移除（固定 v2）。

### 集合竞价采集 `auction`
- **候选池**：默认 `--pool prep`，读取**严格 T-1** 的 `prep_snapshots`，取 eligible（真龙 + `min_score`）中 `verdict != ignore` 的 code（strong/light/unrated/缺 verdict 保留；推荐集为空则回退）。与 `buy`/`review-account` 共用 `market.collect_candidates_ex`，保证 code 集合一致。
- **回退**：T-1 快照缺失/读取异常/无资格/推荐集为空 → 回退最近 N 日 `dragons_v2` 并集，输出 `pool_source="legacy"` + `fallback_reason`（`no_snapshot`/`snapshot_read_error`/`empty_snapshot`/`no_eligible`/`all_ignored`/`calendar`）。回退池不再应用 `ignore` 出池；P 按快照 verdict 如实标注。
- **空池**：仅当 legacy 也为空时正常结束（exit 0），不触发行情/盘口请求；输出 ignore 原文样本 + `prep_degraded` 提示。
- **参数**：`--pool {prep,legacy}`（默认 prep）；`--days` 仅在 legacy/回退路径生效（默认 3，显式传入且 prep 成功时输出 `days_ignored`）；`--json` 输出完整 JSON。
- **输出**：默认人读摘要——标题（日期/参与只数/池来源，含回退原因）+ 逐只「排名/代码/名称/等级/强度/方向」（未验证 `?`、否决 `-`，`｜否决：原因｜未验证` 可叠加）+ 汇总（强/中/弱/其他计数、未验证数、采集失败数）；空池/采集层全失败/评分层全失败/全空盘口分别单行提示。JSON 契约（collect 25 个顶层 key、空池 16 个、recompute 15 个）与字段顺序在 `--json` 下逐字段不变。默认模式错误写 stderr + 非零退出；`--json` 错误保持原 JSON 到 stdout，退出码语义不变。
- `--recompute` 不变（零采集，用已存资产重算）；默认人读摘要（标题标「重算」），`--json` 输出完整 JSON。

### 数据资产云端同步（`assets`）
```bash
python -m dragon_quant assets doctor --profile r2 --samples 50
```
- 目标：把 `assets.db/prep_assets` 与 `assets.db/auction_assets` 的**原始数据资产**备份到 S3 兼容对象存储（Cloudflare R2 主选 / Backblaze B2 备选）；算法快照、行情缓存、Cookie 不同步。旧 `prep.db` 与 dragon.db 遗留竞价表不自动纳入备份（需先按 README 手工迁移）。
- 本地是权威副本；云端只作异地备份。资产不可再生（`same_day_only` 与 `auction` 仅当日可取）。
- 云端布局：`blobs/<date>/<source>_<type>.<content_sha256>.json.gz` + `manifests/<date>.json`（每日一份，重采覆盖）。哈希身份用**未压缩规范化 JSON** 的 sha256/md5，压缩字节另算传输哈希。
- 凭据：`<DQ_DATA_DIR>/secrets/assets_sync.json`（POSIX 0600），`default_profile + profiles{r2,b2}`；环境变量 `DQ_ASSETS_*` 只覆盖当前 profile。
- 当前里程碑：M1 提供 `doctor`（门槛 A：PUT/GET/HEAD/LIST/DELETE + 元数据回环）；`push` / `pull` / `status` / `verify` 在 M2-M4 提供。门槛 A 未通过前不得进入 M2。
- 方案与风险记录：`docs/solutions/2026-10-18-assets-cloud-sync-design.md`、`docs/research/2026-10-18-assets-cloud-storage.md`。

### 评分器接口约定
评分器统一签名，是 **cache 消费者**（只读 `cache.get(key)`，不发请求）：
```python
def score(code: str, cache: DataCache, **kwargs) -> ScoreResult
```
- `scorers/aggregator.evaluate()` 统一调度五维 + 门槛聚合，产出 `DragonVerdict`；四大特征异常必须否决，资金承接异常降级 50 分且不否决。
- 所有候选保留诊断综合分，但 `rank_verdicts()` 只给通过者赋真龙排名（同分按代码排序），否决票 `rank=None`；`raw_output.ranking` 与 `scan_stocks_v2` 保存全部诊断结果，`dragons_v2` 和最终报告只收 Top N 真龙。无真龙仍保存扫描，不用否决票补位；五日去重后不重排 rank。
- `rebuild_dragons_for_date` 仅使用非明确否决、rank 非空且不超原扫描 Top N 的贡献；不改旧表名与 ID，不自动重算历史记录。盘后重新取数评分需 `scan --force --no-cache`。
- cache 键：`kline:1min:{code}` / `kline:1min:SH000001`（上证指数）/ `kline:1min:sector:{source}:{category}:{code}` / `kline:5min:sector:{source}:{category}:{code}`（10日历史）/ `quotes:batch`（`list[Quote]`，含盘口）/ `sector:components:{source}:{category}:{code}` / `sector:ranking:{source}:{category}`。板块键的 `category` ∈ `hy/gn/dq`、`source` ∈ `tdx/ths`，由 `dragon_quant/sectors.py` 的 `sector_key`/`components_key`/`sector_1min_key`/`sector_5min_key` 统一构造（评分器通过 `primary_sector_key` 读取）。裸代码 `000001` 是平安银行，不得用于指数请求或缓存。
- 腾讯行情按代码排序、每批最多 200 只获取并合并，不截断成分股总数；封板池只用同板块主板涨停候选，记录有效样本与缺失情况。
- 稳定封板按最后开板后持续封至最后有效分钟的起点排名，同分钟并列；脉冲使用局部峰抑制，每次匹配只能判带动或跟风之一，同步启动不判方向；相关性 bonus 仅看首次触板前。分时缺失或全程平线时带动子项 40 分降级。
- 抗跌反弹必须确认基准及个股实际回升，个股横盘不获反弹奖励；分钟时序窗口不跨午休、隔夜或双方缺点区间。
- 封单强度需确认现价涨停且买一价在涨停价附近；未涨停买一量不能当封单，从未触板的完整分时稳定性为 0。价格容差统一为涨停价的 0.1%。
- 承接对手盘是扫描日领跌 Top20 行业；六根五分钟 K 必须同连续交易时段，允许累计缓跌达标，不设单根跌超 0.5% 隐藏门槛；每个出逃板块独立通过不晚于拉升且间隔不超十分钟的检查，至少两个有效板块。
- 承接单事件强度/广度/持续性为 60%/20%/20%，拉升满分参考 3%、出逃规模参考 10；重叠窗口（含链式重叠）同交易时段内合并，保留最高分代表，同分取较新窗口，取消次数奖励。按目标 K 线日期计算年龄，事件分 `50+(原始分-50)*2**(-年龄/3)` 向中性衰减，取最强的最多三个独立事件均值。details 保存原始窗口数、独立事件数、所选事件及原始分/年龄/衰减系数/调整分；不保留无效 multi_event_bonus 字段，不重算历史数据。
- 阈值/权重集中在 `scorers/registry.py`，便于回测调参。

### 必须遵守的约束
- **运行时依赖**：`playwright`（Cookie 自动获取 + 浏览器辅助；chromium 内核首次 `cookie-fetch` 时自动下载，走国内镜像）为必选；`tmdx`（通达信 TDX 首选数据链路）为**默认依赖**（`pip install dragon-quant` 自动安装；缺失时每次 CLI 命令除 -h/--help/-v/--version 在 stderr 提示补装）；其余仅用 Python 3 标准库。项目要求 Python≥3.10。
- **跨平台**：数据目录用 `DQ_DATA_DIR` 覆盖，默认按平台存。
- **线程安全**：DataCache 操作持 `threading.Lock`；DB 每次操作独立连接 + WAL。
- **历史兼容**：旧 `*_v1` 表可显式查询；新扫描固定使用 `scorers/` 与 `*_v2` 表，不再保留旧四维评分代码。
- **provider 接口兼容**：`StockProvider` 新增板块 K 线方法用默认 `raise NotImplementedError`（非 `@abstractmethod`），否则 `create_providers()` 一次实例化全部 4 个 provider 时会崩。

### AI Agent 协作规范
> **任何代码修改或破坏性操作前，先输出技术方案（改动范围、涉及文件、风险点），等待用户确认后再执行。** 纯查询类操作（读文件、查数据库、搜索代码）不受此限。
> **技术方案必须配图**：涉及架构、事件流、数据流、状态机或多模块交互时，用 **Mermaid** 代码块（`flowchart`/`sequenceDiagram`/`stateDiagram` 等）画出架构图/流程图，随方案一并输出并写入文档。不用 ASCII 图。

### Cookie 失效处理
板块数据用同花顺（无需 Cookie）。个股依赖雪球 Cookie（有效期几天到数周），返回空/403 先查雪球 Cookie 状态。

### Git 规范
- 仓库：`gitBingxu/dragon-quant`；main 合入需 CODEOWNERS 审批。
- Commit 风格：中文 + emoji 前缀（见 git log）。
- **文档同步（强制）**：每次提交涉及功能/命令/接口/数据源/表结构变更时，**必须在同一 commit 内同步更新 `AGENTS.md` 与 `README.md`**，保证文档与代码一致；纯文档或纯内部重构可酌情豁免。
- **代码结构导航**：模块结构/调用链/数据流由 codebase-memory 索引承担（`.codebase-memory/graph.db.zst`，经 `query_graph`/`trace_path`/`get_architecture` 查询）；较大调整后重跑 `index_repository` 刷新。

---

## 当前状态

### ✅ 已完成
- 五维「识别真龙」评分器 `scorers/`（带动/领涨/抗跌/流动/资金承接 + 门槛加权聚合），由 `scan` 命令触发
- 集合竞价强度评分器 `auction/`（方向/供需/量价/末段抢筹四维加权 + 否决闸门，纯函数）；`auction` 采集与 `auction --recompute DATE` 已接线，结果写 `assets.db/auction_snapshots`（原始资产含 `items_json` + `quote_json`，快照 = 原始资产纯函数）
- 数据资产独立库 `assets.db`（`prep_assets`/`auction_assets` 原始资产 + `prep_snapshots`/`auction_snapshots` 结果），与 dragon.db 物理隔离
- prep 宏观扰动 `macro_global`（日经225期指/A50期指同日 + 美债10Y/黄金 T-1，TDX 扩展行情日 K canonical + Yahoo 仅美债/黄金回退）与近三日龙头逐只参与判定（`facts` → `strong/light/ignore/unrated` + CLI 标的清单），快照 `version=0.5`
- 3 个 Provider（同花顺/雪球/腾讯）含完整反爬；同花顺**行业/概念/地区板块**数据源（排行 HTTP GET+多页+本地排序+403退避、成分股、当日1分K、历史5分K）
- 封单数据走腾讯 gtimg 收盘盘口（`Quote.bid1_volume`）
- DB 板块黑名单表 + CLI `blacklist` 管理
- v2 物理分表（`scans_v2` / `scan_stocks_v2` / `scan_logs_v2` / `dragons_v2`）+ `review --source v1` 历史兼容 / Web UI source 切换
- 量价分析 `vpa/`、结构化日志 `logging/`、统一持久化 `storage/`、交易日历 `utils/trading.py`、龙头回测 `review.py`、Web UI
- CLI `dragons` 命令：导出某交易日扫描物化的龙头 JSON（`--date` 默认最新有数据交易日，`--true-only` 仅真龙）
- 全量单测覆盖 `tests/test_scorers.py`、`tests/test_storage.py` 等核心路径

### ⚠️ 待完成/观察
- 同花顺数据网关 403 频控：高频访问会临时封 IP（已加退避重试，正常每日一两次扫描不触发）

### 📝 已知修复
- 同花顺排行字段 `tradezdf`→`zdf`，修复领跌榜全为正值的 bug
- v2 领涨性涨幅分位样本由「成分股」改为「候选池」，杜绝未拉日K成分股的 0 值污染
- v2 归一化涨幅曲线改用 `KBar.pct`，避免用首分钟价误当昨收
- v2 资金承接强度改为正向口径（出逃规模越大 + 拉升越高 → 分越高）
