---
name: dragon-quant
description: Use when the user asks about A-share 个股行情, 真龙/龙头扫榜 scan/dragons, 盘后复盘 prep, 集合竞价 auction, 盘前预案 premarket, 龙头回测 review, 账户回测 review-account, 因子入库与读回 factor build/query, 扫描诊断/识别质量归因 research scan-quality, 账户策略优化/消融实验 research experiments, or 历史证据读回 research account/signals/snapshots with the dragon-quant CLI. Do not use for generic stock picking or investment advice.
license: MIT
compatibility: Requires dragon-quant CLI >=0.9.0 (Python >=3.10). Node >=22.20 is only needed for installation.
metadata:
  skill-version: "1.0.1"
  min-cli-version: "0.9.0"
---

# dragon-quant 交易日助手

本 skill 是 `dragon-quant` CLI 的薄路由层（Facade）：只对交易日助手场景开放一组稳定能力，把中文/英文意图映射到精确命令，做前置自检，解释输出，分流异常，并在写操作前设门禁。它不承载业务规则，也不复制任何评分权重、策略参数或表结构。

## 权威顺序

**消费项目（npm 安装，通常没有本仓库文档）**：

1. 运行时 `-h` / 叶子能力探测 / 命令 JSON 输出内的字段说明，是能力的唯一契约。
2. 本 skill 的 `references/*`：路由、输出解释、异常分流与写操作门禁。
3. 不假设消费项目存在 `AGENTS.md` / `docs/agent/*` / `STRATEGY.md`，也不把仓库开发流程
   （阶段审批、提交规范）套到普通交易日助手请求上。

**仓库维护模式（当前目录即 dragon-quant 仓库）可额外读取**：

1. `AGENTS.md`（硬约束/项目路由）+ 父级 `AGENTS.md` §4（工作流）+ `docs/agent/*.md`。
2. `dragon_quant/review_account/strategy/STRATEGY.md`：策略与规则数值的权威。

冲突时以更高权威为准；本 skill 不复制权重/参数/表结构，不提供投资建议，不承诺收益，
不用于非 dragon-quant 仓库的通用选股。

## 执行前检查

按顺序短路，失败即停并说明修复方式；不要跳过检查直接拼命令。

1. **CLI 是否存在、版本是否够**：`dragon-quant -v`。项目内优先 `.venv/bin/python -m dragon_quant -v`。低于 `min-cli-version` 时停止，给出升级/安装指引；不要自动安装 Python 包。
2. **叶子能力探测（最低版本之外）**：对本次要用的**叶子**命令取 help（如 `data quote -h`、`factor build -h`）；关键 flag/字段缺失时降级为只读解释，不猜测旧版行为，也不取父级 `-h` 把未开放子命令带进上下文。只使用白名单列出的参数。
3. **能力是否在白名单**：只有 `references/commands.md` 的十类能力可以执行。不在白名单时按「未开放能力」分流，不解释内部命令、不列替代命令名。
4. **时点门禁**：统一按 Asia/Shanghai 与交易日判断。`scan` 当日 15:00 前不执行；`prep` 当日 16:40 前不执行；`auction` 当日 09:25 前不执行。时段不满足时说明等待条件，不用 `--force` 绕过。
5. **数据源**：个股 K 线/分时雪球链路为空或 403 时，改用 `--source tencent` 重试一次；仍失败则如实说明数据不可得。
6. **授权范围（持续有效）**：授权按日期/动作/参数给出的明确范围（如「今天允许跑 prep 和因子 build」）
   在本会话内持续有效，不因「上一轮不算」反复确认；只有用户撤销、日期/动作/参数越界或出现新的
   高风险副作用时才补一次确认。T0 只读可直接执行；T1 写操作仍受动作上限约束。
7. **执行后校验（按输出类型分流）**：`json` 才 `JSON.parse` 并检查 `schema_version` / `trade_date`
   / `scan_id` / `run_id`；`json-lines` 逐行解析、空行只表示该行无数据；`human` 不强行 JSON 化，按
   数值与字段核对；`url` 只核对启动日志里的真实地址与进程存活。空行/失败字符串不等于成功。

## 能力白名单（十类）

只有下面十类能力可以执行，详细命令与参数见 `references/commands.md`：

1. **个股行情**：实时行情、日 K、分时、批量行情。
2. **扫描与真龙**：当日扫描（默认 `--top 5`）→ 用**本次扫描记录**核验（`scan --date D --top 5`
   读回同一 `scan_id` 与原始候选）；`dragons --date D --true-only` 只用来另报「新增回测池数量」——
   它是 5 日去重后的回测池，不是当次扫描的原始名单。
3. **盘后复盘**：`prep`；默认一屏简报，完整九段模板按用户请求加载。
4. **集合竞价**：`auction`；默认一屏简报，完整七段模板按用户请求加载。
5. **盘前预案**：`premarket`（T0 只读）+ 外部证据 + 盘前作战卡八段模板；09:25 后缺失竞价时**主动建议**运行 `auction`（T1，需用户授权该动作），完成后重跑 `premarket`。
6. **龙头回测**：先回测，再用 `dashboard` 打开面板。
7. **账户回测**：先引导区间/资金，再回测，再用 `dashboard` 打开账户页（URL `/account`）。
8. **因子入库**：仅三组内置因子的 `factor build --only` 与代表因子 `factor query` 读回。
9. **可视化面板**：`dashboard`（T0 长驻，**默认只读、不生成 BS 派生数据**；显式 `--bs-autogen`/`DQ_BS_AUTOGEN=1` 才开启，此时按 T1 确认）；缺 `[web]` 时只给手动安装指令，禁止 `--yes` 自动安装。
10. **研究读回与对照实验**：`research datasets/schema/query/scan-quality/account/signals/snapshots/strategy` 与 `research experiments list/show` 为 T0 只读；`research experiments run` 是 T1，必须 `--run-id N --confirm`。用于扫描诊断、识别质量归因、历史证据读回与策略消融；不构造 provider、不自动推广结论。详细问题→覆盖→样本→基线→实验流程见 `references/research.md`，数据集导航见 `references/datasets.md`。

单能力请求只执行用户点名的那一个能力，不隐式附带其他写操作；只有用户明确说“每日全流程 / 每日量化入库 / 今天全套”时才按每日复合 workflow 执行。详见 `references/operations.md`。

## 两个目标 workflow（识别质量 / 账户策略质量）

用户问「识别质量是否改善」「账户策略是否改善」时，先给结论口径，再给证据；不要用单点胜率
拼成因果结论。

- **识别质量是否改善**：需要同一期间、同一 `source`、同一评分/阈值版本的 `review` 机会样本
  （成熟且有效的严格口径）与原始扫描候选对照（`research scan-quality`）；说明分子/分母、
  成熟度、排除原因与基准差。事后机会只能称「机会质量/错失机会描述」。
- **账户策略质量是否改善**：需要同一区间、资金、成本、上游候选母集与策略版本的
  `review-account --json` 批次（取得真实 `run_id`）；再用 `research experiments run --run-id N --confirm`
  显式生成消融结果，比较权益/回撤/交易数/盈利率与基线差；样本不足、版本不一致或
  `status=insufficient_evidence` 时明确「不可比/证据不足」，`no_change` 不能当改进。
- 两者都不能由单次结果推广默认策略；不自动改权重、阈值或参数。

## 胜率/成效解释契约（回答任何胜率前必须带）

指标类型（机会质量 / 方向命中 / 整笔交易盈利率 / 本段权益贡献）、样本范围与单位、
分子/分母、成熟度、时间窗口、费用与成交假设、数据质量、评分/策略/快照版本、比较条件。
缺失写「未知/不可用」，不填 0；不同版本默认不可比；BS 买点机会胜率不等于账户盈利率。
prep 不推荐不是看跌；auction 中性不是看涨；数据缺失不是空仓。

## 交易日时点

- `scan`：助手产品规则为**同一交易日 15:00 后**才执行实时扫描（比 CLI 的 09:00–15:00 更严格；
  历史 `scan --date` 查询不受限）；说明 15:00 后更准确；不提供 `--force`。
- `prep`：当日必须 ≥16:40；历史复盘用 `--date`，不受当日门禁。
- `auction`：当日 ≥09:25 才有集合竞价终态。09:30 后仍读取/采集 09:25 终态并正常生成竞价报告，标注实际运行时点与缓存状态；只有额外行情观察才归“开盘后验证”，不得倒填为竞价事实。
- 时点判断与 CLI 一致；CLI 报错时以报错为准，不伪造交易日。

## 报告交付

- 报告**默认在对话中交付 Markdown**；只有用户明确要求保存时才写用户指定路径，写前确认。
- **默认一屏简报**：结果、风险、缺失、下一验证时点；用户说「完整报告 / 展开模板」时再按对应
  reference 输出完整段数，不把 6 段/7 段/8 段/9 段同时强塞给简单问答。
- 固定三栏语义：**事实**（CLI 字段/数值/来源）、**解读**（允许的推断，必须标注）、**待验证条件**（下一时点如何证伪/观察）。
- 缺失字段写“缺失/不可用/不在本次数据范围”，不补 0、不写“弱/看空/中性”。
- prep 与 auction 的报告骨架见 `references/reports.md`；字段语义见 `references/outputs.md`。

## 明日操盘策略（优先 premarket，T0）

用户问「明天买什么 / 明日操盘策略 / 复盘策略」时，先运行只读 `dragon-quant premarket --json`（T0，不写库、不触网）作为盘前上下文；**不得**为了拿上下文而运行会写库的 `prep`。只有已获得当日 `prep --json` 输出时才复用其 `strategy_context`；默认一屏简报，完整六段模板按需输出：

1. **复盘日与数据质量**：`trade_date`、`frozen_at`、`context_version`、`data_quality`。
2. **市场情绪锚与风险锚**：`market.state/score/coeff/evidence` 与 `market.anchors`。
3. **主线板块、主线龙头与角色**：`mainlines[]` 与 `role_map`。
4. **全部详细标的**：逐只 `positions[]`，含 `position_pct` 三态与 `reason_code`。
5. **正/负反馈与动作**：逐只 `feedback_plans[]` 的冻结预期与四态条件；`allowed_actions` 是**唯一动作依据**，`unknown` 只能 `observe_only`。
6. **组合提示与声明**：`portfolio` 不可相加，原文引用 `disclaimer`。

只引用已有数值与枚举，不新增阈值、不补行情、不按分数排优先顺序。字段细节与四态映射见 `references/outputs.md`；盘前完整流程见下节。

## 盘前预案（作战卡八段模板 + 外部证据）

1. 运行 `dragon-quant premarket [--date D] --json`（T0）。它只读 T-1 `prep` 快照与 T 日 `auction` 快照，缺数据、损坏、未到 09:25、缓存状态未知都只做结构化降级并 exit 0。
2. 先看 `data_quality` / `degraded` / `prep.strictness` / `proven_gap` / `auction.state`：`plan_usable=false` 或 `proven_gap=true` 时只观察，不新建。
3. **09:25 前**：只运行 `premarket`（T0），输出无竞价版；结尾必须主动建议「09:25 后先运行 `auction` 采集竞价终态，再重新生成盘前预案」。09:25 前不运行 `auction`，**不得**声称已有竞价事实。
4. **09:25 后**：先运行 `premarket`（T0）检查 `auction.state`：
   - `ready / partial` → 直接生成有竞价版；
   - 缺失 / 不可用 → **主动建议**运行 `auction`（T1，会写 `assets.db`），给出确认话术；只有用户明确授权该动作后才执行；成功后重跑 `premarket`，确认竞价状态可用，再生成最终预案；
   - 用户拒绝，或用户自行执行 `! dragon-quant auction` → 输出无竞价版，并写明「竞价缺失」。
5. **禁止**：自动运行 `auction`、自动加 `--force`、用 `--force` 绕过 09:25 门禁。`premarket` 仍是 T0，不写库；`auction` 是唯一写入步骤。
6. 外部证据按 `references/evidence.md` 采集：官方/原文优先，媒体只作线索，社交只作待核实；每条保存来源级别与四个时点。
7. 按 `references/premarket.md` 的作战卡八段模板输出：第一屏是「一页结论」（动作上限 / 风险预算 / 必验问题 / 不做什么），随后是作战卡与逐时点检查表；事实 / 工作假设 / 行动分栏；行动只取 `allowed_actions` 与资格、仓位、veto、`data_quality`、账户闸门的交集，不发明买卖点。

## 异常分流（先分类，再回复）

1. **能力不支持执行**（命令/字段不存在、白名单外）：用固定话术，不解释内部命令、不列替代命令名。
2. **可只读解释**（已有 CLI/面板输出可读，只是未开放写操作）：基于已有输出解释，明确
   「只读解释，不执行写入」，不阻断对话。
3. **数据暂缺**（未到采集时点、区域未覆盖、本地行情缺失）：说明缺什么、何时可补齐，不填 0、
   不把缺失说成空仓/看空。
4. **真实故障**（CLI 报错、输出损坏、接口 5xx）：保留原始错误摘要，给出最小复现信息，可按
   issue 模板反馈；不把所有体验问题都回固定 issue 话术。

第 1 类统一回复（逐字）：

> 当前功能还不稳定，暂未对 Agent 开放。你可以到 https://github.com/gitBingxu/dragon-quant/issues 反馈需求或问题。

需要反馈时，按 `references/playbooks.md` 的 issue 最小信息模板收集信息；不承诺排期，不模拟维护者口吻。

## Reference 索引

| 文件 | 何时读 |
|---|---|
| `references/commands.md` | 需要确定白名单命令、关键参数、写类或输出模式时 |
| `references/outputs.md` | 需要解读字段语义、降级字段或 `strategy_context` 六段时 |
| `references/reports.md` | 生成 prep / auction 报告时 |
| `references/premarket.md` | 生成盘前预案（作战卡八段、一页结论、逐时点检查表）时 |
| `references/evidence.md` | 盘前采集外部政策/公告/外围行情与信源分级时 |
| `references/playbooks.md` | 遇到行情失败、门禁、空数据、因子缺失、UI 启动失败时 |
| `references/research.md` | 扫描诊断、识别质量归因、账户策略消融与研究读回流程时 |
| `references/datasets.md` | 需要选择 research 数据集、过滤器或解释 envelope 时 |
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
- 不要复制或推断评分权重、策略参数、表结构；需要时引用 `AGENTS.md`、对应 `docs/agent/*.md` 或 `STRATEGY.md`；运行时字段以 `research schema` / JSON 输出为契约。
- 不要把 `research experiments run` 说成只读，或在没有真实 `run_id` 时猜「最新批次就是本次」。
- 不要自动安装/升级 Python 包，也不要执行 `npm publish`、`git commit`、`git push` 或 PR；这些由用户控制。
