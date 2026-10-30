# safety — 白名单写操作与确认门禁

**何时读我**：执行任何 T1 命令之前。默认假设“写”，除非下表明确标为 T0。

## 分级定义

- **T0 只读**：不写业务记录、不改远端业务状态，可直接执行。`--ui-only` 虽为 T0，但会**长驻**会话，并可能初始化/校验本地 **schema**；启动前要说明。
- **T1 写本地状态**：写 `dragon.db` / `assets.db` / 用户指定文件。执行前必须得到用户显式确认；用户本轮明确点名该操作可视为确认。
- 本 Skill **不开放外发、破坏性或高风险操作**；遇到时走通用未开放话术。

## 白名单矩阵

<!-- safety:start -->
| command | flags | tier | notes |
|---|---|---|---|
| scan | --top | T1 | 实时扫描写 v2 表并触网；15:00 前禁止 |
| scan | --date --top | T0 | 读历史扫描记录；错误 JSON 不等于无数据 |
| dragons | --date --true-only | T0 | 只读 JSON；scan 后核验真龙 |
| data | quote/kline/minute/batch-quote | T0 | 触网只读；失败字符串不等于成功 |
| prep | --date --recompute --force --json | T1 | 写 assets.db；当日需 16:40 后 |
| auction | --force --recompute --json | T1 | 写 assets.db；命中当日快照时本次只读；当日需 09:25 后 |
| review | --date --top | T1 | 回写回测字段；failed>0 如实报告 |
| review | --ui-only --port --no-browser | T0 | 只启动回测面板，长驻，不回测 |
| review-account | --from --to --capital | T1 | 写账户回测表；默认策略与 v2 |
| review-account | --ui-only --port --no-browser | T0 | 只启动账户面板，长驻，不回测 |
| factor | build --date --only --json | T1 | 只写三组内置因子；禁止默认 build |
| factor | query --date --name --json | T0 | 只读；用于入库核对与代表因子读回 |
<!-- safety:end -->

表外说明：

- `-h` / `-v` 为 T0。
- `--ui-only` 面板只监听本机 `127.0.0.1`；不要建议任何公网/局域网暴露方式。
- 报告写用户指定路径属于 T1（本地文件）；默认不写仓库。

## 确认话术

- **T1**：`将执行 <command>，会写入本地数据库（dragon.db / assets.db），范围 <日期/代码>。是否继续？`
- **UI-only**：`将启动本地 Web UI 面板（长驻，可能需要初始化/校验本地 schema，不写业务记录），并默认尝试打开浏览器；是否继续？`
- **文件**：`将把报告写入 <path>（本地文件）。是否继续？`；用户指定仓库内路径时提示会影响 git 状态。
- 用户本轮明确点名某个 T1 操作可视为确认；上一次同意不代表这一次同意。
- 语义模糊（“看看今天的”“整理一下”）时先确认，不自行选择写操作。

## 禁止操作

- 未确认执行 `scan`（实时）/ `prep` / `auction` / `review` / `review-account` / `factor build`，或写用户指定文件。
- 在单能力请求里隐式追加写操作；默认执行每日复合 workflow。
- 用 `--force` 绕过盘中/盘后/竞价时点，或把它当成日常手段。
- 手工用 SQL 删除或修改业务表。
- 回显 Cookie、token 或同步凭据，或把它们写入日志/仓库。
- 把 `--ui-only` 说成“会回测/会写业务记录”，或伪造已经打开浏览器。
- 自动安装/升级 Python 包，或执行 `npm publish`、`git commit`、`git push`、PR。
