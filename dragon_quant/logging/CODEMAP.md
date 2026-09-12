# logging/CODEMAP.md — 扫描日志与报告

## 职责和调用链

```text
../orchestrator.py:291 scan
  → logger.py:35 ScanLogger：单次扫描内存日志
  → ../orchestrator.py:196 _score_one：逐维记录评分与 details
  → reporter.py:19 build_stock_report：全部候选生成可审计报告
  → ../orchestrator.py:648 Phase F：仅 Top N 真龙拼接最终报告
  → ../storage/db.py:1958 save_scan_logs：SQLite 持久化
  → query.py:36 / query.py:51 / query.py:95：历史查询
```

评分器异常、缺数据、无反弹等说明来自 scorer details，报告不重新评分、不发请求。

## 关键入口

| 入口 | 语义 |
|---|---|
| `logger.py:55` phase | Phase A～F 日志，参与 summary |
| `logger.py:58` api | ok/elapsed_ms 为统计协议，失败需携带错误 |
| `logger.py:67` scorer | 展平五维 details；不能覆盖 score/weight 字段 |
| `logger.py:79` error | 错误级别日志，供 CLI 过滤 |
| `logger.py:86` query | 内存日志查询，不是历史数据库查询 |
| `logger.py:150` summary | 汇总耗时、类别数量及 API 统计 |
| `logger.py:182` to_dicts | JSON 可序列化结构，由编排器写 SQLite |
| `reporter.py:19` build_stock_report | 真龙状态、否决原因、五维解释及评分异常 |
| `reporter.py:59` _drive | 稳定封板起点、互斥的带动/跟风证据，分时不足说明 |
| `reporter.py:96` _lead | 连板差、五日收益分位及样本数 |
| `reporter.py:103` _anti | 双基准跳水段，含反弹确认或未确认原因 |
| `reporter.py:114` _liq | 换手、封单强度、开板数、降级原因 |
| `reporter.py:125` _abs | 有效逐板块事件与 fallback 原因 |
| `reporter.py:196` build_summary_report | 调用方传入真龙列表，格式化摘要 |
| `query.py:36` tail_logs | 最新或指定日期末尾日志，默认 v2 |
| `query.py:51` query_logs | 按日期、类别、级别、代码筛选 |
| `query.py:76` clear_logs | 显式清理历史日志操作 |
| `query.py:95` log_summary | SQLite 统计，默认 v2；v1 仅显式历史查询 |

## 数据契约

- 类别前缀固定为 `phase:`、`api:`、`scorer:`；`data` 必须能 JSON 序列化。
- `ScoreResult.details.error` 表示算法异常；四特征异常会否决，承接异常仅降级，报告必须如实显示。
- drive 使用 `early.seal_time/degraded/reason`、`lead.lead_events/follow_events/reason`；缺数据不得描述成已确认纯跟风。
- anti_drop 使用 `market/sector.deepest_event` 和 `rebound_reason`；无回升不能宣称率先起飞。
- liquidity 使用 `n_open`、`s_seal_strength`、`degraded/reasons`；未知开板数为 −1。
- absorption 使用 `fallback/fallback_reason` 或 `best_event/fleeing_sectors`；不把无数据等同于零承接能力。
- `raw_output.ranking` 与扫描明细保留所有候选；最终报告由编排器筛 Top N 真龙，无真龙仍留扫描与否决明细。
- 旧 scan_id 格式和 v2 物理表保持不变；日志保存同 scan_id 仍是覆盖语义，不是追加审计流。

## 修改导航

- 修改评分解释：先核对 `../scorers/评分器Refactor.md:1`，再改 `reporter.py:19` 与 `../../tests/test_logging.py:90`。
- 修改入选/展示范围：改 `../orchestrator.py:228` 缓存展示及 Phase F，不在 formatter 里重新判真龙。
- 修改历史查询：`query.py:51` 与 `../storage/db.py:1958` 相关读写路径；不能让内存日志代替数据库历史记录。
