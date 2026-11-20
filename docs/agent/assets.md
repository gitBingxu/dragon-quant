# assets — 数据资产云端同步

> 触发：改动 `assets push/pull/status/verify`、分区模型与水位、manifest、行级合并、恢复/回填、`dragons` 镜像、`assets capture` 持续采集/覆盖、退出码或凭据时读本文。
> 本文权威：同步单元与分区、manifest 与哈希口径、push 幂等水位、pull 行级恢复、恢复与收敛契约、退出码与凭据、`dragons` 镜像表契约、持续采集计划/观测/租约与覆盖契约。
> 上游权威：AGENTS.md（硬约束与项目路由）；组件图与双向同步流程图见 [资产架构](../../dragon_quant/infra/assets/ASSETS_ARCHITECTURE.md) §1/§4（本文不复制图）。

## 数据资产云端同步（`assets`）

```bash
python -m dragon_quant assets doctor --profile r2 --samples 50
python -m dragon_quant assets push [--from D] [--to D] [--all] [--dry-run] [--allow-shrink]
python -m dragon_quant assets pull --from D --to D [--apply] [--force] [--pick latest] [--target-dir DIR]
python -m dragon_quant assets status [--all] [--orphans]
python -m dragon_quant assets verify [--from D --to D | --samples N] [--deep] [--md5]
python -m dragon_quant assets backfill-dragons [--from D] [--to D] [--dry-run] [--prune] [--json]
python -m dragon_quant assets backfill-stock-daily [--dry-run] [--days N] [--codes A,B] [--json]
python -m dragon_quant assets purge-derived [--apply --yes] [--json]
python -m dragon_quant assets capture plan|once|session|coverage|schedule [options]

- 目标（**M6 全表同步**）：把 `assets.db` **全部 19 张表**的行级数据资产备份到 S3 兼容对象存储（Cloudflare R2 主选 / Backblaze B2 备选），粒度到行/身份键，不做整库文件直传；`dragon.db` 不同步，旧 `prep.db` 与 dragon.db 遗留竞价表不自动纳入（需先按 README 手工迁移）。行情缓存 / Cookie 不同步。
- **dragons 镜像同步（`dragons:mirror`）**：date 分区、`volatile=True`（review 回写只 UPDATE 不刷新 `created_at`，水位不前进），push 每次强制重读、内容哈希判等保证无变化时零 blob PUT；`status.summary.tables` 覆盖 19 表，`not_synced` 保持 `{}`。
- **恢复与收敛（`dragons` 专属）**：唯一受支持的恢复路径是 `assets pull --target-dir`（隔离恢复）；若确需就地 pull，收敛命令是 `assets backfill-dragons --prune`（**不是**裸 backfill）。`--prune` 只删「`dragon.db.dragons_v2` 中不存在」的镜像行——源库是权威、dragons 行可重建，不存在误删不可再生资产的风险。`--dry-run` 区分 `missing_rows`（源有镜像无）与 `orphan_rows`（源无镜像有）。主库 `pull --force` 禁令继续适用。
- **同步单元注册表**：`infra/assets/sync/tables.py` 的 `TableSpec` 驱动 17 张新表（`prep_snapshots` / `auction_snapshots` / `dragons` / 5 张 `factor_*` / 5 张 `fin_*` / 4 张持续采集 `capture_*`·`market_*`；`fin_report_files` 拆成元数据 `fin:report_files` 与原始 blob `fin:report_raw` 两个单元）；`prep_assets` / `auction_assets` 两个既有适配器行为零变化。
- **分区模型**：`YYYY-MM-DD`（date，交易日）/ `fin-YYYY-MM-DD`（period，报告期）/ `finraw-YYYY-MM-DD`（period_raw，报告期原始 blob）/ `static-<domain>`（static，维度表：`static-factor` / `static-fin`）。处理顺序固定 `date → period → period_raw → static`。date 分区 manifest 写 **schema 1**（旧客户端可读），其余写 **schema 2**。
- **status / verify**：`status` 逐分区比较本地与云端，默认最近 30 个 date + 全部 period/period_raw/static；新增 `summary.dates_drift`（仅追加式单元 `factor:build_runs` / `fin:observed` / `fin:records` / `fin:xdxr`、本地行数严格更多，`reason=row_count_only`，**弱信号不计失败**）与 `summary.tables` 覆盖图；`not_synced` 恒为 `{}`（**breaking 值变更**）。`verify` 默认以 HEAD `manifest-id` 为锚点 + body 自洽校验，`--deep` 下载解压校验 blob，**不做 drift 豁免**；`--from/--to` 必须成对（单边退出码 2）。`status --orphans` 在远端 manifest 缺失的分区把 `orphan_blobs` 置 `null` 并附 `orphan_reason`。
- 本地是权威副本；云端只作异地备份。资产不可再生（`same_day_only` 与 `auction` 仅当日可取）。
- 云端布局：`blobs/<partition>/<source>_<type>.<content_sha256>.json.gz` + `manifests/<partition>.json`（每分区一份，重采覆盖）。哈希身份用**未压缩规范化 JSON** 的 sha256/md5，压缩字节另算传输哈希；`created_at` / `computed_at` / `calibrated_at` 是旁路字段，**不进入 manifest_id**；`captured_at` / `first_seen_at` / `available_at` 参与 PIT，**必须进内容哈希**。
- 上传单元：prep 按 `(trade_date, asset_type)`；auction 按日聚合为 `{trade_date, items:[{code,name,items,quote}]}`；新表每「分区 × 单元」一个分片，payload = `{schema, table, partition, columns, rows}`（JSON 列以解析后对象入列，`raw_blob` base64）。
- **push 幂等**：`state.json` **schema 2** 按 **(分区, 来源)** 记录水位（`max(clock)|行数|字节数`）与条目键集合；水位一致 + 远端条目集合 ⊇ 上次集合 + `codec==2` → **复用远端条目（0 本地读）**；volatile 单元（`prepsnap` / `auctionsnap` / 全部 `fin`）每次强制重读，用内容哈希精确判定；`--all` 全量重读。`x-amz-meta-manifest-id` 一致 → 零远端 PUT；仅采集时间变化 → 只重写 manifest（0 blob PUT）；严格子集需 `--allow-shrink`；分区本地整块丢失（state 曾记录）报 `local_empty_remote_present`。schema 1 state 在线迁移为 schema 2（`dates` → `partitions`）。
- **pull 恢复**：默认 dry-run；`--apply` 全量下载并校验后才写库。**新表按行级合并**（缺则补、已存在且不同 → conflict，`--force` 才更新语义列并保留本地旁路列）；prep/auction 保持分片级门禁。`fin:report_raw.raw_blob` 走 **NULL-fill**：本地 NULL 直接补（空库 `--target-dir --apply` 无需 `--force`），本地非空且不同才冲突；元数据行缺失 → `deferred`（不失败）。dry-run 对行级合并以 `merge_pending` 标注，**不伪造行级冲突**；人读输出区分「已判定冲突」与「未判定（需 `--apply`）」。`--target-dir` 写入 `<DIR>/assets.db`，不读写主 state。
- **`--from/--to` 语义**：date 分区 = 交易日；period / period_raw 按**报告期末日期**比较（跨年报告期需放宽 `--from`）；static 分区**始终参与**。显式给区间且排除了报告期分区时，人读输出打印一行提示（不失败、不改退出码）。
- **收敛契约**：就地 pull 是 append-only 补缺，不承诺与源端收敛；`status` 对本地行数更多判 `drift`（退出 0），其余差异 `mismatched`；定性用 `verify`；权威恢复走 `--target-dir`。**禁止在主库执行 `pull --force`**（恢复侧不刷新 prep/auction 的 `created_at`，会造成下次 push 水位盲区）。
- 回滚提示：旧版本读 date 分区 manifest 会把新表条目当作「本地缺失」，`status` 持续 `mismatched`；**回滚期间不得用 status 判断健康**（旧版 push 仍受收缩保护）。
- 分片阈值：未压缩 > 16 MiB 记 `shard_large` 告警（仍上传）；> 32 MiB 该分区失败（`shard_too_large`，不写 manifest）。`raw_blob` 独立 `finraw-*` 分区，超限不阻断账本。
- 进程锁：`<DQ_DATA_DIR>/assets_sync.lock`（并发 push/restore 第二个进程退出码 1）。退出码：0 成功/无差异/仅 drift、1 部分失败/收缩被拒/冲突、2 配置/网络/manifest 不可读/参数错误。
- 凭据：`<DQ_DATA_DIR>/secrets/assets_sync.json`（POSIX 0600），`default_profile + profiles{r2,b2}`；环境变量 `DQ_ASSETS_*` 只覆盖当前 profile。凭据不进入仓库、日志、manifest、state。
- **里程碑**：M1 `doctor`、M2 数据层、M3 同步层、M4 CLI、M6 全表同步 + 行级恢复已完成；**M5（launchd / `assets_daily.sh` / 门槛 B）未实现**。门槛 A 的 R2 通道已实测通过，B2 profile 未配置（v1 以 R2 为主通道）。
- 方案与风险记录：`archived_md/assets_sync/2026-10-23-assets-db-full-sync-design.md`（M6 已落地）、`docs/solutions/2026-10-18-assets-cloud-sync-design.md`（M5 待做，未归档）、`archived_md/assets_sync/2026-10-22-assets-sync-m2-m4-implementation.md`、`archived_md/assets_sync/2026-10-18-assets-cloud-storage.md`、`archived_md/assets_sync/2026-10-22-assets-sync-m2-m4-reality-check.md`；配置样例 `docs/assets_sync.example.json`。

## `assets purge-derived`（本地派生清理，不触云端）

- 默认 dry-run（只读计数，不建库）；`--apply --yes` 才执行，缺 `--yes` exit 2。与 push/pull 共用 `<DQ_DATA_DIR>/assets_sync.lock`（锁占用 exit 1）。
- 删除范围：`prep_snapshots`、25 个 prep/prep_review 因子值/注册版本、引用目标名的模型快照；单事务 `BEGIN IMMEDIATE`，事务内重算计数与 dry-run 不一致即回滚（exit 1），提交前复核保护表未变。
- 保护：`prep_assets`（含 collection_report/prev_plan/auction_feedback/t1_intraday）、`auction_assets`/`auction_snapshots`/`auction_*` 因子、非目标因子、`factor_build_runs`；云端/manifest/state **零操作**（不 import sync.service、不调 provider）。
- 回滚：apply 前 `sqlite3 .backup` 或文件副本；主库禁止 `pull --force`；迁移完成前禁止把云端旧派生恢复到主库。
- 重建由用户显式执行（purge 自身不重建）：`prep --recompute` 升序逐日 → `factor build --only <23 名>` → `factor decision-review`。

## `assets backfill-stock-daily`（本地日 K 回填，不触云端）

用于补齐 dashboard「当前 T 日效果」评估所需的本地 `prep_assets.stock_daily`：计划候选出池后本地日 K 停在离池日，评估窗口跨出池即缺 bar。命令只写本地库，不 import 云端同步层。

```bash
python -m dragon_quant assets backfill-stock-daily --dry-run          # 只体检
python -m dragon_quant assets backfill-stock-daily                    # 回填
python -m dragon_quant assets backfill-stock-daily --days 20 --json
python -m dragon_quant assets backfill-stock-daily --codes 600001,000002
```

- **检测口径**：目标集合 = `list_recent_plan_candidate_codes(before=执行日, days=N)` 的 codes 中，本地 `last_bar_date` 缺失（`missing`）或 `< 本地最新 bar 日`（`stale`）的 code。`--days` 默认 10、实现上限 60（逐 code 体检只扫最近 60 个资产日）；「最新 bar 日」取最新 `stock_daily` 资产行内 bar 最大日期（不扫全历史）。`--codes` 给定时跳过自动检测，但仍计算并报告 `missing`/`stale`，全部补采。
- **目标日**：执行日已有 `stock_daily` 行 → 执行日；工作日 → 执行日；周末 → 本地最新 bar 日。**绝不回写更早的历史资产日**。
- **合并与写入**：保留目标行既有 `rows`，新采 code 覆盖，未涉及 code 原样保留；空结果不覆盖、不写空行。既有行的 `requested_codes`/`returned_codes`/`missing_codes`/`coverage`/`review_health`/`as_of` 一律不动，只追加 `backfill` 审计块（时间、窗口、请求/成功/跳过清单）。新建行标 `partial=True` + `backfill`，`asset_health` 识别为 `partial_payload`（下次 prep 重新采集，`--recompute` 不消费）。
- **根数上限与 C 一致**：code ∈ 目标行 `requested_codes`（池作用域）→ 120 根；否则 → 20 根（新建 partial 行无池清单 → 全部 20 根）。
- **输出**：`--json` 到 stdout，人读摘要到 stderr；键序 `ok, command, dry_run, target_date, latest_bar_date, lookback_days, detected, missing, stale, fetched, updated, skipped, failed, warnings, window`。`window = {trade_dates, codes}` 是**自动检测窗口**（`--codes` 时仍照实报告，不代表显式目标集合）。
- **退出码**：0 成功（含 `detected=0`）/ 1 transport 或写库失败 / 2 参数或环境（`assets.db` 不存在、无 provider、`--days` 越界、非法 code）。
- **幂等**：补齐后再次运行 `detected=0`；停牌/退市 code 无新 bar 记 `skipped_no_new_bars`（`skipped` 列表），不写假 K，下次仍会重试。

## `assets capture`（TDX 持续采集与本地留证）

```bash
python -m dragon_quant assets capture plan [--date D] [--codes A,B] [--sectors S1,S2] \
    [--topics T] [--recent-days 5] [--track-days 10] [--no-holdings] [--dry-run] [--json]
python -m dragon_quant assets capture once (--plan-id ID | --date D) [--topics T] \
    [--slot HHMM] [--budget N] [--deadline-minutes N] [--allow-outside-window] [--dry-run] [--json]
python -m dragon_quant assets capture session (--plan-id ID | --date D) [--phase P] \
    [--max-slots N] [--interval-seconds S] [--grace-minutes N] [--dry-run] [--json]
python -m dragon_quant assets capture coverage [--from D] [--to D] [--codes A,B] [--topics T] [--json]
python -m dragon_quant assets capture schedule [--kind launchd|systemd|cron] [--workdir DIR] [--json]
```

- **定位**：TDX 持续采集、持久化、逐日建立自己的竞价/分钟/题材历史是主路线；猫爪/Tushare 只作可选旧历史补源。无付费 key 是正常场景，采集与覆盖检查不依赖任何 key。
- **门面与分层**：`commands/assets.py → data_capture（plan/collect_once/run_session/read_coverage/handle_capture）→ collectors（auction/bars/market/support）→ provider`；collect 编排在 `collect.py`（预算/时窗/取数/提交/汇总），有界会话在 `session_run.py`；原始仓储固定在 `infra/assets/observations/`（schema/store/plans/runs/write/observations/bars 分层，公共入口不变）。`data_capture` 不 import 买卖评分策略，`infra.assets` 不 import 采集器/provider/因子。
- **四张表（assets.db，DDL 在 `infra/assets/schema.py`）**：

  | 表 | 语义 | 不可变/幂等 |
  |----|------|-------------|
  | `capture_plans` | 事前母集：`plan_id`、交易日、`revision`、pool/members/task_config、`content_hash` | 内容相同重试幂等；内容不同只追加 revision（`UNIQUE(trade_date, revision)`），旧版不覆盖 |
  | `capture_runs` | 计划×阶段×时槽运行：状态、起止、deadline、逐主题报告、`lease_owner/lease_token/lease_expires_at` | `UNIQUE(plan_id, phase, slot)` 防重入；租约是**本机协调**字段，不进同步语义列，恢复后不会变回活跃锁 |
  | `market_observations` | 原始观测：类型、source_event_time、received_at/available_at、units、quality、payload、`content_hash` | 同 `semantic_key` + 同内容去重；内容不同追加 revision（`UNIQUE(semantic_key, revision)` + `(semantic_key, content_hash)`） |
  | `market_bar_versions` | 不复权 bar 版本：period/interval/OHLCV/`completeness`/`time_semantics`/`unit_semantics`/`derived` | 身份 = `(market, code, period, adjustment, interval_end)`，内容不同追加 revision；不同 market/source/复权口径 **不共用** revision；五分钟只能由完整一分派生并标 `derived` |
- **计划口径**：最近 N 个真实交易日 `dragons` 记录 + 严格早于计划日的 prep `review_candidates`**全部**（含 position=0/null/被过滤）+ 持仓代码（只读最近一次账户回测的未平仓，`mode=ro`）+ 上一版计划中 `track_until` 未到的待成熟对象。上游已截断的候选按实际读取量登记并写 `pool.gaps`，不宣称全市场覆盖。
- **时窗与补采**：每主题有可采时窗（竞价过程 09:15–09:26、竞价终态 09:26–09:35、分钟 09:30–15:05、日 K 15:05–23:59、行情 09:30–15:00、归属全天）。窗外只允许**当日**补采并标 `quality=same_day_recovered`；历史日期直接 `history_not_recoverable`。09:26 第一次收到的数据只证明收到之后可用，不证明 09:20 已知；竞价末笔虚拟匹配不是 09:25 终态：`auction_final` 观测保留源声明时间（缺失则空）与 `terminal_source`（TDX 为 `last_tick_virtual_match`），证据层只有 `exchange_final` 才可判可用。
- **预算与状态**：`--budget` 按主题顺序分配，超预算即截断范围并记 `budget_exhausted`（状态降为 `partial`）；运行状态 `planned/running/partial/complete/missed/failed/skipped`，`complete` 只说明本计划范围的数据要求达到，不等于全市场覆盖或标签成熟。`session` 发现已过时槽（含宽限）只记 `missed`，不假装采过；机器睡眠/断电造成的漏采不会被定时器补回。
- **真实时钟与原子 fencing（W0 返工）**：`cutoff`（请求开始）与 `received_at`/`available_at`（网络结束后重新读取的真实时刻）分开；续租/落库/收尾都用实际时钟，deadline 在请求前后各检查一次（跨截止的结果落库但标 `late_after_deadline` 且降为 `partial`）。原始数据经 `commit_topic` 在**单个 `BEGIN IMMEDIATE`** 内校验 `run/owner/token/有效期` 后才写入观测与 bar；取数期间被抢占 → 一行不写、整轮 `lease_lost`（已提交批次保留并标 `partial`）。每个采集调用使用唯一 worker 身份（host+pid+thread+uuid），同进程多线程不互相冒充。
- **读写错误语义（W0 返工）**：库不存在 = `missing_database`、库在但采集表缺失 = `uninitialized`（都是合法未初始化，只读路径不建库/建表/迁移）；查询/权限错误抛 `ObservationStoreError`，JSON 损坏抛 `CorruptEvidence`（`reason=corrupt_json`），CLI 以非零退出显式失败——绝不伪装成「没有数据」或空池。
- **版本化校准（W4a）**：`infra/calibration.py` 按 **来源/协议/安装版本/转换代码指纹/品类/周期** 注册校准。采集时把命中的 `calibration_id/protocol/install_version/converter_sha256/calibrated_at` 冻结到 `market_bar_versions.calibration_json`（行内来源绑定）；`market_observations` 同样增加 `calibration_json`（严格证据读取只认行内绑定；quote/auction/sector 未获实证能力保持 unverified）。读取时以**该行绑定**校验，不拿当前机器指纹替历史行背书。旧无绑定行保持 unverified（不覆盖 raw/received_at）。只有绑定匹配且 `calibrated_at <= cutoff` 才可用；未知协议/篡改 hash/升级未重新校准一律拒绝。仅股票日 K/1 分 K（tmdx 3.0.1 / MAC 0x122E / NONE / start）有生产规格；5 分/竞价/quote 保持 unverified。
- **租约/fencing**：`BEGIN IMMEDIATE` 只做短原子抢占/续租/提交，网络请求在事务外；提交时校验 token 与有效期，过期或已被抢占的旧 worker 抛 `LeaseLostError` 并被拒绝；提交成功即清租约并推进 token，重复提交同样被拒。
- **数据质量**：量/额/匹配量单位与 bar 右端时点未独立核实时一律 `semantics_unverified`；日 K 只接受**不复权**（provider 无该能力时记 `unadjusted_daily_unavailable`，绝不拿 QFQ 当执行价证据）；空响应记 `no_data` 而非空集缓存，停牌/未收盘/分页缺失分别留证。规范化 bar 必须 finite、价格 > 0、OHLC 自洽，缺值直接丢弃并记 `invalid_ohlc`（**绝不补 0**）；分钟 bar 必须在连续竞价时段内；当日未收盘日 K 记 `unfinished_daily_bar`、周末数据记 `weekend_bar`，都不标 `final`。`time_semantics`/`unit_semantics` 随 bar 行留证，W1 消费时必须同时满足 row 与校准规格。
- **区间/质量覆盖（W0 返工）**：`coverage` 按**已结束的预期区间**求缺口（1 分 240 根/日，09:31–11:30 与 13:01–15:00），同一 `interval_end` 多 revision 只算一个区间；分别报告 `missing_count`、`unusable_count`（时间/单位未验证或非 final）、`revised_intervals`，附样本；未来时槽/未收盘窗口不算漏采；`topic` 过滤下推到 bar period。`coverage_complete=false` 或任一截断标记都不得当完整。
- **长历史边界（W0 返工）**：观测/bar 覆盖全部走 SQL 聚合 + 显式 `--limit/--offset`（默认 1000 组），返回准确 `totals` 与 `truncated`；`interval_code_limit`（默认 100 code/计划）超出时写 `interval_coverage_truncated`。截断结果附 `coverage_truncated` 缺口，严格研究不得把截断当完整。
- **只读边界**：`coverage`/`schedule` 与全部 `--dry-run` 不构造 provider、不联网、不写业务记录；`plan` 不联网、只读本地输入，但**默认会冻结并写本地计划**（`--dry-run` 才只体检、不冻结；已存在库的读路径仍可能幂等初始化 schema/WAL，不属于业务写入）；`coverage` 在库不存在时返回空结构且不建库；`schedule` 只输出 launchd/systemd/cron 模板（`installed=false`），**不安装**系统定时器（内置模板仅竞价 09:26、不含 `plan`/日期，是骨架）。每日采集时点、时窗与失败恢复建议见 skill `references/schedule.md`。
- **同步**：四表已注册为 `capture:plans|runs|observations|bars`（date 分区，`drift_ok=True`，追加式）；`capture_runs` 的 `lease_*` 列**刻意不同步**。云端备份仍是可选；无云端 key 不影响本地采集。
- **文档归属**：外部数据的降级矩阵、PIT 与账户动作边界见[已归档方案](../../archived_md/review_account/2026-11-18-bs-account-decision.md) §4–§5.4；旧 `auction_assets` 的每日 UPSERT 与快照短路**不承担**连续观测史，本模块不改写旧表。

## `dragons` 镜像表契约

| 表 | 用途 | 关键点 |
|----|------|--------|
| `dragons`（assets.db） | dragons_v2 全量镜像 | 列 schema 与 `dragons_v2` **完全一致**（含 `id`/`source`）；`UNIQUE(trade_date, code)` + 3 索引；权威仍是 dragon.db，存储门面**锁外 best-effort 双写**（失败只警告、可 `assets backfill-dragons` 自愈），供 prep / review-account / buy / sell / auction 的候选池读取；`volatile=True` 纳入 `infra/assets/sync`（`dragons:mirror`，date 分区） |
