# datasets — research 数据集导航

**何时读我**：需要选择数据集、过滤器或解释 `research` JSON envelope 时。**字段真源**始终是 `dragon-quant research schema --dataset N`；本文只做导航，不复制 DDL。

## 数据集

| dataset | 用途 | 可用过滤器 | 备注 |
|---|---|---|---|
| scan_runs | 每次扫描的元信息 | --from --to --scan-id | 只含已落库扫描 |
| scan_candidates | 原始候选与五维分 | --from --to --code --scan-id | 同日可多次扫描；去重由研究方决定 |
| dragons | 5 日去重后的真龙入选池 | --from --to --code | 不是全市场覆盖 |
| review_strict | 严格可成交回测结论 | --from --to --code | 未回测无行 |
| account_runs | 账户批次与期初状态 | --from --to --run-id | 收益率/盈利率是既有 evaluation 口径 |
| account_snapshots | 逐日权益与持仓 | --from --to --code --run-id | 跨 run 非原子 |
| account_trades | 成交、费用与原因码 | --from --to --code --run-id | 回测撮合，不是券商成交 |
| account_positions | 持仓周期与清仓后窗口 | --from --to --code --run-id | 半仓腿不单独成行 |
| account_events | 资金/风控事件 | --from --to --code --run-id | 只记录已发生动作 |
| signals | 买卖信号与半仓腿 | --from --to --code --status | status=open/closed/all |
| prep_snapshots | 盘后复盘快照 | --from --to --code | 固定 v2；code 只认 candidates[] / review_candidates[] / strategy_context.positions[] 的结构化 code 键；未知旧结构返回 unsupported |
| auction_snapshots | 集合竞价快照 | --from --to --code | 固定 v2；只读已存；analysis 为大字段 |
| factor_values | 逐日逐股因子值 | --from --to --code | 缺失=无行，不是 0 |
| factor_registry | 因子元数据/版本 | — | 同名多版本不可混读 |
| factor_build_runs | 因子构建审计 | --from --to | report 为大字段 |
| factor_models | 模型快照 | — | 可复现决策输入 |
| experiments | 已存对照实验报告 | --run-id --experiment-id | 只读，不重算；父 run 固定 v2；list 默认省略 report，show 返回完整 |

## 口径与单位

- 所有 `scan_*` / `dragons` / `review_strict` / 账户 / 信号 / prep / auction 数据集固定 `source=v2`；账户子表只返回父 run 为 v2 的行。
- 收益率/回撤/胜率/持仓浮动收益等字段是**百分比点**（`1.0 = 1%`），不是比例；不要二次换算。
- `factor_values.source` 是业务来源（`scan` / `prep` / `auction` / `fin`），**不是** v2 口径，不能按 `v2` 过滤。
- prep `--code` 只做结构化股票身份匹配；旧 payload 无可识别结构时 `empty_reason=code_filter_unsupported`，不会退化成金额/自由文本/指数代码包含匹配。
- 缺库/缺表/旧列/无记录分别是 `database_missing` / `table_missing` / `schema_mismatch` / `run_not_found` 等；JSON 损坏标记 `data_quality=degraded` 并给 `corrupt_json_fields`，不宣称完整。
- 大字段 `result` / `analysis` / `report` 默认省略；`show` 显式返回实验完整报告，`query` 需 `--fields` 显式选择。分页截断看 `truncated/next_cursor`，账户摘要看 `ledger_truncated`。

## 授权

`datasets/schema/query/scan-quality/account/signals/snapshots/strategy` 与 `experiments list/show` 为 T0 只读；`experiments run` 是 T1，必须真实 `--run-id` 与 `--confirm`。不开放任意 SQL 或系统表；不读取凭据/Cookie。

## 过滤器

- 通用：`--from --to`（YYYYMMDD / YYYY-MM-DD）、`--code`（6 位）、`--scan-id`、`--run-id`、`--status`、`--experiment-id`。
- 只支持所选数据集声明的过滤器；未知项 exit 2（不静默忽略）。日期必须是真实日期且 `--from <= --to`。
- 分页：`--limit` 默认 100、上限 1000；`next_cursor` 绑定 dataset/过滤器/字段，换过滤器必须从第一页开始。
- 大字段（`result` / `analysis` / `report`）默认不返回；需要时用 `--fields` 显式选择。

## envelope

`schema_version=research_v1`、`ok`、`command`、`request`、`data`、`meta`、`warnings`、`error`。`meta` 固定含 `source=v2`、`data_quality`、`returned`、`truncated`、`next_cursor`、版本可用性、`caveats`；`meta.non_atomic_read=true` 表示跨库读取不是冻结快照。

## 解释纪律

空集先看 `empty_reason`：缺库/缺表/区间无数据/run 不存在是不同结论。`ok=false` 才是真实故障；exit 2 是参数或 cursor 错误。不要把 `truncated=true` 的部分结果说成全量。
