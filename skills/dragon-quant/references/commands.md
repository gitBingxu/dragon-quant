# commands — 唯一命令白名单

**何时读我**：需要把意图落到具体命令、确认关键参数、判断写类或输出模式时。

**总则**：本表是 Skill 侧唯一命令白名单。`research` 是本地只读研究 Facade；除 `research experiments run` 外都不写业务表、不构造 provider、不触网。实验必须用户 `--confirm`，查看不重算。参数以对应叶子命令的 `-h` 为准；本表只列最常用参数，不抄默认值与完整 help。不在本表的命令一律走通用未开放话术。

<!-- routes:start -->
| intent | command | key_flags | write_class | output | notes |
|---|---|---|---|---|---|
| 个股实时行情 | dragon-quant data quote | --code --source | T0 | human/json | 默认腾讯；失败字符串不等于成功 |
| 个股日K | dragon-quant data kline | --code --days --source | T0 | json-lines | 默认 tdx；空输出可改 --source tencent |
| 个股分时 | dragon-quant data minute | --code --source | T0 | json-lines | 默认 tdx；空行表示无数据 |
| 批量行情 | dragon-quant data batch-quote | --codes --source | T0 | json-lines | 多只票优先批量；逐只核对缺失 |
| 当日扫描 | dragon-quant scan | --top | T1 | human | 日常默认 --top 5；15:00 前禁止；不加 --force |
| 历史扫描/本次核验 | dragon-quant scan --date | --date --top | T0 | json | 读回同一 scan_id 与原始候选；不是去重回测池 |
| 新增回测池 | dragon-quant dragons | --date --true-only | T0 | json | 5 日去重池；只报新增数量，不当扫描原始名单 |
| 龙头回测 | dragon-quant review | --date --top | T1 | human | 只处理成熟 pending；failed>0 要如实报告 |
| 可视化面板 | dragon-quant dashboard | --port --no-browser --bs-autogen --no-bs-autogen --workspace-writes --no-workspace-writes | T0 | url | 长驻；默认只读，不生成 BS 派生数据；显式 --bs-autogen/DQ_BS_AUTOGEN=1 才生成（按 T1 确认）；URL 取自启动日志；禁止 --yes 自动安装 |
| 盘后复盘 | dragon-quant prep | --date --recompute --force --json | T1 | human/json | 当日 ≥16:40；--force 不绕门禁 |
| 集合竞价 | dragon-quant auction | --force --recompute --json | T1 | human/json | 当日 ≥09:25；命中快照本次只读；无 --date |
| 盘前预案 | dragon-quant premarket | --date --json | T0 | human/json | 只读；不写库；缺数据降级 exit0；不触网 |
| 因子构建 | dragon-quant factor build | --date --only --json --workspace | T1 | human/json | 内建三组或显式用户因子的精确名单；禁止默认 build 与 --all |
| 因子注册表 | dragon-quant factor list | --json --workspace | T0 | human/json | 只读注册表快照；--workspace 用 AST 元数据列用户因子，不导入 |
| 因子状态 | dragon-quant factor status | --json | T0 | human/json | 只读内建覆盖/构建状态；缺失不等于 0；不支持 --workspace |
| 因子读回 | dragon-quant factor query | --date --from --to --code --name --version --json --workspace | T0 | human/json | 本地只读；用户因子已注册名 ws_<id>.<local> |
| 因子导出 | dragon-quant factor export | --from --to --name --code --version --format --out --json --workspace | T1 | human/json | 写明确 --out 路径；sidecar meta 同批 |
| 因子评估 | dragon-quant factor eval | --from --to --name --version --horizon --json --workspace | T1 | human/json | 可能触网并写本地数据缓存；如实说明，不宣称纯离线 |
| 决策结果回测 | dragon-quant factor decision-review | --source --from --to --horizon --cost-bps --json | T1 | human/json | 只读冻结快照但可能触网/写缓存；insufficient_data 是有效结论 |
| 工作区初始化 | dragon-quant workspace init | --json | T1 | human/json | 显式创建 workspace 并登记活动选择；不覆盖已有文件 |
| 工作区检查 | dragon-quant workspace check | --workspace --json | T0 | human/json | 显式验证路径；导入用户策略/因子，只读 |
| 策略清单 | dragon-quant strategy list | --workspace --json | T0 | human/json | 只读 descriptor，不导入用户代码；含真实 ws_<id> 因子名 |
| 当前策略 | dragon-quant strategy current | --workspace --json | T0 | human/json | 读取保存选择，不导入用户代码 |
| 校验策略 | dragon-quant strategy validate | --workspace --json | T0 | human/json | 显式导入并调用 create_strategy；只读 |
| 切换策略 | dragon-quant strategy use | --workspace --json | T1 | human/json | 持久切换（revision CAS）；只在用户明确要求时执行 |
| 账户回测 | dragon-quant review-account | --from --to --capital --strategy --workspace --json | T1 | human/json | --json 输出 run_id/汇总/strategy_ref；省略 --strategy 沿用保存选择；不传 --config |
| 研究数据集 | dragon-quant research datasets | --json | T0 | human/json | 只读元信息；列可用性与缺口 |
| 研究字段契约 | dragon-quant research schema | --dataset --json | T0 | human/json | 字段/单位/过滤/排序契约；不输出 DDL |
| 研究查询 | dragon-quant research query | --dataset --from --to --code --scan-id --run-id --fields --limit --cursor --json | T0 | human/json | 只查注册表过滤器；cursor 绑定过滤器 |
| 扫描质量 | dragon-quant research scan-quality | --from --to --include-samples --sample-limit --sample-cursor --json | T0 | human/json | 与 Web 同一算法；分母/成熟度/版本可见 |
| 账户读回 | dragon-quant research account | --run-id --view --limit --cursor --json | T0 | human/json | 只读；不触发回测；缺 run 是空态 |
| 信号读回 | dragon-quant research signals | --from --to --code --status --limit --cursor --json | T0 | human/json | 不是券商成交；未存诊断标 unavailable |
| 快照读回 | dragon-quant research snapshots | --kind --date --from --to --code --limit --cursor --json | T0 | human/json | 不采集、不重算；大 payload 需显式字段 |
| 规则元信息 | dragon-quant research strategy | --kind --run-id --json | T0 | human/json | current 与 historical 分清；不补历史 |
| 实验列表 | dragon-quant research experiments list | --run-id --limit --cursor --json | T0 | human/json | 只读已存报告，不重算 |
| 实验详情 | dragon-quant research experiments show | --experiment-id --run-id --json | T0 | human/json | 只读已存报告，不重算 |
| 运行对照实验 | dragon-quant research experiments run | --run-id --confirm --sell-rule --strategy-param --json | T1 | human/json | 必须真实 run_id 与 --confirm；未知 sell-rule 报错；单飞 |
<!-- routes:end -->

## 关键约定

- **scan 实时模式没有 `--json`**：核验本次扫描用 `scan --date D --top N` 读回同一 `scan_id` 与原始候选；要全部历史候选与否决原因也用 `scan --date`。
- **`dragons --true-only`** 是 5 日去重后的回测池，只用于另报「新增回测池数量」；不能替代本次扫描记录核验，也不能用其数量解释“当日只有 N 只真龙”。
- **`scan --date` 的错误 JSON** 要区分“无记录”和 `available_top_n` 不匹配；不能只看退出码。
- **`quote` 的失败字符串** “获取行情失败”按失败处理，即使 exit 0；`kline`/`minute` 的空输出表示无数据。
- **`prep` 当日门禁 16:40**，`--force` 不绕过；`--recompute` 不触网但仍写快照。
- **`auction` 没有 `--date`**；快照命中时本次只读，`--force` 才重采，`--recompute DATE` 用已存资产重算。
- **`factor build` 的 exit 0 不等于全部成功**：必须解析 `reports[0].factors[].status` 并逐因子核对。
- **`dashboard` 只起面板、不回测、不写业务记录**；**默认只读**，不生成 BS 派生数据；只有显式 `--bs-autogen` / `DQ_BS_AUTOGEN=1` 才增量生成 `bs_*`，`--no-bs-autogen` / `DQ_BS_AUTOGEN=0` 始终优先。页面「重新计算买卖点」是显式写操作。缺 `[web]` 依赖时给出手动安装指令，不要加 `--yes`。
- **复合意图“回测并打开面板”** 默认按 CLI 正常行为尝试打开浏览器；只有用户明确只要 URL/不要浏览器，或宿主无浏览器能力时才加 `--no-browser`。
- **`review-account --json` 是执行回执**：含 `run_id`、汇总与 `strategy_ref`（冻结身份/参数/必要因子版本）；详细结果用 `research account --run-id N --view ...` 读回；不要把终端摘要当机器契约；`--config` 不接受用户自定义参数。生命周期胜率与本段权益贡献不可混读；旧批次精确归因缺失时显示 unknown/legacy。
- **workspace / strategy**：`workspace init` 写 workspace 文件并登记活动选择；`check` 与 `strategy validate` 会导入用户策略（只读）；`strategy list/current` 只读 descriptor。`strategy use` 是持久切换，`--strategy` 只覆盖单次命令。用户因子构建必须用 `factor list --workspace` 给出的精确注册名 `ws_<id>.<local>` 配 `--only`；完整流程见 [workspace.md](workspace.md)。
- **`research` 参数纪律**：只用注册表声明的 dataset/filter/field；未知项 exit 2。`--json` stdout 只有一个 `research_v1` envelope；缺库/缺表/空集 exit 0 且有 `empty_reason`，只读不可用等真实故障才非零。
- **实验纪律**：`research experiments run` 需要真实 `--run-id` 与 `--confirm`；先冻结期初与上游，再运行；与 Web 同一单飞；`no_change` / `insufficient_evidence` 不能当改进。
