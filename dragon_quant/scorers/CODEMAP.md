# scorers/CODEMAP.md — 五维评分代码地图

## 职责与入口

评分器只消费 cache，不发网络请求；编排器 Phase D 预填，Phase E 调 `aggregator.py:30` 的 `evaluate()`，Phase F 接入 `aggregator.py:74` 的 `rank_verdicts()`。

| 入口 | 语义与依赖 |
|---|---|
| `aggregator.py:30` | 调五维；四特征低分或异常否决，承接异常仅中性降级；仍计算所有候选诊断分 |
| `aggregator.py:74` | 仅通过者综合分降序、同分代码升序赋 rank；清除否决票旧 rank |
| `drive.py:20` | 稳定封板 40% + 时序带动 35% + 板块共鸣 25% |
| `drive.py:41` | 同行业主板涨停候选有效封板池，缺样本标记降级、同分钟并列 |
| `drive.py:79` | 最后一次回封并保持至末分钟的起点，而不是首次触板 |
| `drive.py:94` | 净振幅局部峰选择、邻近峰抑制，提取真实上涨起点 |
| `drive.py:113` | 一对一脉冲匹配，带动/跟风互斥，同步不判方向；bonus 仅首次触板前 |
| `leadership.py:21` | 同 primary_sector 候选的连板差与五日收益分位，各 50%；按代码去重 |
| `anti_drop.py:25` | 上证 SH000001 60% + 行业 40%，调用相同抗跌函数 |
| `anti_drop.py:49` | 连续时段内跳水段、跌幅加权稳住分、最深段反弹 |
| `anti_drop.py:154` | 完整三分钟内双方实际回升才能奖励反弹；平底取最后低点 |
| `liquidity.py:20` | 换手绝对/相对分及封单质量；普通买一量不视作涨停封单 |
| `liquidity.py:83` | 开板与单分钟触板回落计数；从未触板不拿稳定性满分 |
| `absorption.py:17` | 目标行业与扫描日领跌 Top20 的十日五分钟线，fallback 50 |
| `absorption.py:61` | 六根连续 K 检测、每板块独立因果校验，同/前移窗口命中不重复计板块 |
| `absorption.py:127` | 正向强度 40% + 广度 20% + 持续性 40%，多窗口奖励封顶 15 |

## 共享工具与配置

| 位置 | 不变式 |
|---|---|
| `base.py:22` | UTC+8 日期与上午/下午交易时段 |
| `base.py:27` | 分钟轴缺口、午休和隔夜分段，不把间隔当连续分钟 |
| `base.py:39` | 涨停价相对容差，drive/liquidity 共用 |
| `base.py:44` | DragonVerdict：真龙判定、诊断分、可空排名、五维结果及否决原因 |
| `base.py:75` | 直接使用 KBar.pct，前向填充不跨时段，不把首分钟当昨收 |
| `base.py:99` | 降序竞争排名 `(1-r/n)×100`，单样本为零 |
| `registry.py:9` | 固定五维权重、四维门槛；指数代码与降级分亦集中配置 |

## 数据流契约

| 上游预填 / 参数 | 消费方 |
|---|---|
| `kline:1min:{code}` | drive、anti_drop、liquidity |
| `kline:1min:SH000001` | anti_drop；与股票 `000001` 独立 |
| `kline:1min:sector:{sector}` | drive、anti_drop |
| `sector:components:{sector}` / `quotes:batch`（list[Quote]） | drive、liquidity；来自最多约 50 只行业成分股样本 |
| `candidate_pool` | drive 封板池及 leadership 连板/五日收益 |
| `kline:5min:sector:{sector}` / `all_sector_codes` | absorption；未显式传代码时读 `__meta__:sector_codes` |

## 修改导航与约束

- 调参数：`registry.py:9`；改公式时同步 `评分器Refactor.md:1`、根 README/AGENTS 与 `../../tests/test_scorers.py:1`。
- 改入选：`aggregator.py:74` → `../orchestrator.py:669` → `../storage/db.py:431`；全部明细保留，只有真龙 Top N 入 dragons。
- 改时序：先看 `base.py:27`；不得跨午休/隔夜，五分钟对手盘缺点不填充。
- 封单量、成交量均来自腾讯“手”，不混用雪球“股”；一字不额外惩罚。
- 承接滑动窗口可重叠，bonus 按有效窗口数计算；逐板块先跌后拉只是时序证据，不证明实际资金流。
- 旧评分包已删除，不恢复 scorers_v2；SQLite 继续使用 *_v2 以兼容历史数据。
