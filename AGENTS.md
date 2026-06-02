# AGENTS.md — dragon-quant

> 龙头战法四维量化筛选系统 · AI Agent 操作手册

---

## 这是什么

一套纯 Python 3 的 A 股龙头筛选系统，依赖 `playwright` 做 Cookie 自动获取与 Web 侧辅助能力。从当日涨停榜出发，通过**带动性、抗跌性、领涨性、资金承接**四个维度评估涨停股的龙头质量，加权综合分排名输出；同时支持日志查询、SQLite 持久化、龙头回测与 Web UI 可视化。

数据源：东方财富 + 雪球 + 腾讯公开 API，不依赖任何付费行情接口。

---

## 快速使用

```bash
# 批量扫描（默认全流程）
cd ~/repo/dragon-quant
python -m dragon_quant

# 带参数
python -m dragon_quant scan --top 25 --candidates 5 --workers 2
python -m dragon_quant --top 25 --candidates 5 --workers 2  # 兼容旧写法

# 持久化数据管理
python -m dragon_quant storage status         # 查看存储状态
python -m dragon_quant storage size           # 磁盘占用
python -m dragon_quant storage clear --all    # 清理全部
python -m dragon_quant storage clear --results --days 7  # 保留7天内结果
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
├── orchestrator.py              # 编排主流程 (Phase A→F)
├── data.py                      # 原子数据查询 API
├── rate_limit.py                # 分组并发调度器
├── analyze.py                   # 子进程打分入口（已实现，被主进程内打分取代）
│
├── providers/                   # 数据源适配层
│   ├── base.py                  # StockProvider ABC (6 抽象方法)
│   ├── eastmoney.py             # 东财 — JSONP + urllib/curl + DNS 多 IP 轮询
│   ├── xueqiu.py                # 雪球 — 需 Cookie
│   ├── tencent.py               # 腾讯 — 零认证 + fallback
│   ├── browser.py               # Playwright 浏览器会话（Cookie 获取/辅助请求）
│   └── cookie.py                # Cookie 管理 + CLI
│
├── scorers/                     # 四维评分器（✅ 已实现）
│   ├── drive.py                 # 带动性 (35%)
│   ├── anti_drop.py             # 抗跌性 (15%)
│   ├── leadership.py            # 领涨性 (25%)
│   └── absorption.py            # 资金承接 (25%)
│
├── cache/
│   └── data_cache.py            # 内存+本地双重缓存
│
├── logging/                     # 结构化日志 + 自然语言报告（✅ 已实现）
│   ├── logger.py                # ScanLogger — 线程安全日志器
│   └── reporter.py              # ReportBuilder — 自然语言报告生成
│
├── storage/                     # ✅ 已实现 — 统一持久化管理
│   ├── paths.py                 # 平台路径管理（win32/darwin/linux）
│   ├── db.py                    # SQLite 持久化（scans/dragons/scan_logs）
│   └── manager.py               # StorageManager + CLI (status/size/clear)
│
├── utils/                       # ✅ 已实现 — 公共工具
│   └── trading.py               # 交易日历 + 涨停判断 + 买入日定位
│
├── review.py                    # ✅ 已实现 — 龙头回测验证
├── web_ui/                      # ✅ 已实现 — 回测结果 Web UI
│   ├── server.py                # stdlib HTTPServer 服务端
│   └── index.html               # 前端页面
└── models/
   └── types.py                 # dataclass 数据模型
```

---

## 执行流程

编排器 `orchestrator.run_scan()` 分 6 个阶段：

| Phase | 做什么 | 调用次数 | 关键点 |
|-------|--------|---------|--------|
| **A** 板块排行 | 东财·概念板块涨跌幅 Top10 | 2 次 | JSONP，urllib 失败自动走 curl 兜底 + DNS 多 IP 轮询 |
| **B** 候选筛选 | 每领涨板块取前5成分股，过滤 ST + 双创 | 20 次 | 去重 + 多概念跟踪 |
| **C** 连板+排序 | 雪球日K → 算连板天数 → 按(概念数, 连板数)排序取 Top25 | N+1 次 | 涨停阈值 ≥9.9% |
| **D** 并发加载 | 板块5分K + 个股5分K + 腾讯批量行情 | ~50 次 | RateLimiter 8 线程并发 |
| **E** 四维打分 | 主进程直接调用 4 个 scorer，逐个候选股评分 | N×4 次 | 评分器接口见下方 |
| **F** 输出+持久化 | 加权排序 + 自然语言报告 + SQLite 持久化 + 5 日去重 | — | scan_id={日期}_{top_n} 自动覆盖 / dragons 5 日去重 |

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

**当前反爬策略**：
- 东财主链路为 `urllib` + `curl` 双通道，配合 DNS 多 IP 轮询绕过坏掉的 CDN 节点
- `browser.py` 仍保留，主要用于 Cookie 自动获取与浏览器侧辅助请求
- 雪球和东财都依赖本地 Cookie；接口异常时优先检查 Cookie 是否失效

---

## 当前状态

### ✅ 已完成
- 数据模型（types.py）
- 全部 3 个 Provider（东财/雪球/腾讯），含完整反爬 Header
- Playwright 浏览器能力（Cookie 自动获取 + 浏览器侧辅助请求）
- Cookie 管理（手动设置 + Playwright 自动获取）
- RateLimiter 分组并发调度（`workers` 参数控制线程数）
- DataCache 内存+本地双重缓存 + 快照导出
- Orchestrator 编排全流程（Phase A→F 完整贯通，主进程直接打分）
- 5 日去重：同一标的 5 个交易日内只写入 dragons 一次
- CLI 入口 + `__main__.py` 入口（scan / logs / data / review / storage）
- 四维评分器 `scorers/`（drive.py / anti_drop.py / leadership.py / absorption.py）
- 结构化日志 `logging/`（ScanLogger + ReportBuilder 自然语言报告）
- Logger 全链路打点（Provider/HTTP 层自动记录每次接口调用的耗时与成败）
- 统一持久化路径 `storage/`（paths.py + db.py + manager.py）
- SQLite 持久化（scans / scan_stocks / dragons / scan_logs 四表）
- 扫描结果持久化（results JSON / 报告文本 / latest.json 快照）
- 数据管理 CLI（`storage status/size/clear` 子命令）
- 交易日历工具 `utils/trading.py`（基于雪球日K，不依赖外部假期表）
- 龙头回测模块 `review.py`（一字板跳过 + 5~20 日窗口自动筛选 + 峰值前回撤计算）
- Web UI：`dragon-quant review --ui` / `--ui-only` 查看回测结果与汇总统计
- dragons 表 `version` 字段，记录入库时的 dragon_quant 版本号
- 版本号集中管理 `_version.py`，发布脚本自动同步
- 加密发布流程（`encrypt_token.sh` + `publish_token.enc` + `--passwd` 解密）
- 全量 159 个单元测试覆盖核心模块

### ⚠️ 待完成（按优先级）

1. **单票分析 CLI** — `dragon-quant analyze <code>` 子命令
   - `analyze.py` 作为子进程入口已实现骨架，但缺少 `sector_name_map` 等元数据注入

2. **东财历史 K 线稳定性观察**
   - 历史 K 线链路仍需持续观察东财 CDN 节点稳定性
   - 当前已通过 TLSv1.2 + `curl` + DNS 多 IP 轮询显著降低空响应概率

### 📝 已知修复（2026-05）
- `leadership.py` `_normal_cdf_approx` 正负号反了，已修正
- `anti_drop.py` 日内承接评分的 `prev_close` 始终为 0，已改为从日K线取昨日收盘
- `orchestrator.py` `--workers` 参数未传递到 RateLimiter，已连接
- `eastmoney.py` urllib/curl TLSv1.2 强制 + DNS 多 IP 轮询，根治 CDN 坏节点导致的空响应
- `xueqiu.py` Referer 解析无防御，已加 try/except
- `eastmoney.py` `_get_ut_token()` `html` 变量未初始化导致 `UnboundLocalError`，已修复
- Logger 全链路打点：所有 Provider/HTTP 调用自动记录耗时与成败，可通过 `logger.api_stats()` 统计
- DataCache 默认启用本地持久化 + dataclass JSON 序列化修复
- 结果持久化：每轮扫描输出 results JSON / report TXT / latest.json 到统一数据目录

---

## 开发注意事项

### 龙头回测

```bash
# 自动筛选 5~20 交易日内入选的 pending 票，全部回测
python -m dragon_quant review

# 指定日期和 top N（手动覆盖自动筛选）
python -m dragon_quant review --date 20260519 --top 5

# 强制重算
python -m dragon_quant review --force --date 20260519
```

回测逻辑：从 dragons 表读取 pending 龙头 → 自动过滤入选日距今约 5~20 个交易日 → 拉取日K → 找入选后第一个非一字板日（`high != low`）以最低价买入 → 按收益观察窗口计算 `max_return_5d` 与 `max_return_hold_days` → 再按“买入日至最大收益出现日”窗口计算 `max_drawdown_5d` → 写入 DB。每条 dragon 记录入库时的 `dragon_quant` 版本号写入 `version` 字段，方便按版本分组回溯策略效果。

### 必须遵守的约束
- **运行时依赖**：`playwright` 为必选依赖，用于 Cookie 自动获取与浏览器侧辅助能力；其余模块仅使用 Python 3 标准库。`pyproject.toml` 中声明 `playwright` 为 dependency。
- **Playwright 安装**：`pip install playwright && playwright install chromium`
- **跨平台**：数据目录用 `DQ_DATA_DIR` 环境变量覆盖，默认按平台存（macOS → `~/Library/Application Support/dragon-quant`，Linux → `~/.local/share/dragon-quant`，Win → `%APPDATA%/dragon-quant`）
- **线程安全**：DataCache 的操作持有 `threading.Lock`
- **子进程安全**：共享缓存只读，通过 JSON 文件传递，不通过内存

### AI Agent 协作规范

> **任何代码修改或破坏性操作前，先输出技术方案（改动范围、涉及文件、风险点），等待用户确认后再执行。**
>
> 纯查询类操作（读文件、查数据库、搜索代码）不受此限，可直接执行。

### 评分器接口约定
```python
def score(code: str, cache: DataCache, **kwargs) -> ScoreResult:
    """
    cache 中可用数据：
    - kline:day:{code}           # list[KBar] 日K线
    - kline:5min:{code}          # list[KBar] 5分K线
    - kline:5min:sector:{s_code} # list[KBar] 板块5分K
    - sector:components:{s_code} # list[StockInfo] 板块成分股
    - quotes:batch               # dict[code → Quote] 批量行情

    各评分器额外参数:
    - drive:   candidate_pool, primary_sector
    - anti_drop: （无额外参数）
    - leadership: primary_sector
    - absorption: primary_sector, all_sector_codes, sector_name_map
    """
```

### Cookie 失效处理
东财 Cookie 有时效性（通常几小时到1天），雪球 Cookie 有效期较长（几天到数周）。如果接口返回空数据或 403，先检查 Cookie 状态。

### Git 规范
- 仓库：`gitBingxu/dragon-quant`
- Commit 风格：中文 + emoji 前缀（见 git log）
