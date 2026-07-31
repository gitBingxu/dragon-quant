# dragon_quant/logging 代码地图

> 范围：结构化扫描日志、日志查询 API、五维自然语言报告生成。  
> 入口文件：`dragon_quant/logging/__init__.py:1` 对外导出 `ScanLogger` / `LogEntry` / `ReportBuilder`。

## 一、目录职责

| 文件 | 职责 | 何时被调用 |
| --- | --- | --- |
| `logger.py` | 单次扫描内存日志引擎：写 phase/api/scorer/error，聚合摘要，导出 dict/JSONL | `orchestrator.scan()` 创建 `ScanLogger` 后贯穿 Phase A→F 使用 |
| `reporter.py` | 将五维评分结果 `dimensions` 与日志摘要转为人类可读报告 | Phase F 为每只股票生成 `report_text`，并写汇总报告文件 |
| `query.py` | 面向 CLI/Agent 的 SQLite 日志查询封装：tail/query/clear/list/summary | `dragon-quant logs ...` 子命令调用 |
| `__init__.py` | logging 包轻量导出 | 供 `from dragon_quant.logging import ...` 使用 |

## 二、核心调用链

```text
orchestrator.scan()
  ├─ logger = ScanLogger()                                  orchestrator.py:347
  ├─ providers = create_providers(logger=logger)             orchestrator.py:349
  ├─ Phase A/B/C/D/F 写 phase 日志                           orchestrator.py:413 / :492 / :525 / :568 / :622
  ├─ _score_one 写 scorer:{dim} 日志                         orchestrator.py:197 / :600
  ├─ providers 写 api:{provider}:{endpoint} 日志              providers/ths.py 等
  └─ Phase F logger.to_dicts() → db.save_scan_logs(source=v2) orchestrator.py:696

Phase F results 排序
  ├─ reporter = ReportBuilder(logger)                         orchestrator.py:644
  ├─ build_stock_report(...) → r["report_text"]             orchestrator.py:646
  ├─ top_n 拼接 output["report_text"]                         orchestrator.py:662
  └─ scan_report_v2_*.txt 写 summary + 详细 report_text        orchestrator.py:705

dragon-quant logs tail/query/clear/list/summary
  ├─ cli._cmd_logs(...)                                        cli.py:135
  ├─ query.tail_logs / query.query_logs / query.log_summary    query.py:36 / :51 / :95
  └─ storage.db get_scan_logs / log_summary                    storage/db.py:1239 / :1308
```

关键语义：
- `ScanLogger` 是单次扫描内存态聚合器，不直接写 SQLite；SQLite 写入在 `storage/db.py` 完成。
- provider API 统计依赖 `api:{provider}:{endpoint}` category 约定；评分日志依赖 `scorer:{dim}` category 约定。
- `ReportBuilder` 不重新打分、不查网络；只消费 `dimensions`、`primary_sector_name`、`logger.summary()` 等已生成数据。

## 三、关键导出与语义

### `logger.py`

| 导出 | 位置 | 语义 / 依赖 |
| --- | --- | --- |
| `LogEntry` | `logger.py:22` | 单条内存日志记录；`data` 放可 JSON 序列化的附加字段，最终会经 `to_dicts()` 写入 SQLite。 |
| `ScanLogger` | `logger.py:32` | 单次 scan 生命周期日志容器；内部 `_entries` 由 `threading.Lock` 保护，适配 Phase D 并发加载时 provider 日志写入。 |
| `ScanLogger.phase()` | `logger.py:55` | 写 `phase:{name}` 日志；`summary()` 依赖该前缀聚合 Phase 状态。 |
| `ScanLogger.api()` | `logger.py:58` | 写 `api:{provider}:{endpoint}` 日志；`api_stats()` 与 SQLite `log_summary()` 都依赖 `ok/elapsed_ms` 字段。 |
| `ScanLogger.scorer()` | `logger.py:67` | 写 `scorer:{dim}` 评分日志；`score/weight` 是固定字段，其余 scorer details 会被展平进 `data`。 |
| `ScanLogger.query()` | `logger.py:86` | 内存查询，支持 category 前缀匹配、level、code、dim；不同于 `query.py` 的 SQLite 查询。 |
| `ScanLogger.report_context()` | `logger.py:127` | 从内存日志重构单股 `dimensions/errors/warnings`；用于 agent/debug 场景，不是 Phase F 主报告路径。 |
| `ScanLogger.summary()` | `logger.py:150` | 汇总 elapsed、phase、scorer 计数、api 统计、error_count；`ReportBuilder.build_summary_report()` 读取它。 |
| `ScanLogger.to_dicts()` | `logger.py:182` | 内存日志 → dict 列表；`db.save_scan_logs()` 只接受这种结构。 |

### `reporter.py`

| 导出 | 位置 | 语义 / 依赖 |
| --- | --- | --- |
| `ReportBuilder` | `reporter.py:11` | 报告生成器，持有 `ScanLogger` 以获取 summary；单股报告主要消费传入的 `dimensions`。 |
| `build_stock_report()` | `reporter.py:21` | 五维单股报告入口；包含真龙/非真龙判定与一票否决原因，逐维调用私有 formatter。 |
| `_drive()` | `reporter.py:57` | 带动性证据链；消费 `early.seal_time/bid1_volume`、`lead_events/follow_events`、`voice`。 |
| `_lead()` | `reporter.py:92` | 领涨性证据链；消费 `board_count/b_max/fived_pct/pct_rank/pct_n`。 |
| `_anti()` | `reporter.py:99` | 抗跌性证据链；分别格式化大盘和主板块 `deepest_event/dip_events`。 |
| `_liq()` | `reporter.py:110` | 流动性证据链；消费换手、封单强度和开板次数。 |
| `_abs()` | `reporter.py:119` | 资金承接证据链；消费 `best_event/all_events[0]` 的 `dive_time/rally_time/fleeing_sectors`。 |
| `build_summary_report()` | `reporter.py:189` | 五维汇总排名表；列顺序是综合、带动、领涨、抗跌、流动、承接、真龙。 |

### `query.py`

| 导出 | 位置 | 语义 / 依赖 |
| --- | --- | --- |
| `tail_logs()` | `query.py:36` | 读取最新或指定日期扫描的最后 N 条日志；默认 source=v2。 |
| `query_logs()` | `query.py:51` | 按 date/category/level/code/tail 查询 SQLite `scan_logs_v2`；显式 source=v1 可查历史旧表。 |
| `clear_logs()` | `query.py:76` | 清理 N 天前日志；委托 `store.delete_old_scan_logs()`。 |
| `list_logs()` | `query.py:89` | 返回每个 scan_id 的日志条数与时间范围；委托 `store.list_scan_log_folders()`。 |
| `log_summary()` | `query.py:95` | 返回最新/指定日期扫描的 phase/api/error/scorer 汇总；委托 `store.log_summary()`。 |
| `_find_latest_scan_for_date()` | `query.py:117` | 先匹配 `v2_YYYYMMDD...` 前缀；兼容旧无前缀 scan_id。 |

## 四、数据契约

| 字段 | 来源 | 下游 |
| --- | --- | --- |
| `timestamp` | `_log()` 写入 `time.time()`：`logger.py:45` | `to_dicts()` 改名为 `ts`，SQLite 按 `ts DESC` 查询：`storage/db.py:1268` |
| `category` | `phase/api/scorer/warn/error` 入口生成 | 前缀匹配、summary 聚合、CLI 过滤 |
| `level` | `info/warn/error` | `errors()`、CLI `--level`、summary `error_count` |
| `message` | 调用方提供或格式化 | CLI logs 输出、summary phase 文案 |
| `code` | 股票代码，可空 | 单股过滤、`report_context()` 聚合 |
| `data` | 额外结构化字段 | API 统计、评分 details、SQLite `data_json` |

| 前缀 | 写入方 | 读取/聚合方 | 不变式 |
| --- | --- | --- | --- |
| `phase:{A-F}` | `orchestrator.scan()` | `ScanLogger.summary()`、`storage.db.log_summary()` | Phase 名必须短且稳定。 |
| `api:{provider}:{endpoint}` | providers | `api_stats()`、`log_summary()` | `data.ok` 与 `data.elapsed_ms` 必须存在。 |
| `scorer:{dim}` | `orchestrator._score_one()` | `report_context()`、scorer_count、CLI 过滤 | `data.score`/`data.weight` 是固定字段；details 不要覆盖这两个键。 |

```text
logger.to_dicts()                         logger.py:182
  → db.save_scan_logs(scan_id, entries)    storage/db.py:1202
  → scan_logs_v2(scan_id, ts, category, level, message, code, data_json)
  → query.py / CLI 读取                    query.py:36 / cli.py:135
```

## 五、报告字段契约

| 维度 | 关键字段 | 格式化函数 |
| --- | --- | --- |
| drive | `s_early/early.seal_time/early.bid1_volume/lead.lead_events/follow_events/voice` | `_drive()`：`reporter.py:57` |
| leadership | `s_board/board_count/b_max/s_pct/fived_pct/pct_rank/pct_n` | `_lead()`：`reporter.py:92` |
| anti_drop | `market.deepest_event/dip_events`、`sector.deepest_event/dip_events` | `_anti()`：`reporter.py:99` |
| liquidity | `s_turnover/turnover_rate/s_seal/s_seal_strength/n_open` | `_liq()`：`reporter.py:110` |
| absorption | `fallback_reason/event_count/best_event/all_events/fleeing_sectors` | `_abs()`：`reporter.py:119` |

## 六、任务导航表

| 想做什么 | 主改文件 | 关联/注意 |
| --- | --- | --- |
| 新增日志类别 | `logger.py` 写入口 + 调用方 | 保持 `category` 前缀稳定；若要纳入 summary，同步 `summary()` 与 `storage.db.log_summary()`。 |
| 调整 API 统计口径 | `logger.py:109`、`storage/db.py:1308` | 内存 summary 与 SQLite summary 要同步，否则实时输出和历史查询不一致。 |
| 改 `dragon-quant logs query` 行为 | `query.py:51`、`cli.py:146`、`storage/db.py:1239` | `query.py` 只封装参数；SQL 条件在 DB 层。 |
| 改五维单股报告文案 | `reporter.py:21` 及 私有 formatter | 字段来自 `scorers/* ScoreResult.details`，缺字段必须有 fallback。 |
| 改汇总排名表 | `reporter.py:189` | v2 有 `is_true_dragon` 标记。 |
| 增加日志持久化字段 | `logger.py:182`、`storage/db.py:1202`、schema | 需兼容旧 `scan_logs_v2`，并更新查询反序列化。 |

## 七、关键不变式

1. `ScanLogger` 只负责单次扫描内存日志；历史查询必须走 SQLite `scan_logs_v2`，不要再依赖 JSONL 文件。
2. `category` 前缀是查询协议：`phase:` / `api:` / `scorer:` 不能随意改名。
3. `api()` 日志的 `ok`、`elapsed_ms` 是 API 统计必需字段；provider 适配器新增 endpoint 时必须继续写这两个字段。
4. `scorer()` 会把 details 展平到 `data`；details 内不要使用 `score` / `weight` 覆盖固定字段。
5. `ReportBuilder` 不做网络请求、不重新打分；只消费 orchestrator/scorer 已准备好的字段。
6. 日志持久化必须保证 `data` JSON 可序列化，否则 `db.save_scan_logs()` 会失败并导致历史 logs 不完整。
7. 同一 `scan_id` 的日志保存是覆盖语义（先删后插），不要把它当 append-only 审计日志。
