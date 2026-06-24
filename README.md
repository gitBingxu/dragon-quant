# dragon-quant 🐉

**龙头战法量化筛选系统** — A 股涨停板龙头识别工具

基于同花顺、雪球、腾讯三大公开数据源，对涨停候选股进行多维量化评分，自动识别市场龙头；同时提供日志查询、SQLite 持久化、龙头回测与 Web UI 可视化能力。

内置**两套评分体系**，分别由 `scan`（v1）与 `scan_v2`（v2）命令触发、并存互不影响：

- **v1（默认，四维加权）**：带动性 35% / 领涨性 25% / 抗跌性 15% / 资金承接 25%，简单加权求和。
- **v2（五维「识别真龙」）**：带动性 30% / 领涨性 25% / 抗跌性 15% / 流动性 20% / 资金承接 10%，**门槛 + 加权两段式聚合**（四大特征任一低于门槛即一票否决，资金承接不否决仅加权贡献）。设计哲学：龙头不是预判出来的，是「识别」出来的。详见仓库内《评分器Refactor.md》。

> 板块口径采用同花顺**行业板块**（`thshy`/`hyzjl`，约 90 个真实行业，code 为 881xxx）。

## 📊 龙头回测成绩单（v1 历史样本）

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
# v1 扫榜 — 找 top5 龙头
dragon-quant scan --top 5

# v2 五维「识别真龙」
dragon-quant scan_v2 --top 5

# 强制执行（跳过交易时段拦截 + DB 缓存）
dragon-quant scan_v2 --force

# 龙头回测 + Web UI
dragon-quant review --ui
# 查看 v2 龙头回测面板
dragon-quant review --ui-only --source v2
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

### `scan` / `scan_v2` — 扫榜

```bash
dragon-quant scan    [--top 25] [--candidates 5] [--workers 2] [--force]   # v1 四维
dragon-quant scan_v2 [--top 25] [--candidates 5] [--workers 2] [--force]   # v2 五维识别真龙
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--top` | 25 | 最终输出的候选股数量 |
| `--candidates` | 5 | 每个板块取前 N 只（仅 `scan`；`scan_v2` 取当日全部涨停股）|
| `--workers` | 2 | 并发线程数 |
| `--force` | - | 跳过交易时段拦截与 DB 缓存 |

两命令参数一致，区别仅在评分体系：`scan` 走 v1 四维，`scan_v2` 走 v2 五维「识别真龙」。输出包含：板块排行（领涨/领跌明细）、候选股列表、评分表格、自然语言详细报告，并自动持久化到 `~/Library/Application Support/dragon-quant/`。

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
dragon-quant review --source v2 --date 20260519
dragon-quant review --force --date 20260519
dragon-quant review --ui --source v2      # 回测后启动 Web UI（默认展示 v2）
dragon-quant review --ui-only --port 8765 # 仅看结果（默认 v1，可加 --source v2）
```

`--source` 用于选择回测哪套龙头表：`v1` 读取/写回 `dragons_v1`，`v2` 读取/写回 `dragons_v2`。回测流程：从对应 `dragons_*` 表读 pending 龙头 → 找入选后第一个非一字板日（`high != low`）以最低价买入 → 算 `max_return_5d` / `max_return_hold_days` → 按买入日至峰值窗口算 `max_drawdown_5d` → 写回对应 DB 表。回测时对每只 pending 个股追加一段**量价分析**，结论写入独立的 `vpa_analysis` 表。

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
dragon-quant logs --source v1 tail [-n 20]
dragon-quant logs --source v2 query [--date 20260513] [--category scorer:drive] [--level error] [--code 600172]
dragon-quant logs --source v2 summary
dragon-quant logs --source v1 clear --days 7

dragon-quant storage status      # 存储状态
dragon-quant storage size        # 磁盘占用
dragon-quant storage clear --all # 清理全部
```

## Programmatic API

```python
import dragon_quant

# v1 扫描
result = dragon_quant.scan(top_n=5, candidates_n=5, workers=2)
# v2 五维识别真龙
result = dragon_quant.scan(top_n=5, scorers="v2")

# 返回 dict：
# {
#   "timestamp": "...", "elapsed_s": 38.2,
#   "sectors": {"up": [...], "down": [...]},
#   "ranking": [
#     {"code": "...", "name": "...", "concepts": [...], "board_count": 3,
#      "composite_score": 73.5,
#      "is_true_dragon": true, "reject_reason": null,   # v2 专有
#      "dimensions": {"drive": {...}, "leadership": {...}, "anti_drop": {...},
#                     "liquidity": {...}, "absorption": {...}}}  # v2 为五维
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

### v1 四维（默认）

| 维度 | 权重 | 衡量 |
|------|------|------|
| 带动性 | 35% | 涨停后对同板块小弟的带动效应（板块共鸣 + 跟风力度 + 封板决策力）|
| 领涨性 | 25% | 在行业内的日常空间排名（当日真实分位 + 历史估算）|
| 抗跌性 | 15% | 大盘跳水日的相对回撤 + 日内承接 + 反弹弹性 |
| 资金承接 | 25% | 市场恐慌时跨板块资金虹吸（强度 + 广度 + 持续性）|

### v2 五维「识别真龙」

| 维度 | 权重 | 门槛 | 衡量（仅当日盘面，资金承接回看10日）|
|------|------|------|------|
| 带动性 | 30% | 40 | 封板最早 + 带动板块（脉冲-跟随因果检测）+ 板块共鸣 |
| 领涨性 | 25% | 40 | 连板最多 + 5日涨幅在板块内分位 |
| 抗跌性 | 15% | 35 | 大盘 + 板块**双基准**横盘稳住 + 率先起飞 |
| 流动性 | 20% | 35 | 换手充沛度 + 封板质量（封单/开板次数，一字不罚）|
| 资金承接 | 10% | — | 跨板块虹吸（出逃规模越大 + 拉升越高 → 分越高）|

聚合：四大特征任一 < 门槛 → 一票否决（非真龙）；通过者按综合分降序排名。资金承接不否决，仅加权贡献。阈值/权重集中在 `scorers_v2/registry.py`，便于回测调参。

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
├── orchestrator.py       # 编排器（Phase A→F，含 v1/v2 双分支）
├── data.py               # 原子数据查询 API
├── rate_limit.py         # 并发限流器
├── providers/            # 数据源适配（ths/eastmoney/xueqiu/tencent/browser/cookie）
├── scorers/              # v1 四维评分器
├── scorers_v2/           # v2 五维评分器 + registry + aggregator
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
5. **新旧并存**：v1 与 v2 除数据拉取与编排器外全程隔离，可灰度对比与回滚。

## 持久化

SQLite 表分为三类：

- v1 体系：`scans_v1` / `scan_stocks_v1` / `scan_logs_v1` / `dragons_v1`
- v2 体系：`scans_v2` / `scan_stocks_v2` / `scan_logs_v2` / `dragons_v2`
- 共享表：`vpa_analysis` / `sector_blacklist`

运行时只创建和读写 `*_v1` / `*_v2` 分表，不再创建旧 `scans` / `scan_stocks` / `scan_logs` / `dragons` 表；`source` 是唯一版本路由字段。

`dragons_v1` / `dragons_v2` 表关键字段：
- `source`：固定为 `v1` 或 `v2`。
- `version`：入库时的包版本号。
- review 字段：`buy_date` / `buy_price` / `max_return_5d` / `max_drawdown_5d` / `max_return_hold_days` / `review_status`，按 source 独立维护。

`scan_stocks_v1` / `scan_stocks_v2` 为同构表，v2 会额外填充 `dim_liquidity` / `is_true_dragon` / `reject_reason` 等五维识别字段。

## License

MIT
