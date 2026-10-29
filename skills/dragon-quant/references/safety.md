# safety — 写操作矩阵与确认门禁

**何时读我**：执行任何非只读命令之前。默认假设“写”，除非下表明确标为 T0。

## 分级定义

- **T0 只读**：不写业务记录、不改远端业务状态，可直接执行。长驻 UI 会阻塞会话；`--ui-only` 可能初始化/校验本地 schema，但不写业务记录。
- **T1 写本地状态或远端临时探针**：写 `dragon.db` / `assets.db` / 本地文件 / 凭据，或向远端写可清理的探测对象。执行前必须得到用户显式确认；用户本轮明确点名该操作可视为确认。
- **T2 外发 / 破坏性 / 高风险**：上传业务数据、覆盖主库、删除数据、写凭据、强制覆盖。必须先 dry-run/预览并二次确认，不得自动执行。

## 顶层命令矩阵

<!-- safety:start -->
| command | flags | tier | notes |
|---|---|---|---|
| scan | --top --candidates --workers --force --no-cache | T1 | 写 v2 表并触网；结果优先用 dragons 读 |
| scan | --date --top | T0 | 读历史扫描记录 |
| logs | tail/query/list/summary | T0 | 只读 |
| logs | clear --days | T2 | 删除日志文件与日志表行 |
| data | sector/components/kline/minute/quote/batch-quote/cookie-status | T0 | 触网只读 |
| data | cookie-fetch / cookie-set --cookie | T2 | 写凭据，禁止回显秘密 |
| blacklist | list | T0 | 只读 |
| blacklist | add/remove | T1 | 写板块配置 |
| review | --date --top --force --source --ui | T1 | 回写 review 字段 |
| review | --ui-only | T0 | 只读启动 Web UI，长驻 |
| review-account | --from --to --capital --strategy --config --ui | T1 | 写账户回测表 |
| review-account | --ui-only | T0 | 只读启动 Web UI，长驻 |
| dragons | --date --true-only | T0 | 只读 JSON |
| prep | --date --recompute --force --json | T1 | 写 assets.db；当日需 16:40 后 |
| auction | --pool --days --force --recompute --json | T1 | 写 assets.db；命中当日快照时本次只读，--force 才重采；当日需 09:25 后 |
| assets | status/verify/pull | T0 | pull 默认 dry-run，不加 --apply 不写 |
| assets | doctor | T1 | 向远端 _doctor 前缀写入并删除临时探针 |
| assets | push | T2 | 上传数据到云端；先 dry-run |
| assets | push --dry-run | T0 | 不写远端 |
| assets | pull --apply / pull --force | T2 | 写本地库；主线库禁止 pull --force，恢复走 target-dir |
| assets | backfill-dragons | T1 | 写 assets.db.dragons |
| assets | backfill-dragons --dry-run | T0 | 只读体检 |
| assets | backfill-dragons --prune | T2 | 删除镜像孤儿行 |
| factor | list/query/status/eval/decision-review/fin status | T0 | 只读或计算 |
| factor | build/export/fin sync/fin calibrate | T1 | 写 factor_* / fin_* 或用户指定文件 |
| buy | --date --at --force --json | T1 | 写信号表；必须确认 |
| sell | --date --at --force | T1 | 写信号表；必须确认；T+1 |
| vpa | --code --source --days | T1 | 默认写量价分析表 |
| vpa | --no-save | T0 | 不写库 |
| storage | status/size | T0 | 只读 |
| storage | clear --all --cache --results --logs --days | T2 | 不删 .db，但删缓存/结果/日志与日志表行 |
<!-- safety:end -->

表外说明：

- `-h` / `-v` 为 T0。
- 隐藏兼容别名 `scan_v2` 与 `scan` 同级（T1）。
- 安装器 `install` 写 skill 目录（T1，`--force` 先备份）；`uninstall` 删除或移动 skill 目录（T2，`--force` 移入备份，不删用户修改）。

## 确认话术

- **T1**：`将执行 <command>，会写入 <dragon.db / assets.db / 本地文件>，范围 <日期/代码/文件>。是否继续？`
- **T2**：`该操作会 <上传/覆盖/删除> <范围>。建议先运行 <dry-run 命令> 预览。确认继续？`
- 用户本轮明确说“执行 scan/prep/buy/...”可视为 T1 确认；T2 仍需 dry-run 或二次确认。
- 不因“上次同意过”而自动执行下一次写操作。

## 禁止操作

- 未确认执行 `buy` / `sell` / `blacklist add|remove` / `storage clear` / `assets push` / `assets pull --apply` / `backfill-dragons --prune`。
- 在主库执行 `assets pull --force`；恢复应使用 `--target-dir` 隔离目录。
- 手工用 SQL 删除或修改业务表（包括信号表）；CLI 没有清理接口时，说明限制并停止。
- 回显 Cookie、token、`assets_sync` 凭据，或把它们写入日志/仓库。
- 在 `prep` 与 `scan`/`rebuild` 并发时运行 `prep`。
- 用 `--force` 绕过交易日或 09:30 前门禁；`--force` 不放宽数据/日期校验。
- 把 `--ui-only` 说成“会写库”，或把 `review --ui-only` 与 `review` 混为一谈。
- 自动安装/升级 Python 包，或执行 `npm publish`、`git commit`、`git push`、PR。

## 秘密处理

- 凭据只通过 `data cookie-set`/`cookie-fetch` 或既有配置写入；读取只确认存在与权限，不打印内容。
- 同步凭据位于数据目录下的受保护文件；不要在命令行、日志、manifest 或提交中出现。
- 调试输出如包含凭据，必须脱敏后再展示。

## 常见误判

- `scan`/`prep`/`auction`/`review`/`review-account`/`factor build`/`factor fin sync` 都不是只读。
- `dragons`、`scan --date`、`logs tail`、`data *`、`factor list/query/status/eval/decision-review`、`storage status/size` 是只读。
- `assets pull` 默认只预览；`assets doctor` 虽名为检查，但会远端写探针，按 T1 处理。
- `storage clear` 不删数据库文件，但仍是破坏性操作（删缓存/结果/日志）。
- 部分只读命令在首次运行或旧库上会做幂等的 schema 初始化/迁移并拿写锁（例如读历史扫描、读龙头）；这不改变其 T0 语义，但不要与写库命令并发执行。
