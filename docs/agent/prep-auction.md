# prep-auction — 盘后复盘与集合竞价

> 触发：改动 `prep` 复盘（采集/计算/门禁/快照）、`auction` 竞价采集与重算、`prep_*` / `auction_*` 资产与快照口径、情绪温度或候选池判定时读本文。
> 本文权威：prep 两段式与增量采集、`collection_report` 语义、可采性矩阵与时点门禁、复盘窗口与候选池、宏观扰动口径、auction 候选池/回退/输出契约、prep/auction 资产与快照表契约。
> 上游权威：AGENTS.md（硬约束与项目路由）；两段式执行流与消费流图见 [资产架构](../../dragon_quant/infra/assets/ASSETS_ARCHITECTURE.md) §2/§3（本文不复制图）。

## 盘后复盘 `prep`（两段式 + 增量采集 + 独立资产库）

```bash
python -m dragon_quant prep                              # 最新有龙头数据的交易日
python -m dragon_quant prep --date 20260519
python -m dragon_quant prep --date 20260519 --recompute   # 用已存资产重算快照
python -m dragon_quant prep --date 20260519 --force       # 强制重采全部可采资产
python -m dragon_quant prep --json                        # 完整复盘 JSON（默认 4 行摘要）
```
- **两段式 + 默认增量**：采集段按 `ASSET_COLLECTORS` 注册表逐类采集 → 单事务写 `assets.db/prep_assets`；计算段只从 `prep_assets` 读回（快照=资产纯函数）→ 写 `prep_snapshots`。默认先读存量资产 + `collection_report` 做健康判定：**全部健康则零采集**（含 dragons 本地读取）直接重算；否则只补采不健康且可采的 asset_type。`dragons` 重采且候选 **code 集合变化**时级联补采 `fund_flow`/`cninfo_announcements`；`fund_flow`/`cninfo` 另做 code 覆盖 diff（`quotes`/`items` 未覆盖当前龙头并集 codes → 补采）。新增因子 = 注册一行 + 一个 collector，不动流水线骨架。
- **collection_report 语义**：「该日期资产当前最佳已知状态」，**不是**本次运行的遭遇。`save_prep_assets` 只按键合并本次实际执行的 collector 条目（`BEGIN IMMEDIATE` 单事务），零采集时 report 完全不动。补采失败**不覆盖**既有健康状态（保留原条目并写 `last_attempt`）；采集成功但 `empty_suspicious` 仍按新状态覆盖（payload 与状态自洽）。
- **可采性矩阵**：`same_day_only` 资产（`market_breadth`/`fg_boards`/`fund_flow`）仅 `trade_date == today` 可采；历史日缺失 = 永久降级，**不尝试采集、不写 report**，compute 按缺失降级。旧版本遗留的 `unavailable` 若同 payload 的 `as_of==trade_date` 且自检为 `ok`，在下一次普通运行中窄口径修复为 `ok`（不采集、不扩散到 `error`/`empty_suspicious`）。`macro_global` 不在 `same_day_only` 之列（日 K canonical 可历史重建）。
- **`--force`**：重采全部可采资产（历史日 `same_day_only` 仍不采，但窄口径修复照常生效）；**不绕过** 16:40 当日门禁与交易日校验（与 `scan --force` 语义不同）。`--recompute` 零检测零采集（现状不变）。
- **复盘窗口**：含 T 的最近 5 个交易日 `[T-4 … T]`（按交易日历）。窗口日期在**采集段**解析并写入 `dragons` 资产（`window_days` / `window_trade_dates` / `window_source` / `days` 逐日 scan_id/version/count/status），compute 段仍是资产纯函数、`--recompute` 可重放。窗口内缺日跳过并标 `dragons_partial`；日历不足以填满窗口标 `dragons_window_short`；**窗口并集为空**才算 `empty`；并集无行且存在 error 日 → collector 抛错（不把取数故障伪装成无龙头）。**口径落后**（`window_days < 5`，含旧单日资产）标 `dragons_window_stale` 并触发本地重读自动升级。窗口降级只触发 dragons 本地重读（`collection_health`），**不把 dragons 从 usable 中剔除**（`asset_health` 仍判可用，candidates 正常产出）。窗口加宽使新入候选缺 fund_flow/cninfo 覆盖时标 `dragons_window_widened_dependents_stale`（显式降级，不伪造资金数据）。
- **候选池**：窗口真龙并集，同 code 按 `(-composite_score, rank)` 去重、完全同分取较新入选日；每只保留真实 `trade_date`（下游 `candidate_trade_date` 血缘）。`fund_flow` / `cninfo_announcements` codes 取并集（按 code 去重）。THS 归因字段只对 T 日入选候选填充，跨日候选为 `null`；主力净额/公告是 T 日时点信息，对所有候选附加。
- **dragons 读源**：prep 采集器、review-account / buy / sell 回退池、auction legacy 池、prep 默认日期解析全部读 **`assets.db.dragons` 镜像**（`infra/assets/dragons_mirror.py`）。镜像写失败是锁外 best-effort，因此必须**逐日对账**（`stale_dates`，单向下限）：目标日源库有行而镜像缺行 → 先 200ms 短重试一次（并发窗口），仍缺 → `DragonsMirrorStale`；历史日缺行 → 该日 `status=error` + `dragons_mirror_missing_day`，与真实空日区分。`ensure_populated` / 池 loader 对空镜像 fail-fast 并给出回填命令。**显式例外**：`--source v1` 经 `market.candidate_loader` 分派回 `dragon.db.dragons_v1`（镜像只承载 v2）；review 回测 / `dragons` CLI / Web UI 仍读 dragon.db。
- **对账是单向下限**：`stale_dates` 只发现「源有镜像无」；**孤儿行（镜像有、源无）**不会被对账发现，靠 `assets backfill-dragons --dry-run` 的 `orphan_rows` 或 `--prune` 清除。
- **运维契约**：prep 不与 scan/rebuild 并发运行（fan-out 在锁外，主库已提交而镜像未写完的毫秒级窗口）；开启镜像后 T 日 prep 前建议先 `assets backfill-dragons --dry-run`。
- **数据源**（tmdx 优先、THS 次之、**排除东财**）：`dragons`（dragons_v2 按窗口逐日冻结）/ `ths_limit_up_pool`（涨停原因/板型/炸板次数/连板高度，THS dataapi，T 日）/ `ths_block_top`（题材主线，THS dataapi，T 日）/ `market_breadth`（全 A 扫描五合一：红盘率/涨停/炸板/跌停/成交额）/ `fg_boards`（昨日涨停溢价/晋级交叉/龙虎榜名单）/ `fund_flow`（并集候选股 `main_net_amount`）/ `cninfo_announcements`（并集候选公告标记）/ `macro_global`（宏观扰动：日经225期指/A50期指/美债 10Y/黄金）。
- **宏观扰动 `macro_global`**：品种与口径由 `MACRO_SPECS` 声明，逐品种字段 `same_day_close` 决定 canonical：
  - `jp_nikkei`（日经225期指，`INTL_INDEX/NK0Y`）与 `cn_a50`（富时A50期指，`INTL_INDEX/CNY0`）：`same_day_close=True`，取日 K 中**日期 `<= trade_date` 的最后一根**（同日收盘）；priority 只有 `tdx`，**无回退**。
  - `ust10y`（`MACRO_INDICATOR/8_ATY`）与 `gold`（`COMEX_FUTURES/GC00W` → `SH_GOLD/Au99.99`）：保持 `< trade_date`（上一已完成 bar）；回退 Yahoo chart 日 K（best-effort，可能 429）。
  - 夜盘归属下一交易日 bar，故 T 日 bar 一旦发布即定型、T+1 bar 被 `day > trade_date` 排除；发布延迟由 `macro_same_day_missing` 显式标记。逐品种记录 `caliber`（`same_day_daily_bar`/`previous_daily_bar`），**无顶层 caliber**。任一外围指数 `session_kind != "same_day"` 时标 `macro_same_day_missing`（只影响复盘展示，不触发 buy/auction 的补跑提示）。
  - `get_macro_quotes` transport 失败抛异常 → `source_errors`；单品种缺失只降级。宏观**不设** `same_day_only`（日 K 可确定性历史重建），`as_of=trade_date`，不做 `_AS_OF_CHECKED`。期指连续存在换月 roll 跳变，label 固定「期指」，涨跌幅在换月日可能失真；美债为两位小数日频快照，bp 变动标「近似」且只展示、不进仓位公式。腾讯/雪球 provider 的宏观方法保留历史契约但当前规格未路由。
- **逐只参与判定**：`compute_candidates` 为每只候选写 `facts` 块（`in_pool_today` 与入选日解耦、`t_pct`/`t_turnover`/`t_amount`/`open_num`/`high_days`/`limit_up_type`/`limit_up_suc_rate`/`flow_available`/`announcements_available`/`risk_announcement`）；`review_account/verdicts.py` 按有序规则链产出 `strong`（强烈建议）/`light`（轻仓博弈）/`ignore`（忽略）/`unrated`（未评级）与 2-3 行维度明细。忽略原因优先级：数据不足 → HARD 公告 → 高位炸板 → 非涨停+主力流出 → 退出涨停梯队 → 综合分不足 → 兜底；SOFT 公告（减持/质押/担保等）只降档不忽略。verdict **仅展示**，不过滤 review-account 消费的 candidates。
- **快照口径**：`version=0.6`；顶层 `macro`（`available`/`session`/`instruments`/`degraded`/`sources`，`session` ∈ `same_day`/`previous_session`/`mixed`/`null`（外围指数组不可用时为 null），口径血缘在逐品种 `caliber`）；`candidates[].facts/verdict/verdict_label/dimensions/ignore_reason`。`dragon_source` 含 `window_days` / `dates`（逐日血缘）/ `version_mixed`。`window_days` 语义：`0`=dragons 资产不可用（血缘未知）、`1`=旧版单日口径重算、`5`=当前五日窗口。**`--recompute` 回写语义**：先用 `assets.db.dragons` 镜像本地重建五日窗口并回写 dragons 资产（`window_source="local"` + 只增审计键 `rebuilt_at` / `rebuilt_from_window_days`），**不写 collection_report**；旧单日资产（无 `days[]`）不重建，按单日口径重放（`window_days=1`）。资产行 `created_at` 语义 = **该资产行最后刷新时间（采集或本地重建）**。旧宏观资产中的 `us_*` 会被过滤（degraded/sources 同步），缺失的新 key 进 degraded，无 `macro_global` 时标 `macro_global` 降级。
- **CLI 输出**：默认 4 行市场摘要（是否建议买入/仓位/等级/理由，理由内含外围/美债/黄金片段）+ 空行 + 逐只标的清单（✅ strong/light 在前，组内综合分降序；忽略在后每只一句原因；未评级最后）；候选为空显示「无参与复盘标的」。等级 0.8→强烈买入、0.5→一般、0.3→轻仓博弈、0.0→不建议买入、None→不建议买入（数据不足）；降级信息并入理由。`--json` 输出完整 JSON（`--verbose` 已删除，传旧参数由 argparse 以 exit 2 拒绝）；默认模式错误写 stderr 并以非零码退出，`--json` 模式错误保持原 JSON error 到 stdout。
- **时点门禁**：`same_day_only` 资产（`market_breadth`/`fg_boards`/`fund_flow`）仅当日采集，补跑历史日**不采集、不写 report**（compute 按缺失降级）；`macro_global` 不在此列（日 K canonical 可历史重建）。当日运行须 ≥16:40（A50 期指日盘 16:30 收盘 + 缓冲；15:30–16:40 时段硬拒绝）。日期校验用 TDX 指数日K（不依赖雪球 Cookie），「非交易日」与「日历数据源失败」报错严格区分。`_collect_all` 入口对不可采类型直接 `ValueError`，防止误把 T 日数据写到历史日。
- **错误契约**：新增采集 provider 方法在 transport 失败时**抛异常**（存量方法「返回空」契约不变，如 `get_sector_ranking` 用 `_fetch_with_retry(raise_on_exhaust=False)`）；采集段逐 collector 隔离、计算段逐因子隔离，**任何单点失败不中断 prep**，失败原因落 `collection_report` 与 `sentiment.factor_errors`/`degraded`。collector 失败/返回非 dict/分类异常时不写 payload；若存量资产健康则保留其 report 状态并写 `last_attempt`，不因一次补采失败降级快照。依赖 dragons codes 的采集器在 dragons 未就绪时不执行（记 error，避免空 payload 覆盖存量）。
- **情绪口径**：`limit_up_cnt` 主口径为 THS T 日精确值（`limit_up_cnt_ths`），FG T-1 交叉值并列保存（`limit_up_cnt_t1_fg`）且不进公式；THS 缺失时显式回退 FG 并标 `degraded`+`position_input` 血缘。`position_advice` 阈值（80/5/30/10）不变。
- **只观察不进公式**：`broken_rate`/`yesterday_premium_pct`/`promotion_rate`/`max_board_height`/`lhb_cnt` 等 v0.2 因子先落 JSON 供观察与回测，**不改仓位阈值**（未验证参数不推广）。
- `prep --source` 已移除（固定 v2）。

## 集合竞价采集 `auction`

### 集合竞价采集 `auction`
- **候选池**：默认 `--pool prep`，读取**严格 T-1** 的 `prep_snapshots`，取 eligible（真龙 + `min_score`）中 `verdict != ignore` 的 code（strong/light/unrated/缺 verdict 保留；推荐集为空则回退）。与 `buy`/`review-account` 共用 `market.collect_candidates_ex`，保证 code 集合一致。
- **回退**：T-1 快照缺失/读取异常/无资格/推荐集为空 → 回退最近 N 日 `dragons_v2` 并集，输出 `pool_source="legacy"` + `fallback_reason`（`no_snapshot`/`snapshot_read_error`/`empty_snapshot`/`no_eligible`/`all_ignored`/`calendar`）。回退池不再应用 `ignore` 出池；P 按快照 verdict 如实标注。
- **空池**：仅当 legacy 也为空时正常结束（exit 0），不触发行情/盘口请求；输出 ignore 原文样本 + `prep_degraded` 提示。
- **参数**：`--pool {prep,legacy}`（默认 prep）；`--days` 仅在 legacy/回退路径生效（默认 5，显式传入且 prep 成功时输出 `days_ignored`）；`--json` 输出完整 JSON。
- **输出**：默认人读摘要——标题（日期/参与只数/池来源，含回退原因）+ 逐只「排名/代码/名称/等级/强度/方向」（未验证 `?`、否决 `-`，`｜否决：原因｜未验证` 可叠加）+ 汇总（强/中/弱/其他计数、未验证数、采集失败数）；空池/采集层全失败/评分层全失败/全空盘口分别单行提示。JSON 契约（collect 25 个顶层 key、空池 16 个、recompute 15 个）与字段顺序在 `--json` 下逐字段不变。默认模式错误写 stderr + 非零退出；`--json` 错误保持原 JSON 到 stdout，退出码语义不变。
- `--recompute` 不变（零采集，用已存资产重算）；默认人读摘要（标题标「重算」），`--json` 输出完整 JSON。

## prep / auction 资产与快照表契约

| 表 | 用途 | 关键点 |
|----|------|--------|
| `prep_assets`（assets.db） | 复盘原始数据资产 | `prep` 命令采集；`UNIQUE(trade_date, asset_type)`，单事务按键 UPSERT；资产类型 `dragons`/`ths_limit_up_pool`/`ths_block_top`/`market_breadth`/`fg_boards`/`fund_flow`/`cninfo_announcements`/`macro_global`/`collection_report`；`collection_report` 按条合并，语义为「该日期资产当前最佳状态」（未执行的键沿用存量，失败补采不覆盖健康状态）；每日落库=时间换历史 |
| `prep_snapshots`（assets.db） | 盘后复盘计算结果 | 单列 `result_json` 存完整复盘结果（`sentiment` 情绪/建议仓位 + `main_boards` 主线 + `macro` 宏观扰动 + `candidates` 候选池（含 `facts`/`verdict`/`dimensions`/`ignore_reason`） + `dragon_source` 血缘），`UNIQUE(trade_date, source)`，`source` 固定 `v2`；当前 `version=0.6`；加字段=加 JSON key，无需改表 |
| `auction_assets`（assets.db） | 集合竞价原始资产 | `auction` 命令产出；`items_json` 存当日竞价盘口明细（`time`/`price`/`matched`/`unmatched` 数组），`quote_json`（可空）存 `Quote` 行情快照（昨收/跌停价/流通市值等，旧行为 NULL），`UNIQUE(trade_date, code)`；`trade_date` 为竞价所属交易日（=当日）。仅当日 9:25 后可用、盘后仍可拉取、无历史回看 |
| `auction_snapshots`（assets.db） | 集合竞价强度计算结果 | `auction` 采集/`auction --recompute DATE` 写入；单列 `analysis_json` 存 `AuctionVerdict` 序列化结果（`version`/`code`/`is_weak`/`strength`/`direction`/`level`/`dims`/`reject_reason`/`unverified`/`rank`），`UNIQUE(trade_date, code, source)`、`source` 默认 `v2`；同日重跑整日原子替换，空结果不删旧快照。读接口 `assets_db.list_auction_snapshots` / `get_auction_snapshot`（逐行 JSON 损坏隔离）供候选池 A 维度消费 |
