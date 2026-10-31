# commands — 唯一命令白名单

**何时读我**：需要把意图落到具体命令、确认关键参数、判断写类或输出模式时。

**总则**：本表是 Skill 侧唯一命令白名单。参数以对应叶子命令的 `-h` 为准；本表只列最常用参数，不抄默认值与完整 help。不在本表的命令一律走通用未开放话术。

<!-- routes:start -->
| intent | command | key_flags | write_class | output | notes |
|---|---|---|---|---|---|
| 个股实时行情 | dragon-quant data quote | --code --source | T0 | human/json | 默认腾讯；失败字符串不等于成功 |
| 个股日K | dragon-quant data kline | --code --days --source | T0 | json-lines | 默认雪球；失败可改 --source tencent |
| 个股分时 | dragon-quant data minute | --code --source | T0 | json-lines | 默认雪球；空行表示无数据 |
| 批量行情 | dragon-quant data batch-quote | --codes --source | T0 | json-lines | 多只票优先批量；逐只核对缺失 |
| 当日扫描 | dragon-quant scan | --top | T1 | human | 日常默认 --top 5；15:00 前禁止；不加 --force |
| 历史扫描 | dragon-quant scan --date | --date --top | T0 | json | 明确历史日时才用；优先 dragons |
| 真龙核验 | dragon-quant dragons | --date --true-only | T0 | json | scan 后结构化核验 |
| 龙头回测 | dragon-quant review | --date --top | T1 | human | 只处理成熟 pending；failed>0 要如实报告 |
| 回测面板 | dragon-quant review --ui-only | --ui-only --port --no-browser | T0 | url | 不回测；长驻；URL 取自启动日志 |
| 盘后复盘 | dragon-quant prep | --date --recompute --force --json | T1 | human/json | 当日 ≥16:40；--force 不绕门禁 |
| 集合竞价 | dragon-quant auction | --force --recompute --json | T1 | human/json | 当日 ≥09:25；命中快照本次只读；无 --date |
| 盘前预案 | dragon-quant premarket | --date --json | T0 | human/json | 只读；不写库；缺数据降级 exit0；不触网 |
| 因子构建 | dragon-quant factor build | --date --only --json | T1 | human/json | 只允许三组固定名单；禁止默认 build |
| 因子读回 | dragon-quant factor query | --date --name --json | T0 | human/json | 仅入库核对与代表因子读回 |
| 账户回测 | dragon-quant review-account | --from --to --capital | T1 | human | 默认策略与 v2；不传 --config |
| 账户面板 | dragon-quant review-account --ui-only | --ui-only --port --no-browser | T0 | url | 只开已有面板；URL 取自启动日志 |
<!-- routes:end -->

## 关键约定

- **scan 实时模式没有 `--json`**：要结构化结果用 `dragons`；要全部候选与否决原因用 `scan --date` 回放历史记录。
- **`dragons --true-only`** 才是真龙核验口径；不带该参数会包含非真龙。
- **`scan --date` 的错误 JSON** 要区分“无记录”和 `available_top_n` 不匹配；不能只看退出码。
- **`quote` 的失败字符串** “获取行情失败”按失败处理，即使 exit 0；`kline`/`minute` 的空输出表示无数据。
- **`prep` 当日门禁 16:40**，`--force` 不绕过；`--recompute` 不触网但仍写快照。
- **`auction` 没有 `--date`**；快照命中时本次只读，`--force` 才重采，`--recompute DATE` 用已存资产重算。
- **`factor build` 的 exit 0 不等于全部成功**：必须解析 `reports[0].factors[].status` 并逐因子核对。
- **`review --ui-only` / `review-account --ui-only` 不回测、不写业务记录**；只开面板时不重跑回测。
- **复合意图“回测并打开面板”** 默认按 CLI 正常行为尝试打开浏览器；只有用户明确只要 URL/不要浏览器，或宿主无浏览器能力时才加 `--no-browser`。
- **`review-account` 终端没有 JSON**：只读人读摘要，详细结果在面板；`--config` 不接受用户自定义参数。
