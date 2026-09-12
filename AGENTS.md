# AGENTS.md — dragon-quant

> 龙头战法量化筛选系统 · AI Agent 操作手册

---

## 这是什么

一套纯 Python 3 的 A 股龙头筛选系统。从当日涨停榜出发，评估涨停股的龙头质量并加权排名输出；同时支持日志查询、SQLite 持久化、龙头回测与 Web UI 可视化。

系统当前使用**五维「识别真龙」评分体系**，由 `scan` 命令触发：

- **五维「识别真龙」**：带动性 30% / 领涨性 25% / 抗跌性 15% / 流动性 20% / 资金承接 10%，**门槛+加权两段式聚合**（四大特征任一低于门槛即一票否决，资金承接不否决仅加权贡献）。详见 [评分规范](dragon_quant/scorers/评分器Refactor.md)。

> 为兼容历史数据，SQLite 物理表继续沿用 `*_v2`（如 `dragons_v2` / `scans_v2`），`scan_id` 继续使用 `v2_YYYYMMDD_topN`。`scan_v2` 命令保留为隐藏兼容别名，行为等同 `scan`；旧 `*_v1` 表不再由主流程写入，仅可通过显式 `--source v1` 查询历史记录。

数据源：同花顺（板块数据）+ 雪球（个股 K 线/分时）+ 腾讯（批量行情/收盘盘口），不依赖任何付费行情接口。东财 provider 仍保留但默认不参与扫描。

> **板块口径已切换为「行业板块」**（同花顺 `thshy`/`hyzjl`，约 90 个真实行业，code 为 881xxx），不再用概念板块（`gn`/`gnzjl`）。

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

# 概念板块黑名单管理（拉取领涨/领跌板块时过滤）
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
```

### 前置条件

板块数据用**同花顺**，**无需 Cookie**（curl + GBK 直取，无 Playwright/反爬）。个股数据依赖雪球，需配雪球 Cookie：

```bash
# 查看状态
python -c "from dragon_quant.providers.cookie import get_xq; print(f'雪球: {bool(get_xq())}')"

# 手动设置雪球 Cookie（推荐）
python -m dragon_quant.providers.cookie set --cookie "xq_a_token=...; xq_is_login=1; u=..." --source xq

# 自动获取（需要 playwright）
python -m dragon_quant.providers.cookie fetch --source xq
```

Cookie 文件位置：
- 雪球：`~/Library/Application Support/dragon-quant/cookies/xueqiu`
- 东财（保留备用）：`~/Library/Application Support/dragon-quant/cookies/eastmoney`

---

## 架构总览

```
dragon_quant/
├── __init__.py / __main__.py    # 入口
├── cli.py                       # argparse CLI（scan/logs/data/review/vpa/storage/blacklist；scan_v2 为隐藏兼容别名）
├── orchestrator.py              # 编排主流程 (Phase A→F)，固定五维评分
├── data.py                      # 原子数据查询 API
├── rate_limit.py                # 分组并发调度器
│
├── providers/                   # 数据源适配层
│   ├── base.py                  # StockProvider ABC + 板块 K 线方法
│   ├── ths.py                   # 同花顺 — 行业排行(curl)/成分股(HTML)/板块1分K/历史5分K
│   ├── eastmoney.py             # 东财 — 保留，默认不参与扫描
│   ├── xueqiu.py                # 雪球 — 个股日K/分时，需 Cookie
│   ├── tencent.py               # 腾讯 — 零认证，批量行情 + 收盘盘口(bid1)
│   ├── browser.py               # Playwright 浏览器会话（Cookie 获取/页面渲染）
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
│   └── db.py                    # SQLite（主流程读写 *_v2，兼容查询 *_v1）
├── utils/trading.py            # 交易日历 + 涨停判断 + 买入日定位
├── review.py                    # 龙头回测验证
├── review_account/              # 账户级模拟交易（回测；strategy/simulator/models/indicators/service）
├── live_trade/                  # 实盘辅助交易（buy/sell/account；复用 review_account 策略）
│   ├── row_builder.py           # 实时 Quote + 历史日K → 策略消费的 row
│   ├── trader.py                # LiveTrader.buy/sell（纸上账户）
│   └── service.py               # run_buy/run_sell/run_account_status/init_account
├── web_ui/                      # 回测结果 Web UI（Vite+React+TS+Mantine / stdlib HTTPServer）
└── models/types.py             # dataclass 数据模型
```

---

## 执行流程

编排器 `orchestrator.scan()` 分 6 个阶段：

| Phase | 做什么 |
|-------|--------|
| **A** 板块排行 | 同花顺·行业板块涨跌幅榜，领涨 Top5 + 领跌 Top20，过滤 DB 黑名单 |
| **B** 候选筛选 | 每领涨行业取当日所有涨停股(pct≥9.9)，过滤 ST+双创+北交所 |
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
- **行业排行**：`data.10jqka.com.cn/funds/hyzjl/field/zdf/order/desc/page/{p}/`，curl + GBK 直取。
  - **铁律**：字段必须用 `zdf`（涨跌幅）。旧 `tradezdf` 是资金流字段，**无视 order/page**，永远返回固定 50 行资金流入板块（曾导致领跌榜全是正值的 bug）。
  - 单页 DOM **非严格有序**，必须抓多页后本地按 pct 排序。网关有 **403 频控**，已加退避重试 + 页间延迟。
- **成分股**：`q.10jqka.com.cn/thshy/detail/code/{881xxx}/`（GBK HTML 表格，列 td[1]=code/td[2]=name/td[4]=涨跌幅），翻页走非 ajax `/thshy/detail/order/desc/page/{p}/code/{code}/`。
- **板块当日1分K**：`d.10jqka.com.cn/v6/time/48_{inner}/last.js`（JSONP，原始1分，不聚合）。
- **板块历史5分K**：`d.10jqka.com.cn/v6/line/48_{inner}/30/last1000.js`（JSONP，**周期码 30=5分**，真实 OHLC）。
- innerCode：行业板块 `clid` 即 code 本身（881xxx）；概念板块为 885xxx 映射；统一解析详情页 `<input id="clid">`，进程内缓存。

### 雪球（个股，需 Cookie）
- `minute.json`（当日1分K）/ `kline.json`（日K）/ `quote.json`。Referer：`https://xueqiu.com/S/{SH/SZ}{code}`。
- 注意：`pankou.json` 盘后返回空体，**已弃用**，封单改用腾讯 gtimg 收盘盘口。

### 腾讯（零认证）
- `qt.gtimg.cn/q=` 批量行情（GBK）。封单量取 `f[10]`（买一量，手），与成交量 `f[36]`（手）同源同单位。

### 东财（保留备用，默认不参与扫描）
- `curl` + DoH 多 CDN 节点轮询，全节点失败 fail-fast。依赖本地 Cookie（push2/push2his 分域）。

当前 Chrome UA：同花顺 120 / 雪球·腾讯 147 / 东财 148。大面积失效时更新版本号即可。

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
| `sector_blacklist` | 概念板块黑名单 | 行业切换后默认种子为空 |
| `review_account_runs` | 账户级 review 批次 | 保存 UI 记录名称、策略参数、区间、初始资金、最终权益、收益率、最大回撤 |
| `review_account_snapshots` | 账户每日快照 | 保存现金、市值、总权益、收益曲线、当前持仓 |
| `review_account_trades` | 账户交割单 | 每笔买卖含 `reason_code` / `reason_text` / `signal_json` |
| `review_account_positions` | 已平仓持仓 | 保存买入/卖出价、退出原因、持有天数、实现收益 |
| `review_account_events` | 账户决策时间线 | 保存买入、卖出、持仓和空仓原因，供 UI 解释每日决策 |
| `live_account` | 实盘辅助纸上账户 | buy/sell 命令的账户（默认单账户 `default`），存初始资金、可用现金、策略参数 |
| `live_positions` | 实盘辅助持仓 | 每笔持仓含成本、最高浮盈/价、半仓标记、平仓退出字段（open/closed） |
| `live_trades` | 实盘辅助交割单 | 每笔 buy/sell 含 `command` / `reason_code` / `reason_text` / `signal_json` |

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
`review-account` 不替代现有 `review`，而是按真实账户逐交易日模拟：先根据止盈止损处理持仓，再从**近 `candidate_lookback_days`（默认 3）个交易日**的 `dragons_v2` 真龙池并集（按 `code` 去重、保留 rank 更优者）择优买入，同等信号下优先选择 `rank` 更高、综合分更高的股票；候选必须满足真龙标记与综合分门槛（默认 50）。如果近 3 日都没有龙头记录，则当日不开仓，不再回退使用更早的有记录日期。账户允许多持仓，每次开仓使用可用现金买入，卖出当日释放的现金不再买入，次日起再按策略继续开仓。买入信号按优先级从高到低为：分歧买龙（400）> 开盘贴近 MA5 承接（300）> 开盘突破前高弱转强（200）。分歧买龙针对「连续缩量一字板 → 第一次断板 → 有承接才买」：用截至上日日 K 判定连续一字涨停（`divergence_min_boards` 默认 2 板）且期间缩量，当日开盘价低于「涨停价 × `divergence_break_open_ratio`（默认 0.998）」视为断板，再用断板日开盘后 `divergence_confirm_bars`（默认 6 根=30 分钟）5 分钟 K 确认承接——窗口内最低不破昨收且收盘企稳，或回封涨停即买入，5 分钟 K 缺失则跳过；分歧买点**不套用成交额/换手率/非一字板过滤**，改用连板+缩量+盘中承接把关，成交价取承接窗口末根收盘价（回封则取涨停价）。开盘两类买点仍要求成交额（默认 2 亿）、换手率门槛与非一字板，按当日开盘价成交。卖出信号保留 T+1 约束（买入当日不可卖出，模拟器对 `entry_date==day` 跳过卖出，首个可卖日为 `hold_days==1`）：硬止损优先，买入后首个可卖日（`hold_days<=1`）用 `first_day_stop_loss_pct`（默认 -3.5%）、持有第 2 日起用 `stop_loss_pct`（默认 -5%），触发 `first_day_stop_loss` / `hard_stop_loss`；峰值浮盈（用上一日 `previous_highest_return` 避免未来函数）达到 `trailing_activate_pct`（默认 8%）后启用移动止盈 `trailing_take_profit`，收盘自 `highest_price` 回撤 `trailing_drawdown_pct`（默认 3.5%）或跌破 MA5 则离场；最高浮盈达到 `breakeven_activate_pct`（默认 6%）后，只有在已有浮盈记录或 5 分钟 K 能确认先浮盈后回落时，才按覆盖买入费、卖出滑点及卖出费用的完整成本线保护性卖出（移动止盈层级高于保本，峰值∈[6%,8%) 仍走保本）；开盘高开 7% 以上且 5 分钟内未涨停清仓，开盘高开 5% 以上且 30 分钟内未涨停清仓（历史 5 分钟线缺失时跳过窗口规则）；买入次日收盘涨停卖出半仓，若同时收盘转弱则清仓；弱势清仓加容忍带 `weak_close_tolerance_pct`（默认 1%，`_weak_close_break`）：收盘小幅低于开盘但仍站上 MA5/昨收视为洗盘、继续持有，否则清仓（作用于 `next_day_close_below_open` 与 `close_below_open_stop`）；常规持仓收盘低于日内均线（当前用 MA5 近似）止损，成交量较上日增加 30% 且未涨停则清仓，涨停则继续持有。不再按收盘高于 MA5 止盈，也不按最长持有天数强制卖出。回测仅纳入已收盘交易日，区间末未平仓持仓按最后可得收盘价估值。完整策略见 `dragon_quant/review_account/STRATEGY.md`。UI 独立页面为 `/account`，展示账户持仓、交割单、权益和收益率曲线，并提供“生成回测记录”入口，可在 Modal 中填写记录名称、日期范围和初始资金后直接生成账户回测批次。批次下拉框右侧提供删除按钮（弹窗二次确认），调用 `DELETE /api/account/runs?run_id=` 删除该批次并级联清理其快照/交割单/持仓/时间线（`db.delete_review_account_run` 显式删除子表，不依赖 `PRAGMA foreign_keys`）；首次打开且无任何记录时展示引导空态并给出“生成回测记录”按钮。

### Web UI 前端构建
源码 `web_ui/frontend/`（Vite+React+TS+Mantine），产物 `web_ui/dist/`（已入库随包分发）。运行期仅靠 Python stdlib 托管，**不需要 Node**；改前端时才需 `npm run build`。

### 实盘辅助交易（buy / sell / account）
```bash
python -m dragon_quant account init --capital 100000
python -m dragon_quant buy --date 20260904 --capital 100000
python -m dragon_quant sell --date 20260905
python -m dragon_quant account
```
`buy` / `sell` 把 `review_account` 操盘策略用于每日实盘辅助决策，**策略逻辑 100% 复用**（同一套 `StrategyConfig` / `evaluate_buy` / `evaluate_sell`）。新增模块 `dragon_quant/live_trade/`：`row_builder.py`（用腾讯实时 `Quote` + 雪球历史日 K 现场拼出策略消费的 `row`：buy 用今日开盘价拼开盘决策 row，sell 用实时快照合成今日 KBar 追加历史后 `enrich_daily_klines` 取末行）、`trader.py`（`LiveTrader.buy/sell`）、`service.py`（`run_buy/run_sell/run_account_status/init_account`）。持久化为默认单账户三表 `live_account` / `live_positions` / `live_trades`。
- `buy`（9:25）：近 `candidate_lookback_days`（默认 3）个有龙头记录交易日票池并集去重，用实时开盘价判定**开盘买点**（贴近 MA5 / 突破前高弱转强），择优后默认开盘价整手买入并记账。**9:25 无当日 5 分钟 K，不评估分歧买龙**（待 easy-tdx）。
- `sell`（14:55）：对已持仓按 `review_account` 卖出优先级判定并记账；**严格 T+1**：`entry_date == 交易日` 的持仓（当日买入）跳过卖出。
- 交易日期默认 Asia/Shanghai 当日，可 `--date YYYYMMDD` 覆盖（补录/回放）。成交价/半仓复用 review_account 同款逻辑。

### 评分器接口约定
评分器统一签名，是 **cache 消费者**（只读 `cache.get(key)`，不发请求）：
```python
def score(code: str, cache: DataCache, **kwargs) -> ScoreResult
```
- `scorers/aggregator.evaluate()` 统一调度五维 + 门槛聚合，产出 `DragonVerdict`；四大特征异常必须否决，资金承接异常降级 50 分且不否决。
- 所有候选保留诊断综合分，但 `rank_verdicts()` 只给通过者赋真龙排名（同分按代码排序），否决票 `rank=None`；`raw_output.ranking` 与 `scan_stocks_v2` 保存全部诊断结果，`dragons_v2` 和最终报告只收 Top N 真龙。无真龙仍保存扫描，不用否决票补位；五日去重后不重排 rank。
- `rebuild_dragons_for_date` 仅使用非明确否决、rank 非空且不超原扫描 Top N 的贡献；不改旧表名与 ID，不自动重算历史记录。盘后重新取数评分需 `scan --force --no-cache`。
- cache 键：`kline:1min:{code}` / `kline:1min:SH000001`（上证指数）/ `kline:1min:sector:{s}` / `kline:5min:sector:{s}`（10日历史）/ `quotes:batch`（`list[Quote]`，含盘口）/ `sector:components:{s}`。裸代码 `000001` 是平安银行，不得用于指数请求或缓存。
- 腾讯行情按代码排序、每批最多 200 只获取并合并，不截断成分股总数；封板池只用同板块主板涨停候选，记录有效样本与缺失情况。
- 稳定封板按最后开板后持续封至最后有效分钟的起点排名，同分钟并列；脉冲使用局部峰抑制，每次匹配只能判带动或跟风之一，同步启动不判方向；相关性 bonus 仅看首次触板前。分时缺失或全程平线时带动子项 40 分降级。
- 抗跌反弹必须确认基准及个股实际回升，个股横盘不获反弹奖励；分钟时序窗口不跨午休、隔夜或双方缺点区间。
- 封单强度需确认现价涨停且买一价在涨停价附近；未涨停买一量不能当封单，从未触板的完整分时稳定性为 0。价格容差统一为涨停价的 0.1%。
- 承接对手盘是扫描日领跌 Top20 行业；六根五分钟 K 必须同连续交易时段，允许累计缓跌达标，不设单根跌超 0.5% 隐藏门槛；每个出逃板块独立通过不晚于拉升且间隔不超十分钟的检查，至少两个有效板块。
- 阈值/权重集中在 `scorers/registry.py`，便于回测调参。

### 必须遵守的约束
- **运行时依赖**：`playwright` 为必选（Cookie 自动获取 + 浏览器辅助）；其余仅用 Python 3 标准库。
- **跨平台**：数据目录用 `DQ_DATA_DIR` 覆盖，默认按平台存。
- **线程安全**：DataCache 操作持 `threading.Lock`；DB 每次操作独立连接 + WAL。
- **历史兼容**：旧 `*_v1` 表可显式查询；新扫描固定使用 `scorers/` 与 `*_v2` 表，不再保留旧四维评分代码。

### AI Agent 协作规范
> **任何代码修改或破坏性操作前，先输出技术方案（改动范围、涉及文件、风险点），等待用户确认后再执行。** 纯查询类操作（读文件、查数据库、搜索代码）不受此限。

### Cookie 失效处理
板块数据用同花顺（无需 Cookie）。个股依赖雪球 Cookie（有效期几天到数周），返回空/403 先查雪球 Cookie 状态。

### Git 规范
- 仓库：`gitBingxu/dragon-quant`；main 合入需 CODEOWNERS 审批。
- Commit 风格：中文 + emoji 前缀（见 git log）。
- **文档同步（强制）**：每次提交涉及功能/命令/接口/数据源/表结构变更时，**必须在同一 commit 内同步更新 `AGENTS.md` 与 `README.md`**，保证文档与代码一致；纯文档或纯内部重构可酌情豁免。
- **代码地图（codemap）**：模块结构/调用链/数据流有较大调整后，用 `/codemap` skill（`.trae/skills/codemap/`）生成或刷新 `CODEMAP.md`（执行路径、任务导航、不变式），供 agent 与人快速导航。

---

## 当前状态

### ✅ 已完成
- 五维「识别真龙」评分器 `scorers/`（带动/领涨/抗跌/流动/资金承接 + 门槛加权聚合），由 `scan` 命令触发
- 4 个 Provider（同花顺/东财/雪球/腾讯）含完整反爬；同花顺**行业板块**数据源（排行 curl+多页+本地排序+403退避、成分股、当日1分K、历史5分K）
- 封单数据走腾讯 gtimg 收盘盘口（`Quote.bid1_volume`）
- DB 概念板块黑名单表 + CLI `blacklist` 管理
- v2 物理分表（`scans_v2` / `scan_stocks_v2` / `scan_logs_v2` / `dragons_v2`）+ `review --source v1` 历史兼容 / Web UI source 切换
- 量价分析 `vpa/`、结构化日志 `logging/`、统一持久化 `storage/`、交易日历 `utils/trading.py`、龙头回测 `review.py`、Web UI
- 全量单测覆盖 `tests/test_scorers.py`、`tests/test_storage.py` 等核心路径

### ⚠️ 待完成/观察
- 同花顺数据网关 403 频控：高频访问会临时封 IP（已加退避重试，正常每日一两次扫描不触发）
- 东财历史 K 线 CDN 节点稳定性（保留备用链路）

### 📝 已知修复
- 同花顺排行字段 `tradezdf`→`zdf`，修复领跌榜全为正值的 bug
- v2 领涨性涨幅分位样本由「成分股」改为「候选池」，杜绝未拉日K成分股的 0 值污染
- v2 归一化涨幅曲线改用 `KBar.pct`，避免用首分钟价误当昨收
- v2 资金承接强度改为正向口径（出逃规模越大 + 拉升越高 → 分越高）
