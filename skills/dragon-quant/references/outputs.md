# outputs — 输出解读

**何时读我**：需要向用户解释人读输出、`--json` 顶层字段、错误 JSON 或降级字段时。

## 通用表达顺序

1. **结论**：有没有信号 / 是否建议执行 / 是否降级。
2. **关键字段**：交易日、池来源、排序模式、数据质量、否决或回退原因。
3. **风险**：降级、未验证、停牌、兜底、版本漂移。
4. **下一步**：需要确认的写操作，或需要修复的前置条件。

不倾倒完整 JSON，不把错误当空数据，不把“未验证”说成“已验证”。

## prep

**看到什么**：默认输出四行市场摘要（是否建议买入、建议仓位、建议等级、理由）+ 空行 + 逐只标的清单 + 空行 + **复盘摘要**（市场状态与系数、当日主线、详细池档位分布、逐只 `review_score` / `eligibility` / 单票建议仓位与原因、数据降级）。

**怎么解释**：

- 前四行与逐只清单是既有口径，逐字不变；后追加的复盘摘要才是新的复盘分视图。
- 仓位与等级来自盘后复盘快照，**仅供人工参考，不参与下单**；真正下单看 `buy`。
- 清单里 `strong` 是强烈建议、`light` 是轻仓博弈、`ignore` 是忽略（后面通常跟一句原因）、`unrated` 是未评级；`verdict` 只用于展示，不自动过滤回测候选池。
- **语义防混**：旧 `sentiment.position_pct` 是「市场参考仓位」（0–1 小数）；`review_candidates[].position_pct` 是「单票建议仓位」（占总资产百分比）。二者同名不同义，解释时必须写全称，禁止简写成「仓位」。
- 单票建议仓位是三态：`null` 表示数据或置信不足、`0` 表示明确不参与、`>0` 表示可准备建仓；`reason_code` 解释 `0` / `null` 的原因，`gate_trace` 是闸门顺序留痕。**0 只表示不建议新建仓，不表示应清仓**（持仓处置仍由 `sell` 规则决定）。
- 单票仓位彼此独立，**不可相加为可执行组合**；`review.portfolio.warning="sum_exceeds_100"` 时要在报告首段提示，且不给出「优先买哪只」的结论。
- 「无参与复盘标的」不等于市场没有涨停，也可能是数据降级导致候选为空；要看理由中的降级信息。

**`--json` 顶层**：`trade_date`、`version`、`sentiment`、`main_boards`、`macro`、`candidates`、`dragon_source`、`review`、`review_candidates`、`strategy_context`；存在 T-1 计划时另有 `feedback`、`plan_review`。

- `sentiment`：情绪与市场参考仓位建议、涨停/跌停/炸板等计数、`degraded`、`factor_errors`。
- `main_boards`：当日主线板块。
- `macro`：外围与美债/黄金；看 `available`、`session`、逐品种 `caliber` 与 `degraded`。
- `candidates[]`：`code`、`name`、`composite_score`、`rank`、`verdict`、`verdict_label`、`dimensions`、`ignore_reason`、`facts`。
- `review`：配置版本/哈希、市场状态与分项、主线、锚、组合告警与 `degraded`。
- `review_candidates[]`：角色、六维 `dimensions` / `contributions` / `missing_dims`、`review_score` / `review_grade` / `confidence`、`eligibility`、三态 `position_pct`、`reason_code`、`gate_trace`、`legacy` 只读证据。
- `strategy_context`：`context_version="sc_v1"` 的 Agent 只读视图；`positions[]` 收录**全部**详细池标的（含 `position=0/null` 与原因），`market` / `mainlines` / `portfolio` / `data_quality` / `enums` / `disclaimer` 均来自 `review` 投影，不产生新数值。
- `feedback`：T+1 盘后反馈，仅在存在 T-1 计划时出现。`items[].state` 四态 `confirmed` / `weakened` / `invalidated` / `unknown`；`unknown` 必须带稳定 `unknown_reason`（`auction_backfill_failed` 表示竞价补采失败，不等于看空）；`checkpoint` 为 `auction+close` 或 `close_only`。反馈是评估证据，不是买卖指令。
- `plan_review`：计划 vs 实际执行的复盘。`planned_action` / `planned_position` / `feedback_state` / `actual_entry_price` / `actual_position` / `outcome_pct`；`actual_*` 为 `null` 表示未成交或执行数据缺失（`execution_known=false`），**不得编造成交价/仓位**。`attribution` 只做归因计数，不据此改写策略。
- `dragon_source`：五日窗口血缘；`window_days=0` 表示不可用、`1` 表示旧单日口径、`5` 表示当前窗口。

### 明日操盘策略（`strategy_context`）

**何时用**：用户问「明天买什么 / 明日操盘策略 / 复盘策略 / 这些票明天怎么做」。输入只用 `prep --json` 的 `strategy_context`（`context_version="sc_v1"`），不混入其它命令的推测。固定按下面六段、顺序不变。

**1. 复盘日与数据质量**

- 写 `trade_date`、`frozen_at`、`context_version`；报告须写明「基于 <trade_date> 盘后复盘，用于其下一交易日」，复盘日不是目标交易日时不得说成当日结论。
- `data_quality.degraded[]` / `missing[]` / `confidence{min,max,avg}` 逐项如实转述；`missing` 是分数缺失的代码，不是「没风险」。
- `confidence` 汇总为 null 时写「置信度不可用」，不要补默认值。

**2. 市场情绪锚与风险锚**

- `market.state` 按 `enums.state` 原值引用并给中文：strong 强 / neutral 中性 / weak 弱 / ice 冰点 / unknown 未知；`unknown` 不得写成「一般」或「无风险」。
- 写 `market.score`、`market.coeff`；`market.evidence[]` 原文逐条引用（情绪锚）。
- 风险锚只取 `market.anchors` 的 `broken_rate`、`height_d3_change`、`limit_down_cnt`；`height_d3_change=null` 写「三日高度变化未知」，不要当 0。
- `market.unknown_reason` 非空时说明未知原因。

**3. 主线板块、主线龙头与角色**

- 按 `mainlines[]` 顺序写板块名、`state`（原样引用，不要套用 `enums.state`）、`mainline_score`、`evidence[]`；`unknown_reason` 非空时说明。
- `role_map` 角色键对应中文：`leader` 龙头 / `vanguard` 先锋 / `mainstay` 中军 / `capacity` 容量核心 / `follower` 跟风；值为代码，用 `positions[]` 查名称。
- `role_map` 为空就写「该主线未识别出角色」；不得凭涨幅或分数自行指派角色，也不得把 `role=null` 的标的说成跟风。
- 可用 `positions[].mainline`（板块、排名、状态）补充该股所属主线。

**4. 全部详细标的（`positions[]`）**

- **必须逐只覆盖全部 `positions[]`**，包括单票建议仓位 `0` / `null` 的标的；不得只列可买清单。
- 每只写：代码/名称、`role`（`null` → 「未识别角色/诊断标的」）、`review_score`（`null` → 「分数缺失」并给 `missing_dims`）、`review_grade`、`confidence`、`eligibility`（`eligible` / `not_qualified` / `unknown`）。
- 单票建议仓位三态：`>0`「单票建议仓位 X%」；`0`「单票建议仓位 0，不建议新建仓」；`null`「单票建议仓位无法给出（数据或置信不足）」。**`position_pct=0` 不是清仓**，持仓处置由 `sell` 规则决定。
- `reason_code` 按 `enums.reason_code` 原值引用：`data_insufficient` 数据不足 / `hard_risk` 硬风险 / `suspended` 停牌 / `illiquid` 流动性不足 / `not_mainline` 非主线 / `score_below_floor` 分数不足 / `grade_no_position` 档位无仓位 / `confidence_insufficient` 置信不足 / `position_below_min_unit` 低于最小单位 / `compute_error` 计算错误。
- `position_basis[]` 可选引用（`factor` / `value` 原值），不自行重算；`warnings[]` 有则转述。
- 不按 `review_score` 排「优先买哪只」，不给出「先买 A 再买 B」的顺序。

**5. 正反馈 / 负反馈与动作（`feedback_plans[]`）**

- `feedback_plans[]` 是**唯一动作依据**；动作只能取该条的 `allowed_actions`，不得自增「加仓 / 追高 / 抄底」等。
- `expected` 有值时逐项原文引用冻结预期：`gap_low`、`gap_high`、`ratio_low`、`seal_by`；不得改写成自己的阈值，也不得补数字。
- 按 `conditions[]` 说明四态：
  - **正反馈 `confirmed`**：开盘缺口与竞价量比达到 `expected` 给出的 `gap_low` / `ratio_low`，且在 `seal_by` 前封板或收盘强势 → 动作 `execute_plan` / `execute_partial`。
  - **转弱 `weakened`**：缺口或量比未达标但尚未触发取消条件 → 动作 `downgrade_half` / `wait_pullback`（降档半仓，或只等回踩）。
  - **负反馈 `invalidated`**：深度低开、收盘跌破前收、炸板次数达到条件、或主线跌出前列 → 动作 `cancel_entry` / `exit_via_sell_rules`（取消建仓；已有持仓交给 `sell` 规则）。
  - **未知 `unknown`**：竞价或 T+1 行情缺失、`expected` 缺失 → 动作只有 `observe_only`，**不新建仓**；必须说明是「数据不足」，不是看空。
- 未到判定时点（竞价前 / 收盘前）写「尚未判定（wait），不新建仓」，不得预判为 `confirmed`。
- `expected=null` 且 `unknown_reason="expectations_unavailable"` 时，只能「观察，不新建仓」。
- 判定只能引用命令输出里的实际数值；缺数据就落到 `unknown`，不得用估算或记忆中的行情。

**6. 组合提示与声明**

- `portfolio.sum_pct` 是各单票建议仓位的机械和，**不是可执行组合，也不可相加当成一次买入**；单票仓位彼此独立，不做归一、不做风险平价或相关性处理。
- `portfolio.warning="sum_exceeds_100"` 时在报告开头提示「合计超过 100%，不能同时按此建仓」，仍不给优先级。
- 原文引用 `disclaimer`；明确「阈值为待验证基线、不构成投资建议、不承诺收益」。

**回退**：`strategy_context` 缺失或 `context_version` 不认识时，只输出旧摘要（四行 + 逐只清单）并说明「没有结构化策略上下文，无法给出条件化明日策略」；不得拼凑阈值、仓位或买卖动作。修复路径见 `references/playbooks.md`。

**禁止**：把评分权重/阈值抄进叙述；把 `sentiment.position_pct`（市场参考仓位）当单票仓位；把 `0` 说成清仓；把 `unknown` 说成中性；做组合加总归一或推荐优先买入顺序。

## auction

**看到什么**：人读摘要按排名逐只列出等级、强度、方向，并附否决/未验证标记；汇总行给强/中/弱/其他计数。

**`--json` 顶层**：`ok`、`mode`、`trade_date`、`source`、`pool_source`、`fallback_reason`、`candidate_codes`、`saved`、`empty`、`failed`、`quote`、`quote_missing`、`scored_count`、`snapshots`、`snapshot_saved_count`。

- `pool_source=prep`：候选来自严格 T-1 的复盘推荐；`pool_source=legacy` 表示回退到最近若干交易日的龙头并集。
- `fallback_reason`：`no_snapshot` / `snapshot_read_error` / `calendar` / `empty_snapshot` / `no_eligible` / `all_ignored` 等。回退时这批候选不是今天的盘前重点，解读要降权。
- `unverified=true`：口径尚未实测验证，`strength`/`level`/`direction` 只能当诊断值，不能当决策依据；此时 `rank` 通常为空。
- `is_weak=true`：否决闸门触发，不是“低分档位”；`reject_reason` 给原因。`reject_reason` 为“竞价明细缺失”时是数据不足，不等于确认看空。
- `empty` 是“没有盘口”，`failed` 是“采集失败”，都不是“弱”。`ok=true` 也不等于有数据。

## buy

**看到什么**：`action`、`buys[]`、`details[]`，首行还会显示建议仓位（仅参考）。

**`--json` 关键字段**：

- `details[].checks`：四类买点诊断，每项含 `rule`、`label`、`window`、`status`、`selected`、`reason`。
- `status` 枚举：`passed` / `failed` / `pending` / `window_closed` / `unavailable` / `skipped`。`pending` 是还没到窗口，`window_closed` 是窗口已过，`unavailable` 是数据不足，`skipped` 是前置条件不满足；不要把后三者说成“买点失败”。
- `ranking_mode`：`auction` / `auction_unverified` / `prep_only` / `legacy`，表示择优依据。
- `fallback_reason`：候选池回退原因。
- `data_quality`：`strict_5min` / `mixed` / `daily_fallback`。
- `forced`、`vetoed_signal`、`buy_eval_skipped`：强制模式、被否决但本可买入的信号、买点未评估的原因。

## sell

**看到什么**：人读输出，有卖出则逐条列出代码/名称/成交价/持有天数/原因，`partial` 表示减半仓；没有卖出则提示持仓暂无卖出信号。

**怎么解释**：

- `sell` 没有 JSON；不要臆造字段。
- 当天买入的仓位受 T+1 限制，不会出现在卖出建议里。
- `forced` 只表示强制回放，仍不会用收盘价伪造成交；日K兜底日的保守近似要说明。
- 没有卖出信号不等于没有持仓；必要时提示用户查看持仓来源。

## review-account

**看到什么**：终端人读摘要；完整结构化结果在 Web UI/API，CLI 不输出 JSON。

**怎么解释**：

- `data_quality` 三态：`strict_5min`（严格盘中撮合）、`mixed`（混合）、`daily_fallback`（日K兜底）。
- 同时看 `fallback_days`、`suspended_days`、`unavailable_days`、`data_degraded`。
- 兜底成交价是保守近似，不等于真实可成交价；停牌标的冻结，不评估买卖。

## dragons / scan --date

**看到什么**：`dragons` 输出 `trade_date`、`count`、`dragons[]`；`scan --date` 输出历史完整结果或错误 JSON。

**怎么解释**：

- 要“某天有哪些真龙”，优先 `dragons`；要全部候选与否决原因，用 `scan --date`。
- `scan --date` 只接受 `YYYYMMDD`；格式错误只写 stderr 但仍以 0 退出，不能只看退出码。
- 错误 JSON 中 `available_top_n` 表示该日实际可用的 top 口径，按提示调整 `--top` 重试；“无记录”和“top 不匹配”要分开说。

## assets

**看到什么**：本次推送/拉取/校验的摘要与失败列表。

- `status`：缺失、不一致、drift、体积；`dates_drift` 只是本地行数更多的弱信号，退出码仍为 0。
- `verify`：默认校验 manifest，`--deep` 才下载内容；`mismatched[]` 才是定性失败。
- `push`：`pushed`/`skipped`/`blobs`/`manifests`；`skipped` 不是失败；`--dry-run` 的计数为 0 是正常现象，要看 `summary.dates[]`。
- `pull`：默认只输出计划；`--apply` 才写。主库 `--force` 禁用，恢复用 `--target-dir`。
- 退出码：0 成功/无差异/仅 drift；1 部分失败/冲突/收缩被拒；2 配置、网络、manifest 不可读或参数错误。

## factor

**看到什么**：`query` 的表格或 JSON，`status` 的构建状态，`eval` 的指标报告。

- `query` 输出会带 `resolved_versions`；同名多版本不会混成多行。
- `eval` 的 `insufficient_data` 是有效结论（exit 0），不是失败；**历史不足不得说成「因子有效」**。
- 因子缺失表示“无行”，不等于 0 或中性分。
- `build` 报告里的 `warnings`、`unverified_rows`、`missing_source` 要如实转述。
- `prep_review_*`（复盘分因子族）默认关、不参与选股模型排序（因子排序开关仍只读五维/vpa）；`prep_feedback_state` 的 `available_at` 属 close 阶段，不要当成盘中实时信号。影子/评估结论只在跑满影子期后讨论，当前一律按 `insufficient_data` 处理。

## storage

**看到什么**：`status` 显示数据根目录、数据库体积、缓存/结果/日志占用；`size` 显示目录占用；`clear` 显示各类清理数量。

- `storage clear` 不删除 `.db` 文件，但会删缓存、结果、日志以及日志表行。
- 日志清理计数是“文件数 + 日志表行数”，不要当纯文件数。
- 删除后数据库文件体积可能不变（未 checkpoint/vacuum），不能用“文件大小没变”推断“没删掉”。
