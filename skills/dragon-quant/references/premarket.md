# premarket — 盘前预案（workflow + 八段固定模板）

**何时读我**：生成 T 日盘前预案、解释 `premarket_sc_v1` 字段、叠加外部证据时。
字段事实只来自 `dragon-quant premarket --json`（T0）；本文不复制评分权重、策略参数或表结构。

## 工作流

1. `dragon-quant premarket [--date D] --json`。T0：不写库、不触网、不重算、不新增表。
2. 先读闸门：`data_quality.status / plan_usable / auction_usable / degraded`；`prep.strictness` 与 `prep.proven_gap`；`auction.state`。
3. **信息截止** `info_cutoff` = 本次运行；三个时点分开：`prep.frozen_at`（T-1 决策）、`prep.created_at`（快照写入）、`auction.as_of`（09:25 语义时点）；`auction.positions[].created_at` 是快照写入时间，`evidence.last_time` 才是盘口末笔。
4. 09:25 前 `auction.state=not_ready`：只出无竞价版，写「竞价尚未形成」；09:25 后重跑读取终态，**不得倒填**。
5. 外部证据按 `references/evidence.md` 采集；缺失只降低情景覆盖。
6. 按下方八段模板输出，事实 / 工作假设 / 行动分栏，每段末尾写「待验证/证伪」。

## 硬约束

- 事实栏只写 CLI 字段 / 数值 / 来源 / 时点；推断必须进「工作假设」并可被证伪。
- 缺失写「缺失 / 不可用 / 不在本次数据范围」，不补 0、不写「弱 / 看空 / 中性」。
- `cache=unknown` 与 `trading_day_verified=false` 是已知限制，不当作降级。
- `position_pct=0` 写「不建议新建仓，**不是清仓**」；`null` 写「无法给出，只观察」。
- `auction.state` ∈ `missing / not_ready / corrupt / read_error / partial / ready`；只有 `ready / partial` 可作竞价事实，其余只观察，**不等于看空**。
- 四态 `confirmed / weakened / invalidated / unknown` 只做条件预告；完整判定属于 T+1 收盘反馈。
- `gap_high` 之上只写追价警戒 / 等待复核，不发明买点。
- 持仓退出只归现有 sell 规则；`premarket` 不自动交易、不输出买卖指令。
- 不新增评分、不重排候选、不按分数排买入优先序。

---

# 盘前预案｜{trade_date}

- 信息截止：{info_cutoff}（run_at={run_at}）
- 本报告只读、不自动交易、不构成投资建议、不承诺收益。
- 数据质量：{data_quality.status}；plan_usable={plan_usable}；auction_usable={auction_usable}
- 降级：{degraded}；已知限制：cache_status_unknown / trading_day_unverified

## 1. 信息截止与覆盖

| 事实 | 工作假设 | 行动 |
|---|---|---|
| T={trade_date}；T-1 prep={prep.trade_date}；frozen_at={prep.frozen_at}；created_at={prep.created_at}；version={prep.schema_version}；config={prep.review_config}；strictness={prep.strictness}；proven_gap={prep.proven_gap} | 假设 T-1 计划仍是当前有效计划；`proven_gap=true` 时该假设不成立 | prep 不可用或 proven_gap=true → 只观察，不新建 |
| auction.state={auction.state}；as_of={auction.as_of}；rows={auction.row_count}；created_at={auction.created_at}；cache={auction.cache.status} | 只有 09:25 终态可作竞价事实；缓存状态未知 | 09:25 前写「尚未形成」；09:25 后重跑 |
| 外部覆盖：政策={…}；交易所={…}；公司公告={…}；官方行情={…}；媒体={…}；社交={…} | 未覆盖的来源不能当作无风险 | 缺失只降情景覆盖，不填中性 |

**待验证/证伪**：…

## 2. 市场与账户总闸门

| 事实 | 工作假设 | 行动 |
|---|---|---|
| 数据闸门：{gates.data.status}；降级：{gates.data.reasons} | 数据不足时任何结论都不可执行 | 只观察 / 不新建 |
| T-1 硬风险：{gates.hard_risk.prep_known}；隔夜外部硬风险：{Agent 证据} | 媒体未核原文不构成硬否决 | 有硬风险 → 取消 / 不新建；持仓退出走现有 sell 规则 |
| T-1 市场：{market.state}/{market.score}/{market.coeff}；隔夜外围/新闻增量：{证据} | 外围只调整情景与风险预算，不给个股加分 | 按既有规则：按计划观察 / 降档 / 只观察 / 不新建 |
| 账户闸门：{gates.account.status}（unknown=不知道） | 账户风险约束未知时不得声称可执行仓位 | 未知就写未知；不归一、不机械相加 |
| 组合：sum_pct={portfolio.sum_pct}；warning={portfolio.warning}；same_mainline={portfolio.same_mainline_counts} | 单票仓位独立，合计不是一次买入 | 集中度提示；不给买入优先序 |

**待验证/证伪**：…

## 3. 三类锚与四种情景

三锚只做投影，不新增指标；同一 code 只保留一张角色卡（短线 > 趋势 > 情绪）。

| 锚 | 事实 | 工作假设 | 行动 |
|---|---|---|---|
| 短线 | mainline leaders={short_term.mainline_leaders}；positive={short_term.positive}；negative={short_term.negative} | 龙头/先锋与正锚共振则情绪延续 | 只作观察锚；锚失效写失效，不换锚 |
| 趋势 | mainstay={trend.mainstay}；capacity={trend.capacity}；index={trend.index}（缺失原因 {trend.index_missing_reason}） | 中军/容量承载趋势；指数需外部官方行情 | 指数缺失只降趋势覆盖 |
| 情绪 | positive/negative={sentiment.positive/negative}；breadth={sentiment.breadth} | 宽度只描述 T-1，不是 T 日实时 | 不把 T-1 宽度当 T 日结论 |

| 情景 | 可观察表现 | 行动 |
|---|---|---|
| 正常（normal） | 锚与冻结预期一致；竞价/开盘未触发否决 | 按 `allowed_actions` 的条件计划观察 / 验证 |
| 超预期 | 锚同步走强、容量跟随；但已处 `gap_high` 之上 | 只列追价警戒 / 等待复核，不发明新买点 |
| 低于预期 | 锚走弱、负锚下杀、缺口/量比未达标 | 降档 / 取消新建；持仓退出走现有 sell 规则 |
| 无法判断 | 关键数据缺失、auction unknown、外部未覆盖 | 只观察，不新建；写明缺什么 |

**待验证/证伪**：…

## 4. 主线与角色复核

| 事实 | 工作假设 | 行动 |
|---|---|---|
| 主线：{prep.result.review.mainlines}；T-1 状态={state} | 隔夜公告是否改变逻辑；媒体转述未核原文不算改变 | 逻辑未变 → 沿用；被证伪 → 降档 / 只观察 |
| 角色：leader/vanguard/mainstay/capacity/follower 在竞价中的相对表现={auction.positions} | 候选池不等于板块竞价广度 | 缺失写未知；不从候选池反推全市场 |

**待验证/证伪**：…

## 5. 候选逐只条件表

**5A 事实与血缘**

| 代码/名称 | 角色 | 原始入选/T-1 血缘 | eligibility | position_pct | position_basis | 冻结预期 gap_low/gap_high/ratio_low/seal_by | 公告证据等级 | 竞价 gap/ratio/换手/转换 | auction status / veto / data_quality | 09:30 后验证 | 持仓退出归属 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| {code/name} | {role} | {prep.source_trade_date / facts} | {eligible/not_qualified/unknown} | {>0 / 0 / null} | {position_basis} | {expectations} | {official/media_unverified/none} | {raw_factors/evidence} | {status/is_weak/reject_reason/data_quality} | {requires} | 现有 sell 规则 |

每只另附 `gate_trace` 与 `warnings` 原文摘要；不重新解释成新评分，也不省略 `data_insufficient` / `hard_risk` 等原因。

**5B 四态条件与行动**

| 代码 | confirmed 触发 | weakened 触发 | invalidated 触发 | unknown 触发 | 当前可做（action_ceiling） | 验证通过后 allowed_actions | 阻断原因 |
|---|---|---|---|---|---|---|---|
| {code} | gap/ratio/seal 条件 | gap/ratio 未达标 | 深度低开/跌破前收/炸板/主线跌出 | 数据缺失/预期缺失 | wait / observe_only / no_new_position | 原样引用冻结集合 | {blocked_by} |

**动作交集**：只取 `allowed_actions` ∩（资格 / `position_pct` / 隔夜硬风险 / auction veto / `data_quality` / 账户闸门）。

- `position_pct=0` → 不建议新建仓，不是清仓；`null` → 无法给出，只观察。
- `expectations_unavailable`（含 `role=null` 诊断标的）→ 只观察。
- auction `is_weak` / `reject_reason` → veto：不新建，写明原因。
- `action_ceiling.status` 只有 `wait` / `observe_only` / `no_new_position`；09:30 前不宣布 confirmed。

**待验证/证伪**：…

## 6. 组合与执行约束

| 事实 | 工作假设 | 行动 |
|---|---|---|
| 新开仓上限来源：逐只 position_pct；组合 sum_pct/warning；同主线计数 | 单票仓位独立，不归一 | 只列观察 vs 允许进入开盘验证；不把全部候选写成待命买入 |
| 执行：limit_up_type/open_num/prev_close/official_open/gap_pct；一字板/T+1/滑点未知 | 不可成交与滑点不能补造 | 不可成交 → 不新建；滑点未知写未知 |
| 已有持仓：account.status=unknown | 持仓退出不属于「取消新建」 | 退出走现有 sell 规则 |

**待验证/证伪**：…

## 7. 开盘后验证时间表

| 时点 | 观察 | 证伪 / 行动 |
|---|---|---|
| 09:25 | 正式开盘价、竞价末点、转换、veto、data_quality | 数据不足 → unknown / observe_only；不得倒填 |
| 09:30–10:00 | 分时量价、收复/跌破、板块共振、可成交性 | 未达 confirmed 条件 → weakened / unknown；不提前宣布 confirmed |
| 之后 | 封板/炸板/破昨收/退出规则 | 持仓退出走现有 sell 规则 |
| 盘后 | 计划 vs 实际 vs 执行 | 区分市场/模式/执行/数据问题；系统只在盘后计算反馈 |

**待验证/证伪**：…

## 8. 无法交易与盘后归因

| 事实 | 工作假设 | 行动 |
|---|---|---|
| 无合格候选 / 全部只观察 / 正确回避 | 「不交易」是有效结果 | 不为填满模板而强行找标的 |
| 漏报/误报/数据缺失/新闻证伪 | 单日收益不能倒改盘前事实 | 记录归因，留待盘后复核 |

**待验证/证伪**：…

## 附录：外部证据清单

| claim | source_name | source_level | url | event_time | publish_time | effective_time | first_seen | verification_status | 用于段落 |
|---|---|---|---|---|---|---|---|---|---|

## 声明

原文引用 `disclaimer`；阈值为待验证基线，不构成投资建议，不承诺收益。
