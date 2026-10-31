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
- **严格口径**（写侧表 `dragon_review_strict`，`UNIQUE(trade_date, code, source)`）：入选后**首个交易日**按**开盘价**买入；次日一字涨停按开盘价成交、不顺延。次日缺 bar / 无效 bar / 零成交平盘占位 → `cannot_evaluate`（原因固定「停牌或数据缺失无法区分」）；传输失败不写/不覆盖严格结果；严格窗口为空 `cannot_evaluate`，部分窗口沿用公式并标 `partial_window`；宽松旧 0/0 契约不变。

`review` 默认只处理 pending、取消 20 交易日上限（不足 5 交易日等待），显式 `--date` 绕过成熟度；`--force` 重算所有成熟状态并覆盖严格。按 code 聚合拉 K（每 code 一次，深度覆盖最老记录）、日历一次；拉取后校验 `min(k.date) <= trade_date`，否则报「历史数据不足」而不是拿最近窗口冒充。`--recompute-all` 仅 v2、最老优先、默认跳过已有严格行（`--force` 覆盖）、跳过 VPA；`--dry-run` / `--limit` 只能与其组合（非法组合 exit 2）；dry-run 是未过滤成熟度的上界，正式运行结束输出 processed/completed/cannot_evaluate/failed/skipped 聚合汇总。`--source v1` 保持旧 5~20 窗口与旧口径，不写严格结果。Web UI 默认严格口径，可通过 `review_caliber` 切到宽松；v1 强制旧口径并明确标注。

## 严格口径侧表 `dragon_review_strict`

| 表 | 用途 | 关键点 |
|----|------|--------|
| `dragon_review_strict` | 严格口径 review 结果（侧表）| `UNIQUE(trade_date, code, source)`；`review_status` ∈ `completed`/`cannot_evaluate`，`reason` 记录 partial / 无法区分原因；**无行 = 尚未重算**，不写 0/中性值；`dragons_*` 宽松字段语义不变 |

## Web UI 指标联动

> 面板由 `dashboard` 命令承载（`review --ui` / `--ui-only` 为 deprecated 别名，转发同一安装与端口策略）；`/api/dragons` 契约不变；账户页为 `/account`。

**口径筛选（查询上下文）**：`GET /api/dragons` 支持 `review_caliber=strict|loose`（缺省 / 非法值 → v2 为 `strict`、v1 为 `loose`；`source=v1` 强制宽松）。一个口径同时决定收益 / 回撤范围筛选、状态筛选的 `derived_status`、排序物理列、表格列与卡片指标；响应仍是 `{data, count, summary}`，**不新增键、不回显口径**，默认 strict 下逐值等价改造前。行内始终返回宽松字段与 `strict_*` 全套，前端只做逻辑字段 → 物理字段选择。

**`derived_status` 规则**：v1 恒取 `dragons_v1.review_status`；v2 strict 优先严格侧表状态，缺行时 `pending` / `error` / 其余 `pending_recompute`；v2 loose 直接取 `dragons_v2.review_status`（`completed` / `pending` / `no_entry` / `error`）。因此宽松模式不存在 `pending_recompute`、`cannot_evaluate` 只在严格模式出现；「宽松 completed、严格缺行」的行在宽松模式显示 `completed`，`strict_*` 列仍为 `null`。

**排序键兼容**：`sort_by` 先归一到逻辑键再按口径取物理列（`buy_date` / `buy_price` / `max_return_5d` / `max_drawdown_5d` / `max_return_hold_days` / `review_status`）；旧客户端传 `strict_*` 键在 loose 下语义降级为宽松列（v1 同类降级沿用现状），未知键回退 `composite_score`，`NULLS LAST` 两口径一致。

**前端行为**：FilterBar 在「体系」后新增「口径」Select（默认严格；v1 固定宽松并禁用；切体系 / 切口径导致生效口径变为宽松时，同 patch 把严格专有的 `pending_recompute` / `cannot_evaluate` 状态清空为「全部」，宽松选项列表不含这两项；「重置」保留 source，v2 回严格、v1 保持宽松）。表格、卡片与客户端排序都使用**已加载响应**的口径（`responseCaliber` 与 `data` / `summary` 在 `requestSeq` 守卫内原子写入），不使用表单待提交值。状态列展示服务端 `derived_status`，状态表头排序也按 `derived_status`（不再按可能为 `NULL` 的 `strict_review_status`）。卡片平均收益 / 最佳随口径选择键；宽松胜率与严格胜率两张卡始终同时展示；「待回测」的「待重算 / 失败」副文案来自严格侧重算进度，仅 v2 + strict 展示。

**Web UI 指标联动**：顶部 7 张卡片（总记录 / 已完成 / 待回测 / 平均收益 / 宽松胜率 / 严格胜率 / 最佳）与列表同源。`GET /api/dragons` 成功响应为 `{data, count, summary}`：`summary` 由筛选后的完整 rows 经 `dragons.summarize_review_rows(rows, source, include_derived_counts=True, caliber=caliber)` 计算（客户端分页不影响统计，未来若服务端分页必须先汇总再切片），`data` / `count` 不变；`caliber` 只影响 `derived_completed` / `derived_pending` 的状态来源（宽松直接取 `review_status`），旧键与 `*_strict` 键不随口径变化。`GET /api/summary` 保留为无筛选参数的 legacy 端点，其实现是 `get_review_summary` 兼容 wrapper（同样从 `query_dragons` 全量 rows 汇总），**不接收口径参数、完全不变**。`summary` 在 legacy 键之外附加 `derived_completed` / `derived_pending`（`pending` + `pending_recompute`）；row 只增返回 `derived_status`。前端仅在初始 / 出错时允许 `Summary | null`，修改筛选不自动请求，点击「查询」或回车同时刷新卡片与列表；「重置」保留 source、口径回到该体系默认，并清空其余筛选、立即恢复全量；慢旧响应由请求序号丢弃。空集或无可评测样本显示 `—`，真实无赢家显示 `0.0%`。
