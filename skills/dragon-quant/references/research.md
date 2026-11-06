# research — 研究读回、诊断与消融流程

**何时读我**：用户问“识别质量有没有改善 / 为什么这批扫描入选差 / 某笔账户交易为什么亏 / 哪个卖出规则在拖后腿 / 把历史证据找出来”时。

**边界**：`research` 全部读本地存量；不构造 provider、不触网、不建目录/库/schema/cache/报告（`experiments run` 例外，写实验报告）。字段与过滤器以 `research schema` 和 JSON 输出为唯一契约；不手写 SQL、不复制评分权重或策略参数数值。

## 1. 研究问题 → 覆盖 → 样本 → 基线 → 实验 → 解释

1. **问题**：先写清是识别质量（机会）、方向命中、整笔交易盈利率，还是本段权益贡献；四者不可互换。
2. **覆盖**：`research datasets` 看 `availability`（`available` / `database_missing` / `table_missing`）与 `date_range`；缺库/缺表是有效结论，不补 0、不猜覆盖全市场。
3. **样本**：`research query` / `scan-quality` / `account` 明确样本单位、分母、成熟度、排除原因；`meta.returned/truncated/next_cursor` 说明是否只看了一页。
4. **基线**：同期间、同 `source=v2`、同评分/策略/快照版本；`research strategy` 核对 current vs historical。跨版本默认不可比。
5. **实验**：账户消融用 `research experiments run --run-id N --confirm`（T1）；冻结期初与上游，`list/show` 只读；`no_change` 不是改进，`insufficient_evidence` 不是 0。
6. **解释**：事实 / 解读 / 待验证三分；机会质量不等于账户盈利率；不自动推广参数。

## 2. 常用路径

- **扫描诊断**：`research scan-quality --from D1 --to D2 --json` → 看 `coverage`（原始贡献、canonical 候选、成熟样本、排除）、`baseline`、`groups`、`diffs_vs_baseline`、`coverage_gap`；`status=insufficient_data` 时读 `reason` 与 `read_errors`。需要逐样本账本时加 `--include-samples`，用 `--sample-limit` / `--sample-cursor` 分页（默认不含，cursor 绑定区间）。
- **候选溯源**：`research query --dataset scan_candidates --scan-id <id>` 与 `--dataset scan_runs`；同一 code 同日多次扫描的去重是研究方选择，工具不替你决定。
- **历史入选与严格回测**：`dragons` / `review_strict`；严格结论缺失表示未回测，不是 0 收益。
- **账户复盘**：`review-account --json` 取真实 `run_id` → `research account --run-id N --view summary|snapshots|trades|positions|events`；父 run 固定 v2，收益率/回撤/胜率是百分比点（1.0=1%）；`meta.data_quality` / `ledger_truncated` / `caveats` 说明回测撮合、费用与账本是否截断。
- **信号**：`research signals --status open|closed|all`；含平仓腿与半仓腿、原因码；不是券商成交，未存诊断字段标 unavailable。
- **快照**：`research snapshots --kind prep|auction`；只读已存、固定 v2、不采集、不重算；prep `--code` 只认结构化股票键，未知旧结构返回 `code_filter_unsupported`；大字段默认不返回，需要时用 `research query --fields result|analysis` 显式选择。
- **规则核对**：`research strategy --kind scan`（当前评分器权重/门槛元信息）或 `--kind account --run-id N`（该批次冻结参数）；current 不补历史。
- **消融**：先 `research experiments list --run-id N` 确认基线批次与已存报告，再 `run --confirm`，可重复 `--sell-rule NAME`（只接受已有规则；未知规则报参数错误）。

## 3. 输出约定

- `--json` 的 stdout 只有一个 `research_v1` envelope：`schema_version/ok/command/request/data/meta/warnings/error`。
- 成功与可解释降级都 `ok=true`；`meta.empty_reason` 区分 `database_missing` / `table_missing` / `no_rows_in_scope` / `run_not_found` 等。
- 非法 dataset/field/filter/cursor exit 2；只读不可用、I/O 损坏等真实故障 exit 1，且不混成空集。
- 分页 cursor 绑定 dataset + 过滤器 + 字段选择；改过滤器后旧 cursor 被拒绝。活动库并发变更不冒充冻结快照。
- `experiments run` 单飞：Web 与 CLI 共用进程级互斥；busy 时明确告知，稍后重试，不并发硬跑。

## 4. 禁止

- 不直接查 SQLite / 手工 SQL；不导出凭据、Cookie、token 或系统表。
- 不把 `research experiments run` 说成只读；没有真实 `run_id` 不猜“最新批次就是本次”。
- 不用单次结果证明策略有效；不自动修改权重、阈值、默认策略。
