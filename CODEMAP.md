# CODEMAP.md — dragon-quant 代码地图

> 由 `/codemap` skill 生成的导航/语义层文档，回答「**改某功能要动哪些文件、调用链怎么走、数据怎么流、有哪些不可破的约束**」。与 `AGENTS.md`（操作手册）、`README.md`（对外说明）互补。
> 模块结构/调用链/数据流有较大调整后，重跑 `/codemap` 刷新。行号对应当前代码，仅供跳转参考。

---

## 一、执行路径地图

### scan（五维识别真龙）
```
cli.main 分发                                  cli.py:388 / :638
  ├ _cmd_scan      → orchestrate_scan(..., scorers="v2")  cli.py:57
  └ _cmd_scan_v2   → _cmd_scan(args) 隐藏兼容别名          cli.py:74
      → orchestrator.scan(source 固定 "v2")                orchestrator.py:289 / :301

  Phase A 板块排行                              orchestrator.py:403
    ths.get_sector_ranking(asc=False)          orchestrator.py:408  → 行业涨跌幅榜(field=zdf)
    _sector_ok 过滤(统计概念前缀+DB黑名单)       orchestrator.py:394
    top10_up = [:5]                            orchestrator.py:411
    top10_down = sorted(pct)[:20]              orchestrator.py:412

  Phase B 候选筛选                              orchestrator.py:427
    ths.get_sector_components(all_pages=True)  orchestrator.py:439 → sector:components:{}
    每板块当日所有涨停股(pct≥9.9)               orchestrator.py:475

  Phase C 连板+排序                             orchestrator.py:509
    _compute_consecutive_boards                orchestrator.py:151
    _compute_5day_return → Candidate.fived_pct orchestrator.py:167 / :520
    按(连板,概念数)降序，ranking=全候选池        orchestrator.py:523

  Phase D 并发预填(RateLimiter)                 orchestrator.py:534  (cache 键见 §三)

  Phase E 打分（候选池全部个股）                 orchestrator.py:577
    _score_one → scorers.aggregator.evaluate orchestrator.py:197 / :600

  Phase F 输出+持久化                           orchestrator.py:618
    ReportBuilder.build_stock_report        orchestrator.py:646
    scan_id = v2_YYYYMMDD_topN                 orchestrator.py:691
    db.save_scan / save_scan_logs / save_dragons(source="v2")  orchestrator.py:696 / :717 / :787
```

### 五维评分聚合（Phase E 内部）
```
scorers.aggregator.evaluate(code, cache, ...)     scorers/aggregator.py
  ├ drive.score        带动 30%  scorers/drive.py        封板最早+脉冲跟随因果+板块共鸣
  ├ leadership.score   领涨 25%  scorers/leadership.py   连板最多+5日涨幅板块内分位
  ├ anti_drop.score    抗跌 15%  scorers/anti_drop.py    大盘+板块双基准
  ├ liquidity.score    流动 20%  scorers/liquidity.py    换手+封板质量(一字不罚)
  └ absorption.score   承接 10%  scorers/absorption.py   跨板块虹吸(回看10日,不否决)
  门槛: 四大特征任一 < floor → is_true_dragon=False；通过者 composite 加权
  rank_verdicts 按 composite 降序赋 rank
  权重/门槛/阈值常量集中: scorers/registry.py
```

### Phase D 数据预填对照
| 数据 | 口径 | cache 键 |
|------|------|---------|
| 板块 5分K | 近10日历史，资金承接回看 | `kline:5min:sector:{}` |
| 板块当日1分K | 领涨行业，带动/抗跌基准 | `kline:1min:sector:{}` |
| 大盘当日1分K | 上证指数 000001，抗跌基准 | `kline:1min:000001` |
| 个股当日1分K | 全候选(封板池) | `kline:1min:{}` |
| 批量行情(含盘口) | 同花顺成分股去重后最多200只 | `quotes:batch` |

### review-account（账户级模拟回测）
```
cli._cmd_review_account            cli.py:283  (--from/--to/--capital/--ui[-only])
  → review_account.run_review_account   service.py:11
      cfg = StrategyConfig(...)             service.py:20   (默认/门槛见 models.py:7)
      sim = AccountSimulator(cfg)           service.py:25
      result = sim.run(from, to)            service.py:26 → simulator.py:56

  AccountSimulator.run                    simulator.py:56
    build_trade_calendar(from,to)         simulator.py:57  (utils/trading，真实交易日历)
    for day in trading_days: _process_day simulator.py:62 → :67

  _process_day(day)                       simulator.py:67
    ① 先卖：持仓遍历，entry_date>=day 跳过(T+1)  simulator.py:71
        evaluate_sell(pos,row,hold,cfg,5min)     simulator.py:77 → strategy.py:346
        命中 → _sell(...)                          simulator.py:85 → :249
    ② 后买：_can_open_position 门控           simulator.py:97 → :541
        _try_buy(day)                            simulator.py:98 → :114
          _previous_candidate_date(day)          simulator.py:115 → :452 (严格上一交易日)
          _collect_candidates(day)               simulator.py:123 → :466
            近 lookback_days 池并集去重(rank优先) db.get_dragons_by_date  simulator.py:470 → db.py:1063
          evaluate_buy(cand,row,cfg,...)         simulator.py:158 → strategy.py:8
          signals.sort(priority,-rank,score)[0]  simulator.py:175
    ③ _snapshot(day) + _finalize_events      simulator.py:111 / :112

  持久化(回 service)                        service.py:28 / :42
    db.create_review_account_run(...)         db.py:1400  → run_id
    db.save_review_account_results(run_id,...) db.py:1432  (snapshots/trades/positions/events 批量)
```
买入优先级(高→低)：分歧买龙 400(`evaluate_divergence_buy` strategy.py:108) > 开盘贴 MA5 承接 300 > 开盘突破前高弱转强 200。
卖出阶梯(首个命中即返回) strategy.py:346：硬止损(首日紧) → 移动止盈 → 保本(`_break_even_signal_price` strategy.py:562) → 高开未涨停清仓 → 次日涨停半仓/弱转清 → 弱势容忍带 → 破 MA5 → 放量止盈。

### buy / sell / account（实盘辅助，复用 review_account 策略）
```
cli._cmd_buy      cli.py:334 → live_trade.run_buy       service.py:41
cli._cmd_sell     cli.py:341 → live_trade.run_sell      service.py:53
cli._cmd_account  cli.py:348 → run_account_status/init  service.py:68 / :28

buy(9:25)  LiveTrader.buy                        trader.py:91
  max_positions/现金门控                          trader.py:96 / :100
  _collect_candidates(近3日池并集)                trader.py:67 (list_dragon_trade_dates db.py:1037 + get_dragons_by_date db.py:1063)
  _get_quote 腾讯实时 + _get_klines 雪球日K        trader.py:119 / :49
  build_buy_row(用今日开盘价拼开盘决策 row)        trader.py:126 → row_builder.py:27
  evaluate_buy(..., intraday_bars=None)           trader.py:137 → strategy.py:8
    ⚠ 9:25 无 5分K，显式剔除分歧买龙信号            trader.py:139
  _execute_buy → add_live_position/update_live_cash/add_live_trade  trader.py:151 (db.py:1781/:1766/:1856)

sell(14:55) LiveTrader.sell                       trader.py:203
  ⚠ 严格 T+1：entry_date>=trade_date 跳过         trader.py:207
  build_sell_row(实时快照合成今日 KBar 追加历史后 enrich 取末行)  trader.py:218 → row_builder.py:63
  evaluate_sell(..., intraday_bars=None)          trader.py:242 → strategy.py:346
  _execute_sell → update_live_cash/add_live_trade/update_live_position  trader.py:291 (db.py:1766/:1856/:1809)
```
策略 100% 复用 review_account：`trader.py:15-20` 直接 import `StrategyConfig`/`evaluate_buy`/`evaluate_sell`/`explain_buy_candidate`；live 层只负责用实时数据拼 `row`（`row_builder.py`）与记账，无独立决策逻辑。默认单账户 `DEFAULT_ACCOUNT="default"`（service.py:10）。

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
| 改账户模拟推进/建仓 | `review_account/simulator.py` | 用 `build_trade_calendar` 逐真实交易日；候选取严格上一交易日近 `lookback_days` 池并集 |
| 改实盘辅助 buy/sell 取数 | `live_trade/row_builder.py`(拼 row) + `live_trade/trader.py`(记账) | 决策全部委托 review_account；9:25 无 5分K 故剔除分歧买龙 |
| 改账户级表结构 | `storage/db.py` 的 `review_account_*` / `live_*` DDL + `_ensure_schema` 补列 | 子表删除见 `delete_review_account_run`(显式删子表) |
| 改 /account 面板/接口 | `web_ui/server.py`(路由) + `web_ui/frontend/src/{AccountApp.tsx,api.ts}` | 改前端后 `npm run build` 刷 `web_ui/dist`；server 单线程，POST 生成会阻塞 |
| 加账户级 API 路由 | `web_ui/server.py` `do_GET/do_POST/do_DELETE` 分发 + `_serve_api_account_*` | 前端 fetch 封装在 `api.ts` |

---

## 三、数据流 / cache 键契约（写入方 → 读取方）

| cache 键 | 写入 (set) | 读取 (get) |
|----------|-----------|-----------|
| `sector:components:{}` | orchestrator | orchestrator, scorers/{drive,leadership,liquidity} |
| `kline:day:{}` | orchestrator | orchestrator |
| `kline:1min:{}` | orchestrator | scorers/{drive,anti_drop,liquidity} |
| `kline:1min:000001` | orchestrator | scorers/anti_drop |
| `kline:1min:sector:{}` | orchestrator | scorers/{drive,anti_drop} |
| `kline:5min:sector:{}` | orchestrator | scorers/absorption |
| `quotes:batch` | orchestrator | orchestrator, scorers/{drive,liquidity} |
| `__meta__:candidates` | orchestrator | 日志/调试快照 |
| `__meta__:sector_codes` | orchestrator | 日志/调试快照 |
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
9. **账户回测严格 T+1 + 保守成交**：`entry_date>=day` 当日买入不可卖（simulator.py:71 / live trader.py:207）；日 K 同时满足止损与止盈时，止损优先（`evaluate_sell` 首查止损 strategy.py:346）。
10. **账户策略单一真相**：买卖逻辑只在 `review_account/strategy.py`；`live_trade` 100% 复用同一套 `StrategyConfig`/`evaluate_buy`/`evaluate_sell`，不得在 live 层另写决策。改策略须同步 `STRATEGY.md` + `tests/test_review_account.py`。
11. **候选池 = 近 N 日并集**：`candidate_lookback_days`（默认 3）取**严格上一交易日**起的 `dragons_v2` 真龙池并集，按 `code` 去重保留 rank 更优者；近 N 日无记录当日空仓，不回退更早数据。
12. **9:25 无盘中 5分K**：`buy` 传 `intraday_bars=None` 并显式剔除分歧买龙（`buy_divergence_first_break`，trader.py:139）；分歧买点仅在有 5分K 的回测/待接入盘中数据时生效。
13. **Web UI 运行期零 Node**：`web_ui/dist` 由 Vite 构建后**入库**，运行仅靠 Python stdlib `HTTPServer` 托管；改前端须 `npm run build` 刷 dist。server 单线程，`POST /api/account/runs` 同步跑完整回测会阻塞其它请求。
14. **删除账户回测记录显式删子表**：`delete_review_account_run`（db.py:1513）逐表 `DELETE` snapshots/trades/positions/events 后再删 run，不依赖 `PRAGMA foreign_keys` 级联（裸连接也彻底清理）。

---

## 五、再生成

```
/codemap              # 全量刷新本文件
/codemap scorers      # 只更新某目录（如需）
```

> 本文件覆盖：scan 五维主链路、review-account 账户回测、buy/sell/account 实盘辅助、Web UI `/account` 面板。
> `providers/`、`scorers/`、`logging/` 另有目录级 `CODEMAP.md`。`review_account/` 策略细节见同目录 `STRATEGY.md`。
