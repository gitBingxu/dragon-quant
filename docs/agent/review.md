# review — 龙头回测双口径与 Web UI 指标联动

> 触发：改动 `review` 回测流程、宽松/严格口径、`dragons_*` review 字段写回、`dragon_review_strict` 侧表，或回测 Web UI 的 API 与指标卡片时读本文。
> 本文权威：回测双口径规则与成熟度、严格口径侧表契约、`GET /api/dragons` 汇总契约与前端取数行为。
> 上游权威：AGENTS.md（硬约束与工作流）；实现见 `review/main.py`、`web_ui/server.py`、`scan/storage/dragons.py`。

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

`review` 默认只处理 pending、取消 20 交易日上限（不足 5 交易日等待），显式 `--date` 绕过成熟度；`--force` 重算所有成熟状态并覆盖严格。按 code 聚合拉 K（每 code 一次，深度覆盖最老记录）、日历一次；拉取后校验 `min(k.date) <= trade_date`，否则报「历史数据不足」而不是拿最近窗口冒充。`--recompute-all` 仅 v2、最老优先、默认跳过已有严格行（`--force` 覆盖）、跳过 VPA；`--dry-run` / `--limit` 只能与其组合（非法组合 exit 2）；dry-run 是未过滤成熟度的上界，正式运行结束输出 processed/completed/cannot_evaluate/failed/skipped 聚合汇总。`--source v1` 保持旧 5~20 窗口与旧口径，不写严格结果。Web UI 表格 / 收益 / 回撤 / 状态筛选固定严格口径，v1 明确标注旧口径。

## 严格口径侧表 `dragon_review_strict`

| 表 | 用途 | 关键点 |
|----|------|--------|
| `dragon_review_strict` | 严格口径 review 结果（侧表）| `UNIQUE(trade_date, code, source)`；`review_status` ∈ `completed`/`cannot_evaluate`，`reason` 记录 partial / 无法区分原因；**无行 = 尚未重算**，不写 0/中性值；`dragons_*` 宽松字段语义不变 |
