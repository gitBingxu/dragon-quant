# assets — 数据资产云端同步

> 触发：改动 `assets push/pull/status/verify`、分区模型与水位、manifest、行级合并、恢复/回填、`dragons` 镜像、退出码或凭据时读本文。
> 本文权威：同步单元与分区、manifest 与哈希口径、push 幂等水位、pull 行级恢复、恢复与收敛契约、退出码与凭据、`dragons` 镜像表契约。
> 上游权威：AGENTS.md（硬约束与工作流）；组件图与双向同步流程图见 [资产架构](../../dragon_quant/infra/assets/ASSETS_ARCHITECTURE.md) §1/§4（本文不复制图）。

## 数据资产云端同步（`assets`）

```bash
python -m dragon_quant assets doctor --profile r2 --samples 50
python -m dragon_quant assets push [--from D] [--to D] [--all] [--dry-run] [--allow-shrink]
python -m dragon_quant assets pull --from D --to D [--apply] [--force] [--pick latest] [--target-dir DIR]
python -m dragon_quant assets status [--all] [--orphans]
python -m dragon_quant assets verify [--from D --to D | --samples N] [--deep] [--md5]

- 目标（**M6 全表同步**）：把 `assets.db` **全部 15 张表**的行级数据资产备份到 S3 兼容对象存储（Cloudflare R2 主选 / Backblaze B2 备选），粒度到行/身份键，不做整库文件直传；`dragon.db` 不同步，旧 `prep.db` 与 dragon.db 遗留竞价表不自动纳入（需先按 README 手工迁移）。行情缓存 / Cookie 不同步。
- **dragons 镜像同步（`dragons:mirror`）**：date 分区、`volatile=True`（review 回写只 UPDATE 不刷新 `created_at`，水位不前进），push 每次强制重读、内容哈希判等保证无变化时零 blob PUT；`status.summary.tables` 覆盖 15 表，`not_synced` 保持 `{}`。
- **恢复与收敛（`dragons` 专属）**：唯一受支持的恢复路径是 `assets pull --target-dir`（隔离恢复）；若确需就地 pull，收敛命令是 `assets backfill-dragons --prune`（**不是**裸 backfill）。`--prune` 只删「`dragon.db.dragons_v2` 中不存在」的镜像行——源库是权威、dragons 行可重建，不存在误删不可再生资产的风险。`--dry-run` 区分 `missing_rows`（源有镜像无）与 `orphan_rows`（源无镜像有）。主库 `pull --force` 禁令继续适用。
- **同步单元注册表**：`infra/assets/sync/tables.py` 的 `TableSpec` 驱动 13 张新表（`prep_snapshots` / `auction_snapshots` / `dragons` / 5 张 `factor_*` / 5 张 `fin_*`；`fin_report_files` 拆成元数据 `fin:report_files` 与原始 blob `fin:report_raw` 两个单元）；`prep_assets` / `auction_assets` 两个既有适配器行为零变化。
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

## `dragons` 镜像表契约

| 表 | 用途 | 关键点 |
|----|------|--------|
| `dragons`（assets.db） | dragons_v2 全量镜像 | 列 schema 与 `dragons_v2` **完全一致**（含 `id`/`source`）；`UNIQUE(trade_date, code)` + 3 索引；权威仍是 dragon.db，存储门面**锁外 best-effort 双写**（失败只警告、可 `assets backfill-dragons` 自愈），供 prep / review-account / buy / sell / auction 的候选池读取；`volatile=True` 纳入 `infra/assets/sync`（`dragons:mirror`，date 分区，第 15 表） |
