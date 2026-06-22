# dragon_quant/logging 代码地图

> 范围：结构化扫描日志、日志查询 API、v1/v2 自然语言报告生成。  
> 入口文件：`dragon_quant/logging/__init__.py:1` 对外导出 `ScanLogger` / `LogEntry` / `ReportBuilder`。

## 一、目录职责

| 文件 | 职责 | 何时被调用 |
| --- | --- | --- |
| `logger.py` | 单次扫描内存日志引擎：写 phase/api/scorer/error，聚合摘要，导出 dict/JSONL | `orchestrator.scan()` 创建 `ScanLogger` 后贯穿 Phase A→F 使用 |
| `reporter.py` | 将评分结果 `dimensions` 与日志摘要转为人类可读报告；同时兼容 v1 fewshot 与 v2 五维报告 | Phase F 为每只股票生成 `report_text`，并写汇总报告文件 |
| `query.py` | 面向 CLI/Agent 的 SQLite 日志查询封装：tail/query/clear/list/summary | `dragon-quant logs ...` 子命令调用 |
| `__init__.py` | logging 包轻量导出 | 供 `from dragon_quant.logging import ...` 使用 |

## 二、核心调用链

### 1. 实时扫描日志写入链路

```text
orchestrator.scan()
  ├─ 创建 logger = ScanLogger()                         orchestrator.py:352
  ├─ 注入 providers = create_providers(logger=logger)     orchestrator.py:354
  ├─ Phase A 记录板块排行 logger.phase("A", ...)          orchestrator.py:394
  ├─ Phase B/C/D/F 继续记录阶段日志                       orchestrator.py:495 / :530 / :592 / :653
  ├─ _score_one / _score_one_v2 写 scorer 维度日志         orchestrator.py:163 / :198
  ├─ providers 写 api 日志                                 providers/ths.py:290 等
  └─ Phase F 持久化 logger.to_dicts() → db.save_scan_logs  orchestrator.py:747
```

关键语义：
- `ScanLogger` 是**单次扫描内存态聚合器**，不是 SQLite DAO；SQLite 写入在 `storage/db.py` 完成。
- provider API 统计依赖 `api:{provider}:{endpoint}` category 约定；评分日志依赖 `scorer:{dim}` category 约定。
- 打分异常也按 `scorer:{dim}` category 写 error，便于按维度过滤：`orchestrator.py:166`。

### 2. 报告生成链路

```text
Phase F results 排序
  ├─ reporter = ReportBuilder(logger)                    orchestrator.py:674
  ├─ v2: build_stock_report_v2(...) → r["report_text"]    orchestrator.py:677
  ├─ v1: build_stock_report(...) → r["report_text"]       orchestrator.py:687
  ├─ top_n 拼接 output["report_text"]                    orchestrator.py:701
  └─ 写 scan_report_*.txt：summary + 详细 report_text      orchestrator.py:757
```

关键语义：
- `ReportBuilder` 不重新打分、不查网络；只消费 `dimensions`、`primary_sector_name`、`logger.summary()` 等已生成数据。
- v1 报告走 fewshot 风格 `_format_*`；v2 报告走 `_v2_*` 单行证据链，两条链路并存，字段结构不同。

### 3. CLI 日志查询链路

```text
dragon-quant logs tail/query/clear/list/summary
  ├─ cli._cmd_logs(...)                                   cli.py:113
  ├─ query.tail_logs / query.query_logs / query.log_summary query.py:36 / :51 / :94
  └─ storage.db get_scan_logs / log_summary               storage/db.py:1177 / :1244
```

关键语义：
- `query.py` 只做轻封装和日期 → scan_id 解析；真正的 SQL 条件与 JSON 反序列化在 `storage/db.py:1177`。
- 当前注释虽提到 JSONL，但线上查询源已经是 SQLite `scan_logs` 表：`query.py:4`。

## 三、关键导出与语义

### `logger.py`

| 导出 | 位置 | 语义 / 依赖 |
| --- | --- | --- |
| `LogEntry` | `logger.py:22` | 单条内存日志记录；`data` 放可 JSON 序列化的附加字段，最终会经 `to_dicts()` 写入 SQLite。 |
| `ScanLogger` | `logger.py:32` | 单次 scan 生命周期日志容器；内部 `_entries` 由 `threading.Lock` 保护，适配 Phase D 并发加载时 provider 日志写入。 |
| `ScanLogger._log()` | `logger.py:42` | 所有日志写入的底层入口；统一补 `timestamp/category/level/message/code/data`。 |
| `ScanLogger.phase()` | `logger.py:55` | 写 `phase:{name}` 日志；`summary()` 依赖该前缀聚合 Phase 状态。 |
| `ScanLogger.api()` | `logger.py:58` | 写 `api:{provider}:{endpoint}` 日志；`api_stats()` 与 SQLite `log_summary()` 都依赖 `ok/elapsed_ms` 字段。 |
| `ScanLogger.scorer()` | `logger.py:67` | 写 `scorer:{dim}` 评分日志；`score/weight` 是固定字段，其余 scorer details 会被展平进 `data`。 |
| `ScanLogger.query()` | `logger.py:86` | 内存查询，支持 category 前缀匹配、level、code、dim；不同于 `query.py` 的 SQLite 查询。 |
| `ScanLogger.report_context()` | `logger.py:127` | 从内存日志重构单股 `dimensions/errors/warnings`；用于 agent/debug 场景，不是 Phase F 主报告路径。 |
| `ScanLogger.summary()` | `logger.py:150` | 汇总 elapsed、phase、scorer 计数、api 统计、error_count；`ReportBuilder.build_summary_report*()` 读取它。 |
| `ScanLogger.to_dicts()` | `logger.py:182` | 内存日志 → dict 列表；`db.save_scan_logs()` 只接受这种结构。 |

### `reporter.py`

| 导出 | 位置 | 语义 / 依赖 |
| --- | --- | --- |
| `ReportBuilder` | `reporter.py:11` | 报告生成器，持有 `ScanLogger` 以获取 summary；单股报告主要消费传入的 `dimensions`。 |
| `build_stock_report()` | `reporter.py:17` | v1 单股报告入口；按 drive/anti_drop/leadership/absorption 依次拼 fewshot 段落。 |
| `_format_drive()` | `reporter.py:72` | v1 带动性文案，读取旧 scorer 的 `best_day_detail`、`voice_raw/follow_raw/board_detail`。 |
| `_format_anti_drop()` | `reporter.py:164` | v1 抗跌性文案，读取 `plunge_days/day_details/consecutive_plunge_bonus`。 |
| `_format_absorption()` | `reporter.py:248` | v1 资金承接文案，兼容 `fallback_reason` 与早期 `reason` 字段；`all_events` 控制最多两段事件叙述。 |
| `build_summary_report()` | `reporter.py:324` | v1 汇总排名表；依赖 `logger.summary()` 的 elapsed/error_count。 |
| `build_stock_report_v2()` | `reporter.py:365` | v2 单股报告入口；包含真龙/非真龙判定与一票否决原因，逐维调用 `_v2_*`。 |
| `_v2_drive()` | `reporter.py:401` | v2 带动性证据链；消费 `early.seal_time/bid1_volume`、`lead_events/follow_events`、`voice`。 |
| `_v2_anti()` | `reporter.py:443` | v2 抗跌性证据链；分别格式化大盘和主板块 `deepest_event/dip_events`。 |
| `_v2_abs()` | `reporter.py:463` | v2 资金承接证据链；消费 `best_event/all_events[0]` 的 `dive_time/rally_time/fleeing_sectors`。 |
| `build_summary_report_v2()` | `reporter.py:533` | v2 五维汇总排名表；列顺序是综合、带动、领涨、抗跌、流动、承接、真龙。 |

### `query.py`

| 导出 | 位置 | 语义 / 依赖 |
| --- | --- | --- |
| `tail_logs()` | `query.py:36` | 读取最新或指定日期扫描的最后 N 条日志；日期先经 `_find_latest_scan_for_date()` 映射 scan_id。 |
| `query_logs()` | `query.py:51` | 按 date/category/level/code/tail 查询 SQLite `scan_logs`；category 支持精确或前缀匹配由 DB 层实现。 |
| `clear_logs()` | `query.py:75` | 清理 N 天前日志；委托 `store.delete_old_scan_logs()`，返回兼容旧 JSONL 清理结构。 |
| `list_logs()` | `query.py:88` | 返回每个 scan_id 的日志条数与时间范围；委托 `store.list_scan_log_folders()`。 |
| `log_summary()` | `query.py:94` | 返回最新/指定日期扫描的 phase/api/error/scorer 汇总；委托 `store.log_summary()`。 |
| `_find_latest_scan_for_date()` | `query.py:116` | 用 `scan_id.startswith(YYYYMMDD)` 找指定日期最新扫描；scan_id 格式由 orchestrator Phase F 生成。 |

## 四、数据契约

### 1. 内存日志 `LogEntry` 契约

| 字段 | 来源 | 下游 |
| --- | --- | --- |
| `timestamp` | `_log()` 写入 `time.time()`：`logger.py:45` | `to_dicts()` 改名为 `ts`，SQLite 按 `ts DESC` 查询：`storage/db.py:1208` |
| `category` | `phase/api/scorer/warn/error` 入口生成 | 前缀匹配、summary 聚合、CLI 过滤 |
| `level` | `info/warn/error` | `errors()`、CLI `--level`、summary `error_count` |
| `message` | 调用方提供或格式化 | CLI logs 输出、summary phase 文案 |
| `code` | 股票代码，可空 | 单股过滤、`report_context()` 聚合 |
| `data` | 额外结构化字段 | API 统计、评分 details、SQLite `data_json` |

### 2. category 前缀约定

| 前缀 | 写入方 | 读取/聚合方 | 不变式 |
| --- | --- | --- | --- |
| `phase:{A-F}` | `orchestrator.scan()`：`orchestrator.py:394` 等 | `ScanLogger.summary()`、`storage.db.log_summary()` | Phase 名必须短且稳定，否则历史摘要不可读。 |
| `api:{provider}:{endpoint}` | providers，例如 `providers/ths.py:290` | `api_stats()`、`log_summary()` | `data.ok` 与 `data.elapsed_ms` 必须存在，provider 名取 category 第 2 段。 |
| `scorer:{dim}` | `_score_one/_score_one_v2`：`orchestrator.py:163` / `:198` | `report_context()`、scorer_count、CLI 过滤 | `data.score`/`data.weight` 是固定字段；details 不要覆盖这两个键。 |

### 3. SQLite `scan_logs` 契约

```text
logger.to_dicts()                         logger.py:182
  → db.save_scan_logs(scan_id, entries)    storage/db.py:1148
  → scan_logs(scan_id, ts, category, level, message, code, data_json)
  → query.py / CLI 读取                    query.py:36 / cli.py:113
```

不变式：
- `save_scan_logs()` 会先删除同 `scan_id` 旧日志再插入：`storage/db.py:1153`，因此同日同 top_n 覆盖扫描不会累积旧日志。
- `data_json` 必须能 `json.dumps(..., ensure_ascii=False)`；不要往 logger data 放不可序列化对象。

## 五、报告字段契约

### v1 `build_stock_report()` 依赖字段

| 维度 | 关键字段 | 格式化函数 |
| --- | --- | --- |
| drive | `best_day_detail.voice/follow/board_leadership/voice_raw/follow_raw/board_detail` | `_format_drive()`：`reporter.py:72` |
| anti_drop | `plunge_days/day_details/consecutive_plunge_bonus` | `_format_anti_drop()`：`reporter.py:164` |
| leadership | `intraday_rank/total_components/deviation/lead_lag_bonus` | `_format_leadership()`：`reporter.py:225` |
| absorption | `fallback_reason/reason/event_count/all_events` | `_format_absorption()`：`reporter.py:248` |

### v2 `build_stock_report_v2()` 依赖字段

| 维度 | 关键字段 | 格式化函数 |
| --- | --- | --- |
| drive | `s_early/early.seal_time/early.bid1_volume/lead.lead_events/follow_events/voice` | `_v2_drive()`：`reporter.py:401` |
| leadership | `s_board/board_count/b_max/s_pct/fived_pct/pct_rank/pct_n` | `_v2_lead()`：`reporter.py:436` |
| anti_drop | `market.deepest_event/dip_events`、`sector.deepest_event/dip_events` | `_v2_anti()`：`reporter.py:443` |
| liquidity | `s_turnover/turnover_rate/s_seal/s_seal_strength/n_open` | `_v2_liq()`：`reporter.py:453` |
| absorption | `fallback_reason/event_count/best_event/all_events/fleeing_sectors` | `_v2_abs()`：`reporter.py:463` |

## 六、任务导航表

| 想做什么 | 主改文件 | 关联/注意 |
| --- | --- | --- |
| 新增日志类别 | `logger.py` 写入口 + 调用方 | 保持 `category` 前缀稳定；若要纳入 summary，同步 `summary()` 与 `storage.db.log_summary()`。 |
| 调整 API 统计口径 | `logger.py:109`、`storage/db.py:1244` | 内存 summary 与 SQLite summary 要同步，否则实时输出和历史查询不一致。 |
| 改 `dragon-quant logs query` 行为 | `query.py:51`、`cli.py:124`、`storage/db.py:1177` | `query.py` 只封装参数；SQL 条件在 DB 层。 |
| 改 v1 单股报告文案 | `reporter.py:17` 及 `_format_*` | v1 scorer details 与 v2 details 不同，不要复用 v2 字段假设。 |
| 改 v2 单股报告文案 | `reporter.py:365` 及 `_v2_*` | 字段来自 `scorers_v2/* ScoreResult.details`，缺字段必须有 fallback。 |
| 改汇总排名表 | `reporter.py:324` / `reporter.py:533` | v1/v2 表头不同；v2 有 `is_true_dragon` 标记。 |
| 增加日志持久化字段 | `logger.py:182`、`storage/db.py:1148`、schema | 需兼容旧 `scan_logs`，并更新查询反序列化。 |

## 七、关键不变式

1. `ScanLogger` 只负责单次扫描内存日志；历史查询必须走 SQLite `scan_logs`，不要再依赖 JSONL 文件。
2. `category` 前缀是查询协议：`phase:` / `api:` / `scorer:` 不能随意改名。
3. `api()` 日志的 `ok`、`elapsed_ms` 是 API 统计必需字段；provider 适配器新增 endpoint 时必须继续写这两个字段。
4. `scorer()` 会把 details 展平到 `data`；details 内不要使用 `score` / `weight` 覆盖固定字段。
5. `ReportBuilder` 不做网络请求、不重新打分；只消费 orchestrator/scorer 已准备好的字段。
6. v1/v2 报告字段契约隔离：v1 使用旧 `best_day_detail` 等结构，v2 使用五维 `ScoreResult.details`。
7. 日志持久化必须保证 `data` JSON 可序列化，否则 `db.save_scan_logs()` 会失败并导致历史 logs 不完整。
8. 同一 `scan_id` 的日志保存是覆盖语义（先删后插），不要把它当 append-only 审计日志。

## 八、自检记录

- 已核对 `ScanLogger.scorer()` 行号与展平 details 逻辑：`logger.py:67`。
- 已核对 v2 报告入口与 `_v2_drive/_v2_anti/_v2_abs` 行号：`reporter.py:365` / `reporter.py:401` / `reporter.py:443` / `reporter.py:463`。
- 已核对 SQLite 日志查询/汇总入口：`storage/db.py:1177` / `storage/db.py:1244`。
