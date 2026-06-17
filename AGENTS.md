# AGENTS.md — dragon-quant

> 龙头战法四维量化筛选系统 · AI Agent 操作手册

---

## 这是什么

一套纯 Python 3 的 A 股龙头筛选系统，依赖 `playwright` 做 Cookie 自动获取与 Web 侧辅助能力（含同花顺概念排行页渲染）。从当日涨停榜出发，通过**带动性、抗跌性、领涨性、资金承接**四个维度评估涨停股的龙头质量，加权综合分排名输出；同时支持日志查询、SQLite 持久化、龙头回测与 Web UI 可视化。

数据源：同花顺（板块共振数据）+ 雪球 + 腾讯公开 API，不依赖任何付费行情接口。东财 provider 仍保留但默认不参与扫描。

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

板块共振数据（概念排行 / 成分股 / 板块分时）已改用**同花顺**，**无需 Cookie**（概念排行页用 playwright 渲染）。个股数据仍依赖雪球，需配雪球 Cookie：

```bash
# 查看状态
python -c "from dragon_quant.providers.cookie import get_xq; print(f'雪球: {bool(get_xq())}')"

# 手动设置雪球 Cookie（推荐）
python -m dragon_quant.providers.cookie set --cookie "xq_a_token=...; xq_is_login=1; u=..." --source xq

# 自动获取（需要 playwright）
python -m dragon_quant.providers.cookie fetch --source xq
```

> 东财 provider 仍保留但默认不参与扫描。`cookie-fetch` 默认（`all`）只刷新雪球，东财需显式 `--source eastmoney`。

Cookie 文件位置：
- 雪球：`~/Library/Application Support/dragon-quant/cookies/xueqiu`
- 东财（保留备用）：`~/Library/Application Support/dragon-quant/cookies/eastmoney`

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
│   ├── ths.py                   # 同花顺 — 概念排行(playwright渲染)/成分股(HTML)/板块分时聚合5分K
│   ├── eastmoney.py             # 东财 — JSONP + curl + DoH 多节点轮询（保留，默认不参与扫描）
│   ├── xueqiu.py                # 雪球 — 需 Cookie
│   ├── tencent.py               # 腾讯 — 零认证 + fallback
│   ├── browser.py               # Playwright 浏览器会话（页面渲染/Cookie 获取/辅助请求）
│   └── cookie.py                # Cookie 管理 + CLI
│
├── scorers/                     # 四维评分器（✅ 已实现）
│   ├── drive.py                 # 带动性 (35%)
│   ├── anti_drop.py             # 抗跌性 (15%)
│   ├── leadership.py            # 领涨性 (25%)
│   └── absorption.py            # 资金承接 (25%)
│
├── vpa/                         # ✅ 已实现 — 量价分析（独立模块，不依赖 scorer/编排器）
│   ├── engine.py                # analyze(code) 编排：拉K线→跑因子→汇总健康度/信号
│   ├── types.py                 # FactorResult / VPAReport
│   ├── report.py                # 报告渲染（完整版 / review块 / 单行）
│   └── factors/                 # 插件式因子注册表 FACTORS
│       ├── base.py              # 因子签名约定 + 共享工具
│       ├── vol_amount.py        # 量额灵敏度（高位看额、低位看量）
│       ├── trend_verify.py      # 趋势量价验证（涨放量/调缩量）
│       ├── breakout.py          # 突破放量验证
│       └── divergence.py        # 量价背离（缩量新高=动能衰竭）
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
│   ├── server.py                # stdlib HTTPServer 服务端（托管 dist 静态资源 + /api）
│   ├── dist/                    # Vite 构建产物（提交入库，随包分发）
│   └── frontend/                # 前端源码（Vite + React + TS + Mantine）
└── models/
   └── types.py                 # dataclass 数据模型
```

---

## 执行流程

编排器 `orchestrator.run_scan()` 分 6 个阶段：

| Phase | 做什么 | 调用次数 | 关键点 |
|-------|--------|---------|--------|
| **A** 板块排行 | 同花顺·概念板块涨跌幅榜（涨幅 Top8 + 跌幅 Top20）| 2 次 | playwright 渲染资金流排行页，解析涨跌幅 |
| **B** 候选筛选 | 每领涨板块取前5成分股，过滤 ST + 双创 | 20 次 | 同花顺详情页 HTML 解析；去重 + 多概念跟踪 |
| **C** 连板+排序 | 雪球日K → 算连板天数 → 按(概念数, 连板数)排序取 Top25 | N+1 次 | 涨停阈值 ≥9.9% |
| **D** 并发加载 | 板块5分K(同花顺分时聚合) + 个股5分K + 腾讯批量行情 | ~50 次 | RateLimiter 多线程并发 |
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

用法：`limiter.submit("ths", "ths", fn, arg)` 之后 `limiter.wait_all()`

---

## 反爬要点

### 同花顺（板块共振主数据源，无需 Cookie）
- 概念排行：`data.10jqka.com.cn/funds/gnzjl/`（涨跌幅 JS 填充，playwright 渲染读表）。`order=desc` 涨幅榜 / `order=asc` 跌幅榜
- 成分股：`q.10jqka.com.cn/gn/detail/code/{6位code}/`（GBK HTML 表格，第1页约10只）
- 板块分时：`d.10jqka.com.cn/v6/time/48_{innerCode}/last.js`（JSONP，1分钟粒度，provider 内聚合5分K）
- 两套代码：URL 用 6 位 code（如 301558），行情接口用 innerCode（如 885611），映射在详情页 `<input id="clid">`，进程内缓存
- 注意：概念排行的 `ajax` 翻页接口有 hexin-v 反爬（仅成分股 all_pages 时尝试）

### 雪球（个股，需 Cookie）
- Referer：`https://xueqiu.com/S/{SH/SZ}{code}`

### 腾讯（零认证）
- 不需要 Referer

### 东财（保留备用，默认不参与扫描）
- 主链路 `curl` + DoH 多 CDN 节点轮询；全节点失败判定出口 IP 被风控并 fail-fast
- Referer：`gridlist.html`（排行/成分股）/ 动态 `bk/90.BKxxx.html`（K线）
- 依赖本地 Cookie（push2 / push2his 分域）

当前 Chrome UA 版本：同花顺 120 / 东财 148。如大面积失效，更新到最新 Chrome 版本号即可。

---

## 当前状态

### ✅ 已完成
- 数据模型（types.py）
- 全部 4 个 Provider（同花顺/东财/雪球/腾讯），含完整反爬 Header
- 同花顺 provider（`ths.py`）：概念排行(playwright渲染)/成分股(HTML)/板块分时聚合5分K，对齐东财接口契约，scan/data 主流程已切换至此
- Playwright 浏览器能力（Cookie 自动获取 + 页面渲染 + 浏览器侧辅助请求）
- Cookie 管理（手动设置 + Playwright 自动获取）
- RateLimiter 分组并发调度（`workers` 参数控制线程数）
- DataCache 内存+本地双重缓存 + 快照导出
- Orchestrator 编排全流程（Phase A→F 完整贯通，主进程直接打分）
- 5 日去重：同一标的 5 个交易日内只写入 dragons 一次
- CLI 入口 + `__main__.py` 入口（scan / logs / data / review / vpa / storage）
- 四维评分器 `scorers/`（drive.py / anti_drop.py / leadership.py / absorption.py）
- 量价分析模块 `vpa/`（独立、可扩展、不依赖 scorer/编排器；插件式因子注册表 FACTORS）
  - 4 个因子：量额灵敏度 / 趋势量价验证 / 突破放量验证 / 量价背离，输出量价健康度+信号+判断依据
  - CLI `dragon-quant vpa --code <code>`；review 回测后对每只 pending 个股追加量价分析
  - 独立表 `vpa_analysis` 持久化（`db.upsert_vpa`），不复用 dragons 字段
- 结构化日志 `logging/`（ScanLogger + ReportBuilder 自然语言报告）
- Logger 全链路打点（Provider/HTTP 层自动记录每次接口调用的耗时与成败）
- 统一持久化路径 `storage/`（paths.py + db.py + manager.py）
- SQLite 持久化（scans / scan_stocks / dragons / scan_logs / vpa_analysis 五表）
- 扫描结果持久化（results JSON / 报告文本 / latest.json 快照）
- 数据管理 CLI（`storage status/size/clear` 子命令）
- 交易日历工具 `utils/trading.py`（基于雪球日K，不依赖外部假期表）
- 龙头回测模块 `review.py`（一字板跳过 + 5~20 日窗口自动筛选 + 峰值前回撤计算）
- Web UI：`dragon-quant review --ui` / `--ui-only` 查看回测结果与汇总统计
- dragons 表 `version` 字段，记录入库时的 dragon_quant 版本号
- 版本号集中管理 `_version.py`，发布脚本自动同步
- 加密发布流程（`encrypt_token.sh` + `publish_token.enc` + `--passwd` 解密）
- 全量 186 个单元测试覆盖核心模块（含 `tests/test_vpa.py` 量价因子与引擎）

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

### Web UI 前端构建

回测面板已用 **Vite + React + TypeScript + Mantine** 重构，源码在 `web_ui/frontend/`，
构建产物输出到 `web_ui/dist/`（已提交入库，随 wheel 分发）。运行期仅靠 Python stdlib
托管 `dist` 静态资源，**不需要 Node**；只有改前端源码时才需重新构建：

```bash
cd web_ui/frontend
npm install        # 首次
npm run build      # 生成 ../dist
npm run dev        # 开发模式，/api 自动代理到 127.0.0.1:8765
```

后端 API（`/api/dragons`、`/api/summary`）保持不变，前端改动不影响 Python 逻辑。

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
板块数据已改用同花顺（无需 Cookie），不再有东财 Cookie 失效问题。个股数据依赖雪球 Cookie（有效期较长，几天到数周）。如果个股 K 线/行情返回空数据或 403，先检查雪球 Cookie 状态。东财 provider 保留备用，如需启用其 Cookie 用 `cookie-fetch --source eastmoney`。

### Git 规范
- 仓库：`gitBingxu/dragon-quant`
- Commit 风格：中文 + emoji 前缀（见 git log）
