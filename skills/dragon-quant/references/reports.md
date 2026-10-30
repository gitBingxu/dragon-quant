# reports — prep / auction 报告模板

**何时读我**：需要为盘后复盘（prep）或集合竞价（auction）生成结构化报告时。

**通用规则**：报告**默认对话交付** Markdown；只有用户明确要求保存时才写入**指定路径**，写前**确认**。每段固定三栏语义：**事实**（CLI 字段/数值/来源）、**解读**（允许的推断，必须标注）、**待验证**条件（下一时点如何证伪/观察）。没有字段支撑的内容写“不在本次数据范围”，不补造、不补 0。

## prep 复盘报告

```markdown
# 复盘报告｜{trade_date}

- 目标交易日：用于其下一交易日（不自行推算具体日期，以交易时点与 CLI 输出为准）
- 数据质量：{strategy_context.data_quality.confidence}；降级：{degraded}；缺失代码：{missing}
- 血缘：context_version={context_version}；dragon_source.window_days={n}；snapshot={frozen_at}
- 声明：本报告由 dragon-quant CLI 输出整理，阈值为待验证基线，不构成投资建议，不承诺收益。

## 1. 复盘日与数据质量
| 事实 | 解读 | 待验证条件 |
|---|---|---|
| strategy_context.trade_date / frozen_at / context_version | 复盘日不是目标交易日时，不得说成当日结论 | 目标交易日盘后复核 |

## 2. 市场环境与流动性
| 事实 | 解读 | 待验证条件 |
|---|---|---|
| macro.available / session / instruments[].caliber / degraded | 外围与美债/黄金只作环境背景 | 次日盘前/盘后更新 |

## 3. 情绪与风格
| 事实 | 解读 | 待验证条件 |
|---|---|---|
| review.market.state / score / coeff / evidence / unknown_reason | 情绪回答“能不能做/容错高低” | 次日竞价与收盘验证 |

## 4. 主线题材
| 事实 | 解读 | 待验证条件 |
|---|---|---|
| main_boards；review.mainlines[].state / mainline_score / evidence | 主线回答“做哪里” | 次日板块强度是否延续 |

## 5. 龙头与核心角色
| 事实 | 解读 | 待验证条件 |
|---|---|---|
| positions[] 全量：role / review_score / review_grade / confidence / eligibility / position_pct / reason_code / gate_trace | 龙头是表达与观察锚，不是买入指令 | 次日竞价缺口/量比/封板 |

## 6. 当日计划与实际对照（仅存在 T-1 计划时）
| 事实 | 解读 | 待验证条件 |
|---|---|---|
| feedback.items[].state / unknown_reason / checkpoint；plan_review.planned_* / actual_* / outcome_pct / attribution | 反馈是评估证据，不是买卖指令 | 实际执行数据缺失时保持 null |

## 7. 明日条件计划
| 事实 | 解读 | 待验证条件 |
|---|---|---|
| strategy_context.feedback_plans[].expected（gap_low / gap_high / ratio_low / seal_by）与 conditions[] 四态 | 只引用冻结预期，不新增阈值 | 正/负/未知条件逐条列出 |

## 8. 待验证条件与风险
- 数据缺口：{degraded / missing / suspended_codes / dragon_source}
- 需要下一时点确认的条件：{条件清单}
- 风险：{降级、回退、快照版本、执行数据缺失}

## 9. 声明
原文引用 disclaimer；不构成投资建议；不承诺收益。
```

**字段来源与缺失回退**

| 段落 | 字段来源 | 缺失 / 降级处理 | 措辞约束 |
|---|---|---|---|
| 1 | `strategy_context.trade_date`、`frozen_at`、`context_version`、`data_quality` | `confidence=null` 写“置信度不可用”；`missing` 是分数缺失代码，不是无风险 | 不补默认值 |
| 2 | `macro.available/session/instruments/degraded`；`sentiment` 计数 | 品种缺失写“不在本次数据范围” | 不把 `previous_session` 说成当日 |
| 3 | `review.market.state/score/coeff/evidence/unknown_reason` | `unknown` 原样写“未知”，不写“中性” | 不套用阈值 |
| 4 | `main_boards`、`review.mainlines[]` | `mainline_unavailable` 写“主线不可用” | 不凭涨幅自行指定主线 |
| 5 | `review_candidates` / `strategy_context.positions[]` | `position_pct=0` 写“不建议新建仓”，**不是清仓**；`null` 写“无法给出” | 逐只覆盖，不排优先买入顺序 |
| 6 | `feedback`、`plan_review` | 无 T-1 计划是合法缺席；`actual_* = null` 写“未成交或数据缺失” | 不编造成交价/仓位 |
| 7 | `strategy_context.feedback_plans[]` | `expectations_unavailable` → 只观察；`unknown` → `observe_only` | `allowed_actions` 是唯一动作依据 |
| 8 | `dragon_source`、`suspended_codes`、`degraded` | 历史缺口不可再生，如实说明 | 不假装补采成功 |
| 9 | `disclaimer` | 缺失时写“声明不可用” | 不省略“不构成投资建议” |

**框架顺序**：市场环境/流动性 → 情绪与风格 → 主线题材 → 龙头/核心角色 → 当日计划与实际对照 → 明日条件计划。情绪回答“能不能做/容错”，主线回答“做哪里”，龙头是表达与观察锚，明日计划必须写正/负/未知条件。

**明确排除**：固定阈值、仓位数字、因果断言、确定性周期、收益背书、主力动机推断。

## auction 竞价报告

```markdown
# 竞价报告｜{trade_date}

- 快照时点：09:25（集合竞价终态）
- 实际运行时点：{run_at}（09:30 后运行仍读取/采集 09:25 终态）
- 数据来源：mode={mode}；source={source}；pool_source={pool_source}；fallback_reason={fallback_reason}
- 缓存：from_cache={from_cache}；validation_status={validation_status}
- 数据质量：{data_quality.status}；原因码：{reason_codes}
- 声明：本报告是候选池竞价快照，不是全市场/全板块结论；不构成投资建议。

## 1. 快照时点与口径
- 09:15–09:20 可撤单；09:20–09:25 不可撤单；09:25 形成开盘价。
- 09:25 前的**匹配量**不是实际成交量；本报告不把它当成交量。
- 09:30 后运行仍读取/采集 09:25 集合竞价终态，正常生成竞价强度报告；报告标注实际运行时点与缓存状态。
- 快照命中时明确“来自缓存，未重新采集”。

## 2. 大盘温度
| 事实 | 解读 | 待验证条件 |
|---|---|---|
| 本次数据不含全市场竞价温度/指数 | 不在本次数据范围 | 开盘后观察指数与涨跌家数 |

## 3. 板块方向
| 事实 | 解读 | 待验证条件 |
|---|---|---|
| 本次数据只覆盖候选池，不含板块竞价数据 | 不在本次数据范围 | 开盘后观察板块联动 |

## 4. 个股竞价结构
| 排名 | 代码/名称 | 等级 | 强度 | 方向 | 关键证据 | 数据状态 |
|---|---|---|---|---|---|---|
| {rank} | {code/name} | {level} | {strength} | {direction} | {evidence_summary} | {data_quality.status} |

## 5. 风险
- `is_weak=true` / `reject_reason`：真实风险否决，不是低分档位。
- `risk_events[]`：盘口估计事件，不是订单级成交。
- `unavailable` / `empty` / `failed`：数据不足或采集失败，不等于看空。
- `unverified=true`：本次评价不可用，分数只能当诊断值。

## 6. 开盘后验证（仅额外行情观察）
本节只用于 09:30 后的额外观察，不是竞价事实，**不得倒填**为 09:25 结论。
| 条件（来自 actions[]） | 观察点 | 证伪条件 |
|---|---|---|
| {action} | {requires / post_open} | {若…则不成立} |

## 7. 待验证与声明
- 候选池血缘：{pool_source / fallback_reason}
- 未覆盖维度：全市场温度、板块方向、实际成交量
- 原文引用 validation_status 与 disclaimer；不构成投资建议。
```

**字段来源与缺失回退**

| 段落 | 字段来源 | 缺失 / 降级处理 | 措辞约束 |
|---|---|---|---|
| Header | `trade_date`、`mode`、`source`、`pool_source`、`fallback_reason`、`from_cache`、`validation_status`；`run_at` 由 Agent 记录 | `from_cache=true` 必须写“缓存”；`pool_source=legacy` 必须写“非今日盘前推荐” | 不伪装实时；09:30 后运行仍标 09:25 终态 |
| 1 | 固定时点语义 + `mode` | 无字段的维度写“不在本次数据范围” | 不把 09:25 前匹配量当成交量 |
| 2 | 无 | 明确“不在本次数据范围” | 不伪造大盘/板块数据 |
| 3 | 无 | 明确“不在本次数据范围” | 不从候选池反推板块 |
| 4 | `snapshots[]` 的 `rank/strength/level/direction/dims/evidence_summary/data_quality` | `rank=null` 写 `rank_reason`；`unavailable` 不写分数 | 不把 `unavailable` 说成弱 |
| 5 | `is_weak`、`reject_reason`、`risk_events` | `empty`/`failed` 分开说明 | 不把数据不足说成看空 |
| 6 | `strategy_context.positions[].actions[]` | 上下文缺失 → 只输出旧摘要并说明 | 仅额外行情观察；不得把 09:30 后观察倒填成竞价事实 |
| 7 | `validation_status`、`disclaimer` | 缺失时写“不可用” | 不承诺收益 |

**明确排除**：确定性筛选公式、主力必拉升、确定性结论、收益背书。

## 通用措辞约束

- 事实与推断分开；推断必须写“解读：”。
- 缺失写“缺失/不可用/不在本次数据范围”，不写 0、不写“弱/看空”。
- 不把 `unknown` 写成“中性”；不把 `position_pct=0` 写成“清仓”。
- 不按分数排“优先买哪只”，不给“先买 A 再买 B”。
- 不复制权重、阈值、策略字段名与数值。
