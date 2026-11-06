# operations — 交易日 workflow 与因子入库

**何时读我**：需要安排单能力或每日复合 workflow、启动/停止 Web UI 面板、执行因子入库核对、或决定报告交付形态时。

## 单能力 vs 每日复合

- **单能力**：用户点名一个能力（例如“扫一下今天真龙”），只执行该能力并在报告后结束。**不隐式**追加 `prep`、`factor build` 或其他写操作，也不自动执行“下一步建议”。
- **每日复合**：**只有用户明确**说“每日全流程 / 每日量化入库 / 今天全套”时才启用；否则一律按单能力处理。
- 跨时点无法在同一 session 完成的部分，只输出“下一阶段待执行”，**不自动执行**，不做后台调度。

## 盘后 session（交易日 ≥15:00）

1. `dragon-quant scan --top 5`（默认前 5）；15:00 前不执行实时扫描，也不用 `--force`。
2. 从 scan 输出确认 `trade_date` 与本次 `scan_id`/`top_n`；用 `dragon-quant scan --date D --top <本次top>`
   读回**同一条扫描记录**核验原始候选与 `scan_id`。
3. 需要「新增回测池」时另跑 `dragon-quant dragons --date D --true-only`：它经过 5 日去重，
   只能报“新增回测池 N 只”，**不能**用其数量解释“当日只有 N 只真龙”，也不是本次扫描原始名单。
4. 用户明确要求复盘时：`dragon-quant prep --json`（当日 ≥16:40），默认一屏简报；用户要求完整报告时按 `references/reports.md` 九段模板交付。
5. 用户明确要求入库时：按下方“因子入库”执行三组 `--only` 构建与读回。

## 次日盘前 session（交易日 ≥09:25）

1. `dragon-quant premarket --json`（T0 只读）：拿到 T-1 计划 + T 日竞价 + 结构化降级，默认一屏结论；完整盘前作战卡八段模板按需展开。
2. **09:25 前**：只出无竞价版；结尾主动建议「09:25 后先运行 `auction` 采集竞价终态，再重新生成盘前预案」。
3. **09:25 后**：先跑 `premarket` 检查竞价状态；`ready / partial` 直接生成；缺失 / 不可用时**主动建议**运行 `dragon-quant auction --json`（T1，会写 assets.db），给出确认话术；只有用户明确授权该动作后才执行；成功后重跑 `premarket`，再生成最终预案。用户拒绝或自行执行 → 无竞价版并写明「竞价缺失」。
4. 不自动运行 `auction`、不自动加 `--force`、不用 `--force` 绕过 09:25 门禁。
5. 默认一屏简报，完整报告按 `references/reports.md` 七段模板交付；**09:30 后运行仍读取/采集 09:25 终态**，正常生成竞价强度报告，标注实际运行时点与缓存状态。
6. 只有用户明确提出“竞价因子入库 / 每日量化入库”时，才继续构建 auction 组；单能力 `auction` 到报告结束。

## 十类 workflow

### 1. 个股行情

1. 识别实时（quote）/ 日 K（kline）/ 分时（minute）/ 批量（batch-quote）。
2. 代码必须是 6 位数字；用户只给名称时先要求代码，不猜测映射；多只票优先批量。
3. 执行对应白名单命令；只陈述事实与数据来源，不给买卖建议。
4. 校验：`quote` 输出“获取行情失败”按失败处理（即使 exit 0）；`kline`/`minute` 空输出表示无数据；`batch-quote` 逐只报告成功/缺失。
5. 降级：雪球链路空/403 时，`kline`/`minute` 改用 `--source tencent` 重试一次；仍失败则说明数据不可得。

### 2. 扫描与真龙

1. 用户说“今天/现在”走实时扫描；说“某天/历史”用 `scan --date D --top N` 读回历史扫描记录。
2. 时点：同一交易日 09:00–15:00 拒绝实时扫描，说明 15:00 后更准确，不提供 `--force`。
3. 执行 `scan --top 5`（默认）；只有用户明确要求更多/并发时才加参数。
4. 核验：`trade_date`、本次 `scan_id`/`top_n` 与原始候选必须与刚跑的那次一致（必要时
   `scan --date D --top 5` 读回同一记录比对）；不把读取到的其他 `top_n` 记录当本次结果。
5. 另报 `dragons --date D --true-only` 的**新增回测池数量**；它是 5 日去重池，不是当日原始候选，
   不得据此说“当日只有 N 只真龙”，也不用它反推权重。
6. 研究/校准需求走 `research scan-quality --from D1 --to D2`（原始候选、通过/否决、错失机会与基准差），
   不改 dragons 去重与生产候选池。

### 3. 龙头回测与面板

1. 阶段 1：`review`（默认成熟 pending）或 `review --date D --top N`。
2. 校验汇总 `processed/completed/cannot_evaluate/failed/skipped`；`failed > 0` 如实报告，不把 partial 说成完整。
3. 阶段 2：后台启动 `dashboard`（默认打开复盘页）；只开已有面板时跳过阶段 1。
4. UI 规则见下方“UI 生命周期”；禁止把 `dashboard` 说成会回测，禁止在面板已打开时重复跑回测。

### 4. 盘后复盘

1. 确认“今天”还是“历史/某天”：今天当日 ≥16:40 才可；历史用 `--date D`，不受当日门禁。
2. 执行 `prep --json`；校验 `trade_date` 与目标一致、`strategy_context.context_version`、`data_quality` 缺失项。
3. 默认一屏简报（结果/风险/缺失/下一验证时点）；完整九段报告按需生成。
4. `strategy_context` 缺失或版本不认识时，只输出旧摘要并说明无法给出条件化策略，不拼凑阈值/仓位。

### 5. 集合竞价

1. 当日 ≥09:25 才运行；09:25 前说明数据尚未形成。
2. 执行 `auction --json`；校验 `mode/source/pool_source/fallback_reason/from_cache/data_quality/validation_status`。
3. **09:30 后仍可运行**：读取/采集的仍是 09:25 终态，正常生成竞价报告并标注实际运行时点与缓存状态；只有额外加入的 09:30 后行情观察才归“开盘后验证”，不得倒填为竞价事实。
4. 默认一屏简报；完整七段报告按需生成；`empty`/`failed`/`unavailable`/`unverified` 都不等于看空。

### 6. 因子入库

见下方“因子入库（三组固定名单）”。

### 7. 账户回测与面板

1. 引导：区间 `--from`/`--to`（必填）→ 初始资金（默认 100000，确认）→ 策略（默认 `dragon_pullback_daily`，不展开参数）→ 数据体系（默认 v2）。
2. 说明会写账户回测表，且候选池优先读 prep 快照；目标区间未跑 prep 时会走回退池，报告中需说明回退天数与原因。
3. 执行 `review-account --from D1 --to D2 [--capital N] --json` 取得真实 `run_id` 回执（默认人读格式不变）；再按需用 `research account --run-id N --view summary|snapshots|trades|positions|events` 读回。校验 `ranking_mode`、回退天数、停牌跳过、`data_degraded`。
4. 阶段 2：后台 `dashboard`，账户页 URL 为 `/account`；只开已有面板时跳过回测。
5. 答疑只解释摘要与数据质量三态、兜底成交价近似、停牌冻结、候选池回退；不复制策略参数数值，不承诺收益。
6. 账户页分区：**持仓生命周期胜率**（整笔已平仓）与**本段权益贡献**（期初盯市基准）不可混读；
   旧批次无精确归因时明确「legacy 近似/不可判定」。
7. 对照实验只在用户显式点击或 `research experiments run --run-id N --confirm` 后运行（本地行情、冻结上游母集）；`research experiments list/show` 只读、不重算；不自动推广参数。

### 8. 盘前预案

1. 运行 `premarket --json`（T0）；先核对 `data_quality`、`prep.strictness`、`proven_gap`、`auction.state`、`cache=unknown`。
2. 09:25 前只出无竞价版；`auction.state=missing/not_ready/corrupt` 不等于看空，候选行动上限降为 `observe_only`。
3. 外部证据按 `references/evidence.md` 采集；缺失只降情景覆盖，不填中性、不硬否决。
4. 默认一屏结论，完整八段模板按需展开；不自动运行 `auction`、不自动交易、不写用户文件。

### 9. 可视化面板

1. 用户明确要看面板（龙头 / 扫描 / 因子 / 复盘 / 竞价 / 账户）时，运行 `dashboard`（T0，长驻）。
2. 默认打开复盘页；账户页用 URL `/account`，其他页面按启动日志中的真实地址进入。
3. 缺 `[web]` 依赖时只转述 stderr 的手动安装指令；禁止 `--yes`、禁止自动 pip 安装。
4. 面板默认只读，不回测、不写业务记录；`--bs-autogen` / `DQ_BS_AUTOGEN=1` 会生成 BS 派生数据，按 T1 确认；详见“UI 生命周期”。

## UI 生命周期

- **后台启动**：`dashboard` 是**长驻**进程，不阻塞的判断以实际后台能力为准；宿主没有后台执行机制时，明确告知“面板会阻塞当前会话”，并给出用户自行在终端运行的命令，不假装已启动。
- **真实 URL**：只从启动日志读取真实地址（`http://localhost:<port>/` 或 `/account`），不猜测端口；`dashboard` 未显式给 `--port` 时默认 8765，被占用会警告一次并自动改用系统分配端口，仍以日志为准。
- **浏览器**：复合意图“回测并打开面板 / 账户回测并打开面板”**默认尝试打开浏览器**；只有用户明确说“只启动服务 / 给我 URL / 不要打开浏览器”，或宿主没有浏览器能力时，才加 `--no-browser`。
- **安装门禁**：缺 `[web]` 可选依赖时，`dashboard` 会交互询问或 exit 1 给出手动指令；Skill **只用** stderr 的手动安装指令，禁止 `--yes` / `DQ_WEB_AUTO_INSTALL=1`，禁止自动 pip 安装。
- **不伪造**：不把“已启动进程”说成“已打开浏览器”；浏览器打开失败时只报告真实 URL。
- **访问范围**：面板**仅本机** `127.0.0.1` 可访问，不要建议任何公网/局域网暴露方式。
- **停止方式**：报告 URL 时同时说明停止方式（Ctrl+C / 会话结束后端口释放）。
- **默认只读**：`dashboard` 只启动面板，不回测、不写业务记录；**默认不**后台生成 BS 派生数据。
  只有用户显式要求（`--bs-autogen` 或 `DQ_BS_AUTOGEN=1`）才允许启动时增量生成 `bs_*` 派生表；
  `--no-bs-autogen`/`DQ_BS_AUTOGEN=0` 始终优先。面板已打开时不要重复跑回测。
- **BS 重算**：页面「重新计算买卖点」是显式写操作，会改变 `bs_*` 派生数据；纯查看不触发。

## 因子入库（三组固定名单）

只有下面三组内置因子可以构建，按名称排序，逐字使用；构建只做这三组，其他因子构建请求走通用未开放话术。读回/核对可额外使用白名单内的 `factor list` / `status` / `query`（含区间+code+version）/ `export` / `eval` / `decision-review`。

```text
<!-- factor-groups:start -->
scan: five_dim_absorption,five_dim_anti_drop,five_dim_composite,five_dim_drive,five_dim_is_true_dragon,five_dim_leadership,five_dim_liquidity
prep: prep_chip_concentration,prep_dim_d1,prep_dim_d2,prep_dim_d3,prep_dim_d4,prep_dim_d5,prep_dim_d6,prep_distance_high60,prep_feedback_state,prep_high_days,prep_in_pool_today,prep_limit_up_suc_rate,prep_main_net_ratio,prep_mainline_rank,prep_market_state,prep_position_pct,prep_review_grade,prep_review_score,prep_role,prep_seal_ratio,prep_t_pct,prep_t_turnover,prep_turnover_health
auction: auction_dim_direction,auction_dim_liquidity,auction_dim_supply,auction_dim_tail_grab,auction_dim_vol_price,auction_direction,auction_final_slippage_pct,auction_is_weak,auction_open_gap_pct,auction_rank,auction_strength,auction_strong_to_weak,auction_tail_matched_acceleration,auction_turnover_pct,auction_unmatched_matched_ratio,auction_volume_ratio_5d,auction_weak_to_strong
<!-- factor-groups:end -->
```

**构建与核对**：

1. 确认日期 D 与要入库的组；T1 确认后每组单独执行 `factor build --date D --only <组名单> --json`。
2. **不能只看退出码**：exit 0 不等于全部成功。逐条检查 `reports[0].factors[<name>].status`：`ok` 有行；`empty` 源可用但当日无值；`missing_source` 源不可用或配置 fail-closed，**不等于 0**；`error` 必须报告。
3. 区分 `warnings`：`no_scan_for_date` 对 prep/auction-only 组是预期，对 scan 组是失败信号。
4. 代表因子读回：`factor query --date D --name five_dim_composite --json`、`prep_review_score`、`auction_is_weak`（二次抽查可加 `auction_strength`）；检查 `values.rows > 0` 且 `resolved_versions` 含该因子。auction 数据不足时可合法无行，需说明原因。
5. 输出入库核对表：组 / 预期数 / `ok` 数 / `empty` 数 / `missing_source` 数 / 读回行数 / 缺失原因。

**禁止**：默认 build（会漏 prep/auction）；`--all`（会混入无关因子族）；把缺失当 0。

## 报告保存

- 报告**默认对话交付**，不写仓库；只有用户明确要求保存时才写用户指定路径，写前确认。
- 用户指定仓库内路径时提示会影响 git 状态；不自动提交。
