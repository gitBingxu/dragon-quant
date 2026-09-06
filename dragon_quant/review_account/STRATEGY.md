# 账户级 Review 操盘策略

> 本文档基于 `dragon_quant/review_account/` 下的实际代码梳理，是 `review-account`
> 账户级模拟交易当前执行的完整操盘策略说明。若代码逻辑变更，请同步更新本文件。
>
> 涉及文件：
> - `strategy.py` — 买入/卖出信号判定（策略核心）
> - `simulator.py` — 按交易日推进、撮合成交、账户资金/持仓管理
> - `indicators.py` — 日线指标计算（MA、涨跌幅、缺口、涨停判定等）
> - `models.py` — `StrategyConfig` 策略参数、持仓/交易/快照等数据模型
> - `service.py` — CLI/UI 入口，运行并持久化结果

---

## 一、整体设计思想

`review-account` 不同于旧的 `review`（后验式：以入选后最低价买入并算最大收益）。它**像真实交易员一样按交易日逐日推进账户**，严格遵守以下约束：

- **无未来函数**：开盘买点仅使用「真龙池 + 上一交易日技术指标 + 当日开盘价」；分歧买点在盘中承接确认时只使用「截至上日的日 K 历史 + 当日开盘到确认窗口的 5 分钟 K」，均不读取当日 close/high/low 等未来信息。
- **候选票池（近 N 日并集）**：不再只看昨日真龙池，改为取**近 `candidate_lookback_days`（默认 3）个交易日**真龙池的并集去重，以便在一字板龙头断板日仍能捕捉到它（此前它可能 2–3 天前就已入池）。
- **T+1 制度**：买入当日不可卖出（`entry_date >= day` 的持仓在当日跳过卖出判定）。
- **真实成本**：买入含滑点 + 佣金，卖出含滑点 + 佣金 + 印花税。
- **资金约束**：按可用现金整手买入，卖出当日释放的现金**不再当日买入**（次日起才继续开仓）。
- **交易日历驱动**：用 `build_trade_calendar` 遍历每个真实交易日；若近 N 日均无龙头记录则当日不开仓，**不回退到更早的有记录日期**。
- **多持仓**：允许同时持有多只（默认上限 `max_positions=5`）。

---

## 二、策略参数（`StrategyConfig` 默认值）

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `initial_cash` | 100,000 | 初始资金 |
| `candidate_top_n` | 5 | 每日从真龙池取前 N 只作为候选 |
| `candidate_lookback_days` | 3 | 候选票池回看天数（近 N 个交易日真龙池并集去重） |
| `max_positions` | 5 | 最大同时持仓数 |
| `min_score` | 50.0 | 候选综合分门槛 |
| `min_amount` | 200,000,000（2 亿） | 候选成交额门槛（元，**分歧买点不适用**） |
| `min_turnover` | 5.0 | 候选换手率门槛（%，**分歧买点不适用**） |
| `strong_turnover_min` / `max` | 8.0 / 35.0 | 「弱转强」买点的换手率区间（%） |
| `max_open_gap` | 7.0 | 允许的最大开盘涨幅（%） |
| `max_close_to_ma5` | 12.0 | 开盘价距 MA5 上限（%） |
| `divergence_enabled` | True | 是否启用「分歧买龙」买点 |
| `divergence_min_boards` | 2 | 断板前连续一字涨停的最少板数 |
| `divergence_require_shrinking_volume` | True | 是否要求一字板期间成交量非递增（缩量确认） |
| `divergence_confirm_bars` | 6 | 断板日承接观察窗口的 5 分钟 K 根数（6 根=30 分钟） |
| `divergence_break_open_ratio` | 0.998 | 开盘价低于「涨停价 × 此值」判为断板 |
| `stop_loss_pct` | -5.0 | 硬止损线（%） |
| `breakeven_activate_pct` | 6.0 | 保本止盈的浮盈激活阈值（%） |
| `volume_spike_pct` | 30.0 | 放量止盈阈值（较上日成交量增幅 %） |
| `buy_slippage` / `sell_slippage` | 0.002 / 0.002 | 买入/卖出滑点 |
| `commission_rate` | 0.0003 | 佣金率（双向） |
| `stamp_tax_rate` | 0.0005 | 印花税（仅卖出） |
| `lot_size` | 100 | 最小交易单位（一手） |

> 说明：`take_profit_pct`、`trailing_*`、`max_hold_days` 等字段仍保留在配置中，但**当前主卖出路径不再使用固定止盈 / 移动止盈 / 最长持有天数强制卖出**（详见卖出策略）。

---

## 三、每日执行流程（`simulator._process_day`）

对交易日历中的每个交易日，按顺序执行：

1. **先处理已有持仓的卖出**：遍历所有持仓（跳过当日刚买入的，满足 T+1），
   调用 `evaluate_sell` 取卖出信号并撮合成交；未触发则记「继续持有」事件。
2. **再处理开仓买入**：仅当**当日没有卖出发生**（`sold_today=False`）、现金充足、
   且持仓数未达上限时，才尝试买入（`_try_buy`）。
3. **生成当日快照**：记录现金、市值、总权益、当日收益、累计收益、回撤，以及持仓明细。

回测只纳入已收盘交易日；区间末尚未平仓的持仓按最后可得收盘价估值。

---

## 四、买入策略（`evaluate_buy`）

候选来自**近 `candidate_lookback_days`（默认 3）个交易日**真龙池的并集（按 `code`
去重，保留 rank 更优的一条），每日取综合排序后的前 `candidate_top_n` 只判定买点。

买点分三类，按优先级从高到低：**分歧买龙（400）> 开盘贴近 MA5（300）> 开盘突破前高（200）**。
其中开盘两类买点在**当日开盘**成交，分歧买龙在**断板日盘中承接确认后（约 10:00）**成交。

### 4.1 硬性过滤

**通用门槛（三类买点都要满足）**

1. `is_true_dragon` 不能为 `False`（必须是真龙候选）；
2. 综合分 `composite_score >= min_score`（默认 50）；
3. 已持有该股票则不重复买入（在 `simulator` 中过滤）。

**开盘买点额外门槛（仅买点 B/C 适用，分歧买点豁免）**

4. 成交额 `amount >= min_amount`（默认 2 亿，自动兼容元/万元口径）；
5. 换手率 `turnover >= min_turnover`（默认 5%）；
6. 当日**非一字板**（一字板无法按开盘策略介入）。

> **分歧买点不套用 `min_amount`/`min_turnover`/非一字板过滤**：缩量一字板龙头在入池当日
> 换手与成交额本就很小，若沿用会把目标标的直接过滤掉。分歧买点改用「连板 + 缩量 + 断板日盘中
> 真实承接」三重约束把关（详见 4.2 买点 A）。

### 4.2 三类买点（满足其一即触发）

**买点 A：分歧买龙（`buy_divergence_first_break`，优先级 400）**

针对「连续缩量一字板 → 第一次断板 → 有承接才买」的龙头分歧战法。判定分三步，全部满足才买入：

1. **连续缩量一字板**（用截至上一交易日的日 K 历史判定）：
   - 今日之前紧邻的 K 线连续为「一字涨停」（`is_one_word_board` 且 `pct >= 9.9`），
     连板数 `>= divergence_min_boards`（默认 2）；
   - 若 `divergence_require_shrinking_volume=True`，这些一字板期间成交量**非递增**（缩量确认）。
2. **第一次断板**（当日开盘即可确认，无未来函数）：
   - 今日开盘价**低于涨停价**（`open < 涨停价 × 0.998`），即开盘打开一字、可成交 → 断板。
3. **承接确认**（依赖当日开盘后前 `divergence_confirm_bars` 根 5 分钟 K，默认 6 根=30 分钟）：
   - 窗口内最低价**不跌破昨收**（当日始终红盘；宁可不做也不做弱）；
   - 窗口末根收盘 `>=` 窗口首根开盘（买盘承接、非单边下滑）；
   - **回封涨停**（窗口内任一根触及涨停）视为最强承接，直接买入；
   - **5 分钟 K 缺失则跳过该股分歧买入**（不做日 K 近似回退，避免未来函数）。
   - 成交价：回封按涨停价，否则按窗口末根 5 分钟 K 收盘价（信号携带 `execution_price`）。

**买点 B：开盘贴近 MA5 承接（`buy_open_ma5_pullback`，优先级 300）**

- 存在上日 MA5，且开盘价 `open <= MA5 * 1.03`（贴近或回踩 MA5）；
- 开盘涨幅在 `(-3%, max_open_gap]` 之间（或缺口未知）。
- 逻辑：回踩 5 日线后具备承接条件。

**买点 C：开盘突破前高，竞价弱转强（`buy_open_turn_strong`，优先级 200）**

- 开盘价 `open > 上日最高价`（突破前高）；
- 开盘涨幅在 `[0, min(5.5, max_open_gap)]` 之间；
- 开盘距 MA5 `<= max_close_to_ma5`（默认 12%）；
- 换手率落在 `[strong_turnover_min, strong_turnover_max]`（默认 8%~35%）；
- 成交额 `>= 5 亿`。

### 4.3 多候选择优（`simulator._try_buy`）

当同一日多只候选都触发买点时，按以下键降序排序，取第一名开仓：

1. **信号优先级**（分歧买龙 400 > 开盘贴近 MA5 300 > 开盘突破前高 200）；
2. 真龙池 `rank` 更靠前（rank 越小越优先，取近 N 日并集中的最优）；
3. 综合分更高。

### 4.4 成交与仓位

- 成交价：开盘买点为 `开盘价 × (1 + buy_slippage)`；分歧买点为 `execution_price × (1 + buy_slippage)`；
- 买入数量 = 用**全部可用现金**整手买入（`floor(cash / (price × lot)) × lot`）；
- 若「金额 + 佣金」超过现金则回退一档，再次校验；仍不足一手则记「现金不足」空仓。
- 买入费用 = `金额 × commission_rate`。

---

## 五、卖出策略（`evaluate_sell`）

每个交易日对每只**非当日买入**的持仓按**固定优先级**判定，命中即返回并执行（列表可含多笔，如涨停半仓 + 转弱清仓）。

> 卖出判定同时会更新持仓的历史最高浮盈 `highest_return` 与最高价 `highest_price`，
> 供保本止盈的「先浮盈后回落」判断使用。

### 优先级顺序（自上而下）

**① 硬止损（`hard_stop_loss`）—— 最高优先级**
- 当日最低价触及 `stop_loss_pct`（默认 -5%）即全部清仓。
- 成交价：若开盘已跌破止损价则按开盘价，否则按止损价。

**② 保本止盈 / 浮盈回落保护（`profit_back_to_cost_take_profit`）**
- 体现「坚决不能亏钱」：最高浮盈达到 `breakeven_activate_pct`（默认 6%）后，
  若价格回落到**完整成本线**则保护性离场。
- **完整成本线**（`_break_even_signal_price`）覆盖买入成本、卖出滑点、卖出佣金 + 印花税：
  `break_even = cost / qty / [(1 - sell_slippage) × (1 - commission_rate - stamp_tax_rate)]`
- **触发判定**（`_is_breakeven_retrace`，避免用日 K 高低点臆测盘中顺序）：
  - 若**此前交易日**最高浮盈已达激活阈值 → 当日最低价 `<= 完整成本线` 即触发（按日 K 最低）；
  - 否则需用当日 5 分钟 K 确认「先冲到激活价、后回落到成本线」的真实顺序。

**③ 高开未封板清仓（`_high_open_no_limit_signal`，依赖 5 分钟 K）**
- 开盘高开 **≥ 7%**：若 **5 分钟内（1 根 5 分 K）** 未触及涨停 → 清仓
  （`high_open_7pct_no_limit_5m_clear`）。
- 开盘高开 **≥ 5%**（且 < 7%）：若 **30 分钟内（6 根 5 分 K）** 未触及涨停 → 清仓
  （`high_open_5pct_no_limit_30m_clear`）。
- 历史 5 分钟线缺失时**跳过**该窗口规则（持有事件会标注「缺少 5 分钟 K」）。

**④ 买入次日（`hold_days == 1`）专属规则**
- 次日收盘涨停：先按涨停价**卖出半仓**（`next_day_limit_up_half`）；若同时收盘转弱
  （收盘 < MA5 或收盘 < 开盘）则**清仓剩余**（`next_day_limit_up_clear`）。
- 次日未涨停且**收盘价 < 开盘价** → 清仓（`next_day_close_below_open`）。

**⑤ 常规持仓止损/止盈（`hold_days >= 2`）**
- **收盘价 < 开盘价** → 止损清仓（`close_below_open_stop`）；
- **收盘价 < MA5**（日内均线近似）→ 止损清仓（`break_intraday_ma_stop`）；
- **放量未涨停**：成交量较上日增幅 `>= volume_spike_pct`（默认 30%）且未涨停 → 清仓
  （`volume_spike_take_profit`）；涨停则继续持有。

> **已移除的旧规则**：不再按「收盘高于 MA5」固定止盈，不再按 `max_hold_days` 最长持有天数强制卖出。

### 卖出成交价（`simulator._sell_execution_price`）

- 若信号已带 `execution_price`（如 5 分钟窗口清仓、保本价）则优先用之；
- `hard_stop_loss`：开盘跌破按开盘价，否则按止损价；
- `profit_back_to_cost_take_profit`：按 `entry_price`（成本价近似）；
- `next_day_limit_up_half`：按收盘价（涨停价）；
- 其余默认按当日收盘价。
- 最终成交价再扣卖出滑点：`成交价 × (1 - sell_slippage)`；卖出费用 = `金额 ×
  (commission_rate + stamp_tax_rate)`。

### 半仓处理

- `next_day_limit_up_half` / `take_profit_half` 卖出约一半（整手向下取整）；卖出后
  剩余持仓标记 `took_profit_half=True`，成本按比例摊减，继续持有等待后续信号。

---

## 六、数据来源与指标

- **日 K**：雪球 `XueqiuProvider.get_kline`（`days=260`, `fq_type="normal"`），
  经 `enrich_daily_klines` 计算 MA5/10/20、成交量/额/换手 MA5、开盘缺口
  `open_gap_pct`、`close_to_ma5_pct`、一字板/涨停收盘判定、3/5 日收益、5 日最大回撤、
  5 日平均振幅等。
- **5 分钟 K**：`get_5min_kline_for(code, target_ts)`，仅取当日分钟条，用于高开未封板
  窗口判定、保本止盈的盘中顺序确认，以及分歧买点的断板日承接确认。
- **候选池**：取近 `candidate_lookback_days`（默认 3）个交易日的
  `db.get_dragons_by_date(交易日, top_n, source)` 并集，按 `code` 去重（保留 rank 更优者）
  （默认 `source="v2"`，即 `dragons_v2` 表）。

---

## 七、账户核算与产出

- **快照（`Snapshot`）**：每日现金、市值、总权益、当日/累计收益、回撤，及持仓明细
  （单持仓给出成本、市价、浮盈亏；多持仓聚合为 `MULTI`）。
- **交割单（`Trade`）**：每笔买卖含价格、数量、金额、费用、**当笔已实现盈亏**
  （买入为 `-手续费`，卖出为扣成本后的收益）、成交后现金与持仓、原因码与信号 JSON。
- **已平仓（`ClosedPosition`）**：买入/卖出价、退出原因、持有天数、实现收益率。
- **决策时间线（`TimelineEvent`）**：买入/卖出/持有/空仓每日事件与原因，供 UI 解释每日决策。
- **批次汇总**：初始资金、最终权益、累计收益、最大回撤、成交笔数、胜率。

结果通过 `service.run_review_account` 持久化到 `review_account_runs` /
`review_account_snapshots` / `review_account_trades` / `review_account_positions` /
`review_account_events` 表，并在 UI `/account` 页面展示。

---

## 八、运行方式

```bash
# CLI
python -m dragon_quant review-account --from 20260501 --to 20260601
python -m dragon_quant review-account --from 20260501 --to 20260601 --capital 200000 --ui
python -m dragon_quant review-account --ui-only --source v2

# UI：/account 页面「生成回测记录」按钮，填写记录名称 / 日期范围 / 初始资金
```
