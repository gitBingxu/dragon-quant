# CODEMAP.md — dragon-quant 代码地图（语义层 · 手工维护）

> 与 `AGENTS.md`（操作手册）互补：本文件回答「**改某功能要动哪些文件、调用链怎么走、数据怎么流、有哪些不可破的约束**」。
> 结构骨架（cache 键读写表、评分器签名、provider 方法）由 `scripts/gen_codemap.py` 自动生成到 `CODEMAP.generated.md`，本文件只写自动工具表达不了的**语义与意图**。
>
> 维护方式（2+3）：本文件手工维护并纳入「commit 文档同步」规范；`CODEMAP.generated.md` 改代码后 `python scripts/gen_codemap.py` 重生成（CI 可用 `--check` 校验漂移）。

---

## 一、执行路径地图（按调用链）

### scan / scan_v2 主流程
```
cli._cmd_scan / _cmd_scan_v2            cli.py:20 / :36
  → orchestrator.scan(scorers="v1"|"v2")  orchestrator.py:271
      Phase A  板块排行    ths.get_sector_ranking(asc)         orchestrator.py:391
               v2: 领涨 Top5 + DB 黑名单过滤；v1: 领涨 Top8
      Phase B  候选筛选    ths.get_sector_components            orchestrator.py:~420-500
               v2: 每板块当日所有涨停股；v1: 每板块按5日涨幅 Top5
      Phase C  连板+排序   _compute_consecutive_boards          orchestrator.py:513
               v2 额外: _compute_5day_return → Candidate.fived_pct
      Phase D  并发预填    （见下「数据预填」）                  orchestrator.py:~545-590
      Phase E  打分        v1: _score_one  / v2: _score_one_v2  orchestrator.py:129 / :180
                            v2 → scorers_v2.aggregator.evaluate → DragonVerdict
      Phase F  输出+持久化  db.save_dragons(scorer_version=...)   orchestrator.py:~600-840
```

### v2 评分聚合（Phase E 内部）
```
aggregator.evaluate(code, cache, ...)        scorers_v2/aggregator.py
  ├─ drive.score        带动 30%（封板最早 + 脉冲-跟随因果 + 板块共鸣）
  ├─ leadership.score   领涨 25%（连板最多 + 5日涨幅板块内分位）
  ├─ anti_drop.score    抗跌 15%（大盘+板块双基准）
  ├─ liquidity.score    流动 20%（换手 + 封板质量，一字不罚）
  ├─ absorption.score   承接 10%（跨板块虹吸，回看10交易日，不否决）
  └─ 门槛检查 → 任一四大特征 < floor → is_true_dragon=False
     通过者 composite 加权 → DragonVerdict
  rank_verdicts(...) 按 composite 降序赋 rank
```

### Phase D 数据预填（写哪些 cache 键）
| 数据 | v1 | v2 | 写入键 |
|------|----|----|--------|
| 板块 5分K | 当日(聚合) | **近10日历史** | `kline:5min:sector:{}` |
| 板块当日1分K | — | ✅ | `kline:1min:sector:{}` |
| 大盘当日1分K | — | ✅ | `kline:1min:000001` |
| 个股当日1分K | top_n | **全候选**(封板池) | `kline:1min:{}` |
| 批量行情(含盘口) | ✅ | ✅ | `quotes:batch` |

> cache 键完整读写契约见 `CODEMAP.generated.md` 第一节。

---

## 二、任务导航（「我要做 X，看哪些文件」）

| 想做什么 | 主改文件 | 关联/注意 |
|---------|---------|----------|
| 调 v2 权重/门槛/阈值 | `scorers_v2/registry.py` | 全部常量集中于此，勿散落到算法里 |
| 改 v2 某维算法 | `scorers_v2/{drive,leadership,anti_drop,liquidity,absorption}.py` | 共享工具在 `scorers_v2/base.py` |
| 改 v2 聚合/门槛规则 | `scorers_v2/aggregator.py` | `_HARD_DIMS` 决定哪些维设门槛 |
| 新增/改数据源接口 | `providers/base.py` + 具体 provider | 同步 orchestrator Phase D 预填 + `CODEMAP.generated.md` |
| 改板块数据源(行业/概念) | `providers/ths.py`（URL 常量段） | 排行字段铁律 `zdf`，详情页 `/thshy/` |
| 改候选筛选/排序 | `orchestrator.py` Phase A/B/C | v1/v2 分支由 `use_v2` 控制 |
| 改 dragons 表结构 | `storage/db.py` SCHEMA + `_migrate_dragons` | 加列须幂等；读取方按位置索引解包，新列加末尾 |
| 改龙头入库/版本合并 | `orchestrator.py` Phase F + `db.save_dragons` | `scorer_version` UPSERT 合并 CASE |
| 加 CLI 命令 | `cli.py`（parser + dispatch + `_cmd_*`） | 同步 AGENTS.md/README.md |
| 改回测逻辑 | `review.py` | 读 dragons pending；写 review 字段 + vpa |

---

## 三、关键不变式（破坏即出 bug）

1. **评分器是 cache 消费者**：所有 `score()` 只读 `cache.get(key)`，**绝不发网络请求**；数据由 orchestrator Phase A→D 预填。新增维度若需新数据，必须先在 Phase D 写入对应 cache 键。
2. **v1/v2 隔离**：旧 `scorers/` 与 v1 编排路径零改动；v2 全在 `scorers_v2/` + 编排器 `use_v2` 分支。改 v2 不得影响 v1。
3. **RateLimiter 按 provider 串行**：`limiter.submit(provider, endpoint, fn)`，同 provider 串行防封；同花顺排行有 403 频控，已带退避重试。
4. **封单单位铁律**：封单强度 = `Quote.bid1_volume` ÷ `Quote.volume`，**两者同为腾讯 gtimg「手」**，禁止与雪球成交量(股)混用（否则 100 倍误差）。
5. **粒度铁律**：当日盘中时序对比一律 1分K（个股/板块/大盘对齐）；资金承接回看用 5分K 历史。
6. **板块排行字段铁律**：必须用 `field=zdf`（涨跌幅），`tradezdf`(资金流) 无视 order/page。单页 DOM 非严格有序，须本地按 pct 排序。
7. **dragons 版本合并**：同 `(trade_date, code)` 多次写入时 `scorer_version` 并集去重（v1 在前）；5日去重排除同日，否则版本合并被阻挡。
8. **provider 基类新方法用默认 `NotImplementedError`**（非 `@abstractmethod`），否则 `create_providers()` 实例化全部 4 个 provider 时会崩。

---

## 四、再生成

```bash
python scripts/gen_codemap.py          # 重生成 CODEMAP.generated.md
python scripts/gen_codemap.py --check  # CI 校验是否漂移（不一致非0退出）
```
