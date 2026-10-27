# scan — 数据源、扫描编排、评分器与 cache 契约

> 触发：改动 `scan` 的阶段编排、五维评分器、cache 键、板块口径、数据源与回退、`scans_v2` 等扫描侧表时读本文。
> 本文权威：Phase A–F 编排、数据模型、评分器接口与聚合语义、cache 键命名、v2 物理分表、扫描侧表契约、数据源最小提示。
> 上游权威：AGENTS.md（硬约束与项目路由）；五维评分细则与数值以 [评分规范](../../archived_md/score/评分器Refactor.md) 为准，阈值/权重实现在 `scan/scores/registry.py`。

## 执行流程（Phase A–F）

编排器 `scan.orchestrator.main.scan()` 分 6 个阶段（最近交易日判定 `_get_trade_date` 为 TDX 指数K线优先、雪球兜底，不依赖雪球 Cookie）：

| Phase | 做什么 |
|-------|--------|
| **A** 板块排行 | TDX/THS 三维板块榜（行业/概念/地区）融合取 Top8 + **行业**领跌 Top20，过滤 DB 黑名单；逐维度 TDX→THS 回退，单维度失败标 `degraded`、不中断扫描 |
| **B** 候选筛选 | 每个入选板块取当日所有涨停股(pct≥9.9)，过滤 ST+双创+北交所；跨板块同 code 去重，`primary_sector` = 总榜排名最高板块 |
| **C** 连板+排序 | TDX 日K 算连板天数（雪球回退），写 `Candidate.fived_pct`，按(连板,概念数)降序，候选池全部评分 |
| **D** 并发加载 | 板块历史10日5分K + 板块当日1分K + 大盘1分K + 全候选1分K + 腾讯批量行情 |
| **E** 打分 | `_score_one` 调 `scan.scores.main.evaluate()`，五维门槛+加权 → DragonVerdict |
| **F** 输出+持久化 | 所有候选保留诊断分与明细，仅通过者排名；Top N 真龙报告/入库 + 5日去重，保留原始排名 |

总耗时约 40-80 秒（取决于网络、并发数、v2 拉取量更大）。

## 数据模型

核心类型全部在 `infra/models.py`：

- **KBar** — 一根 K 线（timestamp, OHLCV, 涨跌幅, 换手率, 成交额）
- **StockInfo** — 股票基本信息 + 当日快照（含 `five_day_return`）
- **Quote** — 实时行情快照（现价/涨跌幅/换手率/市值/PE/量比…）+ **收盘盘口 `bid1_price`/`bid1_volume`/`ask1_volume`**（gtimg f[9]/f[10]/f[20]，单位手）
- **SectorPerformance** — 板块行情（代码/名称/涨跌幅/振幅）
- **Candidate** — 候选股（code, concepts, board_count, **fived_pct**, primary_sector, score）
- **ScoreResult** — 单维度评分结果（dim, score 0-100, weight, details）
- **DragonVerdict**（`scan/scores/base.py`）— 五维聚合产物（is_true_dragon, composite, rank, dims, reject_reason）

## 板块口径

- **板块身份**为 `(source, category, code)`，缓存键按 `source:category:code` 命名空间隔离，由 `scan/sectors.py` 的 `sector_key` / `components_key` / `sector_1min_key` / `sector_5min_key` 统一构造。TDX 板块分类码为 `HY`（行业）/ `GN`（概念）/ `DQ`（地区）。
- 板块身份维度参与 innerCode 解析与进程内缓存键（行业 `clid` 即 code 本身，概念为 885xxx 映射，地区 `clid` 即 code）。

## 评分器接口约定

评分器统一签名，是 **cache 消费者**（只读 `cache.get(key)`，不发请求）：
```python
def score(code: str, cache: DataCache, **kwargs) -> ScoreResult
```
- `scan/scores/main.evaluate()` 统一调度五维 + 门槛聚合，产出 `DragonVerdict`；四大特征异常必须否决，资金承接异常降级 50 分且不否决。
- 所有候选保留诊断综合分，但 `rank_verdicts()` 只给通过者赋真龙排名（同分按代码排序），否决票 `rank=None`；`raw_output.ranking` 与 `scan_stocks_v2` 保存全部诊断结果，`dragons_v2` 和最终报告只收 Top N 真龙。无真龙仍保存扫描，不用否决票补位；五日去重后不重排 rank。
- `rebuild_dragons_for_date` 仅使用非明确否决、rank 非空且不超原扫描 Top N 的贡献；不改旧表名与 ID，不自动重算历史记录。盘后重新取数评分需 `scan --force --no-cache`。
- cache 键：`kline:1min:{code}` / `kline:1min:SH000001`（上证指数）/ `kline:1min:sector:{source}:{category}:{code}` / `kline:5min:sector:{source}:{category}:{code}`（10日历史）/ `quotes:batch`（`list[Quote]`，含盘口）/ `sector:components:{source}:{category}:{code}` / `sector:ranking:{source}:{category}`。板块键的 `category` ∈ `hy/gn/dq`、`source` ∈ `tdx/ths`，由 `dragon_quant/scan/sectors.py` 的 `sector_key`/`components_key`/`sector_1min_key`/`sector_5min_key` 统一构造（评分器通过 `primary_sector_key` 读取）。裸代码 `000001` 是平安银行，不得用于指数请求或缓存。
- 腾讯行情按代码排序、每批最多 200 只获取并合并，不截断成分股总数；封板池只用同板块主板涨停候选，记录有效样本与缺失情况。
- 稳定封板按最后开板后持续封至最后有效分钟的起点排名，同分钟并列；脉冲使用局部峰抑制，每次匹配只能判带动或跟风之一，同步启动不判方向；相关性 bonus 仅看首次触板前。分时缺失或全程平线时带动子项 40 分降级。
- 抗跌反弹必须确认基准及个股实际回升，个股横盘不获反弹奖励；分钟时序窗口不跨午休、隔夜或双方缺点区间。
- 封单强度需确认现价涨停且买一价在涨停价附近；未涨停买一量不能当封单，从未触板的完整分时稳定性为 0。价格容差统一为涨停价的 0.1%。
- 承接对手盘是扫描日领跌 Top20 行业；六根五分钟 K 必须同连续交易时段，允许累计缓跌达标，不设单根跌超 0.5% 隐藏门槛；每个出逃板块独立通过不晚于拉升且间隔不超十分钟的检查，至少两个有效板块。
- 承接单事件强度/广度/持续性为 60%/20%/20%，拉升满分参考 3%、出逃规模参考 10；重叠窗口（含链式重叠）同交易时段内合并，保留最高分代表，同分取较新窗口，取消次数奖励。按目标 K 线日期计算年龄，事件分 `50+(原始分-50)*2**(-年龄/3)` 向中性衰减，取最强的最多三个独立事件均值。details 保存原始窗口数、独立事件数、所选事件及原始分/年龄/衰减系数/调整分；不保留无效 multi_event_bonus 字段，不重算历史数据。
- 阈值/权重集中在 `scan/scores/registry.py`，便于回测调参。

## v2 物理分表兼容

- 新扫描的缓存、扫描明细、日志、龙头物化全部读写 `*_v2` 表。
- `scan_id` 继续使用 `v2_YYYYMMDD_topN` 格式；不要假设 `scan_id[:8]` 是日期。
- 5 日去重只看 `dragons_v2`，兼容历史 v2 数据。
- 运行时不创建旧无后缀 `scans` / `scan_stocks` / `scan_logs` / `dragons` 表；`source` 仍是版本路由字段，默认 `v2`。

## 扫描侧表契约

| 表 | 用途 | 关键点 |
|----|------|--------|
| `scans_v2` | 每轮扫描元信息 | 含 `raw_output` 完整结果 JSON；`scan` 固定读写 v2 |
| `scan_stocks_v2` | 每轮全部评分结果 | scan_id 关联；填充 `dim_liquidity` / `is_true_dragon` / `reject_reason` |
| `dragons_v2` | 入选龙头（最终物化）| `UNIQUE(trade_date, code)`；`version`=包版本号；review 字段写回此表 |
| `dragon_review_strict` | 严格口径 review 结果（侧表）| `UNIQUE(trade_date, code, source)`；`review_status` ∈ `completed`/`cannot_evaluate`，`reason` 记录 partial / 无法区分原因；**无行 = 尚未重算**，不写 0/中性值；`dragons_*` 宽松字段语义不变 |
| `scan_logs_v2` | 结构化日志 | `logs` 默认查询 v2 |
| `*_v1` | 历史旧表 | 不再由主流程写入，仅显式 `--source v1` 查询 |
| `vpa_analysis` | 量价分析 | 独立表，不复用 dragons |
| `sector_blacklist` | 板块黑名单 | 行业切换后默认种子为空 |

## 前置条件与 Cookie

板块数据用**同花顺**，**无需 Cookie**（HTTP GET + GBK 直取，无 Playwright/反爬）。个股数据依赖雪球，需配雪球 Cookie：

```bash
# 查看状态
python -c "from dragon_quant.infra.provider.cookie import get_xq; print(f'雪球: {bool(get_xq())}')"

# 手动设置雪球 Cookie（推荐）
python -m dragon_quant data cookie-set --cookie "xq_a_token=...; xq_is_login=1; u=..."

# 自动获取（需要 playwright；首次运行自动下载 chromium 内核约 150MB，走国内镜像）
python -m dragon_quant data cookie-fetch
```

Cookie 文件位置（随平台而定，可用 `DQ_DATA_DIR` 覆盖）：
- macOS：`~/Library/Application Support/dragon-quant/cookies/xueqiu`
- Windows：`%APPDATA%\dragon-quant\cookies\xueqiu`

## 数据源与反爬最小提示

- **回退链**：TDX（`tmdx`，无 Cookie）优先 → 同花顺（板块）→ 雪球（个股 K 线/分时）→ 腾讯（批量行情/收盘盘口）；tdx 链路失败自动回退老链路，单维度失败只标 `degraded`、不中断扫描。
- **同花顺排行字段必须用 `zdf`（涨跌幅）**：旧 `tradezdf` 是资金流字段，会无视 order/page 返回固定 50 行资金流入板块（曾导致领跌榜全为正值的 bug）；网关有 403 频控，需退避重试 + 页间延迟，单页 DOM 非严格有序，须抓多页后本地按 pct 排序。
- **雪球需 Cookie**：失效表现为空体 / 403；`pankou.json` 盘后返回空体，已弃用，封单改用腾讯收盘盘口（`Quote.bid1_volume`，买一量，单位手）。
- **不要在文档重建实现细节**：反爬 URL、DOM 选择器、字段下标、Chrome UA 只留在 provider 实现与 Git 历史；大面积失效时更新 UA 版本号即可。
