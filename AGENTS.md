# AGENTS.md — dragon-quant

> 龙头战法四维量化筛选系统 · AI Agent 操作手册

---

## 这是什么

一套纯 Python 3 零依赖（标准库 only）的 A 股龙头筛选系统。从当日涨停榜出发，通过**带动性、抗跌性、领涨性、资金承接**四个维度评估涨停股的龙头质量，加权综合分排名输出。

数据源：东方财富 + 雪球 + 腾讯公开 API，不依赖任何付费行情接口。

---

## 快速使用

```bash
# 批量扫描（默认全流程）
cd ~/repo/dragon-quant
python -m dragon_quant

# 参数
python -m dragon_quant --top 25 --candidates 5 --workers 2
```

### 前置条件

Cookie 必须先配好，否则东财和雪球接口返回空数据：

```bash
# 查看状态
python -c "from dragon_quant.providers.cookie import get_em, get_xq; print(f'东财: {bool(get_em())}, 雪球: {bool(get_xq())}')"

# 手动设置（推荐）
python -m dragon_quant.providers.cookie set --cookie "qgqp_b_id=...; st_nvi=..." --source em
python -m dragon_quant.providers.cookie set --cookie "xq_a_token=...; xq_is_login=1; u=..." --source xq

# 自动获取（需要 playwright）
python -m dragon_quant.providers.cookie fetch --source all
```

Cookie 文件位置：
- 东财：`~/Library/Application Support/dragon-quant/cookies/eastmoney`
- 雪球：`~/Library/Application Support/dragon-quant/cookies/xueqiu`

---

## 架构总览

```
dragon_quant/
├── __init__.py / __main__.py    # 入口
├── cli.py                       # argparse CLI
├── orchestrator.py              # 编排主流程 (Phase A→G)
├── rate_limit.py                # 分组并发调度器
├── analyze.py                   # 子进程打分入口（桩）
│
├── providers/                   # 数据源适配层
│   ├── base.py                  # StockProvider ABC (6 抽象方法)
│   ├── eastmoney.py             # 东财 — JSONP
│   ├── xueqiu.py                # 雪球 — 需 Cookie
│   ├── tencent.py               # 腾讯 — 零认证 + fallback
│   └── cookie.py                # Cookie 管理 + CLI
│
├── scorers/                     # 四维评分器（⚠️ 待实现）
│   ├── drive.py                 # 带动性 (35%)
│   ├── anti_drop.py             # 抗跌性 (15%)
│   ├── leadership.py            # 领涨性 (25%)
│   └── absorption.py            # 资金承接 (25%)
│
├── cache/
│   └── data_cache.py            # 内存+本地双重缓存
│
├── storage/                     # ⚠️ 待实现
├── logging/                     # ⚠️ 待实现
├── utils/                       # ⚠️ 待实现
└── models/
    └── types.py                 # dataclass 数据模型
```

---

## 执行流程

编排器 `orchestrator.run_scan()` 分 7 个阶段：

| Phase | 做什么 | 调用次数 | 关键点 |
|-------|--------|---------|--------|
| **A** 板块排行 | 东财·概念板块涨跌幅 Top10 | 2 次 | JSONP，反爬 Header 已内置 |
| **B** 候选筛选 | 每领涨板块取前5成分股，过滤 ST + 双创 | 20 次 | 去重 + 多概念跟踪 |
| **C** 连板+排序 | 雪球日K → 算连板天数 → 按(概念数, 连板数)排序取 Top25 | N+1 次 | 涨停阈值 ≥9.9% |
| **D** 并发加载 | 板块5分K + 个股5分K + 腾讯批量行情 | ~50 次 | RateLimiter 4 线程并发 |
| **E** 聚合 | 写入共享 JSON 文件 | 1 次 | 子进程只读 |
| **F** 并行打分 | subprocess 四维评分 | N 个 | ⚠️ 当前是桩 |
| **G** 输出 | 加权排序 + 报告 | — | — |

总耗时约 40-50 秒（取决于网络和并发数）。

---

## 数据模型

核心类型全部在 `models/types.py`：

- **KBar** — 一根 K 线（timestamp, OHLCV, 涨跌幅, 换手率, 成交额）
- **StockInfo** — 股票基本信息（code, name, exchange, sector）
- **Quote** — 实时行情快照（现价/涨跌幅/换手率/市值/PE/量比…）
- **SectorPerformance** — 板块行情（代码/名称/涨跌幅/振幅）
- **Candidate** — 候选股（code, concepts 列表, board_count, primary_sector, score）
- **ScoreResult** — 单维度评分结果（dim, score 0-100, weight, details dict）

---

## 并发模型

`RateLimiter` 的核心规则：
```
同一 provider + 同一 endpoint → 串行排队（防封 IP）
不同 key 之间 → 自由并发
```

用法：`limiter.submit("eastmoney", "sector_5min", fn, arg)` 之后 `limiter.wait_all()`

---

## 反爬要点

**8 个必须请求头**（每个 HTTP 请求都带）：Cookie, User-Agent, sec-ch-ua, sec-ch-ua-platform, Sec-Fetch-Dest, Sec-Fetch-Mode, Sec-Fetch-Site, Referer

- 东财 Referer：`https://quote.eastmoney.com/center/hsbk.html`（排行）/ `gridlist.html`（成分股）/ 动态 `bk/90.BKxxx.html`（K线）
- 雪球 Referer：`https://xueqiu.com/S/{SH/SZ}{code}`
- 腾讯 Referer：不需要（零认证）

当前 Chrome UA 版本：147。如果大面积失效，更新到最新 Chrome 版本号即可。

---

## 当前状态

### ✅ 已完成
- 数据模型（types.py）
- 全部 3 个 Provider（东财/雪球/腾讯），含完整反爬 Header
- Cookie 管理（手动设置 + Playwright 自动获取）
- RateLimiter 分组并发调度
- DataCache 内存+本地双重缓存 + 快照导出
- Orchestrator 编排全流程（Phase A→G 骨架）
- CLI 入口 + `__main__.py` 入口
- 技术方案文档（技术方案.md）

### ⚠️ 待完成（按优先级）

1. **四维评分器** `scorers/` — `drive.py`, `anti_drop.py`, `leadership.py`, `absorption.py`
   - 这是项目核心，目前 `analyze.py` 是桩（只列出缓存数据，不做实际打分）
   - 详细评分逻辑见 README.md 和技术方案文档
   - 每个评分器接受 `(code, DataCache)` → 返回 `ScoreResult`

2. **analyze.py 对接评分器** — 当前是 stub，需要从共享缓存提取数据、调用四维评分器、输出 JSON

3. **orchestrator.py Phase F 并行打分** — 当前 `Popen` 只跑一个子进程，需要改成 `workers` 个并发 + 汇总结果

4. **storage 模块** — `paths.py`（数据目录管理）、`local_store.py`（历史报告持久化）

5. **logging 模块** — 结构化日志 + ReportBuilder（输出格式化）

6. **单票分析 CLI** — `dragon-quant analyze <code>` 子命令

7. **测试** — 无

---

## 开发注意事项

### 必须遵守的约束
- **零依赖**：只用 Python 3 标准库（`urllib.request`, `json`, `threading`, `subprocess`, `dataclasses`）。`pyproject.toml` 里不应有任何 `dependencies`。
- **例外**：Cookie 自动获取需要 `playwright`，但这是可选功能，不影响核心流程运行。
- **跨平台**：数据目录用 `DQ_DATA_DIR` 环境变量覆盖，默认按平台存（macOS → `~/Library/Application Support/dragon-quant`，Linux → `~/.local/share/dragon-quant`，Win → `%APPDATA%/dragon-quant`）
- **线程安全**：DataCache 的操作持有 `threading.Lock`
- **子进程安全**：共享缓存只读，通过 JSON 文件传递，不通过内存

### 评分器接口约定
```python
def score(code: str, cache: DataCache) -> ScoreResult:
    """
    cache 中可用数据：
    - kline:day:{code}           # list[KBar] 日K线
    - kline:5min:{code}          # list[KBar] 5分K线
    - kline:5min:sector:{s_code} # list[KBar] 板块5分K
    - sector:components:{s_code} # list[StockInfo] 板块成分股
    - quotes:batch               # dict[code → Quote] 批量行情
    """
```

### Cookie 失效处理
东财 Cookie 有时效性（通常几小时到1天），雪球 Cookie 有效期较长（几天到数周）。如果接口返回空数据或 403，先检查 Cookie 状态。

### Git 规范
- 仓库：`gitBingxu/dragon-quant`
- Commit 风格：中文 + emoji 前缀（见 git log）
