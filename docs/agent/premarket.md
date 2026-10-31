# premarket — 只读盘前预案上下文（T0）

> 触发：改动 `premarket` 命令、`review_account/premarket/*`、盘前快照 Adapter、三锚/行动上限投影、盘前 JSON 契约或 Skill 盘前模板时读本文。
> 本文权威：`premarket` 的只读语义、快照选择严格性、`premarket_sc_v1` 字段与时点、降级原因码、三锚投影与行动上限、外部证据边界。
> Skill 侧模板与信源流程 owner：`skills/dragon-quant/references/premarket.md` / `evidence.md`。
> 上游权威：AGENTS.md（硬约束与项目路由）；prep/auction 产物契约见 [prep-auction](prep-auction.md)；策略数值见 `review_account/strategy/STRATEGY.md`。

## 命令

```bash
dragon-quant premarket                    # T=今天（Asia/Shanghai），human 摘要
dragon-quant premarket --date 20261030    # 指定 T（YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD）
dragon-quant premarket --json             # 完整 JSON（premarket_sc_v1）
```

- 顶层命令注册为 `LazyCommand(..., needs_providers=_never_providers)`；`commands/premarket.py` 只做参数 / 输出 / 退出码。
- **T0**：只读 `_connect_readonly`（`mode=ro`、不执行 WAL PRAGMA、跳过 `_ensure_schema`、零 DDL、零 WAL/SHM sidecar）；不构造 provider、不触网、不重算、不写库、不新增表。
- 退出码：`0` 成功（含结构化降级）；`2` 参数错误（非法 / 未来日期）；`1` 运行错误。`--json` 错误 JSON 到 stdout，默认错误写 stderr。
- `-h` / `import dragon_quant` / `premarket -h` 在空 `DQ_DATA_DIR` 下不创建任何数据目录、DB、sidecar、缓存（B1）。

## 模块

| 模块 | 职责 |
|---|---|
| `review_account/premarket/main.py` | Facade `run_premarket`：日期解析、时钟注入、组装 context |
| `snapshot.py` | Adapter：只读 prep / auction / dragons 镜像；逐行 JSON 损坏隔离 |
| `context.py` | `premarket_sc_v1` 顶层结构、时点、池重建、候选 join、数据质量 |
| `anchors.py` | 三锚投影（短线 / 趋势 / 情绪）与角色卡去重 |
| `actions.py` | 行动上限过滤链；只复制既有动作枚举 |
| `report.py` | 固定行序 human 摘要（Template Method） |

纯函数模块（context / anchors / actions / report）不读时钟、环境、文件或 DB；`run_at` / `now` 由 Facade 注入。

## 快照选择与严格性

- prep：`max(trade_date) where trade_date < T`，`strictness="latest_strictly_before"`；绝不自比、绝不选 T 日或未来快照。
- `proven_gap`：读取本地 `assets.db.dragons` 镜像交易日；若存在 `prep.trade_date < d < T` 的镜像日 → `true` 且 `strictness="proven_not_t_minus_1"`，降级 `prep_not_strict_t_minus_1`；不存在 → `null`（离线不能证明紧邻 T-1，不伪装成已确认）。
- `age_calendar_days` 只作展示，不推断是否 T-1。
- frozen_at 只取 `result.strategy_context.frozen_at`；缺失即 `null` + 原因，不用 `prep_frozen_at()` 合成。

## 时点字段（不得混用）

| 字段 | 来源 | 含义 |
|---|---|---|
| `run_at` / `info_cutoff` | 本次运行 | 报告信息截止，不是数据时点 |
| `prep.frozen_at` | `strategy_context.frozen_at` | T-1 盘后决策阶段时点 |
| `prep.created_at` | 快照行 | 快照最后写入时间 |
| `auction.as_of` / `ready_at` | 由 T 推导 | 09:25 竞价终态语义时点 |
| `auction.positions[].created_at` | 快照行 | 写入时间 |
| `auction.positions[].analysis.evidence.last_time` | 盘口末笔 | 不是快照写入时间 |

## 来源状态与降级

- `prep.state`：`ready` / `missing` / `corrupt` / `read_error` / `unsupported_schema`。
- `auction.state`：`ready` / `partial` / `not_ready` / `missing` / `corrupt` / `read_error`。
- `auction.available=true` 仅当 `state ∈ {ready, partial}`；其余不参与行动，`observe_only`，不等于看空。
- `data_quality.status`：`ok` / `degraded` / `unavailable`。`plan_usable = prep.available ∧ schema_supported ∧ review_available`；`auction_usable = auction.available ∧ state ∈ {ready, partial}`。
- 09:25 前当日 `auction.state=not_ready`，已有行放 `positions[]` 且 `usable=false`、`pre_ready_rows_present=true`（只审计，不参与行动）。
- `cache.status="unknown"`（`auction_snapshots` 不持久化 `from_cache`）与 `trading_day_verified=false`（离线无交易日历）是已知限制，不进入 `degraded`，只写 `data_quality.notes`。
- 稳定原因码由 `context.py` 的 `_prep_degraded` / `_auction_degraded` 按固定顺序生成并去重；新增原因码必须同步 `context.py`、Skill 模板与本节。

## JSON 契约要点（`premarket_sc_v1`）

顶层键序固定：`ok / context_version / generated_by / source / trade_date / run_at / info_cutoff / trading_day_verified / compatibility / as_of / prep / auction / gates / market / anchors / portfolio / candidates / data_quality / disclaimer`。

- `prep.result` 原样嵌入完整 `result_json`；`auction.positions[].analysis` 原样嵌入完整 `analysis_json` v2（去 envelope 键 `trade_date` / `created_at` / `error`）。
- `auction.pool` 只按 `eligibility=="eligible" ∧ position_pct>0` 从 `review_candidates` 重建；`extra_codes` 只说明快照含非 T-1 推荐 code，不据此判定 auction 回退。
- `candidates[]` 是只读 join 视图：按 `review_candidates` 顺序，不重排、不新增分数；完整原值仍在 `prep.result` 与 `auction.positions[].analysis`。
- `gates.hard_risk.prep_known` 列出逐只风险字段；`gates.account` 恒为 `unknown`（CLI 不读账户），账户闸门由 Agent 在报告层与用户确认。

## 三锚投影

- 短线：`review.mainlines[].role_map.leader/vanguard` + `review.anchors.positive/negative`。
- 趋势：`role_map.mainstay/capacity`；宽基 / 风格指数离线不可得 → `index=null` + `index_missing_reason="not_in_snapshot"`，不得用候选池冒充指数。
- 情绪：`review.anchors.positive/negative` + `result.sentiment` 的 T-1 宽度；缺失即 `null`，不补 0。
- 角色卡同一 code 只保留一张：`short_term > trend`；不新增股票、不新增阈值、不做全市场推断。

## 行动上限

固定谓词链（`actions.py`）：prep 可用 → `eligibility=eligible` → 冻结预期存在 → `position_pct` 三态 → auction 行可用 → veto → auction `data_quality=ok` → `wait`。

- `position_pct=0` → `no_new_position`（不建议新建，**不是清仓**）；`null` → `observe_only`。
- `expectations` 缺失（含 `role=null` 诊断标的）→ `observe_only`。
- auction veto / `is_weak` → `no_new_position`；auction 质量非 `ok` → `observe_only`。
- 全部通过 → `wait`（09:30 前不宣布 confirmed）；`after_verification` 原样复制冻结四态集合。
- 任何情况下 `position_exit="existing_sell_rules_only"`；不输出买卖指令、不自动交易。

## 外部证据边界

Python 不接外网、不做 Cookie 登录、不逆向私有 APP；外部政策 / 公告 / 外围行情由 Agent 按 Skill `evidence.md` 采集，官方原文优先，媒体未核原文不得硬否决，缺失只降低情景覆盖。需要登录时提示用户授权。

## 测试

- 单元：`dragon_quant/tests/test_premarket_{snapshot,context,actions,cli}.py`。
- E2E：`dragon_quant/tests/e2e/test_premarket.py` + `golden/premarket/context.json`；coverage-map 固定 54 节点 / 16 顶层命令。
