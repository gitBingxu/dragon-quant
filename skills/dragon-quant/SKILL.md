---
name: dragon-quant
description: Use when the user asks about A-share 个股行情, 真龙/龙头扫榜 scan/dragons, 盘后复盘 prep, 集合竞价 auction, 盘前预案 premarket, 龙头回测 review, 账户回测 review-account, or 因子入库与读回 factor build/query with the dragon-quant CLI. Do not use for generic stock picking or investment advice.
license: MIT
compatibility: Requires dragon-quant CLI >=0.8.0 (Python >=3.10). Node >=22.20 is only needed for installation.
metadata:
  skill-version: "1.3.0"
  min-cli-version: "0.8.0"
---

# dragon-quant 交易日助手

本 skill 是 `dragon-quant` CLI 的薄路由层（Facade）：只对交易日助手场景开放一组稳定能力，把中文/英文意图映射到精确命令，做前置自检，解释输出，分流异常，并在写操作前设门禁。它不承载业务规则，也不复制任何评分权重、策略参数或表结构。

## 权威顺序

1. `AGENTS.md`（硬约束/项目路由）+ 父级 `AGENTS.md` §4（工作流）+ 其索引的 `docs/agent/*.md`（模块细节）：项目规则、命令语义、运维契约的权威。
2. `dragon_quant/review_account/strategy/STRATEGY.md`：策略与规则数值的权威。
3. 本 skill：只做路由、预检、输出翻译、异常分流、写操作门禁。
4. 冲突时以更高权威为准；本 skill 不复制权重/参数/表结构。
5. 本 skill 不提供投资建议，不承诺收益；不用于非 dragon-quant 仓库的通用选股。

## 执行前检查

按顺序短路，失败即停并说明修复方式；不要跳过检查直接拼命令。

1. **CLI 是否存在、版本是否够**：`dragon-quant -v`。项目内优先 `.venv/bin/python -m dragon_quant -v`。低于 `min-cli-version` 时停止，给出升级/安装指引；不要自动安装 Python 包。
2. **能力是否在白名单**：只有 `references/commands.md` 的八类能力可以执行。不在白名单时，直接使用本文件末尾的固定话术，不解释内部命令、不列替代命令名。
3. **命令与参数是否存在**：只对白名单**叶子**命令取 help（如 `data quote -h`、`factor build -h`）；不要取父级 `data -h` / `factor -h`，避免把未开放子命令带进上下文。只使用白名单列出的参数。
4. **时点门禁**：统一按 Asia/Shanghai 与交易日判断。`scan` 当日 15:00 前不执行；`prep` 当日 16:40 前不执行；`auction` 当日 09:25 前不执行。时段不满足时说明等待条件，不用 `--force` 绕过。
5. **数据源**：个股 K 线/分时雪球链路为空或 403 时，改用 `--source tencent` 重试一次；仍失败则如实说明数据不可得。
6. **写确认**：T0 可直接执行；T1 必须本轮明确请求或确认，上一次同意不代表这一次同意。
7. **执行后校验**：输出非空、JSON 可解析、`trade_date` 与请求日期一致；空行/失败字符串不等于成功。

## 能力白名单（八类）

只有下面八类能力可以执行，详细命令与参数见 `references/commands.md`：

1. **个股行情**：实时行情、日 K、分时、批量行情。
2. **扫描与真龙**：当日扫描（默认 `--top 5`）→ `dragons --true-only` 结构化核验；历史日优先读 `dragons`。
3. **盘后复盘**：`prep` + 固定九段报告。
4. **集合竞价**：`auction` + 固定七段报告。
5. **盘前预案**：`premarket`（T0 只读）+ 外部证据 + 固定八段模板。
6. **龙头回测**：先回测，再 `--ui-only` 打开回测面板。
7. **账户回测**：先引导区间/资金，再回测，再 `--ui-only` 打开账户面板。
8. **因子入库**：仅三组内置因子的 `factor build --only` 与代表因子 `factor query` 读回。

单能力请求只执行用户点名的那一个能力，不隐式附带其他写操作；只有用户明确说“每日全流程 / 每日量化入库 / 今天全套”时才按每日复合 workflow 执行。详见 `references/operations.md`。

## 交易日时点

- `scan`：同一交易日 09:00–15:00 不执行实时扫描，说明 15:00 后更准确；不提供 `--force`。
- `prep`：当日必须 ≥16:40；历史复盘用 `--date`，不受当日门禁。
- `auction`：当日 ≥09:25 才有集合竞价终态。09:30 后仍读取/采集 09:25 终态并正常生成竞价报告，标注实际运行时点与缓存状态；只有额外行情观察才归“开盘后验证”，不得倒填为竞价事实。
- 时点判断与 CLI 一致；CLI 报错时以报错为准，不伪造交易日。

## 报告交付

- 报告**默认在对话中交付 Markdown**；只有用户明确要求保存时才写用户指定路径，写前确认。
- 固定三栏语义：**事实**（CLI 字段/数值/来源）、**解读**（允许的推断，必须标注）、**待验证条件**（下一时点如何证伪/观察）。
- 缺失字段写“缺失/不可用/不在本次数据范围”，不补 0、不写“弱/看空/中性”。
- prep 与 auction 的报告骨架见 `references/reports.md`；字段语义见 `references/outputs.md`。

## 明日操盘策略（优先 premarket，T0）

用户问「明天买什么 / 明日操盘策略 / 复盘策略」时，先运行只读 `dragon-quant premarket --json`（T0，不写库、不触网）作为盘前上下文；**不得**为了拿上下文而运行会写库的 `prep`。只有本轮已有当日 `prep --json` 输出时才复用其 `strategy_context`，再用固定六段输出：

1. **复盘日与数据质量**：`trade_date`、`frozen_at`、`context_version`、`data_quality`。
2. **市场情绪锚与风险锚**：`market.state/score/coeff/evidence` 与 `market.anchors`。
3. **主线板块、主线龙头与角色**：`mainlines[]` 与 `role_map`。
4. **全部详细标的**：逐只 `positions[]`，含 `position_pct` 三态与 `reason_code`。
5. **正/负反馈与动作**：逐只 `feedback_plans[]` 的冻结预期与四态条件；`allowed_actions` 是**唯一动作依据**，`unknown` 只能 `observe_only`。
6. **组合提示与声明**：`portfolio` 不可相加，原文引用 `disclaimer`。

只引用已有数值与枚举，不新增阈值、不补行情、不按分数排优先顺序。字段细节与四态映射见 `references/outputs.md`；盘前完整流程见下节。

## 盘前预案（八段模板 + 外部证据）

1. 运行 `dragon-quant premarket [--date D] --json`（T0）。它只读 T-1 `prep` 快照与 T 日 `auction` 快照，缺数据、损坏、未到 09:25、缓存状态未知都只做结构化降级并 exit 0。
2. 先看 `data_quality` / `degraded` / `prep.strictness` / `proven_gap` / `auction.state`：`plan_usable=false` 或 `proven_gap=true` 时只观察，不新建。
3. 09:25 前 `auction.state=not_ready`：先按 T-1 计划 + 外部证据出「无竞价版」；**不得**声称已有竞价事实。09:25 后再重跑 `premarket` 读取终态。`premarket` 不会自动运行 `auction`；只有用户明确要求时才运行会写库的 `auction`。
4. 外部证据按 `references/evidence.md` 采集：官方/原文优先，媒体只作线索，社交只作待核实；每条保存来源级别与四个时点。
5. 按 `references/premarket.md` 的八段模板输出，事实 / 工作假设 / 行动分栏；行动只取 `allowed_actions` 与资格、仓位、veto、`data_quality`、账户闸门的交集，不发明买卖点。

## 未开放能力

任何不在八类白名单内的能力（含用户点名但未开放的功能、缺功能、体验问题、数据异常无法解释），统一回复下面这句话，逐字使用，不解释内部命令、不列替代命令名：

> 当前功能还不稳定，暂未对 Agent 开放。你可以到 https://github.com/gitBingxu/dragon-quant/issues 反馈需求或问题。

需要反馈时，按 `references/playbooks.md` 的 issue 最小信息模板收集信息；不承诺排期，不模拟维护者口吻。

## Reference 索引

| 文件 | 何时读 |
|---|---|
| `references/commands.md` | 需要确定白名单命令、关键参数、写类或输出模式时 |
| `references/outputs.md` | 需要解读字段语义、降级字段或 `strategy_context` 六段时 |
| `references/reports.md` | 生成 prep / auction 报告时 |
| `references/premarket.md` | 生成盘前预案（八段模板、三锚/四态、候选条件表）时 |
| `references/evidence.md` | 盘前采集外部政策/公告/外围行情与信源分级时 |
| `references/playbooks.md` | 遇到行情失败、门禁、空数据、因子缺失、UI 启动失败时 |
| `references/operations.md` | 单能力/每日复合 workflow、UI 生命周期、因子三组名单时 |
| `references/safety.md` | 执行任何 T1 命令前 |

只加载需要的那一份；不要一次把全部 reference 读进来。

## 禁止事项

- 不要伪装执行：没有运行命令就不要输出“已扫描/已复盘/已打开面板/已入库”。
- 不要执行白名单以外的能力，也不要在正文、表格或否定示例里列出它们的命令名。
- 不要在单能力请求里隐式追加写操作；不要默认执行每日复合 workflow。
- 不要用 `--force` 绕过盘中/盘后/竞价时点。
- 不要手工用 SQL 删改业务表。
- 不要回显 Cookie、token 或同步凭据。
- 不要把 `unverified`、`daily_fallback`、`degraded`、`missing_source` 的结果说成确定结论或 0。
- 不要复制或推断评分权重、策略参数、表结构；需要时引用 `AGENTS.md`、对应 `docs/agent/*.md` 或 `STRATEGY.md`。
- 不要自动安装/升级 Python 包，也不要执行 `npm publish`、`git commit`、`git push` 或 PR；这些由用户控制。
