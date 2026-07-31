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

---

## 五、再生成

```
/codemap              # 全量刷新本文件
/codemap scorers   # 只更新某目录（如需）
```
