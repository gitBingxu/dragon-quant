# safety — 白名单写操作与确认门禁

**何时读我**：执行任何 T1 命令之前。默认假设“写”，除非下表明确标为 T0。

## 分级定义

- **T0 只读**：不写业务记录、不改远端业务状态，可直接执行。`dashboard` 虽为 T0，但会**长驻**会话，
  并可能初始化/校验本地 **schema**；启动后**默认不**生成 BS 派生数据，只有显式 `--bs-autogen` /
  `DQ_BS_AUTOGEN=1` 才增量生成 `bs_*` 派生表；启动前要说明。
- **T1 写本地状态**：写 `dragon.db` / `assets.db` / 用户指定文件，或可能触网并写本地数据缓存（`factor eval` / `decision-review`）。执行前必须得到用户明确授权。
  **授权按日期/动作/参数/副作用范围持续有效**：用户说「今天允许跑 prep 和因子 build」后，本会话内
  同一范围不再反复确认；只有范围变更、用户撤销或出现新的高风险副作用才补确认。
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
| auction | --force --recompute --json | T1 | 写 assets.db；命中当日快照时本次只读；当日需 09:25 后；premarket 可主动建议，执行需用户授权该动作 |
| premarket | --date --json | T0 | 只读；不写库；缺数据降级 exit0；不触网 |
| review | --date --top | T1 | 回写回测字段；failed>0 如实报告 |
| review-account | --from --to --capital --json | T1 | 写账户回测表；--json 仅回执；默认策略与 v2 |
| dashboard | --port --no-browser --bs-autogen --no-bs-autogen | T0 | 长驻；默认只读；显式 --bs-autogen / DQ_BS_AUTOGEN=1 才生成 BS 派生数据，该分支按 T1 确认；--no-bs-autogen / DQ_BS_AUTOGEN=0 优先；禁止 --yes 自动安装 |
| factor | build --date --only --json | T1 | 只写三组内置因子；禁止默认 build |
| factor | list --json / status --json / query --date --from --to --code --name --version --json | T0 | 本地只读；用于入库核对与研究读回 |
| factor | export --from --to --name --out | T1 | 写用户明确 --out 路径与 sidecar meta |
| factor | eval --from --to --name --json | T1 | 可能触网并写本地数据缓存；不宣称纯离线 |
| factor | decision-review --source --from --to --json | T1 | 只读冻结快照但可能触网/写缓存 |
| research | datasets/schema/query/scan-quality/account/signals/snapshots/strategy --json | T0 | 本地只读、固定 v2、不构造 provider |
| research | experiments list/show --run-id/--experiment-id | T0 | 只读已存报告，不重算、不建锁 |
| research | experiments run --run-id --confirm | T1 | 冻结期初与上游、本地行情、单飞；必须真实 run_id 与 --confirm |
<!-- safety:end -->

表外说明：

- `-h` / `-v` 为 T0。
- `dashboard` 面板只监听本机 `127.0.0.1`；不要建议任何公网/局域网暴露方式。
- 报告写用户指定路径属于 T1（本地文件）；默认不写仓库。

## 确认话术

- **T1**：`将执行 <command>，会写入本地数据库（dragon.db / assets.db），范围 <日期/代码>。是否继续？`
- **面板**：`将启动本地 Web UI 面板 dashboard（长驻，可能需要初始化/校验本地 schema；默认只读、不生成 BS 派生数据），并默认尝试打开浏览器；是否继续？`
- **文件**：`将把报告写入 <path>（本地文件）。是否继续？`；用户指定仓库内路径时提示会影响 git 状态。
- 用户按日期/动作/参数给出的明确授权持续有效；不因「上一轮不算」反复确认，越界或撤销才补确认。
- 盘前预案流程可以主动建议运行 `auction`，但不得因用户要预案就默认执行；需用户明确授权该动作。
- 面板：`dashboard` 若交互询问是否安装 `[web]`，回答否，只转述手动安装指令；不要用 `--yes` 代替用户决定。
- 语义模糊（“看看今天的”“整理一下”）时先确认，不自行选择写操作。

## 禁止操作

- 未确认执行 `scan`（实时）/ `prep` / `auction` / `review` / `review-account` / `factor build` / `factor export` / `factor eval` / `factor decision-review` / `research experiments run`，或写用户指定文件。
- 在单能力请求里隐式追加写操作；默认执行每日复合 workflow。
- 用 `--force` 绕过盘中/盘后/竞价时点，或把它当成日常手段。
- 手工用 SQL 删除或修改业务表。
- 回显 Cookie、token 或同步凭据，或把它们写入日志/仓库。
- 把 `dashboard` 说成“会回测/会写业务记录”，或伪造已经打开浏览器；默认只读，BS 派生数据生成需显式 opt-in 并按 T1 确认。
- 把 `research experiments run` 说成只读，或在没有真实 `run_id` 时猜「最新批次就是本次」。
- 自动安装/升级 Python 包（含 `dashboard --yes` 与 `DQ_WEB_AUTO_INSTALL=1`），或执行 `npm publish`、`git commit`、`git push`、PR。
