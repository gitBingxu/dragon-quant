# Changelog

记录 dragon-quant 的主要功能、修复与发布变更，沿用 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 分类与 [Semantic Versioning](https://semver.org/spec/v2.0.0.html) 版本格式。

## 记录口径

- 主分支为 `main`，本仓库没有 `master`；MR 按 `git log --first-parent --merges main` 核对，内容取各次合并相对第一父提交的净变更。
- 版本标题日期采用对应 Git 版本提交的北京时间；MR 合入日期另外注明。功能先合入、随后升版本时，归入包含该变更的版本，同时保留每次 MR 的记录。
- 本文依据 Git 历史与标签整理，不代表已经核验 PyPI 或 GitHub Release 的实际发布时间；历史条目描述当时行为，不代表当前版本仍保留该功能。
- 截至主分支 `aafe8e0`，覆盖 18 次 MR 合入（编号为 1～5、8～18、20～21）。`v0.2.3` 标签不在 main 历史中，不列作主分支发布；尚未合入 main 的分支提交不计入已发布版本。
- `0.1.0` 无对应 Git 标签，保留首版日期并按早期提交校正功能归属；后续版本均注明标签与版本提交。

## [Unreleased]

### Added
- `dashboard` 命令 + P0 五页（总览 / 扫描 / 因子 / 复盘 / 账户）与只读 FastAPI 后端。
- `[web]` 可选 extra（fastapi + uvicorn）；首次调用 `dashboard` 交互式安装，非 TTY / 失败降级为手动指令。

### Changed
- `web_ui` 后端由 stdlib `http.server` 重写为 FastAPI；`web_ui.start_server` 降级为 deprecated 薄壳，`review --ui` / `review-account --ui` 仅保留为弃用别名。
- 前端新增极简 pathname 路由与 AppShell，`vite base` 改为 `/` 以支持 `/scans/<id>` 深链。

### Fixed
- E2E `test_g2_version_matches_package_version` 改为从 `dragon_quant._version` 读取版本，不再硬编码。

## [0.7.4] - 2026-10-30

> 草稿：覆盖 README 展示修正；合入 main 并完成版本提交后补记版本依据。

### Fixed
- 修复 PyPI 项目页无法显示 `scan` / `review` 截图的问题，图片引用改为与项目图标一致的 GitHub Raw 绝对地址。

### Docs
- 将「快速使用」移至「稳定性说明」之后，并同步调整 README 目录顺序。

## [0.7.3] - 2026-10-30

> 草稿：覆盖 `v0.7.2`（`e22d783`）至 `95e4580`，包含 MR #31～#33 与 `feat/auction-op` 后续提交；合入 main 并完成版本提交后补记版本依据。

### Added
- **review 双口径回测与面板筛选**：保留宽松最低价口径，新增严格开盘价口径及 `dragon_review_strict` 侧表，支持 `review --recompute-all` 历史双口径回填；回测面板同时展示双胜率，收益、回撤、状态筛选、排序、表格与指标卡片可统一切换严格/宽松口径，且卡片与筛选后的列表保持同源。
- **prep 六维决策与次日策略**：快照升级至 `version=0.8`，新增 `review_candidates` 六维评分、闸门、仓位与 `strategy_context` 明日操盘契约；支持 T+1 计划反馈、盘中冻结刻度对照和结果归因。`review_candidates` 成为唯一权威，`candidates[]` 仅保留同结果兼容投影。
- **auction 强度分析 v2 与盘中预案**：围绕 09:20–09:25 重建五维评分，分离强度、风险事件、数据质量与模型验证状态；新增 `auction_sc_v1` 条件化观察动作、17 个竞价因子及完整证据/配置血缘，缺失数据不再以 0 或中性分冒充有效结果。
- **决策结果回测**：新增只读 `factor decision-review --source prep|auction`，按决策分组统计 1/3/5 日扣费净收益、方向命中、覆盖率与分位指标，保留逐样本账本、版本过滤、剔除原因和小样本质量提示，不要求预先执行 `factor build`。
- **派生数据安全清理**：新增 `assets purge-derived`，默认 dry-run，`--apply --yes` 后在单事务内清理 prep 快照及相关派生因子/模型快照；支持按日期升序重放 prep、重建因子并复核决策结果。

### Changed
- **auction 重复执行改为快照优先**：当日已有可用快照时直接返回只读结果，零采集、零评分、零业务写入；`--force` 用于强制重采，`--recompute` 用于离线重算，两者互斥，损坏快照按行隔离并支持回退。
- **prep 下游候选语义统一**：`auction` / `buy` / `review-account` 直接消费 `eligible ∧ position_pct > 0` 的复盘候选；有效决策空池不再回落旧算法，旧 `prep_composite` / `prep_verdict` 因子退役。
- **项目结构按业务与基建边界重组**：收敛 `scan` / `review` / `review_account` / `vpa` 功能入口，provider、storage、factor、assets 统一迁入 `infra`，CLI 经 `commands` 分发；Python 测试迁入包内，建立覆盖 53 个 parser 节点、15 个顶层命令的全量离线 E2E 与真实数据隔离门禁。
- **README 面向人类读者精简**：突出多维量化龙头识别定位、稳定性、scan/review 截图与快速使用；完整命令参数改由 CLI `-h` 和模块文档承担。

### Fixed
- 修正 auction 正式开盘价、虚拟匹配量、未匹配方向与换手单位口径，避免错误行情语义进入评分。
- review 取消默认 20 个交易日回测上限，并修复默认 Web UI 端口占用时的启动回退。
- 清理现有检查范围内的类型错误，补齐架构、文档与离线行为回归门禁。

### Docs
- `AGENTS.md` 拆分为根约束与按需加载的模块文档，统一父级工作流来源；归档本轮架构、prep、auction、review 与决策回测方案。

## [0.7.2] - 2026-10-24

> 草稿：覆盖 `feat/skills` 分支的 `d71abcb` 与 `96dadc8`；合入 main 后补记 MR 编号。

### Added
- **Agent Skill 包（issue #29）**：新增单主 skill `skills/dragon-quant/`（`SKILL.md` + `references/{commands,outputs,playbooks,operations,safety}.md`）与根目录零运行时依赖安装器 `dragon-quant-skills`（`bin/` + `lib/`，Node ≥22.20）。`npx dragon-quant-skills install|doctor|uninstall` 默认安装到项目 `.claude/skills/dragon-quant`，`--global` 才写用户目录；manifest + 内容哈希做幂等/升级/冲突/卸载保护，`--force` 先备份。安装器与 Python CLI 诊断解耦：CLI 缺失只警告，`doctor` 才失败。skill 只做路由/预检/解读/异常/写操作门禁，不复制评分权重、策略参数或表结构；安全矩阵覆盖全部 15 个顶层命令。新增 `test/*.test.mjs` 与 `tests/test_skill_{package,content,routes}.py`，含 `npm pack` 白名单与本地 tarball E2E。npm 包已发布，官方 registry 与 npmmirror 的仓库外 `npx` dry-run 均已实测；通用 `npx skills add` 与 Windows 路径未实测。

### Docs
- **main–execute 自审工作流**：`AGENTS.md` 引入两角色阶段门禁（阶段 0 → 1 → 2 → 3 → 3.5），明确任务包、单次验收、self-review 门禁、节点报告与快速通道；新增对应方案文档。

## [0.7.1] - 2026-10-24

> 草稿：覆盖 `v0.7.0`（`f051fbd`）之后 `feat/factor-framework` 分支的 9 个提交（`4d58519..80668c7`）。因子层与资产同步均为**默认零影响**，升级后 `scan`/`review` 行为与 0.7.0 等价；但因 `prep` 窗口与候选池读源变更，历史回测结果不可直接与 0.7.0 比较。

### Added
- **量化因子框架（P0–P2）**：新增 `factors/` 包与 `factor` CLI 全家桶（`build`/`list`/`query`/`status`/`export`/`eval`/`fin`）。P0 以只读方式迁移五维 7 + vpa 5 共 12 因子到 `assets.db` 的 `factor_values` 长表（主键 `(trade_date, code, factor_name, factor_version)`，缺失即无行，绝不写 0/中性分），带内容哈希版本、`canonical_scan` 同日归并与 `recorded`/`derived`/`asof` 三套 PIT 语义；P0.5 打通 TDX 财务链路（`providers/tdx.py` 财务接口、`fin_*` 五表追加式 vintage 账本、法定截止日三桶 PIT、6 个财务因子默认关且 fail-closed 校准）；P1 提供 `factor export`（`date × code` 矩阵 + `.meta.json` sidecar、当日截面清洗）与 `factor eval`（RankIC/ICIR/Newey-West/分组/换手/覆盖率，`--pit-policy strict|research`）；P2 加入决策接入 `factor_ranking_enabled`（默认关，关闭时排序逐位等价）与 `EqualWeightRankModel` 模型快照、`sharpe_ratio`、`industry_exposure`、`compare_strategies` 因子 A/B 门禁。
- **prep / auction 因子化（P1.5）**：新增 prep 族 7 因子（verdict/composite/high_days/t_pct/t_turnover/limit_up_suc_rate/in_pool_today）与 auction 族 8 因子（strength/is_weak/direction/rank + 四维子分），全部 `default_enabled=False`、`pit_rule=derived`；因子注册表 18 → 33。`unverified`/明细缺失/真实否决路径按哨兵语义不写行，避免把未验证数据当有效信号。
- **`assets.db` 全表云端同步（M6）**：同步范围从 prep/auction 扩展到 `assets.db` 全部 15 张表（含 5 张 `factor_*` 与 5 张 `fin_*`），由 `assets_sync/tables.py` 的 `TableSpec` 注册表驱动 13 张表、14 个同步单元（`fin_report_files` 拆为元数据与原始 blob 两个单元）；新增 `date` / `fin-YYYY-MM-DD`（period）/ `finraw-*`（period_raw）/ `static-<domain>` 四类分区；`pull` 对新表改为行级合并（缺则补、冲突需 `--force`），`fin:report_raw.raw_blob` 走 NULL-fill；`status` 新增 `dates_drift` 弱信号与 `tables` 覆盖图。
- **`dragons` 镜像与 5 日复盘窗口**：`assets.db` 新增 `dragons` 表（与 `dragons_v2` 同构），写门面采用「锁内主写 dragon.db → 锁外 best-effort 镜像」，失败只告警不回滚主写；新增 `assets backfill-dragons [--from/--to/--dry-run/--prune/--json]` 幂等回填与逐日对账（`stale_dates` 单向下限）；`prep` 复盘窗口由 3 个交易日扩为 5 个，快照升至 `version=0.6`，旧资产按 `dragons_window_stale` 自动升级。
- **`factor build --all`**：一次性构建全部注册因子（含默认关因子），与 `--only` 互斥（exit 2）；无本地财务数据时跳过 TDX 日历加载，保持零触网。
- **测试隔离基建**：新增 `tests/conftest.py`（pytest 启动时把 `DQ_DATA_DIR` 重定向到临时目录）与 `tests/support.patch_storage_dbs()`（同时隔离 dragon.db 与 assets.db 连接），并补 `tests/test_isolation.py` 回归覆盖。

### Changed
- **候选池读源切换到 `assets.db.dragons` 镜像**：`prep` 采集器、`review-account` / `buy` / `sell` 回退池、`auction` legacy 池、`prep` 默认日期解析统一读镜像；`--source v1` 例外仍回源库，`review` 回测 / `dragons` CLI / Web UI 继续读 dragon.db。
- **回测基线变更**：`prep` 窗口 3 → 5 个交易日，`candidate_lookback_days` 默认值同步 3 → 5，历史 run 与新基线**不可直接比较**；复现旧基线需显式传 `candidate_lookback_days=3`（prep 命中路径不受该参数影响）。
- **`assets status` 输出语义**（脚本侧 breaking）：`not_synced` 恒为 `{}`（原按来源列出未同步项），新增 `summary.dates_drift` 与 `summary.tables`。
- **`assets push` 状态文件升级**：`state.json` 由 schema 1 迁至 schema 2（按 `(分区, 来源)` 记录水位与条目集合），旧状态在线迁移；`date` 分区 manifest 仍写 schema 1，其余分区写 schema 2。
- **`factor build` 默认集合**：不带 `--only` 时只构建 `default_enabled=True` 的因子，默认关因子（全部 `fin_*` / `prep_*` / `auction_*`）必须显式指定。
- **依赖约定**：`AGENTS.md` 解除「仅标准库」约束，改为「许可证 MIT 兼容 + 活跃维护 + 文档同步」三条底线（因子层零新增运行时依赖，pandas 仍由 tmdx 传递）。

### Fixed
- `scan` 交易日判定改为 TDX 上证指数（`SH000001`）优先、雪球降级兜底，消除每次扫描固定两次访问雪球导致的 Cookie 过期告警；全链路失败时补 stderr 提示，不再静默回退自然日。
- 单测写穿真实 `assets.db`：`save_dragons` 新增镜像 fan-out 后，只 patch `db._connect` 的用例把脏数据写进开发者真实库（清理 16 行孤儿并回填 285 行）；现已通过双连接隔离 + 进程级 `DQ_DATA_DIR` 重定向根治，并补回归测试。

### Docs
- `README.md` 增加居中图标与 6 枚 shields 徽章（PyPI 版本、Python `>=3.10`、License、下载量、Stars、稳定度），新增「成熟度声明」（当前仅 `scan` 与 `review` 稳定，其余功能处于探索验证阶段）；文档相对链接改为 GitHub 绝对地址，修复 PyPI 渲染无法打开的问题，评分规范链接同步到归档路径。
- 归档因子框架调研/方案、`assets.db` 全表同步方案、dragons 镜像与 5 日窗口方案；`AGENTS.md` 增补「算法演进约定」段（新增/修改选股与买卖规则前先查因子注册表）。

### 升级提示
- 首次升级到 0.7.1 后**必须执行一次** `python -m dragon_quant assets backfill-dragons`：`prep` / `review-account` / `buy` / `sell` / `auction` 已改读 `assets.db.dragons` 镜像，镜像缺行会触发 `DragonsMirrorStale` / `DragonsMirrorEmpty`。可先跑 `--dry-run` 体检（`missing_rows` / `orphan_rows`）。
- 就地 `pull` 后如需收敛，用 `assets backfill-dragons --prune`，不要在镜像上手工删行。
- 因子层保持默认关闭：不显式设置 `factor_ranking_enabled=True` 时排序与 0.7.0 逐位等价；`factor_values` 需自行 `factor build` 才有数据。

## [0.7.0] - 2026-10-22

> 草稿：覆盖 v0.6.2（`a94a907`）之后 `feat/reviewa-account-op` 与 `feat/assets-sync-m2` 分支的全部变更（提交 `3610e98`…`533df8d`）。按记录口径，合入 main 并完成版本提交后补记日期与 MR 编号。

### Added
- **盘后复盘 `prep`**：两段式架构（采集段按注册表逐类采集原始资产 → `assets.db/prep_assets`，计算段为资产纯函数 → `prep_snapshots`），默认增量语义（全健康零采集 / 失败补采 diff / `collection_report` 逐条合并），`--recompute` 用已存资产离线重算、`--force` 重采全部可采资产；复盘窗口为含 T 的近 3 个交易日龙头并集；资产覆盖 THS 涨停原因/题材主线、市场宽度五合一、昨日涨停溢价/晋级交叉、主力净额、巨潮公告、宏观扰动（日经225期指/富时A50 同日口径 + 美债10Y/黄金 T-1，TDX 扩展行情主链路 + Yahoo 回退）；逐只参与判定 `strong/light/ignore/unrated` + 维度明细；CLI 默认输出 4 行买入摘要（是否建议买入/仓位/等级/理由）+ 逐只标的清单；当日运行门禁 ≥16:40（A50 日盘收盘 + 缓冲），历史日 `same_day_only` 资产不补采按缺失降级。
- **集合竞价 `auction`**：采集 T-1 prep 推荐标的（strong/light；推荐集为空/快照缺失自动回退最近 3 日龙头并集，输出 `fallback_reason`）的当日竞价盘口与行情快照，落 `auction_assets`；四维加权强度评分（方向/供需/量价/末段抢筹 + 否决闸门）写 `auction_snapshots`；`--recompute DATE` 零触网重算；仅当日 9:25 后可采、无历史回看。
- **数据资产云端同步 `assets`**：`doctor`（门槛 A：PUT/GET/HEAD/LIST/DELETE + `x-amz-meta-*` 元数据回环）+ `push`/`pull`/`status`/`verify`（S3 兼容 R2/B2，自研 SigV4 签名，零新增运行时依赖）；云端布局 `blobs/<date>/...json.gz` + 每日 manifest（覆盖写、哈希身份为未压缩规范化 JSON）；`state.json` 增量水位 + 跨平台进程锁使 push 幂等（无变化零上传，仅采集时间变化只重写 manifest）；pull 默认 dry-run、内容冲突默认只报告不覆盖（`--force` 才覆盖、`--target-dir` 隔离恢复、新插入行还原源端 `created_at`、auction 远端 quote 为 null 不抹掉本地值）；凭据 `<DQ_DATA_DIR>/secrets/assets_sync.json`（0600）+ `DQ_ASSETS_*` 环境变量覆盖，凭据不进仓库/日志/manifest/state。M5（launchd 定时调度）尚未实现。
- **买入链路增强**：候选池由 prep 快照驱动（`verdict=ignore` 出池，快照缺失/推荐集为空回退 3 日真龙并集）；买入 P/A 三值优先级排序（tier → 综合分 → 买点得分 → 代码）与竞价否决（`auction_ranking_enabled`/`auction_veto_enabled`，均默认关、可按买点豁免）；`buy` 首行输出建议仓位（仅参考不参与下单），`--json` 输出四类买点逐条诊断 `checks`（passed/failed/pending/window_closed/unavailable/skipped）；`buy`/`sell` 新增 `--force` 放行午间休市与已收盘时段回放（09:30 前仍拒绝，不伪造收盘价成交，落库标记 `"forced": true`）。
- **策略与风控**：择时与仓位（复盘情绪 → sizing/禁买、组合风控日级熔断/净值回撤降仓/连亏降仓、`soft_time_stop`/`hard_time_stop` 时间止损、分级/ATR 移动止盈——除龙回头外均默认关，`sell_rule_toggles` 逐条开关供消融）；龙回头买点默认开启（显式 `false` 可复现旧基线）；新增首日止损减半 `first_day_stop`（默认关：盘中触及 -3.5% 先卖半仓、灾难线整仓优先、与次日涨停半仓共用唯一半仓腿守卫）。
- **review-account 评估**：已平仓持仓清仓后 5 日窗口指标（`post_exit_max_return`/`post_exit_max_drawdown`，事后评估不参与决策，可越出回测区间，旧记录为 NULL 不回溯）；买点统计 `entry_reason_stats`（四类买点 + 未知 + 合计：笔数/胜率/平均每笔收益/累计贡献），`GET /api/account/positions` 返回 `{data, stats}`，前端新增「买点统计」卡片与清仓窗口两列。
- **scan**：板块口径改为「行业 + 概念 + 地区」三维融合总榜 Top8（TDX `HY`/`GN`/`DQ`，THS 回退同支持三类；领跌 Top20 仍按行业），板块身份 `(source, category, code)`、缓存键按 `source:category:code` 命名空间隔离。
- **数据分级**：历史交易日按 code 三态判定（完整 48 根 5 分 K 严格盘中撮合 / 仅日K整日兜底 / 停牌冻结估值），结果带 `data_quality`、`suspended_codes` 等标记；兜底成交价保守近似。

### Changed
- 最低 Python 版本由 `>=3.9` 提升为 `>=3.10`（`requires-python` 与 `classifiers` 同步，新增 3.13/3.14 声明）。`tmdx` 随之变为无条件默认依赖，移除 `python_version >= '3.10'` 环境标记、空 `[tdx]` extra 及 `warn_if_unavailable()` 中 3.9 分支。Python 3.9 用户不再能安装新版本，将停留在 0.6.2（该版本仍含升级提示）。
- 存储层统一重构：旧 `prep.db` 并入 `assets.db` 并新建 `auction_assets`；数据资产（prep/auction 原始资产 + 算法快照）与 dragon.db 物理隔离，避免被 `storage clear --all` 连带清理；旧 prep.db 与 dragon.db 遗留竞价表保留但不再读写、不自动迁移。
- `buy`/`sell` 执行层完全对齐 review-account（保本线/半仓/影子风控/数据分级），提取 `apply_sell_fill`/`track_peak`/`notional_quantity` 共享原语，两引擎不复制规则；`SignalEngine` 卖出数量按回测首仓同口径冻结，`break_even_price` 与回测精确一致。
- `auction`/`prep` 输出开关统一为 `--json`（移除 `--verbose`，传旧参数由 argparse 以 exit 2 拒绝）；默认模式错误写 stderr 并非零退出。
- `buy` 信号记账改 buy-all（不再择优取第一只），排序只影响 pending 顺序；峰值跨日写回 `buy_sell_signals` 供移动止盈/保本判定。

### Fixed
- 修复 review-account 回测停牌标的缺日K崩溃（停牌按 code 冻结估值、不评估买卖，结果带 `suspended_codes`）。
- 修正 prep 维度碰撞后的 `degraded`/`sources` 归因并加固 THS 回退解析。
- prep 增量提示补充复盘日期与锚定原因说明。

### Docs
- 归档 15 份已消费调研/方案文档至 `archived_md/`（prep/scan/live_trade/auction/cli/assets_sync/review_account 分域），索引登记原路径；资产同步主设计文档因 M5 未实现暂不归档。

## [0.6.2] - 2026-10-13

### Changed
- `tmdx` 从可选依赖（`[tdx]` extra）改回**默认依赖**：`pip install dragon-quant` 在 Python ≥3.10 时自动安装（环境标记 `python_version >= '3.10'`）；Python 3.9 因 tmdx 自身要求被跳过，包仍可正常安装。同时新增运行时控制台提示：缺失 tmdx 时每次 CLI 命令（除 -h/--help/-v/--version）打印建议升级 Python（后续 review-account / buy / sell 将依赖 tmdx），并保留 `[tdx]` 空 extra 作向后兼容别名。

## [0.6.1] - 2026-10-13

### Fixed
- `tmdx` 从必装依赖改为可选依赖（`pip install dragon-quant[tdx]`，仅 Python≥3.10 可用），`requires-python` 由 `>=3.10` 放宽回 `>=3.9`：代码本身在缺失 `tmdx` 时已优雅回退老链路，硬性捆绑会阻断 Python 3.9 环境安装 v0.6.0。

## [0.6.0] - 2026-10-10

版本依据：`v0.6.0` → `159d81c`。

### Added
- 新增通达信 TDX 首选数据链路：`TdxProvider` 基于 `tmdx` 包（通达信 TCP 协议），无 Cookie、无反爬，覆盖板块排行、成分股、K 线、分时与五档行情；主链路失败自动回退老链路（板块链「链级一致」，个股链「endpoint 级」）。
- `tmdx` 设为必装依赖（Python ≥3.10）；指数映射 `SH000001` → TDX `999999`，`000001` 仍为平安银行。
- 新增 `dragons` 命令：从 `dragons_v2` 导出某交易日扫描物化的龙头 JSON，支持 `--date`（`YYYYMMDD` / `YYYY-MM-DD` / `YYYY/MM/DD`，默认最新有数据交易日）与 `--true-only`（仅真龙）；无数据输出 `error` 并以非零码退出。

### Changed
- 接入 pyright + ruff 工具链（类型检查 0 错误、ruff E/F 0 错误），全仓类型收窄与死代码清理。
- 发布流程：`publish.sh` 新增第 7 步，自动从 `CHANGELOG.md` 提取当前版本段并创建 GitHub Release。

### Docs
- `CHANGELOG.md` 补全 0.5.2 / 0.5.3 条目；`pyproject.toml` 增加 `Changelog` 链接指向 GitHub Releases。

## [0.5.3] - 2026-10-09

版本依据：`v0.5.3` → `2413d9d`。

合入记录：MR #21（2026-10-09，`aafe8e0`，`refactor`）。

### Added
- `cookie-fetch` 首次运行自动下载 chromium 内核（走国内镜像），并提供友好兜底提示。（MR #21）
- 补齐 `buy/sell` 与 `review-account` 编排层集成测试。（MR #21）

### Changed
- 移除东财 Provider，数据源收敛为通达信 TDX / 同花顺 / 雪球 / 腾讯；拆分 `db`、`cli` 巨型模块。（MR #21）

### Fixed
- 修复 `buy` 命令未按 code 去重的问题。（MR #21）
- 修复 `cookie-fetch` 缺少 playwright 时友好提示而非抛裸 traceback。（MR #21）
- 修复 Windows 兼容：文件写入补齐 UTF-8 编码，同花顺去除外部 curl 依赖。（MR #21）
- 同花顺取数失败日志去噪，按类别汇总而非逐条刷屏。（MR #21）

### Docs
- README 增加章节目录，快速开始补充雪球 Cookie 获取说明。（MR #21）

## [0.5.2] - 2026-10-07

版本依据：`v0.5.2` → `a26382c`。

合入记录：MR #20（2026-10-07，`e1c4401`，`feat/score-op`）。

### Added
- `buy/sell` 改为纯信号记账，复用 `review_account` 的买卖策略与成交函数（`evaluate_buy`/`evaluate_sell`/`fill_price`/`fee`），不再维护独立模拟账户；`SignalEngine` 复刻两段式决策与峰值跟踪。（MR #20）
- 新增可选「入场质量硬门槛」，默认关闭。（MR #20）
- 未触发买点时给出具体标的与逐条原因；优化买入选股逻辑并友好化 `buy/sell` 时段提示。（MR #20）
- 结构导航由 CODEMAP 切换为 codebase-memory 代码图索引。（MR #20）

### Changed
- `review-account` 与 `buy/sell` 统一到共享交易引擎，去除重复的买卖/成交规则。（MR #20）

### Fixed
- `review-account` 缺失分时数据时按日 K 兜底，恢复多月回测能力。（MR #20）
- 降低资金承接评分饱和并增强事件区分度；修正五维评分偏差并贯通真龙入选约束。（MR #20）

### Docs
- README 添加免责声明；补全主分支历次合并与版本变更记录（CHANGELOG）。（MR #20）

## [0.5.1] - 2026-09-07

版本依据：`v0.5.1` → `6bbaf58`。

合入记录：MR #18（2026-09-07，`4cde291`，`feat/review-refactor`）。

### Added
- 新增 `review-account` 账户级模拟回测，按现金、持仓、交割单和真实交易日推进，保存批次、每日快照、交易、已平仓持仓及决策时间线。（MR #18）
- 新增独立 `/account` 面板，展示持仓、交割单、权益/收益曲线、上证基准、持仓收益汇总和分页；买卖记录保留决策原因与单笔实现收益。（MR #18）
- 账户面板支持填写名称、日期范围和初始资金生成回测批次，提供记录删除、子表清理及首次使用空态引导。（MR #18）
- 新增“分歧买龙”买点：连续缩量一字板首次断板后，用开盘后五分钟 K 线确认承接；盘中数据不足则跳过。（MR #18）
- 新增 `buy`、`sell`、`account` 实盘辅助命令，复用账户回测策略，维护纸上账户与独立持仓/交割单表；支持日期覆盖和严格 T+1。（MR #18）
- 增加龙头数据 SQL 导出快照，便于本地数据备份与复现。（MR #18）

### Changed
- 账户回测支持多持仓、半仓止盈和卖出次日再使用释放现金开仓；候选取近三个交易日真龙池并集，按代码去重并保留更优排名。（MR #18）
- 开盘买点保留贴近 MA5 承接、突破前高弱转强；默认成交额门槛调整为 2 亿，候选综合分门槛调整为 50，分歧买点使用独立的承接确认条件。（MR #18）
- 卖出规则增加首个可卖日更紧的止损、移动止盈、弱势收盘容忍带、完整成本线保本及高开未封板的盘中窗口清仓。（MR #18）

### Fixed
- 回测按真实交易日而非数据库已有记录日期推进，缺候选记录时不回退更早日期开仓。（MR #18）
- 保本价计入买入费用、卖出费用和滑点；避免仅凭日线高低点虚构先盈利后回落的顺序，硬止损保持优先。（MR #18）
- 回测排除尚未收盘的当日数据，区间末未平仓持仓使用最后可得收盘价估值；雪球日 K 回看跨度随请求天数扩大。（MR #18）

### Docs
- 补全账户回测、实盘辅助命令、策略说明和代码地图，并在 README 增加免责声明。（MR #18）

## [0.4.2] - 2026-07-31

版本依据：`v0.4.2` → `18d3f42`。

合入记录：
- MR #16（2026-07-31，`e399865`，`fix/help`）：CLI 帮助与评分器主流程切换。
- MR #17（2026-07-31，`c576b40`，`fix/help`）：版本更新为 0.4.2，无额外业务功能。

### Added
- 增加 Linux 风格的 CLI 帮助提示，统一 `-h`、命令说明、参数分组与使用示例。（MR #16）

### Changed
- `scan` 默认且固定使用五维“识别真龙”评分器；`scorers_v2/` 统一更名为 `scorers/`，`scan_v2` 保留为隐藏兼容入口。（MR #16）
- 主流程、回测和日志默认使用 `*_v2`；保留物理表名与扫描 ID 格式，旧 `*_v1` 仅供显式 `--source v1` 查询。（MR #16）
- 移除旧四维评分器、旧编排分支与废弃的子进程分析入口，统一报告、测试和文档。（MR #16）

## [0.4.1] - 2026-06-28

版本依据：`v0.4.1` → `4669912`。

合入记录：
- MR #14（2026-06-28，`4124a4c`，`fix/score_version`）：评分体系分表、扫描诊断和 Provider 缓存。
- MR #15（2026-06-28，`b114d81`，`fix/score_version`）：版本更新为 0.4.1，无额外业务功能。

### Added
- 扫描元信息、明细、日志和龙头分别采用 `*_v1` / `*_v2` 物理表，使用 `source` 路由与带版本前缀的扫描 ID，避免两套体系互相覆盖。（MR #14）
- `dragons_v2` 持久化真龙标记；`review`、`logs` 和 Web UI 支持按评分体系查询、切换及展示。（MR #14）
- 增加按交易日保存的 Provider JSON 磁盘缓存及 dataclass 还原；新增 `--no-cache` 强制刷新行情缓存，与 `--force` 的结果缓存语义分离。（MR #14）
- 各扫描阶段按板块展示候选明细，按数据源和接口聚合失败提示，区分完整与部分加载成功。（MR #14）

### Changed
- 移除早期 `scorer_version` 并集合并方式，改为 v1/v2 数据、排名、回测状态及五日去重各自隔离。（MR #14）
- 删除评分链路未消费的领跌板块成分股请求；缓存命中跳过限流器，减少重复访问同花顺。（MR #14）

## [0.4.0] - 2026-06-22

版本依据：`v0.4.0` → `d5d3b55`。

合入记录：MR #13（2026-06-22，`f4f966f`，`feat/op-alg`）。

### Added
- 新增 `scorers_v2/` 五维评分：带动性 30%、领涨性 25%、抗跌性 15%、流动性 20%、资金承接 10%，采用四大特征门槛与加权综合判定。（MR #13）
- 新增 `scan_v2` 命令运行五维评分，当时 `scan` 仍保留旧四维路径；增加 CLI 版本参数。（MR #13）
- 新增板块当日原始一分钟分时、近十日历史五分钟 K 线及腾讯收盘盘口字段，为时序比较、封单质量和资金承接提供数据。（MR #13）
- 新增板块黑名单表及 `blacklist` 管理命令；龙头记录当时通过 `scorer_version` 保存与合并评分器版本。（MR #13）

### Changed
- 板块数据由同花顺概念板块切换为行业板块；五维候选从领涨 Top5 行业的当日涨停股中筛选。（MR #13）
- 对全部候选评分，移除固定 Top25 截断；`Candidate.fived_pct` 保存五日涨幅，报告补充封板、带动、跳水和承接事件证据。（MR #13）
- 资金承接强度改为目标拉升与出逃规模同向加分，封单使用腾讯买一量并与同源成交量比较。（MR #13）

### Fixed
- 同花顺排行改用 `zdf` 字段、多页抓取后本地排序并增加 403 退避，修复涨跌排行与分页失效。（MR #13）
- 成分股使用非 AJAX 路径翻页；同日重复入选不再被跨日五日去重条件拦截。（MR #13）

### Docs
- 增加 `/codemap` skill 及根目录、Provider、评分器、日志代码地图；更新 README、AGENTS 与文档同步规范。（MR #13）

## [0.3.1] - 2026-06-17

版本依据：`v0.3.1` → `5fb3be8`。

合入记录：MR #12（2026-06-17，`e1ba718`，`fix/cookie`）。

### Added
- 新增同花顺 Provider，提供当时的概念板块排行、成分股和板块分时聚合五分钟 K 线。（MR #12）
- 增加 CODEOWNERS，main 合入需要代码所有者审批。（MR #12）

### Changed
- 板块数据主链路从东财切换为同花顺；东财 Provider 保留备用，默认不参与扫描。（MR #12）
- Cookie 自动刷新默认仅处理雪球，东财需显式指定；移除不再依赖的 `fix-api` 命令及 API 模板配置模块。（MR #12）

### Fixed
- 改进 Cookie 获取，东财请求按 CDN 节点轮询并通过 DoH 扩充地址池，过滤空响应和网关错误，全节点失效时快速终止。（MR #12）

## [0.3.0] - 2026-06-12

版本依据：`v0.3.0` → `720f91a`。

合入记录：MR #11（2026-06-12，`078ef37`，`feat/review-refactor`）。

### Changed
- Review Web UI 使用 Vite、React、TypeScript 和 Mantine 重构，增加暗色主题、折叠筛选区、日期选择器及版本范围筛选。（MR #11）
- 版本范围按语义化数字和输入精度比较；表格支持红涨绿跌展示并调整买入日列位置。（MR #11）
- 构建产物提交至 `web_ui/dist` 随包分发，Python 标准库服务器托管静态资源，运行期不依赖 Node。（MR #11）

### Fixed
- 静态资源服务增加路径穿越防护，保留原有回测数据和汇总 API。（MR #11）

## [0.2.4] - 2026-06-11

版本依据：`v0.2.4` → `2dd0bf4`。

合入记录：MR #10（2026-06-11，`5292bd2`，`feat/bs-advice`）。

### Added
- 新增独立量价分析模块 `vpa/` 与 `vpa --code` 命令，输出量价健康度、方向信号及判断依据。（MR #10）
- 内置量额灵敏度、趋势量价验证、突破放量验证、量价背离四个插件式因子。（MR #10）
- Review 回测追加个股量价分析，结果写入独立 `vpa_analysis` 表；支持指定数据源、历史天数及 `--no-save`。（MR #10）

### Docs
- 同步量价分析用法、模块结构、数据表与开发说明。（MR #10）

## [0.2.2] - 2026-06-11

版本依据：`v0.2.2` → `750d82f`。

合入记录：MR #9（2026-06-11，`861e778`，`fix/east-cookie`）。

### Added
- 新增 `data cookie-set`，可手动设置东财 push2、push2his 和雪球 Cookie。（MR #9）

### Changed
- 东财请求重构为 curl 主通道与浏览器兜底，改进请求参数、动态 token 获取和重试；push2/push2his Cookie 分域保存。（MR #9）
- 限流支持按 Provider 配置随机延迟，东财接口串行访问；板块五分钟 K 拉取范围调整为领涨 8 个、领跌 20 个。（MR #9）

### Fixed
- 修复 Cookie 刷新遗漏 push2his 域的问题，改进验证完成检测和刷新后的 Cookie 获取。（MR #9）

## [0.2.1] - 2026-06-09

版本依据：`v0.2.1` → `f43f4f7`。

合入记录：MR #8（2026-06-09，`cb98051`，`release-202606280`）。

### Added
- 新增 API 请求模板本地持久化及 `fix-api` 浏览器辅助修复命令，支持模板验证、保存与请求失败回退。（MR #8）
- 扫描保留同日多次记录；`--force` 支持替换同日同 Top N 的旧扫描贡献，并按当日扫描并集重建龙头池。（MR #8）

### Changed
- 限流串行粒度收紧为 Provider 级，避免同数据源不同接口并发触发限制。（MR #8）
- 扩大资金承接历史观察和领跌板块范围，使用同窗口或前移窗口识别虹吸，并补充时序、回撤约束。（MR #8）
- Review 胜率改为已完成回测中“最大收益 >0 且最大回撤 >−5%”的占比，并补充说明。（MR #8）

### Fixed
- 修复抗跌性 K 线排序、日期对齐、前收盘与次日反弹取值，以及连续跳水段错配。（MR #8）
- 龙头写入改用 UPSERT，避免覆盖已有回测字段；改进五日去重统计。（MR #8）
- 修复东财板块成分股请求参数与稳定性、浏览器响应捕获及验证码检测复用问题。（MR #8）
- 修复错误日志重复，统一资金承接降级字段与报告文案。（MR #8）

## [0.2.0] - 2026-06-02

版本依据：`v0.2.0` → `2d4d5f4`（功能合入后升版本）。

合入记录：MR #5（2026-06-02，`2b5b463`，`feat/ui`）。

### Added
- 新增首版 Review Web UI、回测结果表格与汇总统计，提供 `review --ui`、`--ui-only` 及端口选择。（MR #5）

### Fixed
- 回撤统计改为买入日至最大收益出现日的窗口，避免把峰值后的走势计入该次收益对应的回撤。（MR #5）

### Docs
- 更新回测统计口径、历史成绩示例、UI 用法与模块说明。（MR #5）

## [0.1.11] - 2026-06-01

版本依据：`v0.1.11` → `c89fe26`（功能合入后升版本）。

合入记录：MR #4（2026-06-01，`de36db9`，`fix/storage`）。

### Fixed
- 修复龙头入库五日去重对排名提升的处理：同股近期已入选时，新排名更优允许更新，否则跳过，并分别统计更新与跳过数量。（MR #4）

## [0.1.10] - 2026-06-01

版本依据：`v0.1.10` → `ed5a44d`（功能合入后升版本）。

合入记录：MR #3（2026-06-01，`1344725`，`feat/review-report`）。

### Added
- 增加回测结果展示与最大收益对应的持有交易日数 `max_return_hold_days`，数据库查询和输出携带完整回测字段。（MR #3）

### Changed
- Review 日 K 使用不复权价格，改进回测参数及进度结果输出。（MR #3）

### Fixed
- 修复 `review --force` 未绕过回测状态过滤的问题，允许已处理记录重新计算。（MR #3）

## [0.1.9] - 2026-05-22

版本依据：`v0.1.9` → `c91f50d`。

合入记录：MR #2（2026-05-22，`431fdc9`，`feat/get-past-record`）。

### Added
- `scan --date YYYYMMDD` 支持查询历史扫描结果；新增按日期列出扫描记录的数据库接口。（MR #2）
- 指定 Top N 与历史记录不匹配时，提示该日期可用的 Top N 选项。（MR #2）

## [0.1.8] - 2026-05-21

版本依据：`v0.1.8` → `a6e8f36`（功能合入后升版本）。

合入记录：MR #1（2026-05-21，`1018171`，`feat/review-module`）。另含主分支此前于 2026-05-17 合入的 SQLite / review 基础提交 `f9ce2c8`、`f35f50f`、`ef098db`。

### Added
- 完成 SQLite 扫描与龙头持久化、扫描缓存读取、盘中执行拦截及回测状态保存。（MR #1 与前述主分支基础提交）
- 完成 `review` 命令和交易日历辅助逻辑，支持指定日期、Top N 与批量回测，并补充 14 个回测测试。（MR #1）
- 龙头记录新增 `version` 字段，`_version.py` 集中管理版本；发布脚本支持加密凭据与自动同步版本号。（MR #1）

### Changed
- 回测买入日按入选后的首个非一字板日（`high != low`）定位，以当日最低价买入；自动回测筛选约 5～20 交易日内的 pending 记录。（MR #1）
- 改进东财请求的 urllib/curl、TLS 和 DNS 多节点处理，共享限流通道并移除无效回退路径。（MR #1）

### Fixed
- 修复数据库使用问题、东财空响应重试和验证轮询阻塞，增加请求失败原因输出。（MR #1 与前述主分支基础提交）
- 修复动态 token 获取失败时未初始化变量及空响应未提示的问题；发布脚本不再要求预先手动修改版本号。（MR #1）

## [0.1.7] - 2026-05-16

版本依据：`v0.1.7` → `74c567d`；主分支直接提交 `f80bba2`，无 MR。

### Changed
- 补全腾讯请求头及 Referer，候选阶段的板块成分股请求接入限流器，串行队列增加调用间隔。
- 东财动态提取前端 token，并保留获取失败时的默认值；移除当时无效的 Playwright 请求降级路径。

## [0.1.6] - 2026-05-16

版本依据：`v0.1.6` → `e5477d2`；主分支直接提交 `375c090`、`1c4eca6`、`6f87f21`，无 MR。

### Added
- 候选筛选新增北交所（8/92 开头）过滤，`StockInfo` 增加 `five_day_return`。
- 增加覆盖评分器、编排、缓存、Provider、日志和存储的单元测试，当时共 140 个用例。

### Changed
- Phase B 按五日累计涨幅而非当日涨跌幅筛选；Phase C 改为连板优先、概念数次之。

### Fixed
- 修复连板计算中窗口过窄与过早截断导致最高连板数偏低的问题。

### Docs
- 新增 Keep a Changelog 格式的更新日志。

## [0.1.5] - 2026-05-13

版本依据：`v0.1.5` → `05a2069`；主分支直接提交，无 MR。

### Added
- 新增 `publish.sh` 一键发布脚本，包含版本校验、构建及上传流程。

### Changed
- 当时东财 push2/push2his 请求跳过无效的 urllib 尝试，直接使用 Playwright，减少失败重试耗时。

### Docs
- 评分器技术方案合并入技术方案文档。

## [0.1.4] - 2026-05-13

版本依据：`v0.1.4` → `3fcc69c`；主分支直接提交，无 MR。

### Fixed
- 修复 CLI `cookie-status`、`cookie-fetch`、`batch-quote` 因连字符与下划线命名不匹配而未执行 handler 的问题。

## [0.1.3] - 2026-05-13

版本依据：`v0.1.3` → `ecbb1c2`；主分支直接提交，无 MR。

### Changed
- `top_n` 仅控制输出范围，当时固定对前 25 只候选做四维评分。

## [0.1.2] - 2026-05-13

版本依据：`v0.1.2` → `2957a24`；主分支直接提交，无 MR。

### Fixed
- 领涨性 deviation 使用与板块排名一致的东财数据，避免腾讯与东财快照不一致造成假跑输。

## [0.1.1] - 2026-05-13

版本依据：`v0.1.1` → `64c9c7e`；主分支直接提交，无 MR。

### Added
- 增加 Programmatic API、原子数据查询能力、日志和存储 CLI、Cookie 刷新能力及 Agent 集成示例。
- 补齐全链路结构化日志、统一数据目录及扫描结果持久化。

### Fixed
- 修复领涨性 `_lead_lag_score` 调用 `_bar_return` 参数错误造成评分全部异常兜底的问题。
- 修复 Playwright 线程安全、CDF 与抗跌评分函数问题，过滤统计类概念板块并调整报告模板。
- license 改用 SPDX 字符串格式。

## [0.1.0] - 2026-05-10

早期首版记录，无 `v0.1.0` 标签；核心实现见 `d7914a0`、`7284612`、`5bbf58c`、`1e9eefe`。

### Added
- 建立东财、雪球、腾讯三个 Provider、RateLimiter、DataCache 和 Orchestrator 扫描架构。
- 实现带动性 35%、抗跌性 15%、领涨性 25%、资金承接 25% 的四维评分体系。
- 增加结构化日志、自然语言报告、CLI 和 Cookie 管理基础能力。

### Fixed
- 雪球 K 线查询由 `type=before` 改为 `type=after`，修复数据截断。
- 封板时间改为当日最早接近涨停价的分钟，修正报告中“开盘即涨停”的不准确表述。
