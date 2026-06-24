# AGENTS.md — dragon-quant

> 龙头战法量化筛选系统 · AI Agent 操作手册

---

## 这是什么

一套纯 Python 3 的 A 股龙头筛选系统。从当日涨停榜出发，评估涨停股的龙头质量并加权排名输出；同时支持日志查询、SQLite 持久化、龙头回测与 Web UI 可视化。

系统内置**两套评分体系**，分别由 `scan`（v1）与 `scan_v2`（v2）命令触发，并存互不影响：

- **v1（旧四维，默认）**：带动性 35% / 领涨性 25% / 抗跌性 15% / 资金承接 25%，简单加权求和。
- **v2（新五维「识别真龙」）**：带动性 30% / 领涨性 25% / 抗跌性 15% / 流动性 20% / 资金承接 10%，**门槛+加权两段式聚合**（四大特征任一低于门槛即一票否决，资金承接不否决仅加权贡献）。详见《评分器Refactor.md》。

数据源：同花顺（板块数据）+ 雪球（个股 K 线/分时）+ 腾讯（批量行情/收盘盘口），不依赖任何付费行情接口。东财 provider 仍保留但默认不参与扫描。

> **板块口径已切换为「行业板块」**（同花顺 `thshy`/`hyzjl`，约 90 个真实行业，code 为 881xxx），不再用概念板块（`gn`/`gnzjl`）。

---

## 快速使用

```bash
cd ~/repo/dragon-quant

# 批量扫描（默认 v1 评分器）
python -m dragon_quant
python -m dragon_quant scan --top 25 --candidates 5 --workers 2

# 使用 v2 五维「识别真龙」评分器
python -m dragon_quant scan_v2 --top 5

# 强制执行（跳过交易时段拦截 + DB 缓存）
python -m dragon_quant scan_v2 --force

# 概念板块黑名单管理（拉取领涨/领跌板块时过滤）
python -m dragon_quant blacklist list
python -m dragon_quant blacklist add "次新股"
python -m dragon_quant blacklist remove "次新股"

# 持久化数据管理
python -m dragon_quant storage status        # 查看存储状态
python -m dragon_quant storage size          # 磁盘占用
python -m dragon_quant storage clear --all   # 清理全部

# 按评分体系回测 / 查看 UI
python -m dragon_quant review --source v1 --date 20260519
python -m dragon_quant review --ui-only --source v2
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
├── cli.py                       # argparse CLI（scan/scan_v2/logs/data/review/vpa/storage/blacklist）
├── orchestrator.py              # 编排主流程 (Phase A→F)，含 v1/v2 双分支
├── data.py                      # 原子数据查询 API
├── rate_limit.py                # 分组并发调度器
├── analyze.py                   # 子进程打分入口（v1 路径，保留）
│
├── providers/                   # 数据源适配层
│   ├── base.py                  # StockProvider ABC + scorers_v2 新增板块 K 线方法
│   ├── ths.py                   # 同花顺 — 行业排行(curl)/成分股(HTML)/板块1分K/历史5分K
│   ├── eastmoney.py             # 东财 — 保留，默认不参与扫描
│   ├── xueqiu.py                # 雪球 — 个股日K/分时，需 Cookie
│   ├── tencent.py               # 腾讯 — 零认证，批量行情 + 收盘盘口(bid1)
│   ├── browser.py               # Playwright 浏览器会话（Cookie 获取/页面渲染）
│   └── cookie.py                # Cookie 管理 + CLI
│
├── scorers/                     # v1 四维评分器（旧，保留不动）
│   ├── drive.py / anti_drop.py / leadership.py / absorption.py
│
├── scorers_v2/                  # v2 五维「识别真龙」评分器（✅ 新增）
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
│   └── reporter.py              # ReportBuilder（v1 四维 + v2 五维报告）
├── storage/                     # 统一持久化
│   ├── paths.py / manager.py
│   └── db.py                    # SQLite（v1/v2 分表 + vpa_analysis/sector_blacklist）
├── utils/trading.py            # 交易日历 + 涨停判断 + 买入日定位
├── review.py                    # 龙头回测验证
├── web_ui/                      # 回测结果 Web UI（Vite+React+TS+Mantine / stdlib HTTPServer）
└── models/types.py             # dataclass 数据模型
```

---

## 执行流程

编排器 `orchestrator.scan(scorers="v1"|"v2")` 分 6 个阶段。下表标注 v1/v2 差异：

| Phase | 做什么 | v1 | v2 |
|-------|--------|----|----|
| **A** 板块排行 | 同花顺·行业板块涨跌幅榜 | 领涨 Top8 + 领跌 Top20 | 领涨 **Top5** + 领跌 Top20；过滤 DB 黑名单 |
| **B** 候选筛选 | 每领涨板块取候选股，过滤 ST+双创+北交所 | 每板块按5日涨幅取前5 | 每板块**当日所有涨停股**(pct≥9.9) |
| **C** 连板+排序 | 雪球日K 算连板天数 → 按(连板,概念数)降序，**对候选池全部评分(不截断)** | — | 额外写 `Candidate.fived_pct`（5日涨幅） |
| **D** 并发加载 | 板块/个股 K 线 + 腾讯批量行情 | 板块当日5分K + 个股1分K | 板块**历史10日5分K** + 板块**当日1分K** + 大盘1分K + 全候选1分K（封板池）|
| **E** 打分 | 逐候选股评分（候选池全部，无 Top N 截断） | `_score_one`（四维加权）| `_score_one_v2`（五维门槛+加权 → DragonVerdict）|
| **F** 输出+持久化 | 排序 + 报告 + SQLite + 5日去重 | 四维报告 + 写 v1 分表 | 五维报告 + 写 v2 分表 |

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
- **DragonVerdict**（`scorers_v2/base.py`）— v2 聚合产物（is_true_dragon, composite, rank, dims, reject_reason）

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
| `scans_v1` / `scans_v2` | 每轮扫描元信息 | 含 `raw_output` 完整结果 JSON；`scan` 只读写 v1，`scan_v2` 只读写 v2 |
| `scan_stocks_v1` / `scan_stocks_v2` | 每轮全部评分结果 | scan_id 关联；v2 额外填充 `dim_liquidity` / `is_true_dragon` / `reject_reason` |
| `dragons_v1` / `dragons_v2` | 入选龙头（最终物化）| 各自 `UNIQUE(trade_date, code)`；`version`=包版本号；review 字段按体系独立 |
| `scan_logs_v1` / `scan_logs_v2` | 结构化日志 | `logs --source v1|v2` 查询对应体系 |
| `vpa_analysis` | 量价分析 | 独立表，不复用 dragons |
| `sector_blacklist` | 概念板块黑名单 | 行业切换后默认种子为空 |

### v1/v2 物理分表
- `scan` 命令的缓存、扫描明细、日志、龙头物化全部读写 `*_v1` 表；`scan_v2` 全部读写 `*_v2` 表，避免同日同 topN 覆盖。
- `scan_id` 使用 `v1_YYYYMMDD_topN` / `v2_YYYYMMDD_topN` 格式；不要再假设 `scan_id[:8]` 是日期。
- `dragons_v1` 与 `dragons_v2` 同日同股可以各自保存 rank/score/report/review 状态；运行时不存在跨体系合并态。
- 5 日去重按 source 独立执行：v1 只看 `dragons_v1`，v2 只看 `dragons_v2`。
- 运行时只创建和读写 `*_v1` / `*_v2` 分表，不再创建旧 `scans` / `scan_stocks` / `scan_logs` / `dragons` 表；`source` 是唯一版本路由字段。

---

## 开发注意事项

### 龙头回测
```bash
python -m dragon_quant review                        # 自动筛 5~20 交易日内 pending 票全回测
python -m dragon_quant review --date 20260519 --top 5
python -m dragon_quant review --source v2 --date 20260519
python -m dragon_quant review --ui --source v2        # 回测后启动 Web UI，默认展示 v2
python -m dragon_quant review --ui-only --source v1   # 仅看结果
```
回测逻辑：按 `--source` 从 `dragons_v1` 或 `dragons_v2` 读 pending → 找入选后第一个非一字板日（`high != low`）最低价买入 → 算 `max_return_5d` / `max_return_hold_days` → 按买入日至峰值窗口算 `max_drawdown_5d` → 写回对应 dragons 表。Web UI 的表格与 summary 也按 source 加载，并在页面显示 v1/v2 体系。

### Web UI 前端构建
源码 `web_ui/frontend/`（Vite+React+TS+Mantine），产物 `web_ui/dist/`（已入库随包分发）。运行期仅靠 Python stdlib 托管，**不需要 Node**；改前端时才需 `npm run build`。

### 评分器接口约定
两套评分器统一签名，均为 **cache 消费者**（只读 `cache.get(key)`，不发请求）：
```python
def score(code: str, cache: DataCache, **kwargs) -> ScoreResult
```
- v1 由 `scorers/__init__.py` 的 `SCORERS` 注册表加权。
- v2 由 `scorers_v2/aggregator.evaluate()` 统一调度五维 + 门槛聚合，产出 `DragonVerdict`。
- v2 cache 键：`kline:1min:{code}` / `kline:1min:000001`（大盘）/ `kline:1min:sector:{s}` / `kline:5min:sector:{s}`（10日历史）/ `quotes:batch`（含盘口）/ `sector:components:{s}`。
- v2 阈值/权重集中在 `scorers_v2/registry.py`，便于回测调参。

### 必须遵守的约束
- **运行时依赖**：`playwright` 为必选（Cookie 自动获取 + 浏览器辅助）；其余仅用 Python 3 标准库。
- **跨平台**：数据目录用 `DQ_DATA_DIR` 覆盖，默认按平台存。
- **线程安全**：DataCache 操作持 `threading.Lock`；DB 每次操作独立连接 + WAL。
- **v1/v2 并存**：旧四维 `scorers/` 与 v1 编排路径保留；v2 全在 `scorers_v2/` + 编排器 v2 分支；持久化使用 v1/v2 物理分表，可灰度回滚。

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
- v1 四维评分器 `scorers/`（drive/anti_drop/leadership/absorption）
- **v2 五维「识别真龙」评分器 `scorers_v2/`**（带动/领涨/抗跌/流动/资金承接 + 门槛加权聚合），由 `scan_v2` 命令触发
- 4 个 Provider（同花顺/东财/雪球/腾讯）含完整反爬；同花顺**行业板块**数据源（排行 curl+多页+本地排序+403退避、成分股、当日1分K、历史5分K）
- 封单数据走腾讯 gtimg 收盘盘口（`Quote.bid1_volume`）
- DB 概念板块黑名单表 + CLI `blacklist` 管理
- v1/v2 物理分表（`scans_*` / `scan_stocks_*` / `scan_logs_*` / `dragons_*`）+ `review --source` / Web UI source 切换
- 量价分析 `vpa/`、结构化日志 `logging/`、统一持久化 `storage/`、交易日历 `utils/trading.py`、龙头回测 `review.py`、Web UI
- 全量单测覆盖 `tests/test_scorers_v2.py`、`tests/test_storage.py` 等核心路径

### ⚠️ 待完成/观察
- 单票分析 CLI `analyze <code>`：子进程入口仅 v1 骨架，缺 `sector_name_map` 等元数据注入；v2 暂只接主进程路径
- 同花顺数据网关 403 频控：高频访问会临时封 IP（已加退避重试，正常每日一两次扫描不触发）
- 东财历史 K 线 CDN 节点稳定性（保留备用链路）

### 📝 已知修复
- 同花顺排行字段 `tradezdf`→`zdf`，修复领跌榜全为正值的 bug
- v2 领涨性涨幅分位样本由「成分股」改为「候选池」，杜绝未拉日K成分股的 0 值污染
- v2 归一化涨幅曲线改用 `KBar.pct`，避免用首分钟价误当昨收
- v2 资金承接强度改为正向口径（出逃规模越大 + 拉升越高 → 分越高）
