# 数据资产云端同步 M2-M4 现状核对（阶段 1 · 轻量）
> **当前状态（2026-10-25 整仓结构重构 M0–M12）**：本文中的 `dragon_quant/cli_commands.py` / `dragon_quant/review_account/prep.py` 等实现位置已迁移：命令入口在 `dragon_quant/commands/<command>.py`（经 `commands.dispatch`），prep 在 `dragon_quant/review_account/prep/` 包（service/collectors/compute/rebuild），auction 编排在 `dragon_quant/auction/service.py`，assets CLI 装配在 `dragon_quant/assets_sync/cli_service.py`。历史设计正文保留原样；当前结构与命令以 `AGENTS.md`「架构总览」为准。



> 日期：2026-10-22
> 上游基准：`docs/solutions/2026-10-18-assets-cloud-sync-design.md`（v3，历史 review APPROVE）
> 上游调研：`docs/research/2026-10-18-assets-cloud-storage.md`
> 本次任务：M2 数据层 + M3 同步层 + M4 CLI（M5 调度不做）
> 目的：确认设计文档与当前代码/库结构是否仍然吻合，登记偏差，作为阶段 2 方案输入。
> 修订：根据 research-reviewer 第 1 轮（0 CRITICAL / 6 MAJOR / 3 MINOR）补齐证据与偏差登记。

## 1. 核对结论（TL;DR）

设计文档 v3 的**主干假设仍然成立**：远端键布局、manifest 身份口径、增量 state、后端 Strategy 与现有 M1 代码一致。需要处理的偏差集中在**设计成文之后才发生的存储层演进**（`assets.db` 单库化 + `auction_assets.quote_json` 新增）、**任务 AC 对恢复语义的收紧**（`created_at` / 隔离目录），以及**读路径已有的损坏隔离行为**。共登记 6 个偏差（D1-D6）与 7 个设计未固定的实现选择（I1-I7）。门槛 A 仅 R2 通道留证，B2 profile 未配置（需 main/用户豁免，见 §2）。

## 2. 设计 ↔ 代码逐项核对

| 设计条目 | 现状 | 结论 |
|---|---|---|
| §5.1 `blobs/<date>/<source>_<type>.<sha256>.json.gz` + `manifests/<date>.json` | `codec.blob_key()` / `codec.manifest_key()` 已按此实现 | 一致 |
| §5.4 内容哈希 = 未压缩规范化 JSON 的 sha256/md5；传输哈希 = gzip 字节 sha256 | `codec.canonical_json()` / `hashes()` / `compress()` 已实现，gzip `mtime=0` 确定性 | 一致 |
| §5.2 manifest 结构 + `manifest_id` 只含逻辑身份字段 | `codec.manifest_from_entries/to_bytes/from_bytes` 已实现；`ManifestEntry.identity()` 排除 blob 元数据/时间 | 一致（需扩展 `created_at`，见 D6） |
| §5.3 对象元数据契约（blob 8 项 / manifest 2 项） | `codec.blob_metadata()` 已实现 blob 侧；manifest 侧待 M3 写入 | 待实现 |
| §4 后端 Strategy + 分页 LIST + 重试/重签 | `backends/s3.py` 已实现；`LocalDirBackend` 支持 metadata 回读，可做确定性集成测试 | 一致 |
| §7.4 profile 凭据（0600、env 覆盖、`repr=False`） | `credentials.py` 已实现并有单测 | 一致 |
| §5.5 上传单元：prep `(trade_date, asset_type)`；auction 按日聚合 | `assets.db` 两张原始资产表结构与 `UNIQUE` 约束一致 | 一致（payload 需扩展，见 D1） |
| §6.1 push 幂等/收缩保护/提交点 | 未实现 | M3 |
| §6.2 `state.json` 增量水位 | 未实现 | M3 |
| §6.3 pull dry-run/`--apply`/重建/restore | 未实现；`paths.py` **未预建** `tmp/assets_sync` | M3（I3） |
| §6.3 跨库事务（prep.db + dragon.db） | 已被 2026-10-20 存储层重构推翻：两源同库 `assets.db` | **偏差 D4** |
| §6.4 status/verify | 未实现 | M3 |
| §7.2 CLI 五命令 | 仅 `doctor` 已注册（`cli.py` 397-420、`cli_commands._cmd_assets`） | M4 |
| §7.3 退出码 | `doctor` 已按 0/1/2 实现，可作为四命令模板 | 一致 |
| §8 SigV4 | `signer.py` + 回环验签单测已通过 | 一致 |
| 门槛 A（M1.5 阻塞门禁） | **R2 通道已验证**：2026-10-22 复跑 `assets doctor --profile r2 --samples 3`：`ok=true`、`metadata_roundtrip=true`、`success_rate=1.0`、13 次操作、`errors=[]`、`cleanup_errors=[]`；endpoint `https://000a7cf18a589be0a61b96109de2b0ee.r2.cloudflarestorage.com`、bucket `dragon-quant-assets`。**B2 未验证**：`load_config(profile="b2")` 返回「profile 不存在或为空: b2」 | R2 通过；B2 未验证，按任务包以 R2 为 v1 主通道放行（需 main/用户确认豁免） |

## 3. 偏差与必须裁决项

### D1 `auction_assets.quote_json` 晚于设计成文（payload 扩展 + 恢复合并语义）

- 设计 §5.5 的 auction 上传单元是 `{trade_date, items:[{code,name,items}...]}`，§6.3 restore 只更新 `name + items_json`。
- 现状：2026-10-20 存储层重构新增 `auction_assets.quote_json`（`Quote` 快照，含昨收/跌停价/流通市值等评分输入，**不可再生**）。
- 若按原文实现，恢复后的竞价资产会丢 `quote_json`，`auction --recompute` 结果不可重现。
- **处理**：auction shard payload 扩展为逐 code 携带 `quote`，restore 同步写 `quote_json`。
- **合并语义（新增，必须遵守写入侧既有契约）**：`assets_db.save_auction_asset` 用 `quote_json=COALESCE(excluded.quote_json, auction_assets.quote_json)` 保护不可再生行情。restore 必须复用同一语义：**远端 `quote=null` 时保留本地已有 `quote_json`，即使 `--force` 也不得用 NULL 抹掉本地行情**；`items` / `name` 才按 `--force` 覆盖。需有「云端 quote=NULL + 本地有值」的恢复测试。

### D2 AC3「还原 created_at」与设计 §6.3「新插入行取默认值」冲突

- 任务 AC3：`pull --to <scratch>` 恢复后**还原 created_at**，逐键比对 payload 一致。
- 设计 §6.3：restore「不得改变 created_at 语义」——prep 分支只更新 `payload_json`，冲突时不刷新 `created_at`（新插入行仍取默认值）。
- **处理**：插入新行时写入源端 `created_at`；目标库已存在同键行时保留本地 `created_at`（本地权威）。
- **比对口径（新增）**：AC3 的「payload 一致」定义为「解析后语义相等 **且** 内容 `sha256` 相等」；`payload_json` 的 DB 文本与云端 canonical JSON 文本必然不同（`_dumps` 用默认分隔符），**不得做 DB 文本逐字节断言**（设计 §11 已声明）。

### D3 任务 AC3 的 `pull --to <scratch>` 与设计 §7.2 的 `--to <DATE>` 语义冲突

- 设计 CLI：`assets pull --from D --to D`，`--to` 是**日期上界**；恢复目标固定为当前 `DQ_DATA_DIR/assets.db`。
- 任务 AC3 字面：`pull --to <scratch>` 指**隔离目标目录**。
- 二者不能共用同一参数名。**处理**：保留设计已评审的 `--from/--to` 日期语义，新增 `pull --target-dir DIR`（恢复写入 `<DIR>/assets.db`，临时区也落在 `<DIR>/tmp`）。AC3 用 `--from/--to` 加 `--target-dir` 满足。属新增接口，已在阶段 2 方案登记。

### D4 单库化推翻「跨库事务」假设（必须裁决事务边界）

- 设计 §6.3 与 §11 假设 prep（`prep.db`）与 auction（`dragon.db`）分库、无跨库原子性。
- 现状：两者同在 `assets.db`（`storage/assets_db.py` 单一 `ASSETS_DB_PATH`，两张表）。
- **裁决**：restore 仍**逐来源独立事务**（prep 与 auction 各一个 `BEGIN IMMEDIATE`），保留「单来源失败不回滚另一来源、重跑幂等收敛」的语义；不使用跨两表的大事务，避免一个来源的坏行拖垮另一个来源的可恢复数据。
- 设计 §11 的「跨库部分提交」测试改为「同库两源失败隔离」：注入 auction 写入失败 → prep 已提交、auction 回滚 → 重跑收敛。

### D5 auction 读路径已存在的「损坏行吞没」必须被 Adapter 显式拦截

- `assets_db.list_auction_assets` / `get_auction_asset` 对损坏 JSON **不抛异常**：`items` 回落 `[]`、`quote` 回落 `None`，只附 `items_error` / `quote_error`（逐行隔离是既有契约）。
- 若 Adapter 照单全收，会把**有损 payload 上传**，后续 pull 覆盖本地。现有消费范例（`cli_commands.py` 621-627、873-879）是把这两个标志当失败处理。
- **处理**：`AuctionAssetSource.read_date` 遇到任一 `items_error` / `quote_error` → 该 shard 记 `failed`，该日不写 manifest，绝不降级为空 payload。prep 侧 `payload_json` 损坏由存储层直接抛异常（源级失败，见 I4）。

### D6 `created_at` 载体与 manifest 序列化链路当前不支持（必须扩展）

- 现有 `ManifestEntry` 无 `created_at`，`manifest_to_bytes` / `manifest_from_bytes` 只处理固定字段；重建路径（LIST + HEAD）也没有 `created_at` 来源。
- **处理（阶段 2 必须实现）**：
  1. `AssetShard.created_at` 与 `ManifestEntry.created_at`（默认 `""`），**不进入 `manifest_id`**；
  2. `manifest_to_bytes` 写出、`manifest_from_bytes` 保真解析 `created_at`；
  3. blob 元数据增加 `created-at`，`manifest.py` 重建时从元数据读取；元数据缺失时取 `""`，restore 回落到 DB 默认时间（明确降级，不报错）；
  4. **仅 created_at 变化**（重采但内容未变）：manifest_id 相同，跳过 blob 上传，但 GET 远端 manifest 比较 `created_at` 映射，不同则**重写 manifest body**（0 次 blob PUT）。这样 AC3 在「重采后 push → pull」序列下仍能还原最新采集时间，同时不破坏 AC1 的「无变化零上传」。
- **AC3 的 created_at 基准**：与内容版本一起上传的采集时间；测试在 push 后立即 pull，断言相等。

## 4. 设计未固定、需在方案中定稿的选择

| 编号 | 选择点 | 建议 |
|---|---|---|
| I1 | `state.json` 与进程锁路径 | 锁 `<DQ_DATA_DIR>/assets_sync.lock`（设计已定）；state 定 `<DQ_DATA_DIR>/assets_sync/state.json` |
| I2 | 数据层导出/恢复 API 落点 | 新增到 `storage/assets_db.py`（统一持久化门面），`assets_sync/sources.py` 只做 Adapter；新函数支持可选 `db_path` 以服务 `--target-dir` 与测试隔离 |
| I3 | pull 临时目录 | `<DQ_DATA_DIR>`（或 `--target-dir`）下 `tmp/assets_sync/`，首次 `mkdir(parents=True)`；成功后清理、失败保留现场 |
| I4 | `read_date()` 失败隔离与源级失败 | 返回 `ShardBatch(shards, failed)`（**协议偏离，阶段 2 登记**）；`failed` 只承载**逐分片**失败（坏 JSON/NaN/损坏行）。DB 打不开、表不可读等**源级异常直接抛**，由 service 让该日整体失败——不得把源级失败当成「该源无资产」 |
| I5 | created_at 载体 | 见 D6；prep 用 shard/entry 级。**auction 的逐 code 时间已由阶段 2 方案 D1 改为 `ManifestEntry.created_at_map`（不进 payload/内容哈希）**，不要按早期设想的「payload 内字段」实现 |
| I6 | `--all` 语义 | 只表示「忽略 state 水位、重算全部**本地存在**的日期」；不全量 LIST 远端（远端独有日期由 `status` 报告，push 不主动删除） |
| I7 | manifest 重建歧义 | 同日同 `source:type` 多候选 → 默认 `AmbiguousError`；`--pick latest` 才按 `LastModified` 选择并标 `degraded`；重建 manifest 不用于 push 幂等 skip |

## 5. 现状数据与测试基线

- 本地 `assets.db`（只读核对，未写入）：
  - `prep_assets`：2026-10-20、2026-10-21、2026-10-22 各 9 类资产（含 `collection_report`）。
  - `auction_assets`：仅 2026-10-21，3 个 code，`quote_json` 均已填充；盘口明细条数按 code 分别为 68 / 64 / 198。
  - `prep_snapshots` / `auction_snapshots` 不上传（设计非目标）。
- 基线测试：`.venv/bin/python -m pytest tests/ -x -q` → **963 passed, 14 subtests passed**（12.26s，Python 3.14.8）。
- 为执行 AC4 的 pytest 命令，已向项目 `.venv` 安装 **pytest（仅开发工具，不进入运行时依赖）**。
- 运行时依赖仍只有标准库 + `playwright` / `tmdx`；本里程碑**零新增运行时依赖**，SigV4 继续走自研 signer。

## 6. 对阶段 2 的输入（含 AC 落点）

1. 按 D1-D6 修订接口与数据模型；
2. M2 先做 `AssetSource` + `assets_db` 导出/恢复事务 + created_at 保留 + 损坏行拦截；
3. M3 复用 M1 codec/backends，新增 manifest/state/service；
4. M4 CLI 四命令 + 日期规范化 + 统一 JSON 信封 + 退出码；
5. **AC2 落点**：默认 `verify` 以 **HEAD `x-amz-meta-manifest-id` 为锚点**，与 GET body 重算的 `manifest_id`、本地计算的 `manifest_id` 三方比对；仅当 HEAD 元数据缺失时才降级为 body 自洽校验。blob 字节篡改由 `verify --deep` 发现。故障注入用例：① 改 manifest body 字段（不改 id）→ 默认 verify 失败；② **body 与 `manifest_id` 同时改写**→ HEAD 元数据锚点不匹配，默认 verify 失败（退出码 1）；③ 改/截断 blob 字节 → `--deep` 失败（退出码 1）；④ 删除 blob → `--deep` 失败；
6. 集成测试一律 `LocalDirBackend`，真实 R2 冒烟留 main 终审。
