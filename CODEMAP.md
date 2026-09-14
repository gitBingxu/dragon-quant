# CODEMAP.md — dragon-quant 代码地图

> 由 `/codemap` skill 生成的导航/语义层文档，回答「**改某功能要动哪些文件、调用链怎么走、数据怎么流、有哪些不可破的约束**」。与 `AGENTS.md`（操作手册）、`README.md`（对外说明）互补。
> 模块结构/调用链/数据流有较大调整后，重跑 `/codemap` 刷新。行号对应当前代码，仅供跳转参考。

---

## 一、执行路径地图

### scan（五维识别真龙）

以下路径相对 `dragon_quant/`。

```text
cli.main cli.py:475，隐藏 scan_v2 兼容别名归一化为 scan
  → _cmd_scan cli.py:58 → orchestrator.scan orchestrator.py:291
  A 行业 Top5 / Bottom20、黑名单       orchestrator.py:406
  B 行业最多五页成分股、主板涨停候选    orchestrator.py:450
  C 日K连板与五日收益 → Candidate      orchestrator.py:532
  D 全候选/行业分钟线、SH000001、十日5分K orchestrator.py:557
    腾讯排序分批取数、合并 list[Quote]  orchestrator.py:587
  E _score_one → aggregator.evaluate    orchestrator.py:196 / scorers/aggregator.py:30
  F 全候选诊断分 + 通过者真龙 rank      orchestrator.py:648 / scorers/aggregator.py:74
    全部结果 → scan_stocks_v2/raw_output storage/db.py:431
    通过者 TopN → 报告 → 五日去重 → dragons_v2 storage/db.py:666
    无真龙保留明细与原因，不以否决票补位
```

缓存回显入口 `orchestrator.py:228` 同样只展示 Top N 真龙。历史重建 `storage/db.py:783` 过滤明确否决及超过原扫描 Top N 的贡献，保留旧无真龙标记记录的兼容语义。

### 五维评分聚合（Phase E 内部）
```
scorers.aggregator.evaluate(code, cache, ...)     scorers/aggregator.py
  ├ drive.score        带动 30%  scorers/drive.py        封板最早+脉冲跟随因果+板块共鸣
  ├ leadership.score   领涨 25%  scorers/leadership.py   连板最多+5日涨幅板块内分位
  ├ anti_drop.score    抗跌 15%  scorers/anti_drop.py    大盘+板块双基准
  ├ liquidity.score    流动 20%  scorers/liquidity.py    换手+封板质量(一字不罚)
  └ absorption.score   承接 10%  scorers/absorption.py   跨板块虹吸(回看10日,不否决)
  门槛: 四大特征任一低分或异常 → is_true_dragon=False；承接异常中性50不否决
  所有候选保留 composite 诊断分，rank_verdicts 只给通过者赋 rank
  权重/门槛/阈值常量集中: scorers/registry.py
```

### Phase D 数据预填对照
| 数据 | 口径 | cache 键 |
|------|------|---------|
| 板块 5分K | 近10日历史，资金承接回看 | `kline:5min:sector:{}` |
| 板块当日1分K | 领涨行业，带动/抗跌基准 | `kline:1min:sector:{}` |
| 大盘当日1分K | 显式请求 SH000001 上证指数，与平安银行分离 | `kline:1min:SH000001` |
| 个股当日1分K | 全候选(封板池) | `kline:1min:{}` |
| 批量行情(含盘口) | 成分股去重排序，每批200只，合并全部结果 | `quotes:batch`（list[Quote]） |

### review-account 与 buy/sell：单一事件引擎

```text
cli._cmd_review_account → review_account/service.py:11
  → StrategyConfig.from_dict → AccountSimulator.run（review_account/simulator.py:20）
  → MarketData.calendar（日历+前置窗口）、collect_candidates（review_account/market.py:33）
  → historical_events（review_account/data.py）：
      全员有完整48根5分钟K → _intraday_events(open→fill→bar…→late→fill→close)
      任一缺失 → _daily_events 日K兜底(open→fill→late→fill，intraday_bars 恒空)
  → TradingEngine.step（review_account/engine.py:11）
      待执行卖出/买入撮合 → evaluate_sell / evaluate_buy（strategy.py）→ 新待执行信号
      execution.py：整手、风险预算、滑点、最低佣金、涨跌停可执行性
      AccountState：现金、持仓、待执行、处理时间、当日买卖限制、峰值
  → 每日快照 / 交割单 / 已平仓 / 时间线 → 原 review_account_* 表

cli._cmd_buy / _cmd_sell → live_trade/service.py：读取账户保存的完整配置
  → LiveTrader._run（live_trade/trader.py）：同一候选池、历史回放或实时行情适配
  → build_live_row → 同一个 TradingEngine.step
  → db.save_live_engine_step：revision 校验 + 同事务写现金/持仓/交割单/live_engine_state
```

- `buy` 开启买入并先处理卖出；`sell` 禁止新增买入，不实现另一套卖出规则。
- `market.build_row` / `build_daily_row` 仅暴露截至事件时间已知数据，开盘量额来自上日；`data.MarketData` 用不复权日K/5分钟K，严格验证48根交易时段及OHLC，缺失则 `try_intraday` 返回 None 触发整日日K兜底（成交价保守近似，`data_quality=daily_fallback`）。
- 信号不能在同事件成交，历史按下一根K开盘、实时按更晚的新鲜报价；相同参数、事件和账户状态产生相同交易。
- `review_account/evaluation.py` 提供固定四组参数、60/20/20时间划分、最少交易数/回撤门槛、双倍滑点测试；缺数返回不可验证，不自动推广策略。
- `--config` 共享参数；已有纸上账户拒绝静默换参数；`--account` 独立账户；历史日期必须 `--at`，不混用当前报价。
- `review_account_snapshots.positions_json` 保存完整未平仓状态，查询返回 `positions`；已平仓表保持原语义，旧数据增量补列。

### Web UI /account（账户面板 + 生成/删除记录）
```
web_ui/server.py  ReviewHandler（stdlib HTTPServer，单线程，server.py:515）
  do_GET    server.py:83
    GET /api/account/runs        → _serve_api_account_runs      :236 → db.query_review_account_runs :1540
    GET /api/account/{snapshots,benchmark,trades,positions,events}  :304/:310/:327/:333/:339
  do_POST   server.py:113
    POST /api/account/runs       → _serve_api_account_run_create :247
        run_review_account(...)  server.py:278 (⚠ 单线程，回测同步阻塞其它请求)
        → 201 {data, run_id}
  do_DELETE server.py:129
    DELETE /api/account/runs?run_id= → _serve_api_account_run_delete :294
        db.delete_review_account_run(run_id)  server.py:298 → db.py:1513（显式删子表，不依赖 PRAGMA）
        无记录 → 404；成功 → {deleted:True}

前端 main.tsx:15 按 pathname==/account 路由到 AccountApp.tsx:49
  loadRuns → 下拉批次 Select                       AccountApp.tsx:72 / :155
  renderOption 每个选项后内嵌删除图标(onMouseDown 阻断选中)  AccountApp.tsx:166
    → setDeleteTarget → DeleteRunModal(二次确认)   AccountApp.tsx:260
    → confirmDelete → deleteAccountRun → 重载       AccountApp.tsx:78 (api.ts:227)
  runs.length===0 → EmptyRuns 引导空态             AccountApp.tsx:219 / :243
  CreateRunModal → createAccountRun(POST)          AccountApp.tsx:294 (api.ts:206)
```

---

## 二、任务导航（「改 X 看哪些文件」）

| 想做什么 | 主改文件 | 关联/注意 |
|---------|---------|----------|
| 调 v2 权重/门槛/阈值 | `scorers/registry.py` | 常量集中于此，勿散落算法 |
| 改 v2 某维算法 | `scorers/{drive,leadership,anti_drop,liquidity,absorption}.py` | 共享工具 `scorers/base.py` |
| 改 v2 聚合/门槛规则 | `scorers/aggregator.py` | `_HARD_DIMS` 决定哪些维设门槛 |
| 新增/改数据源接口 | `providers/base.py` + 具体 provider | 同步 orchestrator Phase D 预填 |
| 改板块数据源(行业/概念) | `providers/ths.py` URL 常量段 | 排行字段铁律 `zdf`，详情页 `/thshy/` |
| 改候选筛选/排序 | `orchestrator.py` Phase A/B/C | 当前固定五维候选：领涨行业当日所有涨停股 |
| 改 dragons 表结构 | `storage/db.py` 的 `_create_versioned_tables` / `_ensure_schema` | 默认读写 `*_v2`，显式 `source="v1"` 仅历史兼容 |
| 改龙头入库/source 路由 | `orchestrator.py` Phase F + `db.save_dragons` | 新扫描固定 `source="v2"`，不要改表名以免破坏历史 v2 数据 |
| 加 CLI 命令 | `cli.py` parser + dispatch + `_cmd_*` | 同步 AGENTS.md/README.md |
| 改回测逻辑 | `review.py` | 默认读写 `dragons_v2` pending；写 review 字段 + vpa |
| 改板块黑名单 | `storage/db.py`(表) + `cli.py`(blacklist 命令) | Phase A `_sector_ok` 消费 |
| 改账户回测买卖策略 | `review_account/strategy.py`(evaluate_buy/sell) + `review_account/models.py`(StrategyConfig 门槛/默认) | live 层同步生效(100% 复用)；改后同步 `STRATEGY.md` 与 `tests/test_review_account.py` |
| 改账户模拟推进/建仓 | `review_account/engine.py` + `execution.py` | simulator只重放事件；市场数据/候选在data.py与market.py |
| 改实盘辅助 buy/sell 取数 | `live_trade/row_builder.py` + `live_trade/trader.py` | 所有决策/撮合交给同一引擎；严格时间戳与缺失数据检查 |
| 改账户级表结构 | `storage/db.py` 的 `review_account_*` / `live_*` DDL + `_ensure_schema` 补列 | 子表删除见 `delete_review_account_run`(显式删子表) |
| 改 /account 面板/接口 | `web_ui/server.py`(路由) + `web_ui/frontend/src/{AccountApp.tsx,api.ts}` | 改前端后 `npm run build` 刷 `web_ui/dist`；server 单线程，POST 生成会阻塞 |
| 加账户级 API 路由 | `web_ui/server.py` `do_GET/do_POST/do_DELETE` 分发 + `_serve_api_account_*` | 前端 fetch 封装在 `api.ts` |

---

## 三、数据流 / cache 键契约（写入方 → 读取方）

| cache 键 | 写入 (set) | 读取 (get) |
|----------|-----------|-----------|
| `sector:components:{}` | orchestrator | orchestrator, scorers/{drive,liquidity}；leadership 仅用候选池参数 |
| `kline:day:{}` | orchestrator | orchestrator |
| `kline:day:{code}:normal:{days}` / `kline:5min:{code}:normal` | review_account/data.py | 共享历史/实时适配器；account namespace，按交易日隔离 |
| `kline:1min:{}` | orchestrator | scorers/{drive,anti_drop,liquidity} |
| `kline:1min:SH000001` | orchestrator.py:577 | scorers/anti_drop.py:28 |
| `kline:1min:sector:{}` | orchestrator | scorers/{drive,anti_drop} |
| `kline:5min:sector:{}` | orchestrator | scorers/absorption |
| `quotes:batch` | orchestrator | orchestrator, scorers/{drive,liquidity} |
| `__meta__:candidates` | orchestrator | 日志/调试快照 |
| `__meta__:sector_codes` | orchestrator（领跌 Top20） | absorption.py:26，未显式传入代码时使用 |
| `__meta__:sector_name_map` | orchestrator | 日志/调试快照 |

> 封单数据不走 cache 键，随 `quotes:batch` 的 `Quote.bid1_volume`(gtimg f[10]) 一起来。

---

## 四、关键不变式（破坏即出 bug）

1. **评分器是 cache 消费者**：`score()` 只读 `cache.get`，绝不发网络请求；新数据须先在 orchestrator Phase D 预填对应键。
2. **主流程固定五维**：`scan` 和隐藏兼容别名 `scan_v2` 都走当前 `scorers/`；旧四维评分代码已删除。
3. **RateLimiter 按 provider 串行防封**；同花顺排行有 403 频控，`get_sector_ranking` 带退避重试。
4. **封单单位铁律**：封单强度 = `Quote.bid1_volume` ÷ `Quote.volume`，二者同为腾讯 gtimg「手」，禁与雪球成交量(股)混用（否则 100 倍误差）。
5. **粒度铁律**：当日盘中时序对比一律 1分K（个股/板块/大盘对齐）；资金承接回看用 5分K 历史。
6. **板块排行字段铁律**：必须 `field=zdf`（涨跌幅），`tradezdf`(资金流) 无视 order/page；单页 DOM 非严格有序须本地按 pct 排序（ths.py `get_sector_ranking`）。
7. **v2 表兼容**：新扫描固定写 `scans_v2` / `scan_stocks_v2` / `scan_logs_v2` / `dragons_v2`，`scan_id` 保持 `v2_YYYYMMDD_topN`；旧 `*_v1` 表仅显式查询。
8. **provider 基类新方法用默认 `NotImplementedError`**（非 `@abstractmethod`），否则 `create_providers()` 实例化全部 4 个 provider 时崩。
9. **账户严格 T+1 + 保守成交**：当日买入不可卖，但跟踪买入后峰值；完成K产生信号，后续事件成交，同K内顺序不明时不能倒推保护。日K不可替代缺失的盘中数据。
10. **账户策略单一真相**：买卖逻辑只在 `review_account/strategy.py`；`live_trade` 100% 复用同一套 `StrategyConfig`/`evaluate_buy`/`evaluate_sell`，不得在 live 层另写决策。改策略须同步 `STRATEGY.md` + `tests/test_review_account.py`。
11. **候选池 = 近 N 日全部真龙并集**：`candidate_lookback_days`（默认 3）取严格上一交易日起 `dragons_v2` **全部真龙**（`get_dragons_by_date(top_n=None)`），共享层过滤 `is_true_dragon=False`/综合分<50，按 `code` 去重保留综合分更高者，不再截断前 N 只；近 N 日无记录当日空仓，不回退更早数据。择优在 engine 按**买点得分 → 五维综合分 → 代码**（不看 rank）。突破前高买点在首根5分钟K确认且需换手≥`turn_strong_bar_turnover_min`。
12. **分时协议一致**：开盘只判断开盘买点；分歧买点等完整窗口；14:55尾盘判断；15:00只更新估值与峰值。实时需重复调用取得信号后的报价，错过时点不补记过去成交。
13. **Web UI 运行期零 Node**：`web_ui/dist` 由 Vite 构建后**入库**，运行仅靠 Python stdlib `HTTPServer` 托管；改前端须 `npm run build` 刷 dist。server 单线程，`POST /api/account/runs` 同步跑完整回测会阻塞其它请求。
14. **删除账户回测记录显式删子表**：`delete_review_account_run`（db.py:1513）逐表 `DELETE` snapshots/trades/positions/events 后再删 run，不依赖 `PRAGMA foreign_keys` 级联（裸连接也彻底清理）。

---

### 评分链路补充约束

- `scorers/base.py:27`：分钟窗口按连续交易时段分割；`scorers/absorption.py:81`：五分钟窗口必须六根连续，不能跨午休、隔夜或缺失点。
- `scorers/drive.py:79`：封板排名按最后回封段；`scorers/drive.py:113`：带动/跟风事件互斥，同步启动不判方向。
- `scorers/anti_drop.py:154`：双方实际反弹才奖励，横盘只由稳定性奖励。
- `scorers/liquidity.py:42`：普通买一量不能当封单；`scorers/absorption.py:89`：每个出逃板块单独满足时序条件。
- `scorers/absorption.py:42`：重叠窗口合并为独立事件，取消次数奖励，按三交易日半衰期向 50 衰减后取最强三事件均值；强度/广度/持续性为 60%/20%/20%。
- `scorers/aggregator.py:74`：否决者无真龙 rank；扫描全明细可读，但报告/入选只看通过者 Top N。
- 缓存不会自动作历史算法迁移；盘后 `scan --force --no-cache` 才重新取数评分，不自动删除或重写旧记录。

## 五、再生成

```
/codemap              # 全量刷新本文件
/codemap scorers      # 只更新某目录（如需）
```

> 本文件覆盖：scan 五维主链路、review-account 账户回测、buy/sell/account 实盘辅助、Web UI `/account` 面板。
> `providers/`、`scorers/`、`logging/` 另有目录级 `CODEMAP.md`。`review_account/` 策略细节见同目录 `STRATEGY.md`。
