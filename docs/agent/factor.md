# factor — 因子层、财务链路与决策接入

> 触发：改动因子层（注册/构建/查询）、新增或修改因子、财务原始链路与 PIT、矩阵导出/评估、`rank_key` 排序注入、`factor_*` 表时读本文。
> 本文权威：因子层边界与「缺失=无行」语义、PIT 时点、canonical scan、默认启用集与 `--all`、prep/auction 因子族、财务链路与 PIT 三桶、矩阵导出与因子评估口径、决策接入（P2）与排序注入点。
> 上游权威：AGENTS.md（硬约束与项目路由）；设计背景与准入条件见 `archived_md/factor_framework/2026-10-22-factor-framework-design.md`（历史记录）。

## 因子层（P0 / P0.5 / P1 / P1.5 / P2：只读迁移 + 财务链路 + 矩阵导出/评估 + prep/auction 因子化 + 决策接入，默认零影响）

```bash
python -m dragon_quant factor list
python -m dragon_quant factor build --date 20260519
python -m dragon_quant factor build --from 20260501 --to 20260531
python -m dragon_quant factor query --date 20260519 --name five_dim_composite
python -m dragon_quant factor status

# 矩阵导出（P1）：长表 → date × code，CSV/JSON + <out>.meta.json sidecar
python -m dragon_quant factor export --from 20260501 --to 20260531 \
    --name five_dim_composite --name vpa_breakout --out /tmp/matrix.csv
python -m dragon_quant factor export --from 20260501 --to 20260531 \
    --name five_dim_composite --clean --winsorize mad --normalize rank \
    --format json --out /tmp/matrix.json

# 因子评估（P1）：RankIC / ICIR / NW-t / 2-3 分组 / 换手 / 覆盖率 / PIT 口径
python -m dragon_quant factor eval --name five_dim_composite --from 20260501 --to 20260831
python -m dragon_quant factor eval --name fin_roe --from 20260101 --to 20260930 \
    --pit-policy research --groups 3 --json

# 财务原始链路（assets.db 的 fin_* 五表）
python -m dragon_quant factor fin sync --snapshot            # 当日观测（same_day_only）
python -m dragon_quant factor fin sync --backfill --from-period 20250331
python -m dragon_quant factor fin calibrate --sample 20      # 恒等式 + 层级不等式锚定字段
python -m dragon_quant factor fin status

- **边界**：五维评分仍是唯一决策基线。因子层只读复制既有产物（`scan_stocks_v2` / `vpa_analysis`），**不重算**、不改写源表；因子层**不加过滤、不加否决**，唯一允许的写入方向是 `rank_key` 的 tier 内排序分量（P2，单开关 `factor_ranking_enabled`，默认关）。
- **缺失 = 无行**：`is_true_dragon IS NULL`、维度分 NULL、vpa 因子名缺失/重复都不写行，绝不写 0 / 50 / 中性分（与五维异常写 `ABS_NEUTRAL=50` 刻意不同）；源整体不可用（无扫描/读取失败）**不删旧行**，源可用但当日无值才删除该因子当日行。
- **PIT 时点**：每行带 `available_at`（Asia/Shanghai，`YYYY-MM-DDTHH:MM:SS`），由 `infra/factor/engine.resolve_available_at` 唯一实现按 `pit_rule` 分派：`recorded` = `max(phase, created_at)` + 跨日拒绝；`derived` = phase；`asof` = phase + 账本可用性硬门禁（P0.5 财务用）。全程 aware datetime 比较，禁止字符串比较。
- **canonical scan**：同日多扫描按 `created_at DESC, top_n DESC, id DESC` 归并为唯一观测；`factor_values.source_scan_id` 留血缘。
- **主板块快照**：从 `scans.raw_output.ranking` 解析 `primary_sector` / `primary_sector_category`，`category` 如实记录（`hy`/`gn`/`dq`），缺失不猜；本期不给 `scan_stocks` 加列。
- **vpa 跨日回填**：`review/main.py` 用运行当天 K 线写历史 `trade_date` 的行，`created_at.date() != trade_date` 一律整行 skip，计 `skipped_backfilled`（源限制不在本期修复）。
- **CLI 契约**：`--json` 输出稳定顶层键（build: `ok/mode/dates/build_ids/reports/warnings`）；默认模式错误写 stderr + 非零退出（参数错误 exit 2，运行时失败 exit 1）。
- **默认启用集**：`factor build` 不带 `--only` 只构建 `default_enabled=True` 的因子（P0.5 的 6 个 `fin_*`、P1.5 的 15 个 `prep_*`/`auction_*` 全部默认关，必须显式 `--only` 才计算）；显式 `--only` 不受影响。
- **`factor build --all`**：构建**全部注册因子**（含默认关的 `prep_*` / `auction_*` / `fin_*`），`--all` 展开为**显式全量名单**传给 `resolve_specs`；与 `--only` **互斥**（argparse 后校验 → `ValueError` → exit 2），`enabled_only` 只在未给名单时生效；区间 `--all --from/--to` 逐日展开。**触网边界**：只有本地已存在财务原始数据（`storage.finance.has_any_fin_data()` 预检 `fin_observed_snapshots` / `fin_report_records`，任一有行）时才加载 TDX 交易日历做 stale 逾期判定；无财务数据时 fin 因子 fail-closed（`missing_source`）且 **不触网**、`build_date.ok` 不置 false（exit 0）。该收敛同样影响 `--only fin_*`（无数据时不再出现 `stale_calendar_missing`）。
- **prep / auction 因子族（P1.5）**：`builtin/prep.py` 7 个（`prep_verdict` 档位序 strong=3/light=2/ignore=0、`prep_composite`、`prep_high_days`（`(\d+)天(\d+)板` 取板数）、`prep_t_pct`、`prep_t_turnover`、`prep_limit_up_suc_rate`（0-1 比率）、`prep_in_pool_today`）+ `builtin/auction.py` 8 个（`auction_strength` / `auction_is_weak` / `auction_direction` / `auction_rank` + 四维子分），**全部 `default_enabled=False`**、`pit_rule=derived`、`available_phase` 分别为 `prep`（T@16:40）/ `auction`（T@09:25）。源适配在 `infra/factor/engine.py` 的 `_load_prep_source` / `_load_auction_source`（只读 `assets.db`，`create=False`），`builtin/*` 零 I/O、不 import `review_account/*`（含 `review_account/auction/*`）。因子行 `trade_date` = **观测日 T**（不按候选入选日拆行，避免前视），候选自身日期只进 `source_meta_json` 血缘；`source_meta_json` 另含 `snapshot_version` / `snapshot_created_at`（回填重算时 `snapshot_backfilled=true`）。
- **prep/auction 缺失语义**：`unrated` 不写行（≠ ignore=0）；`ignore` 写 0；`facts` 缺失时 facts 族不写；`prep_in_pool_today` 仅 `facts.pool_available is True` 时写行；auction 族公共门槛 `_usable_row` = 无 `error` 键 + `unverified is False`（缺键 fail-closed）+ `reject_reason != "竞价明细缺失"`。真实否决路径只写 `auction_strength=0` / `auction_is_weak=1`，**`auction_direction`、四维子分、`auction_rank` 不写行**（否决分支的 `direction="neutral"` 是占位值）。`rank=None` 只缺 `auction_rank`；子维度稀疏不填 0。`prep_snapshots` 单列 JSON 损坏 → 整族 `missing_source` 不删旧行；`candidates=[]` 仅在 dragons 血缘健康（`window_days>=1`、逐日 `status=ok`、`sentiment.degraded` 不含 dragons 标记）时按真实空 DELETE，否则 `empty_degraded_source` 保留旧行。
- **auction 族当前零产出（预期）**：`review_account/auction/registry.SEMANTICS_VERIFIED=False` 使正常路径恒 `unverified=True`，因子层 fail-closed 不写行；build 报告以 `unverified_rows` 计数（不报 `empty_unexpected`）。语义实测固化后需 `auction --recompute DATE` → `factor build --date DATE --only auction_*` 补历史。竞价空池与命令未运行在库中不可区分（R33），旧行可能滞留，需人工确认后清理。
- **重复计数警告（P1.5，刻意不加互斥检测）**：`auction_*` ↔ A 维（`auction_ranking_enabled`）/竞价否决同源，开 A 时**不要**把任何 `auction_*` 放进模型快照；`auction_strength` = 四个 `auction_dim_*` 的线性组合，只取聚合分**或**子分（推荐子分），不要并存；`auction_is_weak` / `auction_rank` 是 `strength` 的确定性派生，只作诊断；`prep_verdict` ↔ `prep_composite` / P 维单调编码，二选一（推荐 `prep_composite`），生产快照不含 `prep_verdict`；`prep_composite` ↔ `five_dim_composite`（同一 `composite_score` 的中转）需从快照排除；`prep_high_days` ↔ `five_dim_leadership`、`prep_t_pct`/`prep_t_turnover` ↔ 带动性/流动性存在部分重复。**`auction_*` 存在结构性零覆盖**（竞价池取严格 T-1，而候选在自己的因子日 09:25 几乎不在池内），仅用于 `factor eval` / 研究，**不得进入任何生产模型快照**；`prep_*` 决策按 `candidate_trade_date` 读取，值是入选日当天的观测（混合 vintage），不要按决策日截面解释 IC。
- **版本过滤**：`factor query` / `get_series` 在 `--version` 缺省时先按 `factor_registry` 解析默认版本，再由 SQL 按 `(factor_name, factor_version)` 精确过滤；所有输出携带 `resolved_versions`，绝不把同名多版本混成多行/静默取字典序末位。
- **财务链路（P0.5）**：`infra/provider/tdx.py` 新增 `get_finance_info` / `get_financial_files` / `get_financial_records` / `get_financial_file` / `get_xdxr_info`（独立 `TdxClient` 第二连接，transport 失败抛异常，零 `import pandas`）。`infra/factor/finance_store.py` 承载 `fin_observed_snapshots`（日度观测，`updated_date` 回退只刷新 `captured_at`）/ `fin_report_files`（`(period, md5)` 身份）/ `fin_report_records`（**追加式 vintage**，修订新行、缺 code 记 `retracted_at`、空 records 不删行）/ `fin_field_calibrations` / `fin_xdxr_records`。`factor fin sync --snapshot` 仅当日可采；`--backfill` 默认只处理最近 12 个报告期（`--from-period` 显式扩大），按 md5 幂等、md5 变化追加 vintage、新 vintage 可用时刻以首次下载为下界；**值未变的 code 不产新行、也绝不撤销**（撤销仅针对新文件里已不存在的 code）。字段未校准 → 财务因子 fail-closed（`field_map_unverified`）；未校准布局原始 float 以 gzip blob 存 `raw_blob`。
- **财务 PIT（§6.4）**：法定截止日 23:59:59（一季/年报 04-30、半年 08-31、三季 10-31，年报次年）；三桶 `observed`（观测值哈希与首版相等且早于首版写入）/ `statutory_backfilled`（从未观测，研究级近似）/ `revised`（修订，可用时刻 ≥ 首次下载）；`builtin/financial.py` 的 6 因子（bps/roe/net_margin/net_profit_yoy/revenue_yoy/net_assets）按 as-of 逐日展开，严格众数报告期 cohort（非众数不写行 + `cohort_excluded_profile` 含综合分分位/规模代理），分母 ≤0 / 营收=0 / 缺 P-4Q / 负基数均不写行并区分 `invalid_denominator` 与 `missing_field`；`valid_from` 由 build 运行期派生（`fin_earliest_available`）；布局未校准丢弃计入 `layout_unverified`。stale 逾期判定注入真实交易日历（`finance.load_trade_calendar` → `utils.trading.tdx_trade_dates`），缺失时警告 `stale_calendar_missing` 并退化为工作日近似。
- **矩阵导出（P1）**：`factor export` 为派生视图（不写因子表），CSV/JSON + `<out>.meta.json`（`dates`/`codes`/`resolved_versions`/`clean`/`missing_rate_by_factor`/`coverage_by_date`/`build_ids`/`generated_at`/`pipeline_version`）。默认原始值，`--clean` 才清洗；清洗**只在当日截面内**：MAD=`median ± 3×1.4826×MAD`（MAD=0 跳过并记 `winsorize_skipped="mad_zero"`）、quantile 按 `--limits`、rank=`(平均名次/n−0.5)×3.46`（并列取平均）、zscore 标准差 0 跳过。
- **因子评估（P1）**：`factor eval` 主指标 Spearman RankIC（Pearson 附加）、ICIR、`nw_lag=max(horizon−1,0)` 的 Newey-West 修正 t、2/3 分组（`5` 直接 ValueError，某日某组 <3 只跳过）、换手（分组成员变化）、覆盖率、`effective_independent_days≈days/horizon`；样本不足返回 `insufficient_data`。标签从 `available_at` 之后第一个可成交日开盘起算（开盘→开盘），停牌剔除；`data` 以参数注入（不 import review_account）。`1q` 额外输出 `effective_independent_observations`、报告期 block bootstrap t、IC 一阶自相关，日频 naive t 标 `pseudo_independent`；财务额外输出 `by_report_period`/`finance_data_versions`/`pit_basis_mix`/`retracted_count`/`restatement_unverifiable`/`pit_policy_samples`。`--pit-policy strict`（默认）只统计 `observed`/`revised`，`research` 纳入 `statutory_backfilled` 但结论禁止写入模型快照。
- **决策接入（P2）**：`infra/factor/decision.py` 提供 `FactorModel` 协议 + `EqualWeightRankModel` + `annotate_pool`；模型快照存 `factor_model_snapshots`（JSON，禁止 pickle），`factor_model_version=""` 取最新快照，**无快照 → 整日 `degraded(model_snapshot_missing)`，不隐式兜底**。归一分组 = 同一候选入选日 cohort，组内 rank 归一后跨日可比；任一必需因子缺值 → 该候选不得分；可评分 cohort < 3 → 整组缺失并记 `small_cohorts`。
- **算法演进约定（新增/修改选股、打分、排序、买卖规则时必读）**：①动手前先 `python -m dragon_quant factor list` 查注册表，已有因子可复用就复用，不在策略代码里重复造信号；②新信号**优先注册为因子**（`builtin/` 一个 FactorSpec + 纯函数 compute），而不是写死在策略里——注册即获得版本管理、`factor eval` IC 评估与「缺失≠中性」语义；③新因子默认 `default_enabled=False`，先评估证明增量（`factor eval` / `compare_strategies` A/B），再考虑进模型快照；④消费决策只经 `rank_key` 单注入点 + `factor_ranking_enabled` 开关，不在别处读因子表；⑤注意上文的重复计数警告，同一信号只进模型一次。
- **排序注入点**：`review_account/strategy/priority.key_components` 是因子分量的唯一归零/缺失判定处，`rank_key` 只消费。关闭时 `factor_rank=0 / factor_score=0.0 / factor_mode="off"`，排序与旧实现逐位等价；开启时 `(A, P, factor_rank, -factor_score, -composite, -priority, code)`，缺失候选进 `factor_rank=1` 桶排在有分者之后。6 个调用点（engine×2 / strategy×2 / signal_engine / priority 内部）共用同一实现，禁止复制。
- **读取日期与降级**：`market.collect_candidates_ex(factor_loader=None)` 只在开关开启时读取，按**每只候选自己的 `trade_date`** 读因子值（按决策日读会恒缺 T 日盘中值）。快照/读取异常、候选缺日期、覆盖率 `< factor_min_coverage`（默认 0.6）→ 整日 `degraded`，排序完全回退，绝不中断回测/buy；`ctx` 只增 `factor_mode/factor_model/factor_coverage/factor_degraded`，不改 `ranking_mode` 枚举。
- **StrategyConfig**：`factor_ranking_enabled`（默认 False）/ `factor_model_name`（`equal_rank_v1`）/ `factor_model_version`（空 = 最新）/ `factor_min_coverage`（0.6，`from_dict` 校验非法值报错）。
- **组合指标（P2）**：`evaluation.metrics` 只增 `sharpe`（日频权益年化；样本 <2 / std=0 / 非有限 → None，不伪造 0）；`evaluation.industry_exposure` 只统计 `category="hy"`，归属日用 `entry_date` 的 `factor_sector_snapshots`，其余计入 `unattributed_ratio` + `unattributed_reasons`（`non_hy_primary`/`no_snapshot`）。`compare_strategies` 的因子 variant 先检查区间内 build 完成率 ≥ 95%，否则 `insufficient_data`，`trial_count` 照常计入。
- **`factor eval` 退出码**：`insufficient_data` 是有效结论，进程 exit 0（`ok=true`）；只有参数错误 exit 2、运行时失败 exit 1。
- **未实现（P3）**：替换五维评分、行业中性化/风险平价、ML 模型、财务原始资产云端同步。设计背景与准入条件见 `archived_md/factor_framework/2026-10-22-factor-framework-design.md`（历史记录）。

## `factor_*` 表契约

| 表 | 用途 | 关键点 |
|----|------|--------|
| **`factor_*`（assets.db）** | 因子层（P0） | `factor_values` 长表主键 `(trade_date, code, factor_name, factor_version)`，缺失=无行（绝不写 0/中性分）；`factor_registry` 元数据历史快照（`ON CONFLICT DO NOTHING`，重插旧版本不得刷新 `created_at`）；`factor_build_runs` 逐日审计；`factor_sector_snapshots` 主板块快照（`UNIQUE(trade_date, code, category)`，`category` 可能是 `gn`/`dq`，只有 `hy` 是真实行业）；`factor_model_snapshots` 供 P2 使用。M6 起纳入 `infra/assets/sync`（`factor` domain source，行级合并；派生数据可重算但一并备份） |
