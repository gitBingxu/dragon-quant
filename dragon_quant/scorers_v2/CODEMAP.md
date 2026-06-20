# scorers_v2/CODEMAP.md — v2 五维「识别真龙」评分器

> 子目录代码地图。整体执行路径见根 `../CODEMAP.md`。
> 统一签名 `score(code, cache, **kwargs) -> ScoreResult`，**只读 `cache.get`，不发请求**。

## 职责

把「真龙」拆成五个可计算维度，每维独立评分（0-100），再由 `aggregator` 做
**门槛 + 加权**两段式聚合：四大特征（drive/leadership/anti_drop/liquidity）任一低于门槛
→ 一票否决（`is_true_dragon=False`）；absorption 不否决仅加权贡献。

## 关键导出

| 文件 | 入口 | 维度/权重 | 子因子（算法要点） |
|------|------|----------|------------------|
| `aggregator.py:30` | `evaluate(code, cache, *, candidate_pool, primary_sector, all_sector_codes, sector_name_map)` | 聚合 → `DragonVerdict` | 调五维 → 门槛检查(`_HARD_DIMS`) → composite 加权 |
| `aggregator.py:68` | `rank_verdicts(verdicts)` | — | 通过门槛者按 composite 降序赋 rank |
| `drive.py:22` | `score(...)` | 带动 30% | 封板最早(`_early_seal:46`) + 带动板块脉冲跟随因果(`_lead_sector:84`) + 板块共鸣(`_voice:214`) |
| `leadership.py:21` | `score(...)` | 领涨 25% | 连板最多(BOARD_W) + 5日涨幅板块内分位(`_peer_fived:64`，样本=候选池) |
| `anti_drop.py:22` | `score(...)` | 抗跌 15% | 双基准 `_antidrop_vs(:46)`：大盘60%+板块40%；横盘稳住+率先起飞(`_rebound:125`)；跳水段 `_dip_segments:87` |
| `liquidity.py:20` | `score(...)` | 流动 20% | 换手充沛度(绝对+相对分位) + 封板质量(封单强度+开板次数 `_count_open:84`，一字不罚) |
| `absorption.py:22` | `score(...)` | 承接 10% | 跨板块虹吸 `_detect_events:71` + 三维打分 `_score_event:170`(强度40%/广度20%/持续40%)，回看10交易日 |

## 共享工具（base.py）

| 导出 | 行 | 用途 |
|------|----|------|
| `DragonVerdict` | `base.py:19` | 聚合产物：is_true_dragon / composite / rank / dims / reject_reason |
| `clip` | `base.py:29` | 数值截断到 [lo,hi] |
| `align_1min` / `common_minute_axis` | `base.py:33/38` | 1分K 按分钟 bucket 对齐 |
| `gain_curve` | `base.py:50` | 归一化涨幅曲线（**用 `KBar.pct`，非首分钟价**） |
| `desc_rank_score` | `base.py:69` | 板块内降序排名分位 `(1−r/n)×100` |

## 配置常量（registry.py）

权重/门槛/各维阈值全部集中在 `registry.py`——`DIM_WEIGHTS`、`DIM_FLOORS` 及各维子因子常量。调参只改这里，**勿散落进算法文件**。

## 依赖关系

- 上游：`orchestrator.py` Phase D 预填 cache（`kline:1min:{}`/`kline:1min:000001`/`kline:1min:sector:{}`/`kline:5min:sector:{}`/`quotes:batch`/`sector:components:{}`），Phase E `_score_one_v2` 调 `evaluate`。
- 读取的 cache 键见根 `../CODEMAP.md` §三。
- 与旧 `scorers/`（v1）**完全隔离**，互不引用。

## 本维度相关不变式

1. 评分器只读 cache，不发请求。
2. `gain_curve` 用 `KBar.pct`（provider 已按昨收填好），避免高开股量级被压缩。
3. leadership 涨幅样本用**候选池 fived_pct**（非成分股 five_day_return，后者未拉日K者为0会污染）。
4. liquidity 封单强度 = `bid1_volume ÷ volume`（同为 gtimg「手」）；一字板不罚。
5. absorption 强度正向口径：出逃规模越大 + 拉升越高 → 分越高（非旧版反向稀释）。
