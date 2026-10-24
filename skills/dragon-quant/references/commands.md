# commands — 命令路由与关键参数

**何时读我**：需要把意图落到具体命令、确认关键参数、判断写类或输出模式时。

**总则**：参数以 `dragon-quant <command> -h` 为准。本表只列最常用参数，不抄默认值、不抄完整 help。`scan_v2` 是隐藏兼容别名，按 `scan` 处理，不单独列出。

## 路由表

<!-- routes:start -->
| intent | command | key_flags | write_class | output | notes |
|---|---|---|---|---|---|
| 扫描今日真龙 | dragon-quant scan | --top --candidates --workers --force --no-cache | T1 | human | 实时无 JSON；写 v2 表并触网 |
| 查询历史扫描 | dragon-quant scan --date | --date --top | T0 | json | 仅 YYYYMMDD；格式错只写 stderr 但 exit 0；错误 JSON 含 available_top_n |
| 导出龙头 | dragon-quant dragons | --date --true-only | T0 | json | 结构化结果优先；默认最新有数据交易日 |
| 盘后复盘 | dragon-quant prep | --date --recompute --force --json | T1 | human/json | 当日 ≥16:40；重算不触网但仍写快照 |
| 集合竞价 | dragon-quant auction | --pool --days --recompute --json | T1 | human/json | 当日 ≥09:25；无 --date；空池不等于弱 |
| 买入信号 | dragon-quant buy | --date --at --force --json | T1 | human/json | 写信号表；必须确认 |
| 卖出信号 | dragon-quant sell | --date --at --force | T1 | human | 写信号表；无 JSON；T+1 |
| 龙头回测 | dragon-quant review | --date --source | T1 | human | 回写 review 字段 |
| 龙头回测 UI | dragon-quant review --ui-only | --ui-only --port --no-browser | T0 | human | 长驻；可能初始化/校验 schema |
| 账户回测 | dragon-quant review-account | --from --to --capital --config | T1 | human | 写账户表；终端无 JSON |
| 账户回测 UI | dragon-quant review-account --ui-only | --ui-only --port --no-browser | T0 | human | 长驻；可能初始化/校验 schema |
| 资产状态 | dragon-quant assets status | --profile --all --orphans | T0 | human/json | 网络读；drift 是弱信号 |
| 资产校验 | dragon-quant assets verify | --profile --from --to --samples --deep --md5 | T0 | human/json | --deep 会下载校验 |
| 资产拉取预演 | dragon-quant assets pull | --profile --from --to --pick | T0 | human/json | 默认 dry-run，不加 --apply 不写 |
| 资产拉取落库 | dragon-quant assets pull --apply | --apply --target-dir | T2 | human/json | 主库 --force 禁止 |
| 资产探测 | dragon-quant assets doctor | --profile --samples | T1 | human/json | 会向远端 _doctor 前缀 PUT/DELETE 探针 |
| 资产推送 | dragon-quant assets push | --profile --from --to --all --allow-shrink | T2 | human/json | 先 --dry-run |
| 资产回填体检 | dragon-quant assets backfill-dragons --dry-run | --dry-run --json | T0 | human/json | 区分 missing_rows / orphan_rows |
| 资产回填 | dragon-quant assets backfill-dragons | --from --to | T1 | human/json | 写 assets.db.dragons |
| 资产回填清理 | dragon-quant assets backfill-dragons --prune | --prune | T2 | human/json | 删除镜像孤儿行 |
| 黑名单查看 | dragon-quant blacklist list | — | T0 | human | 只读 |
| 黑名单新增 | dragon-quant blacklist add | — | T1 | human | 写配置；需确认 |
| 黑名单移除 | dragon-quant blacklist remove | — | T1 | human | 写配置；需确认 |
| 存储状态 | dragon-quant storage status | — | T0 | human | 只读 |
| 存储占用 | dragon-quant storage size | — | T0 | human | 只读 |
| 存储清理 | dragon-quant storage clear | --all --cache --results --logs --days | T2 | human | 不删 .db；删缓存/结果/日志与日志表行 |
| 因子只读 | dragon-quant factor list | — | T0 | human | 只读 |
| 因子查询 | dragon-quant factor query | --date --from --to --name --code | T0 | human/json | 输出 resolved_versions |
| 因子状态 | dragon-quant factor status | — | T0 | human | 只读 |
| 因子评估 | dragon-quant factor eval | --name --from --to --groups --pit-policy | T0 | human/json | insufficient_data 是有效结论，exit 0 |
| 因子构建 | dragon-quant factor build | --date --from --to --only --all | T1 | human/json | 写 factor_*；与 --only 互斥的 --all |
| 因子导出 | dragon-quant factor export | --from --to --name --out | T1 | human/json | 写用户指定文件；确认路径 |
| 财务同步 | dragon-quant factor fin sync | --snapshot --backfill --force | T1 | human/json | 写 fin_*；触网 |
| 财务校准 | dragon-quant factor fin calibrate | --sample --period | T1 | human/json | 写校准表 |
| 财务状态 | dragon-quant factor fin status | — | T0 | human/json | 只读 |
| 日志查询 | dragon-quant logs tail | — | T0 | human | 只读 |
| 日志清理 | dragon-quant logs clear | --days | T2 | human | 删除日志文件与日志表行 |
| 板块排行 | dragon-quant data sector | --asc | T0 | human | 触网只读 |
| 板块成分股 | dragon-quant data components | --sector | T0 | human | 触网只读 |
| 个股日K | dragon-quant data kline | --code --source --days | T0 | human | 触网只读 |
| 个股分时 | dragon-quant data minute | --code --source | T0 | human | 触网只读 |
| 个股行情 | dragon-quant data quote | --code --source | T0 | human | 触网只读 |
| 批量行情 | dragon-quant data batch-quote | --codes --source | T0 | human | 触网只读 |
| Cookie 状态 | dragon-quant data cookie-status | — | T0 | human | 只读 |
| Cookie 刷新 | dragon-quant data cookie-fetch | — | T2 | human | 写凭据；启动浏览器 |
| Cookie 写入 | dragon-quant data cookie-set | --cookie | T2 | human | 写凭据；禁止回显秘密 |
| 量价分析 | dragon-quant vpa | --code --source --days | T1 | human | 默认写 vpa_analysis |
| 量价不落库 | dragon-quant vpa --no-save | --no-save | T0 | human | 不写库 |
<!-- routes:end -->

## 常见误用

- **`scan` 实时模式没有 `--json`**。要结构化结果用 `dragons`；要全部候选与否决原因用 `scan --date` 回放历史记录。
- **`scan --date` 只接受 `YYYYMMDD`**。格式错误只写 stderr 但仍 exit 0，不能只看退出码判断成功；错误 JSON 里会有 `available_top_n`，按提示调整 `--top` 重试。
- **`sell` 没有 `--json`**。只有 `buy --json` 带 `details[].checks` 四类买点诊断。
- **`prep` 当日门禁是 16:40**，`--force` 不绕过；`--recompute` 零采集但仍写快照。
- **`auction` 没有 `--date`**。只采当日，盘后仍可拉取，但无历史回看；`--recompute DATE` 用已存资产重算且不触网。
- **`buy`/`sell` 历史日期必须 `--date` + `--at`**；`--force` 只放宽午休/收盘，不放宽 09:30 前与交易日校验。
- **`assets pull` 默认 dry-run**，`--apply` 才写；主库 `--force` 禁止，恢复用 `--target-dir`。
- **`assets push` 默认遍历全部本地分区**；收窄用 `--from/--to`，预览用 `--dry-run`。
- **`assets doctor` 会远端写探针**，不是纯只读；只有只读凭据时改用 `assets status`/`verify`。
- **`storage clear` 不删 `.db` 文件**，但会删缓存、结果、日志与日志表行；`--days` 只作用于结果/日志。
- **`factor eval` 的 `insufficient_data` 是有效结论**（exit 0），不是命令失败。
- **`review --ui-only` / `review-account --ui-only` 不回测、不写业务记录**，不要与不带 `--ui-only` 的形式混为一谈。
