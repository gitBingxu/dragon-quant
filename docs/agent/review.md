# review — 龙头回测双口径与 Web UI 指标联动

> 触发：改动 `review` 回测流程、宽松/严格口径、`dragons_*` review 字段写回、`dragon_review_strict` 侧表，或回测 Web UI 的 API 与指标卡片时读本文。
> 本文权威：回测双口径规则与成熟度、严格口径侧表契约、`GET /api/dragons` 汇总契约与前端取数行为。
> 上游权威：AGENTS.md（硬约束与项目路由）；实现见 `review/main.py`、`web_ui/server.py`、`scan/storage/dragons.py`。

## 命令

```bash
python -m dragon_quant review                        # 所有距当日 >=5 交易日的 pending 票（无 20 日上限）
python -m dragon_quant review --date 20260519 --top 5
python -m dragon_quant review --date 20260519
python -m dragon_quant review --recompute-all --dry-run   # 全量重算预检（上界统计；不写库/不拉个股K）
python -m dragon_quant review --recompute-all --limit 20  # 最老 20 条 canary
python -m dragon_quant review --recompute-all             # 全量双口径回填（默认跳过已有严格行）
python -m dragon_quant review --ui                    # 回测后启动 Web UI，默认展示 v2
python -m dragon_quant review --ui-only               # 仅看结果
```

## 双口径

**双口径**（共享收益/回撤窗口与公式；胜利条件均为 `max_return_5d > 0 且 max_drawdown_5d > -5`，0 / -5 都不算赢）：

- **宽松口径**（历史行为，写 `dragons_*` 现有 review 字段）：入选后首个 `high != low` 日按**最低价**买入。
- **严格口径**（写侧表 `dragon_review_strict`，`UNIQUE(trade_date, code, source)`）：入选后**首个交易日**按**开盘价**买入；次日一字涨停按开盘价成交、不顺延。次日缺 bar / 无效 bar / 零成交平盘占位 → `cannot_evaluate`（原因固定「停牌或数据缺失无法区分」）；传输失败不写/不覆盖严格结果；严格窗口为空 `cannot_evaluate`；部分窗口写 `partial`，原因区分 `partial_window`（窗口还没走完，时间未成熟）与 `partial_missing_bars`（窗口日期已过但个股缺日 K / 停牌），两者都可展示但不进入正式分母；宽松窗口为空同样写 `partial`（不再 0/0 completed）。

`review` 默认处理 `pending + partial`、取消 20 交易日上限（完整收益窗口约需 6 个交易日，由 `maturity_cutoff()` 派生），显式 `--date` 可返回 partial；`--force` 重算所有成熟状态并覆盖严格。按 code 聚合拉 K（每 code 一次，深度覆盖最老记录）、日历一次；拉取后校验 `min(k.date) <= trade_date`，否则报「历史数据不足」而不是拿最近窗口冒充。`--recompute-all` 仅 v2、最老优先、默认跳过**非 partial** 的已有严格行（partial 必须补算，`--force` 覆盖）、跳过 VPA；`--dry-run` / `--limit` 只能与其组合（非法组合 exit 2）；dry-run 是未过滤成熟度的上界，正式运行结束输出 processed/completed/cannot_evaluate/partial/failed/skipped 聚合汇总（`failed` 只统计 K 线拉取失败 / 历史不足等真错误，窗口不完整单列 `partial`，恒等式为 processed = completed + cannot_evaluate + partial + failed）。`--source v1` 保持旧 5~20 窗口与旧口径，不写严格结果。Web UI 默认严格口径，可通过 `review_caliber` 切到宽松；v1 强制旧口径并明确标注。

## 严格口径侧表 `dragon_review_strict`

| 表 | 用途 | 关键点 |
|----|------|--------|
| `dragon_review_strict` | 严格口径 review 结果（侧表）| `UNIQUE(trade_date, code, source)`；`review_status` ∈ `completed`/`cannot_evaluate`，`reason` 记录 partial / 无法区分原因；**无行 = 尚未重算**，不写 0/中性值；`dragons_*` 宽松字段语义不变 |

## Web UI 指标联动

> 面板由 `dashboard` 命令承载（`review --ui` / `--ui-only` 为 deprecated 别名，转发同一安装与端口策略）；`/api/dragons` 契约不变；账户页为 `/account`。

**口径筛选（查询上下文）**：`GET /api/dragons` 支持 `review_caliber=strict|loose`（缺省 / 非法值 → v2 为 `strict`、v1 为 `loose`；`source=v1` 强制宽松）。一个口径同时决定收益 / 回撤范围筛选、状态筛选的 `derived_status`、排序物理列、表格列与卡片指标；响应仍是 `{data, count, summary}`，**不新增键、不回显口径**，默认 strict 下逐值等价改造前。行内始终返回宽松字段与 `strict_*` 全套，前端只做逻辑字段 → 物理字段选择。

**`derived_status` 规则**：v1 恒取 `dragons_v1.review_status`；v2 strict 优先严格侧表状态，缺行时 `pending` / `error` / 其余 `pending_recompute`；v2 loose 直接取 `dragons_v2.review_status`（`completed` / `pending` / `no_entry` / `error`）。因此宽松模式不存在 `pending_recompute`、`cannot_evaluate` 只在严格模式出现；「宽松 completed、严格缺行」的行在宽松模式显示 `completed`，`strict_*` 列仍为 `null`。

**排序键兼容**：`sort_by` 先归一到逻辑键再按口径取物理列（`buy_date` / `buy_price` / `max_return_5d` / `max_drawdown_5d` / `max_return_hold_days` / `review_status`）；旧客户端传 `strict_*` 键在 loose 下语义降级为宽松列（v1 同类降级沿用现状），未知键回退 `composite_score`，`NULLS LAST` 两口径一致。

**前端行为**：FilterBar 在「体系」后新增「口径」Select（默认严格；v1 固定宽松并禁用；切体系 / 切口径导致生效口径变为宽松时，同 patch 把严格专有的 `pending_recompute` / `cannot_evaluate` 状态清空为「全部」，宽松选项列表不含这两项；「重置」保留 source，v2 回严格、v1 保持宽松）。**列表默认按入选日倒序**（`sort_by=trade_date` / `sort_dir=desc`，同日行按 `composite_score` 倒序兜底保证翻页稳定）。表格、卡片与客户端排序都使用**已加载响应**的口径（`responseCaliber` 与 `data` / `summary` 在 `requestSeq` 守卫内原子写入），不使用表单待提交值。状态列展示服务端 `derived_status`，状态表头排序也按 `derived_status`（不再按可能为 `NULL` 的 `strict_review_status`）。卡片平均收益 / 最佳随口径选择键；宽松胜率与严格胜率两张卡始终同时展示；「待回测」的「待重算 / 失败」副文案来自严格侧重算进度，仅 v2 + strict 展示。

## 龙头总览增量回测作业（Web，2026-11-05）

只读页面不触发回测；写入只经显式 POST 的子进程作业（Ports/Adapters：HTTP 处理函数
不建连接、不 import `dragon_quant.review`）：

- `GET /api/review/jobs/status`：纯只读 `{state, last_run, summary}`，**不启动任何任务**。
  `summary` 从既有 `--recompute-all` 输出块解析（processed / completed /
  cannot_evaluate / partial / failed / skipped / skipped_immature），解析不到为 `null`。
  **作业状态 ≠ 业务结果**：CLI 对单条 error 仍 exit 0，前端不得把 succeeded 当全部成功。
- `POST /api/review/jobs/incremental`：单飞启动
  `python -m dragon_quant review --source v2 --recompute-all`（**固定不带 `--force`**）。
  返回 `202` + 同一状态载荷；运行中重复触发 `409 {"error":"job_running", "detail": 中文}`；
  跨站 `Origin` `403 {"error":"invalid_origin"}`；spawn 同步失败 `500 {"error":"spawn_failed"}`。
  进程退出时服务 `terminate()` 清理。
- 语义复用 CLI：成熟 `pending`、`partial` 与严格侧表缺失行补算，已有非 partial 严格结果
  跳过，未成熟保留（约 6 个交易日后才能补算），不把全量强制重算伪装成增量。
- 前端：`SummaryCards` 的「待回测」卡片在 `derived_pending > 0` 时在标签行渲染 primary
  「回测」按钮（`ReviewJobControl` 以状态 hook + action/status/overlay 插槽提供）；计数未知
  （加载中/失败）不渲染。首次出现自动弹说明（只有点击「我知道了」才写版本化
  localStorage；展示不写、storage 异常不崩溃），按钮运行期间禁用防双击。
- **恢复与轮询**：挂载仅 GET status——运行中立即挂接现有作业、禁用按钮且不重复 POST；
  最近 10 分钟内完成的作业恢复结果（包括待回测归零后刷新）；父级在页面加载时只读探测运行中或最近终态，保持卡片状态区
  挂载（筛选 pending=0 也不丢状态）。轮询用串行 `setTimeout`（2s，不重叠），按
  `started_at` 作业身份守卫，过期 running 不得覆盖终态；网络失败只提示重试并继续，
  **不暗示后端任务被取消**。
- **结果展示**：有 `summary` 时展示 completed/cannot_evaluate/partial/skipped；只有
  `failed > 0`（K 线拉取失败 / 历史不足）才显示「任务部分失败」；`partial > 0` 提示
  「收益窗口不完整（未走完或窗口内缺 K 线/停牌），已排除在正式统计之外」；`processed=0`
  明确「没有需要补算的成熟标的」；没有汇总时只给「任务执行结束，仍有 N 条待处理，请查看
  状态/错误」，不推断剩余全是未成熟。成功刷新
  按**最近一次提交的筛选**进行；刷新请求失败时显示「任务已结束，但列表刷新失败…已保留
  上次有效数据」，绝不伪报已刷新（刷新被更新的查询接管时单独提示）。
- 测试 owner：`test_dashboard_followup_20261105.py`、`test_web_frontend_dashboard_followup.py`；
  浏览器交互证据脚本见 `docs/solutions/2026-11-05-dashboard-user-reported-fixes.md`。

## 入选后表现（只读端点，固定宽松口径）

`GET /api/dragons/{trade_date}/{code}/performance?as_of=YYYYMMDD` 是抽屉内的局部增强，与 `/api/dragons` 冻结契约、固定 5 日回测、`dragon_review_strict` 侧表完全隔离。

- **实现**：`dragon_quant/review/performance.py::compute_performance` 纯函数（无 DB / 无 provider / 无时钟），仅复用 `find_entry_day` 与 `kbar_to_dict`；有效 bar 逻辑本地实现并与 `review.main._is_valid_bar` 做 parity 测试。路由经 `deps.kline_provider()`（雪球，普通 `def` 线程池）与 `deps.clock_module()`（缺省 `as_of` = Asia/Shanghai 当天）注入，均可被测试替换。
- **口径**：宽松入场——入选后首个 `high != low` 日按最低价买入；窗口从买入日到 `as_of` 的最新**有效**日 K（不依赖交易日历、不跳过 T+1）。峰值只看买入日之后，并列取最早日期；回撤窗口为「买入日 → 峰值日」，峰值后的新低不计入。
- **状态与 HTTP**：记录存在时一律 200；`status` ∈ `ok` / `no_entry` / `empty_window` / `error`（传输或数据问题写 `error` 文本）。400 只给参数错误（非法日期、`as_of < trade_date`），404 只给龙头记录不存在。
- **记录存在性（2026-11-01 增量）**：`dragons_v2` 行命中，**或**当日原始扫描真龙贡献（`scan_stocks_v2` join `scans_v2`，过滤 `is_true_dragon is not False` / `rank is not None` / `rank <= scan_top_n`，按 `code` 取最佳贡献）命中即视为记录存在；两处都无才 404。计算链路不变。
- **命名**：返回 `max_return_since_entry` / `max_drawdown_since_entry` / `peak_date` / `latest_bar_date`，**不返回** `max_return_5d` / `max_drawdown_5d`；前端标签为「至最新日 K」。
- **不写库、不改 CLI**：不读/写 review 字段、不写严格侧表、不缓存状态。
- **trade-dates**：`DragonDateEntry` 增加 `items` 全量（同一排序），`preview` 仍为前三；前端本地展开，不追加按日请求。
- **近几日卡片数据源（2026-11-01 增量）**：卡片读**原始扫描真龙榜**（`scans_v2` + `scan_stocks_v2`），展示被 `dragons_v2` 5 日去重排除的当日真龙（同为 rank1 也按日展示）；日期取 `list_scan_dates` 与 `list_dragon_trade_dates` 并集，逐日构建非空卡片直到收满 `limit`。无扫描贡献的遗留日期回退 `get_dragons`。**总览表 / `/api/summary` / 回测池 / `dragons_v2` 仍只读 `dragons_v2`，5 日去重语义完全不变**——`count` 表示「当日原始真龙支数」，可与回测池条数不同。
- **明细回退**：`GET /api/dragons/{date}/{code}` 优先按口径查 `dragons_v2`；缺行时回退当日扫描最佳贡献（`dims` / `reject_reason` 仍经 `get_scan_stocks`，`review_loose` / `review_strict` / `derived_status` 为空，`dims_source` 为 `scan_stocks`）。
- **抽屉展示**：移除双口径对照卡片，保留真伪/否决/当前口径徽标、Meta、概念、五维、否决原因与扫描报告原文（默认展开）；绩效卡独立请求、失败卡内降级并可重试，不阻塞明细。

**Web UI 指标联动**：顶部 7 张卡片（总记录 / 已完成 / 待回测 / 平均收益 / 宽松胜率 / 严格胜率 / 最佳）与列表同源。`GET /api/dragons` 成功响应为 `{data, count, summary}`：`summary` 由筛选后的完整 rows 经 `dragons.summarize_review_rows(rows, source, include_derived_counts=True, caliber=caliber, maturity_cutoff=…)` 计算（客户端分页不影响统计，未来若服务端分页必须先汇总再切片），`data` / `count` 不变；`caliber` 只影响 `derived_completed` / `derived_pending` 的状态来源（宽松直接取 `review_status`），旧键与 `*_strict` 键均不随口径变化。`maturity_cutoff` 由端点用本地已确认交易日（`prep_assets.stock_daily` bar 日期并集，只读不触网）经 `maturity_cutoff()` 计算；日历缺失时传 `None` 退回旧口径。`GET /api/summary` 保留为无筛选参数的 legacy 端点，其实现是 `get_review_summary` 兼容 wrapper（同样从 `query_dragons` 全量 rows 汇总），**不接收口径参数、完全不变**。`summary` 在 legacy 键之外附加 `derived_completed` / `derived_pending` / `derived_pending_immature`，以及 `partial_window` / `partial_missing_bars` 分项计数：`derived_pending` 只统计**收益窗口已走完**（入选日 ≤ 成熟度上界）且**尚无严格结果**的记录（`pending` + `pending_recompute`）；未成熟记录进 `derived_pending_immature`；所有 `partial`（时间未到或窗口内停牌缺 K 线）都已经有结果、且停牌型不会自愈，一律不计入待回测，避免按钮/数字被不可回测的记录长期占住。旧数据把停牌缺 bar 写成 `partial_window` 的成熟行，由 `compat._normalize_legacy_partial_reason` 在响应里改判为 `partial_missing_bars`（只改展示，不回写库；下次重算落新 reason）。row 只增返回 `derived_status`。前端仅在初始 / 出错时允许 `Summary | null`，修改筛选不自动请求，点击「查询」或回车同时刷新卡片与列表；「重置」保留 source、口径回到该体系默认，并清空其余筛选、立即恢复全量；慢旧响应由请求序号丢弃。空集或无可评测样本显示 `—`，真实无赢家显示 `0.0%`。

## 严格指标共享核（2026-11-02）

严格口径的收益 / 回撤纯计算已抽到无 I/O 的 `dragon_quant/review/strict_metrics.py`：
`is_valid_bar` / `window_metrics` / `evaluate_strict`。`review/main.py` 的
`_is_valid_bar`、`_strict_review` 保留为兼容别名并改为调用共享核；`_loose_review`
也改用 `window_metrics`。Web 的 `presenters/day_effect.py` 只调用
`evaluate_strict`，不 import `review.main`（避免把 provider / storage 拉进 Web 进程）。

约束：共享核逐字迁移、**不得**改变数值或输出字段形状。`dragon_quant/tests/test_review_strict_metrics.py`
以 parity 断言旧测试全部不变的数值语义；新公式或阈值必须改 owner 文档并补 parity。
`bs_eval` 也复用本核计算买后/卖后 5 日窗口（不改 review 双口径），见 [bs_eval](bs_eval.md)。
`review/performance.py` 的宽松「至最新日 K」口径与此核完全隔离。

## 只读投影说明（2026-11-01）

本次不改固定 5 日回测与 `dragon_review_strict`。dashboard 新增的扫描运行态、复盘/竞价日历与成效全部是只读投影（`web_ui/backend/presenters/`）；竞价成效复用 `infra.factor.decision_review`，行情只读本地 `prep_assets.stock_daily`，不触网。

## 成熟度与 partial（2026-11-05，A1/KR1）

- 收益窗口仍是「买入后第 2~5 个交易日」（`WINDOW_SKIP=1` / `WINDOW_SIZE=4`），由此派生
  **所需交易日数 = 1 + WINDOW_SKIP + WINDOW_SIZE = 6**；成熟度上界由
  `review.strict_metrics.maturity_cutoff()` 统一计算，不再硬编码「入选满 5 日」。
- 窗口未走完时严格侧表与宽松 `review_status` 都写 `partial`（原因 `partial_window`）；
  窗口日期已过但个股缺日 K（停牌 / 数据缺失）写 `partial`（原因
  `partial_missing_bars`）：两者都可展示，**不进入正式成效分母**；
  `summarize_review_rows` 的 `partial` / `partial_window` / `partial_missing_bars`
  计数与 `avg_return_strict` / `win_rate_strict` 分母均已排除。列表状态列据此区分
  「窗口未成熟」与「窗口缺K线」。
- 「待回测」口径：只统计**收益窗口已走完**（入选中日 ≤ `maturity_cutoff()`）且**尚无
  严格结果**的记录（`pending` + `pending_recompute`）；未成熟记录进
  `derived_pending_immature`。任何 `partial`（时间未到 / 窗口内停牌缺 K 线）已有结果、
  且停牌型重试不会自愈，**不计入待回测**；成熟行上的旧 `partial_window` 在展示层按
  `partial_missing_bars` 归类。
- 默认 `review` 选择 `pending + partial`，partial 不是终态，下次可合法补算；
  `--recompute-all` 只跳过非 partial 的已有严格行。
- 新增独立研究口径 `tradable_open_v1`（`evaluate_tradable_open`）：T+1 开盘入场，
  一字板（`high==low`）排除、不顺延；固定 1/3/5 持有期收盘净收益，扣固定成本。
  它只作可成交估算，不替代历史严格公式，也不宣称真实成交。
