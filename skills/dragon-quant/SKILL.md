---
name: dragon-quant
description: Use when the user asks about A-share dragon scanning, 真龙/龙头扫榜, 盘后复盘 prep, 集合竞价 auction, 回测 review, 买卖信号 buy/sell, 数据资产同步, 黑名单, or dragon-quant CLI usage. Do not use for generic stock picking or investment advice.
license: MIT
compatibility: Requires dragon-quant CLI >=0.7.1 (Python >=3.10). Node >=22.20 is only needed for installation.
metadata:
  skill-version: "0.7.1"
  min-cli-version: "0.7.1"
---

# dragon-quant

本 skill 是 `dragon-quant` CLI 的薄路由层：把中文/英文意图映射到命令，做前置自检，解读输出，分流异常，并在写操作前设门禁。它不承载业务规则，也不复制任何评分权重、策略参数或表结构。

## 权威顺序

1. `AGENTS.md`：项目规则、命令语义、运维契约的权威。
2. `dragon_quant/review_account/strategy/STRATEGY.md`：策略与买卖规则的权威。
3. 本 skill：只做路由、预检、输出翻译、异常分流、写操作门禁。
4. 三者冲突时以更高权威为准；skill 不复制权重/参数/表结构。
5. 本 skill 不提供投资建议，不承诺收益；不用于非 dragon-quant 仓库的通用选股。

## 执行前检查

按顺序短路，失败即停并说明修复方式；不要跳过检查直接拼命令。

1. **CLI 是否存在、版本是否够**：`dragon-quant -v`。项目内优先 `.venv/bin/python -m dragon_quant -v`，避免 PATH 上的旧版本。低于 frontmatter 的 `min-cli-version` 时停止，给出升级/安装命令；不要自动安装 Python 包。
2. **命令与参数是否存在**：不确定时先运行 `dragon-quant <command> -h`。本 skill 只保留关键参数，**参数以 help 为准**。
3. **数据源与凭证**：TDX 是首选链路，失败会自动回退 THS / 雪球 / 腾讯。只有路径确实依赖雪球个股数据时，才检查 `dragon-quant data cookie-status`；不要在只走 TDX 的场景反复提 Cookie。
4. **交易日与时段门禁**：不要自行发明门禁，以 CLI 报错为准。已知硬约束：`prep` 当日运行需 ≥16:40；`auction` 当日数据需 ≥09:25；`buy`/`sell` 实时运行需交易时段，历史回放必须 `--date YYYYMMDD --at HH:MM`；`--force` 不解除交易日与 09:30 前限制。
5. **数据就绪与输出模式**：`dragon-quant storage status` 看本地库；结构化结果优先 `dragons`（`scan` 实时模式没有 JSON，`sell` 没有 JSON）；需要机器可读时先确认该命令是否支持 `--json`。

**只读优先**：能 `dragons` 就不 `scan`，能 `scan --date` 就不重扫，能 `assets status` 就不 `doctor`，能 `pull` 预览就不 `--apply`。任何“先写一下再说”的做法都要先过写操作门禁。

## 意图路由

| 用户意图 | 路由 | 说明 |
|---|---|---|
| 今天有哪些真龙 / 扫一下 / 龙头扫榜 | `dragon-quant scan` | 写库且触网，先确认；结果优先用 `dragons` 读 |
| 历史扫描结果 / 某天的候选 | `dragon-quant scan --date` 或 `dragon-quant dragons` | 只读；`dragons` 更稳 |
| 盘后复盘 / 收盘总结 / 明天买什么 | `dragon-quant prep` | 当日 ≥16:40；写 `assets.db` |
| 竞价怎么样 / 集合竞价 | `dragon-quant auction` | 当日 ≥09:25；默认读 T-1 复盘快照 |
| 能不能买 / 买入信号 / 持仓建议 | `dragon-quant buy` | 写信号表，必须确认 |
| 要不要卖 / 卖出信号 | `dragon-quant sell` | 写信号表，必须确认；T+1 |
| 回测 / 龙头回测 / 看回测页面 | `dragon-quant review` | 回写 review 字段；`--ui-only` 只读 |
| 账户回测 / 模拟交易 | `dragon-quant review-account` | 写账户表；终端无 JSON |
| 数据同步 / 备份 / 恢复 / 推到云端 | `dragon-quant assets` | 先区分只读与写；推送/落库需二次确认 |
| 黑名单 / 过滤板块 | `dragon-quant blacklist` | 查询只读；增删写配置 |
| 存储 / 清理缓存 / 日志 | `dragon-quant storage`、`dragon-quant logs` | 清理是破坏性操作 |
| 因子 / 因子评估 / 财务数据 | `dragon-quant factor` | 构建/同步写库；查询/评估只读 |

详细命令、关键参数、写类与常见误用见 `references/commands.md`。

**路由原则**：

1. 先判断读/写：任何写操作先过“写操作门禁”，再拼命令；不要把写命令包装成“只是查一下”。
2. 先判断时点：`prep`/`auction`/`buy`/`sell` 有硬门禁；时点不对时直接说明等待条件，不要用 `--force` 硬闯。
3. 结构化优先：能读库就不重扫；要机器可读优先 `dragons` 或支持 `--json` 的命令。
4. 参数不确定时先 `-h`；不要凭记忆编造参数或字段。

## 输出翻译

统一按四步表达：

1. **结论**：是否有信号、是否建议执行、是否降级。
2. **关键字段**：交易日、池来源、排序模式、数据质量、否决/回退原因。
3. **风险与降级**：`data_quality`、`fallback_reason`、`unverified`、`degraded`、`suspended_codes`。
4. **下一步**：需要用户确认的写操作，或需要修复的前置条件。

不要原样倾倒 JSON；不要把错误 JSON 当空数据；不要把“未验证”说成“已验证”；不要把“空池/无数据”说成“市场弱”。各命令的字段含义见 `references/outputs.md`。

按命令的解读重点：

- `prep`：默认是市场摘要 + 逐只标的清单；仓位/等级来自复盘快照，仅供人工参考，不参与下单。需要完整结构时用 `--json`。
- `auction`：逐只看强度、方向、等级、是否否决、是否未验证；`unverified` 为真时分数只能当诊断值，不能当决策依据；`pool_source`/`fallback_reason` 说明候选池是不是今天的盘前推荐。
- `buy`：先看有没有买入信号，再看 `ranking_mode`、`fallback_reason`、`data_quality`；四类买点诊断在 `details[].checks`，状态分为通过/失败/待定/窗口已过/不可用/跳过，不要把“跳过”说成“失败”。
- `sell`：按人读输出解释卖出、减半仓或继续持有；`partial` 表示减半不是清仓；当天买入的仓位不会出现在卖出建议里。
- `review-account`：终端没有 JSON；重点看数据质量三态与降级天数，兜底成交价是保守近似，不等于真实成交。
- `dragons` / `scan --date`：结构化结果优先 `dragons`；`scan --date` 的错误 JSON 要区分“无记录”和“top 不匹配”。
- `assets`：`pull` 默认只预览；`status` 的 drift 是弱信号；`verify` 才做定性校验；退出码 0/1/2 含义不同。
- `factor`：`query` 会带解析后的版本；`eval` 的样本不足是有效结论；缺失不等于 0 或中性。
- `storage`：状态/占用只读；清理不删数据库文件，但会删缓存、结果与日志。


## 写操作门禁

- **T0 只读**：不写业务记录、不改远端业务状态。可直接执行；长驻 UI 要说明会阻塞、`--ui-only` 可能初始化/校验本地 schema。
- **T1 写本地状态或远端临时探针**：写 `dragon.db` / `assets.db` / 本地文件 / 凭据，或向远端写可清理的探测对象。执行前必须得到用户显式确认；用户本轮明确点名该操作可视为确认。
- **T2 外发 / 破坏性 / 高风险**：上传业务数据、覆盖主库、删除数据、写凭据、强制覆盖。必须先 dry-run/预览并二次确认，不得自动执行。

`scan`、`prep`、`auction`、`review`、`review-account`、`buy`、`sell`、`factor build`、`factor fin sync`、`vpa`（默认落库）、`assets doctor`、`assets backfill-dragons` 都**不是**只读。完整矩阵、确认话术与禁止操作见 `references/safety.md`。

确认时把范围说清楚，不要只说“要不要执行”：

- T1：`将执行 <command>，会写入 <dragon.db / assets.db / 本地文件>，范围 <日期/代码/文件>。是否继续？`
- T2：`该操作会 <上传/覆盖/删除> <范围>。建议先运行 <dry-run 命令> 预览。确认继续？`
- 用户本轮明确点名某个 T1 操作可视为确认；T2 仍需 dry-run/二次确认。上一次同意不代表这一次同意。

## 运维契约

- **T+1**：当天买入的仓位当天不卖；`sell` 每个交易日至少运行一次，否则首日窗口会静默错过。
- **峰值新鲜度**：每个交易日至少运行一次 `buy` 或 `sell`，否则移动止盈/保本的峰值判断会过期。
- **历史回放**：`buy`/`sell` 历史日期必须同时给 `--date` 与 `--at`；回放会写库，幂等命中时会标注未落库。
- **并发**：`prep` 不与 `scan`/`rebuild` 并发；`assets` 同步有进程锁。
- **数据耐久**：`prep`/`auction` 之后按需 `assets push`；恢复优先隔离目录，禁止在主库执行 `assets pull --force`。
- **行情口径**：历史日期绝不使用今天的实时报价；停牌/日K兜底要如实说明。
- **数据不可再生**：竞价与部分当日资产仅当日可采；历史缺口不要假装补得回来，明确说明降级。
- **候选池血缘**：`auction` 默认读严格 T-1 的复盘快照，`prep` 读最近五日窗口；回退时输出会标 `pool_source`/`fallback_reason`。
- **每日顺序**：`scan` → `prep`（≥16:40）→ 次日 `auction`（≥09:25）→ `buy`（交易时段）。细节见 `references/operations.md`。

## 异常分流

出现错误、降级或空结果时，不要猜，按症状分流：

- Cookie 缺失/失效 → 先确认是否真的依赖雪球；需要时用 `data cookie-status`，修复走 `data cookie-set`/`cookie-fetch`（T2）。
- THS 403 / 频控 → 等待退避，不要连续重试；确认是否可走 TDX 链路。
- TDX 失败 → 接受回退并在输出中标注 `degraded`/`sources`。
- 非交易日/时段门禁 → 按 CLI 报错处理；不要用 `--force` 绕过交易日或 09:30 前限制。
- 停牌/日K兜底 → 说明 `data_quality`、`fallback_days`、`suspended_codes`；不要把兜底近似当真实成交。
- 空池/快照缺失/回退 → 解释 `fallback_reason`，区分“无信号”和“没数据”。
- 版本漂移 → 用 `doctor` 或 `-v` 定位，升级 CLI 后重跑。
- 因子缺失 → 缺失不等于 0/中性；确认是否默认关闭、源是否可用。
- 资产同步失败 → 看退出码与失败分区；不要用 `--allow-shrink` 或主库 `--force` 掩盖问题。

完整症状/诊断/处理/停止条件见 `references/playbooks.md`。

## Reference 索引

| 文件 | 何时读 |
|---|---|
| `references/commands.md` | 需要确定命令、关键参数、写类、输出模式或常见误用时 |
| `references/outputs.md` | 需要解读人读输出、JSON 顶层字段、错误 JSON 或降级字段时 |
| `references/playbooks.md` | 遇到 Cookie/403/停牌/兜底/空池/版本漂移/同步失败时 |
| `references/operations.md` | 日常调度、历史回放、并发、峰值、资产备份时 |
| `references/safety.md` | 执行任何非只读命令前 |

只加载需要的那一份；不要一次把全部 reference 读进来。reference 是补充，不是前置阅读清单。

## 禁止事项

- 不要要求用户先读完整 `AGENTS.md` 才能完成 `scan`；本 skill 已覆盖路由、预检与解读。
- 不要伪装执行：没有运行命令就不要输出“已扫描/已复盘/已卖出”。
- 不要自动执行写操作；`scan`/`prep`/`auction`/`buy`/`sell`/`storage clear`/`assets push`/`assets pull --apply` 必须先确认。
- 不要手工用 SQL 删改业务表（包括信号表）；CLI 没有提供清理接口时，明确告知并停止。
- 不要回显 Cookie、token 或 `assets_sync` 凭据。
- 不要把 `unverified`、`daily_fallback`、`degraded` 的结果说成确定结论。
- 不要复制或推断评分权重、策略参数、表结构；需要时引用 `AGENTS.md` 或 `STRATEGY.md`。
- 不要自动安装/升级 Python 包，也不要执行 `npm publish`、`git commit`、`git push` 或 PR；这些由用户控制。
- 不要提供投资建议或收益承诺。
