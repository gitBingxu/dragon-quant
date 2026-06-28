# CODEMAP.md — dragon-quant 代码地图

> 由 `/codemap` skill 生成的导航/语义层文档，回答「**改某功能要动哪些文件、调用链怎么走、数据怎么流、有哪些不可破的约束**」。与 `AGENTS.md`（操作手册）、`README.md`（对外说明）互补。
> 模块结构/调用链/数据流有较大调整后，重跑 `/codemap` 刷新。行号对应当前代码，仅供跳转参考。

---

## 一、执行路径地图

### scan（v1 四维） / scan_v2（v2 五维）
```
cli.main 分发                              cli.py:488 (scan) / :490 (scan_v2)
  ├ _cmd_scan      → scorers="v1"          cli.py:20
  └ _cmd_scan_v2   → scorers="v2"          cli.py:36
      → orchestrator.scan(scorers)         orchestrator.py:271
          use_v2 = (scorers=="v2")         orchestrator.py:302
          rank_up_count = 5(v2)/8(v1)      orchestrator.py:303

  Phase A 板块排行                          orchestrator.py:370
    ths.get_sector_ranking(asc=False)      orchestrator.py:393  → 行业涨跌幅榜(field=zdf)
    _sector_ok 过滤(统计概念前缀+DB黑名单)   orchestrator.py:384
    top10_up = [:rank_up_count]            orchestrator.py:394  (v2=5 / v1=8)
    top10_down = sorted(pct)[:20]          orchestrator.py:395

  Phase B 候选筛选                          orchestrator.py:409
    ths.get_sector_components(all_pages)   → sector:components:{}
    v2: 每板块当日所有涨停股(pct≥9.9)
    v1: 每板块按5日涨幅 Top candidates_n

  Phase C 连板+排序                         orchestrator.py:511
    _compute_consecutive_boards            orchestrator.py:65
    v2 额外 _compute_5day_return → fived_pct  orchestrator.py:81
    按(连板,概念数)降序，ranking=全候选池(不截断)  orchestrator.py:530

  Phase D 并发预填(RateLimiter)             orchestrator.py:540  (cache 键见 §三)

  Phase E 打分（对候选池全部个股，v1/v2 均不截断）  orchestrator.py:599
    v1: _score_one      → 四维加权          orchestrator.py:130 / 调用 :626
    v2: _score_one_v2   → aggregator.evaluate  orchestrator.py:182 / 调用 :622

  Phase F 输出+持久化                       orchestrator.py:652
    5日去重(按 source 隔离，排除同日)          orchestrator.py:804
    db.save_dragons(source=source)          orchestrator.py:844
```

### v2 评分聚合（Phase E 内部）
```
scorers_v2.aggregator.evaluate(code, cache, ...)     scorers_v2/aggregator.py
  ├ drive.score        带动 30%  scorers_v2/drive.py        封板最早+脉冲跟随因果+板块共鸣
  ├ leadership.score   领涨 25%  scorers_v2/leadership.py   连板最多+5日涨幅板块内分位
  ├ anti_drop.score    抗跌 15%  scorers_v2/anti_drop.py    大盘+板块双基准
  ├ liquidity.score    流动 20%  scorers_v2/liquidity.py    换手+封板质量(一字不罚)
  └ absorption.score   承接 10%  scorers_v2/absorption.py   跨板块虹吸(回看10日,不否决)
  门槛: 四大特征任一 < floor → is_true_dragon=False；通过者 composite 加权
  rank_verdicts 按 composite 降序赋 rank
  权重/门槛/阈值常量集中: scorers_v2/registry.py
```

### Phase D 数据预填对照
| 数据 | v1 | v2 | cache 键 |
|------|----|----|---------|
| 板块 5分K | 当日(聚合) | 近10日历史 | `kline:5min:sector:{}` |
| 板块当日1分K | — | ✅ | `kline:1min:sector:{}` |
| 大盘当日1分K | — | ✅ | `kline:1min:000001` |
| 个股当日1分K | top_n | 全候选(封板池) | `kline:1min:{}` |
| 批量行情(含盘口) | ✅ | ✅ | `quotes:batch` |

---

## 二、任务导航（「改 X 看哪些文件」）

| 想做什么 | 主改文件 | 关联/注意 |
|---------|---------|----------|
| 调 v2 权重/门槛/阈值 | `scorers_v2/registry.py` | 常量集中于此，勿散落算法 |
| 改 v2 某维算法 | `scorers_v2/{drive,leadership,anti_drop,liquidity,absorption}.py` | 共享工具 `scorers_v2/base.py` |
| 改 v2 聚合/门槛规则 | `scorers_v2/aggregator.py` | `_HARD_DIMS` 决定哪些维设门槛 |
| 改 v1 评分 | `scorers/*.py` + `scorers/__init__.py`(权重) | 与 v2 隔离，勿混改 |
| 新增/改数据源接口 | `providers/base.py` + 具体 provider | 同步 orchestrator Phase D 预填 |
| 改板块数据源(行业/概念) | `providers/ths.py` URL 常量段 | 排行字段铁律 `zdf`，详情页 `/thshy/` |
| 改候选筛选/排序 | `orchestrator.py` Phase A/B/C | v1/v2 由 `use_v2` 分支 |
| 改 dragons 表结构 | `storage/db.py` 的 `DRAGON_SCHEMA_TEMPLATE` / `_ensure_schema` | v1/v2 分表结构须保持一致；读取方按位置索引，新列加末尾 |
| 改龙头入库/source 路由 | `orchestrator.py` Phase F + `db.save_dragons` | `source` 选择 `dragons_v1` / `dragons_v2`，跨体系互不覆盖 |
| 加 CLI 命令 | `cli.py` parser + dispatch + `_cmd_*` | 同步 AGENTS.md/README.md |
| 改回测逻辑 | `review.py` | 读 dragons pending；写 review 字段 + vpa |
| 改板块黑名单 | `storage/db.py`(表) + `cli.py`(blacklist 命令) | Phase A `_sector_ok` 消费 |

---

## 三、数据流 / cache 键契约（写入方 → 读取方）

| cache 键 | 写入 (set) | 读取 (get) |
|----------|-----------|-----------|
| `sector:components:{}` | orchestrator | orchestrator, scorers/{drive,leadership}, scorers_v2/{drive,leadership,liquidity} |
| `kline:day:{}` | orchestrator | orchestrator, scorers/{drive,anti_drop,leadership} |
| `kline:day:000001` | orchestrator(:527 market) | scorers/anti_drop |
| `kline:1min:{}` | orchestrator | scorers/{drive,anti_drop,leadership}, scorers_v2/{drive,anti_drop,liquidity} |
| `kline:1min:000001` | orchestrator | scorers_v2/anti_drop |
| `kline:1min:sector:{}` | orchestrator | scorers_v2/{drive,anti_drop} |
| `kline:5min:sector:{}` | orchestrator | scorers/{absorption,leadership}, scorers_v2/absorption |
| `quotes:batch` | orchestrator | orchestrator, scorers/{drive,leadership}, scorers_v2/{drive,liquidity} |
| `__meta__:candidates` | orchestrator:605 | analyze.py |
| `__meta__:sector_codes` | orchestrator:610 | analyze.py |
| `__meta__:sector_name_map` | orchestrator:614 | analyze.py |

> 封单数据不走 cache 键，随 `quotes:batch` 的 `Quote.bid1_volume`(gtimg f[10]) 一起来。

---

## 四、关键不变式（破坏即出 bug）

1. **评分器是 cache 消费者**：`score()` 只读 `cache.get`，绝不发网络请求；新数据须先在 orchestrator Phase D 预填对应键。
2. **v1/v2 隔离**：旧 `scorers/` 与 v1 编排路径零改动；v2 全在 `scorers_v2/` + `use_v2` 分支（orchestrator.py:302）。
3. **RateLimiter 按 provider 串行防封**；同花顺排行有 403 频控，`get_sector_ranking` 带退避重试。
4. **封单单位铁律**：封单强度 = `Quote.bid1_volume` ÷ `Quote.volume`，二者同为腾讯 gtimg「手」，禁与雪球成交量(股)混用（否则 100 倍误差）。
5. **粒度铁律**：当日盘中时序对比一律 1分K（个股/板块/大盘对齐）；资金承接回看用 5分K 历史。
6. **板块排行字段铁律**：必须 `field=zdf`（涨跌幅），`tradezdf`(资金流) 无视 order/page；单页 DOM 非严格有序须本地按 pct 排序（ths.py `get_sector_ranking`）。
7. **dragons source 隔离**：`scan` 只写 `dragons_v1`，`scan_v2` 只写 `dragons_v2`；同 `(trade_date, code)` 可在两套表独立存在，5日去重也按 source 查询。
8. **provider 基类新方法用默认 `NotImplementedError`**（非 `@abstractmethod`），否则 `create_providers()` 实例化全部 4 个 provider 时崩。

---

## 五、再生成

```
/codemap              # 全量刷新本文件
/codemap scorers_v2   # 只更新某目录（如需）
```
