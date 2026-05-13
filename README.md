# dragon-quant 🐉

**龙头战法四维量化筛选系统** — A 股涨停板龙头识别工具

基于东方财富、雪球、腾讯三大公开数据源，对涨停候选股进行四维量化评分，自动识别市场龙头。

## 安装

```bash
pip install dragon-quant
```

或从源码安装：

```bash
git clone https://github.com/gitBingxu/dragon-quant.git
cd dragon-quant
pip install -e .
```

## 快速开始

```bash
# 扫榜 — 找 top5 龙头
dragon-quant scan --top 5

# 完整扫榜
dragon-quant scan --top 25 --candidates 5 --workers 2
```

## CLI 命令大全

### `scan` — 扫榜

```bash
dragon-quant scan [--top 25] [--candidates 5] [--workers 2]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--top` | 25 | 最终输出的候选股数量 |
| `--candidates` | 5 | 每个板块取前 N 只 |
| `--workers` | 2 | 并发线程数 |

输出包含：板块排行、候选股列表、四维评分表格、自然语言详细报告，同时自动持久化日志和结果到 `~/Library/Application Support/dragon-quant/`。

### `logs` — 日志查询

```bash
# 查看最近 20 条日志
dragon-quant logs tail [-n 20]

# 按条件查询
dragon-quant logs query [--date 20260513] [--category scorer:drive] [--level error] [--code 600172]

# 查看最新扫描摘要（API 统计、错误数等）
dragon-quant logs summary

# 列出所有日志文件
dragon-quant logs list

# 清除 7 天前的日志
dragon-quant logs clear --days 7
```

### `data` — 原子数据查询

```bash
# 板块排行榜
dragon-quant data sector          # 涨幅榜
dragon-quant data sector --asc    # 跌幅榜

# 板块成分股
dragon-quant data components --sector BK0487

# 个股日 K 线
dragon-quant data kline --code 600172 [--source xueqiu] [--days 20]

# 个股 1 分钟 K 线（分时）
dragon-quant data minute --code 600172

# 实时行情
dragon-quant data quote --code 600172
dragon-quant data batch-quote --codes 600172,000001,002409

# Cookie 管理
dragon-quant data cookie-status    # 查看 Cookie 状态
dragon-quant data cookie-fetch     # 刷新全部 Cookie
dragon-quant data cookie-fetch --source xueqiu  # 只刷新雪球
```

### `storage` — 数据管理

```bash
dragon-quant storage status      # 查看存储状态
dragon-quant storage size        # 磁盘占用
dragon-quant storage clear --all # 清理全部数据
dragon-quant storage clear --logs --days 3  # 清理 3 天前的日志
```

## Programmtic API

### 编排器 — 完整扫描

```python
import dragon_quant

result = dragon_quant.scan(top_n=5, candidates_n=5, workers=2)
# 返回 dict:
# {
#   "timestamp": "20260513_160000",
#   "elapsed_s": 38.2,
#   "sectors": {"up": [...], "down": [...]},
#   "ranking": [
#     {
#       "code": "600172", "name": "黄河旋风",
#       "concepts": ["培育钻石"], "board_count": 3,
#       "composite_score": 71.8,
#       "dimensions": {
#         "drive": {"score": 99.0, "weight": 0.35, "details": {...}},
#         "anti_drop": {"score": 61.0, "weight": 0.15, "details": {...}},
#         "leadership": {"score": 50.0, "weight": 0.25, "details": {...}},
#         "absorption": {"score": 62.0, "weight": 0.25, "details": {...}},
#       }
#     },
#     ...
#   ],
#   "api_stats": {...},
#   "report_text": "黄河旋风(600172)——培育钻石——3连板-71.8分-强票\n..."
# }
```

### 原子数据查询

```python
from dragon_quant.data import (
    get_sector_ranking, get_sector_components, get_sector_5min_kline,
    get_kline, get_minute_kline, get_quote, batch_get_quotes,
    cookie_status, fetch_cookies,
)

# 板块
sectors = get_sector_ranking()                 # 涨幅榜
stocks = get_sector_components("BK0487")       # 成分股
skline = get_sector_5min_kline("BK0487")       # 板块 5 分 K

# 个股
kline = get_kline("600172", source="xueqiu", days=30)
mline = get_minute_kline("600172")             # 1 分 K
quote = get_quote("600172")                    # 实时行情
quotes = batch_get_quotes(["600172", "000001", "002409"])

# Cookie 管理
status = cookie_status()                       # 查看 Cookie 是否有效
fetch_cookies()                                # 刷新全部 Cookie
fetch_cookies(source="xueqiu")                 # 只刷新雪球 Cookie
```

### 日志查询

```python
from dragon_quant.logging.query import (
    tail_logs, query_logs, clear_logs, list_logs, log_summary,
)

# 最近 20 条
entries = tail_logs(20)

# 按条件查
errors = query_logs(level="error")
drive = query_logs(category="scorer:drive", code="600172")

# 扫描摘要
s = log_summary()  # {"api_stats": {...}, "error_count": 0, "phases": {...}}

# 列出日志文件
files = list_logs()

# 清除 7 天前
result = clear_logs(days=7)  # {"cleared": 3, "kept": 2, "files_removed": [...]}
```

## 四维评分体系

### 一、带动性（权重 35%）— "你是带头大哥吗？"

评估这只股票涨停后对同板块其他股票的带动效应。

- **板块共鸣（30%）**：同板块涨停股占比
- **跟风力度（30%）**：同板块非涨停股中涨幅 >3% 的比例
- **封板决策力（40%）**：封板时间早晚、在板块内的封板排名、小弟跟进紧密度
- 每多一连板额外 +5 分（封顶 100）

### 二、抗跌性（权重 15%）— "大盘崩了你扛得住吗？"

分析近期大盘跳水日（单日跌幅 < -0.7%）中个股表现。

- **相对回撤（40%）**：个股涨跌幅 vs 大盘涨跌幅
- **日内承接（30%）**：下影线比例 + 收盘位置
- **反弹弹性（30%）**：次日超额收益

### 三、领涨性（权重 25%）— "平时你在行业里排老几？"

不看涨停日，评估该股在同行业中的日常排名。

- **当日真实排名**：在行业成分股中的确切分位
- **历史估算排名**：近 5 个非涨停日的正态分布近似排位
- 偏离度加成：比行业中位数高出几个标准差

### 四、资金承接性（权重 25%）— "钱从别的板块跑你这来了吗？"

检测市场恐慌时跨板块的资金虹吸效应。

- 多板块 5 分 K 滑动窗口检测（≥2 个板块跌 >1% & 目标板块涨 >0.3%）
- **虹吸强度（40%）**：板块涨幅 / 窗口振幅
- **广度（20%）**：被抽血的板块数量
- **持续性（40%）**：尾盘回撤控制

## 数据源

| 数据源 | 用途 | 接口数 |
|---|---|---|
| 东方财富 | 板块排行、成分股、板块 5 分 K | 3 |
| 雪球 | 日 K 线、1 分 K 线 | 2 |
| 腾讯 | 实时行情、批量行情 | 2 |

## 设计思想

### 分层架构

```
┌─────────────────────────────────┐
│  CLI / Programmtic API          │  ← cli.py / __init__.py
├─────────────────────────────────┤
│  Orchestrator（编排器）          │  ← Phase A→F 全流程
├──────────┬──────────────────────┤
│  Scorers │  Logger & Reporter   │  ← 四维评分 / 结构化日志
├──────────┴──────────────────────┤
│  DataCache + RateLimiter        │  ← 内存/本地双缓存 + 限流
├─────────────────────────────────┤
│  Provider 适配层                 │  ← 东财 / 雪球 / 腾讯
└─────────────────────────────────┘
```

### 核心设计原则

1. **Provider 抽象**：所有数据源实现 `StockProvider` 接口，评分器只依赖接口不依赖具体实现，可无缝切换/新增数据源
2. **并发与限流**：`RateLimiter` 按 `(provider, endpoint)` 维度串行，不同 key 并发，控制 API 请求频率
3. **结构化日志**：`ScanLogger` 全链路打点，记录每次 API 调用、每项评分、每个阶段。支持按类别/级别/股票代码查询，方便排查问题
4. **结果持久化**：每次扫描自动保存 JSONL 日志、JSON 结果、文本报告三份文件，保留最新快照供 Agent 随时读取
5. **懒加载 Provider**：`data.py` 中 Provider 单例延迟初始化，模块 import 不触发网络请求

## 目录结构

```
dragon_quant/
├── __init__.py          # 公共 API 导出
├── __main__.py          # python -m 入口
├── cli.py               # CLI 命令（scan/logs/data/storage）
├── orchestrator.py      # 编排器（Phase A→F）
├── data.py              # 原子数据查询 API
├── providers/           # 数据源适配器
│   ├── base.py          # StockProvider 抽象接口
│   ├── eastmoney.py     # 东方财富
│   ├── xueqiu.py        # 雪球
│   ├── tencent.py       # 腾讯
│   └── cookie.py        # Cookie 管理
├── scorers/             # 四维评分器
│   ├── drive.py         # 带动性
│   ├── anti_drop.py     # 抗跌性
│   ├── leadership.py    # 领涨性
│   └── absorption.py    # 资金承接
├── models/
│   └── types.py         # 数据模型（KBar, Quote, ScoreResult...）
├── cache/
│   └── data_cache.py    # 内存+本地双缓存
├── logging/
│   ├── logger.py        # ScanLogger 结构化日志
│   ├── reporter.py      # ReportBuilder 自然语言报告
│   └── query.py         # 日志查询 API
├── storage/
│   ├── paths.py         # 数据目录管理
│   └── manager.py       # StorageManager
├── rate_limit.py        # 并发限流器
└── utils/
    └── __init__.py
```

## Agent 集成指南

本节展示 AI Agent 如何通过 Python API 调用 dragon-quant 完成常见任务。

### 场景 1：今日龙头扫榜 + 输出报告

```python
import dragon_quant

# 扫榜取 top5
result = dragon_quant.scan(top_n=5, candidates_n=5, workers=2)

print(f"🐉 今日龙头 TOP5 | 耗时 {result['elapsed_s']}s")
print()
print(f"{'排名':4s} {'代码':8s} {'名称':8s} {'综合':>6s} {'带动':>6s} {'抗跌':>6s} {'领涨':>6s} {'承接':>6s}")
print("-" * 56)
for i, r in enumerate(result["ranking"], 1):
    dims = r.get("dimensions", {})
    print(f"{i:4d} {r['code']:8s} {r['name']:8s} "
          f"{r['composite_score']:6.1f}  "
          f"{dims.get('drive',{}).get('score',0):6.1f}  "
          f"{dims.get('anti_drop',{}).get('score',0):6.1f}  "
          f"{dims.get('leadership',{}).get('score',0):6.1f}  "
          f"{dims.get('absorption',{}).get('score',0):6.1f}")

# 输出自然语言报告（可直接发给用户）
print()
print(result["report_text"])
```

### 场景 2：只取龙头排行数据，不打印（Agent 内部消费）

```python
import dragon_quant

result = dragon_quant.scan(top_n=10, verbose=False)

# 提取关键信息
for r in result["ranking"]:
    code = r["code"]
    name = r["name"]
    score = r["composite_score"]
    concepts = r.get("concepts", [])
    boards = r.get("board_count", 0)
    # 判断等级
    if score >= 80:
        grade = "🐲 龙头"
    elif score >= 65:
        grade = "🔥 强票"
    else:
        grade = "📊 一般"
    print(f"{grade} {code} {name} | {boards}连板 | {', '.join(concepts)} | 综合{score}")
```

### 场景 3：查某只股票的 K 线和实时行情

```python
from dragon_quant.data import get_kline, get_minute_kline, get_quote

code = "600172"

# 日K线
kline = get_kline(code, days=30)
print(f"{code} 最近 30 日 K 线:")
for k in kline[-5:]:  # 最近5天
    print(f"  {getattr(k, 'time', '?')} | "
          f"开{getattr(k, 'open', 0):.2f} 收{getattr(k, 'close', 0):.2f} "
          f"涨{getattr(k, 'pct', 0):.2f}%")

# 实时行情
quote = get_quote(code)
if quote:
    print(f"当前价: {quote.price} | 涨跌幅: {quote.pct}% | 换手率: {getattr(quote, 'turnover', 0):.2f}%")
```

### 场景 4：API 返回 400/空数据 → 刷新 Cookie

当 scan() 或数据查询返回 400 错误、空数据时，通常是 Cookie 过期了。刷新后重试即可。

```python
from dragon_quant.data import cookie_status, fetch_cookies

# 先查看状态
status = cookie_status()
for source, info in status.items():
    print(f"{source}: {'✅ 有效' if info['ok'] else '❌ 过期'} ({info['length']}字符)")

# 如果东财或雪球过期，刷新
if not status["eastmoney"]["ok"] or not status["xueqiu"]["ok"]:
    print("Cookie 过期，正在刷新...")
    fetch_cookies()
    # 刷新后重新检查
    new_status = cookie_status()
    for source, info in new_status.items():
        print(f"  {source}: {'✅' if info['ok'] else '❌'} ({info['length']}字符)")

# 刷新后重试 scan
import dragon_quant
result = dragon_quant.scan(top_n=5)
print(f"扫描成功，{len(result['ranking'])} 只候选")
```

### 场景 5：查板块热度（哪个方向最强）

```python
from dragon_quant.data import get_sector_ranking, get_sector_components

# 今日涨幅榜 top5 板块
sectors = get_sector_ranking(asc=False)[:5]
print("今日最强板块:")
for s in sectors:
    print(f"  {s.name} ({s.code}) | +{s.pct:.2f}%")

# 看龙头板块的涨停分布
if sectors:
    stocks = get_sector_components(sectors[0].code)
    up_limit = [s for s in stocks if s.pct and s.pct >= 9.9]
    print(f"\n{sectors[0].name} 涨停股 ({len(up_limit)} 只):")
    for s in up_limit:
        print(f"  {s.code} {s.name} | +{s.pct:.2f}%")
```

### 场景 6：排查问题 — 查看扫描日志

```python
from dragon_quant.logging.query import list_logs, tail_logs, query_logs, log_summary, clear_logs

# 列出所有日志文件
files = list_logs()
print(f"共 {len(files)} 个日志文件")
for f in files[:5]:
    print(f"  {f['name']} | {f['size']} | {f['lines']} 行")

# 查看最新扫描的摘要
summary = log_summary()
print(f"\n最新扫描: {summary['file']}")
print(f"  阶段: {list(summary['phases'].keys())}")
print(f"  API 调用: {summary['api_stats']['total']} 次 | 成功 {summary['api_stats']['ok']} | 失败 {summary['api_stats']['error']}")
print(f"  评分: {summary['scorer_count']} 次")
print(f"  错误: {summary['error_count']} 条")

# 如果有很多错误，查具体原因
if summary['error_count'] > 0:
    errors = query_logs(level="error", tail=10)
    print(f"\n最近 10 条错误:")
    for e in errors:
        print(f"  [{e.get('category', '')}] {e.get('message', '')}")
        if e.get('data', {}).get('exception'):
            print(f"    exception: {e['data']['exception'][:120]}")

# 查某只股票的评分细节
entries = query_logs(category="scorer", code="600172")
for e in entries:
    print(f"  {e['category']} → score={e.get('data',{}).get('score',0)}")
```

### 场景 7：清理日志

```python
from dragon_quant.logging.query import clear_logs, list_logs

# 清理前
before = list_logs()
print(f"清理前: {len(before)} 个日志文件")

# 保留最近 3 天
result = clear_logs(days=3)
print(f"删除了 {result['cleared']} 个文件 | 保留 {result['kept']} 个")
for f in result.get("files_removed", []):
    print(f"  - {f}")
```

### 场景 8：拿上一次扫描结果（无需重新跑）

```python
import json
from pathlib import Path

# macOS 默认路径
latest_path = Path.home() / "Library" / "Application Support" / "dragon-quant" / "results" / "latest.json"

if latest_path.exists():
    with open(latest_path) as f:
        data = json.load(f)

    print(f"上次扫描: {data['timestamp']} | 耗时 {data['elapsed_s']}s")
    for r in data["ranking"]:
        print(f"  {r['code']} {r['name']} — {r['composite_score']}分 — {r.get('board_count', 0)}连板")
else:
    print("暂无扫描缓存，运行一次 scan() 即可生成")
```

### 场景 9：批量获取多只票的行情对比

```python
from dragon_quant.data import batch_get_quotes

codes = ["600172", "605589", "603052", "603203", "603126"]
quotes = batch_get_quotes(codes)

print(f"{'代码':8s} {'价格':>8s} {'涨跌幅':>8s} {'换手率':>8s} {'量比':>6s}")
print("-" * 44)
for q in quotes:
    if q:
        price = getattr(q, 'price', 0)
        pct = getattr(q, 'pct', 0)
        turnover = getattr(q, 'turnover', 0)
        volume_ratio = getattr(q, 'volume_ratio', 0)
        print(f"{q.code:8s} {price:8.2f} {pct:+7.2f}% {turnover:7.2f}% {volume_ratio:6.2f}")
```

## License

MIT
