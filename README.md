# dragon-quant 🐉

**龙头战法量化筛选系统** — A 股涨停板龙头识别工具

基于同花顺、雪球、腾讯三大公开数据源，对涨停候选股进行多维量化评分，自动识别市场龙头；同时提供日志查询、SQLite 持久化、龙头回测与 Web UI 可视化能力。

当前主流程使用**五维「识别真龙」评分体系**：带动性 30% / 领涨性 25% / 抗跌性 15% / 流动性 20% / 资金承接 10%，采用**门槛 + 加权两段式聚合**（四大特征任一低于门槛即一票否决，资金承接不否决仅加权贡献）。设计哲学：龙头不是预判出来的，是「识别」出来的。详见仓库内《评分器Refactor.md》。

> 为兼容历史数据，SQLite 物理表继续沿用 `*_v2`（如 `dragons_v2` / `scans_v2`），`scan_v2` 命令保留为隐藏兼容别名，行为等同 `scan`。旧 `*_v1` 表不再由主流程写入，仅可通过显式 `--source v1` 查询历史记录。

> 板块口径采用同花顺**行业板块**（`thshy`/`hyzjl`，约 90 个真实行业，code 为 881xxx）。

## 📊 龙头回测成绩单（历史样本）

> 入选后第一个非一字板日以最低价买入；最大收益按收益观察窗口统计，最大回撤按「买入日至最大收益出现日」窗口统计。

| 排名 | 代码 | 名称 | 入选日 | 综合分 | 买入日 | 买入价 | 最大收益% | 最大回撤% |
|------|------|------|--------|--------|--------|--------|-----------|-----------|
| 1 | 002552 | 宝鼎科技 | 2026-05-22 | 77.8 | 2026-05-25 | 41.02 | +51.37 | +0.00 |
| 2 | 000636 | 风华高科 | 2026-05-22 | 83.0 | 2026-05-26 | 40.18 | +50.72 | +0.00 |
| 3 | 600172 | 黄河旋风 | 2026-05-22 | 75.1 | 2026-05-25 | 11.16 | +43.91 | +0.00 |
| 4 | 000725 | 京东方Ａ | 2026-05-21 | 77.5 | 2026-05-22 | 4.47 | +36.24 | +0.00 |
| 5 | 603989 | 艾华集团 | 2026-05-22 | 81.1 | 2026-05-25 | 29.00 | +32.48 | +0.00 |
| 6 | 002579 | 中京电子 | 2026-05-26 | 62.2 | 2026-05-27 | 15.99 | +29.83 | +0.00 |
| 7 | 002585 | 双星新材 | 2026-05-22 | 72.4 | 2026-05-25 | 9.62 | +22.66 | +0.00 |
| 8 | 002975 | 博杰股份 | 2026-05-25 | 79.4 | 2026-05-26 | 125.00 | +21.55 | -5.60 |
| 9 | 600707 | 彩虹股份 | 2026-05-21 | 82.8 | 2026-05-22 | 10.57 | +21.38 | +0.00 |
| 10 | 002952 | 亚世光电 | 2026-05-21 | 78.5 | 2026-05-22 | 28.80 | +17.15 | +0.00 |

## 安装

```bash
pip install dragon-quant
# 或从源码
git clone https://github.com/gitBingxu/dragon-quant.git
cd dragon-quant && pip install -e .

# Playwright（雪球 Cookie 自动获取所需）
playwright install chromium
```

## 快速开始

```bash
# 查看 Linux 风格帮助提示
dragon-quant -h
dragon-quant scan -h

# 五维「识别真龙」扫榜 — 找 top5 龙头
dragon-quant scan --top 5

# 强制执行（跳过交易时段拦截 + DB 缓存）
dragon-quant scan --force

# 龙头回测 + Web UI
dragon-quant review --ui
# 查看龙头回测面板（默认读取 dragons_v2）
dragon-quant review --ui-only
```

### 前置条件

板块数据用**同花顺**，**无需 Cookie**（curl + GBK 直取）。个股数据依赖雪球 Cookie：

```bash
# 查看状态
dragon-quant data cookie-status

# 手动设置雪球 Cookie（推荐）
python3 -m dragon_quant.providers.cookie set --source xq --cookie 'xq_a_token=...; xq_is_login=1; u=...'

# 自动获取（需要 playwright）
dragon-quant data cookie-fetch          # 默认仅刷新雪球
```

Cookie 文件位置：`~/Library/Application Support/dragon-quant/cookies/{xueqiu,eastmoney}`

## CLI 命令大全

### `scan` — 扫榜

```bash
dragon-quant scan [--top 25] [--candidates 5] [--workers 2] [--force]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--top` | 25 | 最终输出的候选股数量 |
| `--candidates` | 5 | 兼容参数；当前五维路径按每个领涨行业当日全部涨停股入池 |
| `--workers` | 2 | 并发线程数 |
| `--force` | - | 跳过交易时段拦截与 DB 缓存 |

`scan` 走五维「识别真龙」体系。输出包含：板块排行（领涨/领跌明细）、候选股列表、评分表格、自然语言详细报告，并自动持久化到 `~/Library/Application Support/dragon-quant/` 的 `*_v2` 表。`scan_v2` 仍可用于旧脚本兼容，但帮助文档不再展示。

### `blacklist` — 概念板块黑名单

拉取领涨/领跌板块时按子串过滤（行业板块切换后默认种子为空，按需维护）。

```bash
dragon-quant blacklist list
dragon-quant blacklist add "次新股"
dragon-quant blacklist remove "次新股"
```

### `review` — 龙头回测

```bash
dragon-quant review                       # 自动筛 5~20 交易日内 pending 票全回测
dragon-quant review --date 20260519 --top 5
dragon-quant review --date 20260519
dragon-quant review --force --date 20260519
dragon-quant review --ui                  # 回测后启动 Web UI（默认展示 dragons_v2）
dragon-quant review --ui-only --port 8765 # 仅看结果（默认 dragons_v2）
```

`review` 默认读取/写回 `dragons_v2`；`--source v1` 仅用于查询和回测历史旧表。回测流程：从对应 `dragons_*` 表读 pending 龙头 → 找入选后第一个非一字板日（`high != low`）以最低价买入 → 算 `max_return_5d` / `max_return_hold_days` → 按买入日至峰值窗口算 `max_drawdown_5d` → 写回对应 DB 表。回测时对每只 pending 个股追加一段**量价分析**，结论写入独立的 `vpa_analysis` 表。

### `review-account` — 账户级模拟交易

```bash
dragon-quant review-account --from 20260501 --to 20260601
dragon-quant review-account --from 20260501 --to 20260601 --capital 200000 --ui
dragon-quant review-account --ui-only --source v2
```

`review-account` 保留现有 `review` 不变，新增账户级交易模拟：按交易日推进账户现金、持仓、交割单和权益曲线。也可以先用 `dragon-quant review-account --ui-only --source v2` 打开 `/account`，在页面点击“生成回测记录”，填写记录名称、日期范围和初始资金后由 UI 生成新批次。第一版策略为 `dragon_pullback_daily`，候选池取**近 `candidate_lookback_days`（默认 3）个交易日**的 `dragons_v2` 真龙池并集去重（按 `code` 保留 rank 更优者），同等信号下优先选择 `rank` 更高、综合分更高的股票；候选必须满足真龙标记与综合分门槛（默认 50）。如果近 3 日都没有龙头记录，则当日不开仓，不再回退使用更早的有记录日期。账户允许多持仓，每次开仓使用可用现金买入，卖出当日释放的现金不再买入，次日起再按策略继续开仓。买入信号按优先级从高到低为：**分歧买龙 > 开盘贴近 MA5 承接 > 开盘突破前高弱转强**。分歧买龙针对「连续缩量一字板 → 第一次断板 → 有承接才买」：用截至上日日 K 判定连续一字涨停（默认 ≥2 板）且期间缩量，当日开盘价低于涨停价视为断板，再用断板日开盘后 30 分钟（默认 6 根 5 分钟 K）确认承接——窗口内最低不破昨收且收盘企稳，或回封涨停即买入，5 分钟 K 缺失则跳过；分歧买点**不套用成交额/换手率/非一字板过滤**，改用连板+缩量+盘中承接把关。开盘两类买点仍要求成交额（默认 2 亿）、换手率门槛与非一字板。卖出策略保留 T+1 约束：硬止损优先；最高浮盈达到 `breakeven_activate_pct` 后，只有在已有浮盈记录或 5 分钟 K 能确认先浮盈后回落时，才按覆盖买入费、卖出滑点及卖出费用的完整成本线保护性卖出；开盘高开 7% 以上且 5 分钟内未涨停清仓，开盘高开 5% 以上且 30 分钟内未涨停清仓（历史 5 分钟线缺失时跳过窗口规则）；买入次日收盘涨停则卖出半仓，若同时收盘转弱则清仓；买入次日未涨停且收盘低于开盘价则清仓；常规持仓按收盘低于开盘价、收盘低于日内均线（当前用 MA5 近似）止损，成交量较上日增加 30% 且未涨停则清仓，涨停则继续持有。不再按收盘高于 MA5 止盈，也不按最长持有天数强制卖出。回测仅纳入已收盘交易日，区间末未平仓持仓按最后可得收盘价估值。每笔交割单保存 `reason_code` / `reason_text` / `signal_json`，用于解释买入卖出逻辑。

### `vpa` — 量价分析

```bash
dragon-quant vpa --code 600519 [--source xueqiu] [--days 60] [--no-save]
```

独立于评分体系的量价健康度验证模块，基于「多空博弈 + 量能验证」，内置 4 个插件式因子：量额灵敏度 / 趋势量价验证 / 突破放量验证 / 量价背离。输出健康度（0-100）+ 偏多/中性/偏空信号 + 判断依据，定位「验证器」而非买卖指令。

### `data` — 原子数据查询

```bash
dragon-quant data sector [--asc]                       # 行业板块涨/跌幅榜
dragon-quant data components --sector 881167           # 行业成分股（同花顺 6 位代码）
dragon-quant data kline --code 600172 [--days 20]      # 个股日 K
dragon-quant data minute --code 600172                 # 个股 1 分 K（分时）
dragon-quant data quote --code 600172                  # 实时行情
dragon-quant data batch-quote --codes 600172,000001    # 批量行情
dragon-quant data cookie-status                        # Cookie 状态
```

### `logs` / `storage` — 日志与数据管理

```bash
dragon-quant logs tail [-n 20]
dragon-quant logs --source v2 query [--date 20260513] [--category scorer:drive] [--level error] [--code 600172]
dragon-quant logs --source v2 summary
dragon-quant logs clear --days 7

dragon-quant storage status      # 存储状态
dragon-quant storage size        # 磁盘占用
dragon-quant storage clear --all # 清理全部
```

## Programmatic API

```python
import dragon_quant

result = dragon_quant.scan(top_n=5, candidates_n=5, workers=2)

# 返回 dict：
# {
#   "timestamp": "...", "elapsed_s": 38.2,
#   "sectors": {"up": [...], "down": [...]},
#   "ranking": [
#     {"code": "...", "name": "...", "concepts": [...], "board_count": 3,
#      "composite_score": 73.5,
#      "is_true_dragon": true, "reject_reason": null,
#      "dimensions": {"drive": {...}, "leadership": {...}, "anti_drop": {...},
#                     "liquidity": {...}, "absorption": {...}}}
#   ],
#   "report_text": "..."
# }
```

原子数据查询：

```python
from dragon_quant.data import (
    get_sector_ranking, get_sector_components,
    get_kline, get_minute_kline, get_quote, batch_get_quotes,
    cookie_status, fetch_cookies,
)

sectors = get_sector_ranking(asc=False)        # 行业涨幅榜
stocks = get_sector_components("881167")       # 行业成分股
kline = get_kline("600172", days=30)
quote = get_quote("600172")
```

## 评分体系

### 五维「识别真龙」

| 维度 | 权重 | 门槛 | 衡量（仅当日盘面，资金承接回看10日）|
|------|------|------|------|
| 带动性 | 30% | 40 | 封板最早 + 带动板块（脉冲-跟随因果检测）+ 板块共鸣 |
| 领涨性 | 25% | 40 | 连板最多 + 5日涨幅在板块内分位 |
| 抗跌性 | 15% | 35 | 大盘 + 板块**双基准**横盘稳住 + 率先起飞 |
| 流动性 | 20% | 35 | 换手充沛度 + 封板质量（封单/开板次数，一字不罚）|
| 资金承接 | 10% | — | 跨板块虹吸（出逃规模越大 + 拉升越高 → 分越高）|

聚合：四大特征任一 < 门槛 → 一票否决（非真龙）；通过者按综合分降序排名。资金承接不否决，仅加权贡献。阈值/权重集中在 `scorers/registry.py`，便于回测调参。

## 数据源

| 数据源 | 用途 | Cookie |
|---|---|---|
| 同花顺 | 行业板块排行 / 成分股 / 板块当日1分K / 历史5分K | 无需 |
| 雪球 | 个股日 K / 当日 1 分 K | 需要 |
| 腾讯 | 批量实时行情 + 收盘盘口（买一封单量）| 无需 |

> 东财 provider 仍保留但默认不参与扫描，可作回退。封单数据走腾讯 gtimg 收盘盘口（盘后仍保留收盘瞬间状态）。

## 目录结构

```
dragon_quant/
├── cli.py                # CLI（scan/logs/data/review/vpa/storage/blacklist）
├── orchestrator.py       # 编排器（Phase A→F，固定五维评分）
├── data.py               # 原子数据查询 API
├── rate_limit.py         # 并发限流器
├── providers/            # 数据源适配（ths/eastmoney/xueqiu/tencent/browser/cookie）
├── scorers/           # 五维评分器 + registry + aggregator
├── vpa/                  # 量价分析（插件式因子）
├── cache/                # 内存+本地双缓存
├── logging/              # ScanLogger + ReportBuilder + query
├── storage/              # paths / db（SQLite）/ manager
├── utils/trading.py     # 交易日历工具
├── review.py             # 龙头回测
├── web_ui/               # 回测 Web UI（Vite+React+TS / stdlib HTTPServer）
└── models/types.py      # 数据模型
```

## 设计原则

1. **Provider 抽象**：所有数据源实现 `StockProvider` 接口，评分器只依赖接口，可无缝切换/新增数据源。
2. **评分器是 cache 消费者**：统一签名 `score(code, cache, **kwargs) -> ScoreResult`，只读缓存不发请求；编排器 Phase A→D 预填，Phase E 打分。
3. **并发与限流**：`RateLimiter` 按 provider 串行排队 + 随机延迟，不同 provider 并发。
4. **结构化日志**：`ScanLogger` 全链路打点，支持按类别/级别/代码查询。
5. **历史兼容**：主流程固定写 `*_v2` 表；旧 `*_v1` 表保留显式查询能力，不参与新扫描。

## 持久化

SQLite 表分为三类：

- 当前主流程：`scans_v2` / `scan_stocks_v2` / `scan_logs_v2` / `dragons_v2`
- 历史旧表：`scans_v1` / `scan_stocks_v1` / `scan_logs_v1` / `dragons_v1`（仅显式 `--source v1` 查询）
- 共享表：`vpa_analysis` / `sector_blacklist`
- 账户级 review 表：`review_account_runs` / `review_account_snapshots` / `review_account_trades` / `review_account_positions` / `review_account_events`

运行时不创建旧无后缀 `scans` / `scan_stocks` / `scan_logs` / `dragons` 表；新扫描固定写 `source="v2"` 和 `*_v2` 表，以兼容已存在的 v2 历史数据。

`dragons_v2` 表关键字段：
- `source`：固定为 `v2`；旧 `dragons_v1` 仅用于历史记录。
- `version`：入库时的包版本号。
- review 字段：`buy_date` / `buy_price` / `max_return_5d` / `max_drawdown_5d` / `max_return_hold_days` / `review_status`，按 source 独立维护。

`scan_stocks_v2` 填充 `dim_liquidity` / `is_true_dragon` / `reject_reason` 等五维识别字段。

`review_account_*` 表用于账户级模拟交易：`runs` 保存 UI 记录名称、策略参数与汇总，`snapshots` 保存每日权益/现金/持仓快照，`trades` 保存交割单及买卖逻辑，`positions` 保存已平仓持仓的收益和退出原因，`events` 保存买入、卖出、持仓和空仓原因时间线。

## License

MIT
