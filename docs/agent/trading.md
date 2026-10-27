# trading — review-account 与 buy/sell 的产品与运维契约

> 触发：改动 `review-account` 回测、`buy` / `sell` 实盘辅助、候选池消费、数据分级、`buy_sell_signals` 信号表、`/account` API、费用与评估口径时读本文。
> 本文权威：账户回测与实盘辅助的产品/CLI/运维契约（候选池消费、数据分级、信号表语义、`/account` API、费用与评估口径）。
> 上游权威：AGENTS.md（硬约束与工作流）；策略规则、参数、卖出链、P/A、买点诊断的判定与数值以 [策略规则](../../dragon_quant/review_account/strategy/STRATEGY.md) 为准（本文只引用、不复制）。

## 账户级模拟交易

```bash
python -m dragon_quant review-account --from 20260501 --to 20260601
python -m dragon_quant review-account --from 20260501 --to 20260601 --capital 200000 --ui
python -m dragon_quant review-account --ui-only --source v2
```
`review-account` 不替代 `review`。统一实现位于 `review_account/`：`market.py` 负责候选与时间/指标切片，`data.py` 校验日K和完整5分钟K，`strategy/` 产生信号（`buy.py` 买点、`checks.py` 诊断、`sell.py` 责任链、`execution.py` 撮合/仓位/费用），`engine.py` 顺序推进账户。`simulator.py` 与 `review_account/live_trade/signal_engine.py` 必须复用同一 `evaluate_buy`/`evaluate_sell`/`fill_price`/`fee`，不得复制买卖/成交规则。

- 候选池优先读**盘后复盘快照**（`prep_snapshots`，`prep` 命令产出：主线龙头 + 最高标 + 晋级者 + 情绪温度/建议仓位）；快照消费仅对 `source=="v2"` 生效。默认 `prep_verdict_filter_enabled=True`：`verdict=ignore` 出池，`strong/light/unrated` 保留（unrated 仅 P=False 排序垫底）；推荐集（strong/light）为空/快照缺失/读取异常/无资格时回退旧逻辑——取之前 **5** 个真实交易日的**全部真龙**并集（`assets.db.dragons` 镜像经 `candidate_loader("v2")`；`--source v1` 例外读 `dragons_v1`），共享层过滤否决/低于50分，按 code 去重保留综合分更高者。回退池**不再应用 ignore 过滤**，P 按快照 verdict 如实标注（ignore→False、无该 code→Unknown），输出 `fallback_reason`。回测额外加载区间前交易日，首日可开仓。**研究基线声明**：`candidate_lookback_days` 默认 3→5 是策略默认参数变更，历史 run 与本基线**不可直接比较**；复现旧基线请显式 `candidate_lookback_days=3`（prep 命中路径不受该参数影响）。

- Asia/Shanghai 时序与四类买点判定（开盘回踩 MA5、首根 5 分钟 K 突破确认、分歧买龙、龙回头）的阈值与窗口见 [策略规则 §8](../../dragon_quant/review_account/strategy/STRATEGY.md)。硬性约束：**不允许用未来信号倒选**；日K兜底日不触发首根 5 分钟 K 突破确认。

- 择优（P/A 三值）与竞价排序/否决开关由 `priority.rank_key` 单一实现、`TradingEngine` 与 `SignalEngine` 共用，语义与默认值见 [策略规则 §7](../../dragon_quant/review_account/strategy/STRATEGY.md)。产品层契约：`buy` 保持 buy-all（排序只影响 pending 顺序）；`buy` / `review-account` 共用排序，输出 `ranking_mode` 与 `fallback_reason`，被否决但本可买入的标的在 `details` 带 `vetoed_signal`。

- 信号待下一可执行事件成交，实时需更晚的新鲜报价；封死涨跌停不假定成交，跳空不能补填止损或成本线。T+1不卖当日仓，但跟踪买入后峰值；同一根K的高点不能反向激活该根K内的保护。

- 卖出为 10 条 `SellRule` 责任链（顺序即优先级、命中短路），规则清单、默认开关与 `sell_rule_toggles` 语义见 [策略规则 §1/§2](../../dragon_quant/review_account/strategy/STRATEGY.md)。硬性时序：15:00 只估值与更新峰值，不以最终收盘价补成交。

- **首日止损 `first_day_stop`（D3 首日浮亏减半，默认关）**：半仓减仓、灾难线优先整清、`took_profit_half` 单一半仓腿、`first_day_risk_distance_pct()` 风险距离口径见 [策略规则 §1.1](../../dragon_quant/review_account/strategy/STRATEGY.md)。**运维契约：开启后 T+1 必须至少运行一次 `sell`**（`buy` 不评估卖出），否则首日窗口静默错过。

- 当天卖出后不买入；不因下午卖出而取消早上交易。默认每日最多买一次，仓位基线仍使用可用现金；共享配置可开启单票25%/风险1%、MA5距离/趋势、ATR移动止盈实验，未验证参数不自动推广。
- `--config` JSON 原样传给共享 StrategyConfig；滑点0.2%、佣金0.03%、卖出印花税0.05%、每笔最低佣金5元。`evaluation.compare_strategies` 按60/20/20时间划分、最少交易数与双倍滑点验证，不自动换默认参数。

- 择时与仓位/组合风控/`entry_veto`/龙回头由共享 `StrategyConfig` 字段驱动，字段与默认值见 [策略规则 §3/§4](../../dragon_quant/review_account/strategy/STRATEGY.md)。**基线声明**：`dragon_comeback_enabled` 默认开启（显式 `false` 可复现旧基线），默认开启后历史回测结果不可直接与旧基线比较；其余改造 default-off，未验证参数不自动推广。

- 数据分级：历史交易日按 code 三态判定——完整 48 根 5 分钟 K → 严格盘中撮合；无分时但当日日K有效 → 整日**日K兜底**（开盘价买、当日 OHLC 判卖，分时相关规则自然跳过）；当日日K缺行或 `volume` / `amount` 双零且无分时 → **停牌**（不进 `rows`、不评估买卖、按最近可得收盘价估值）。剔除停牌后再判整日兜底；分时可用但当日日K缺行记 `daily_unavailable`（不建行、不静默消失）。日K无历史/预热不足/无效 OHLC 仍报 `DataCoverageError`，不持久化部分结果；当日（盘中与收盘后）不做停牌分类，保持旧契约硬报错。兜底成交价保守近似，结果标 `data_quality`（`strict_5min` / `mixed` / `daily_fallback`）、`fallback_days`、`suspended_days` / `suspended_codes` / `suspended_days_count` / `unavailable_days` / `data_degraded`；停牌候选仍在 `details` 带 `reason_code=suspended`。行情按日期/周期/不复权口径隔离缓存；`SH000001` 用于交易日历，个股 `000001` 仍是平安银行。状态机与回测↔实盘一致性见 [策略规则 §5](../../dragon_quant/review_account/strategy/STRATEGY.md)。

- `/account` 生成/删除和历史批次查询保持不变；API可传 `strategy_params`，快照增量补 `positions_json` 存全部持仓（查询返回 `positions`），旧数据仍可读取；已平仓表保持原语义。完整规则见 `review_account/strategy/STRATEGY.md`。
- **清仓后窗口指标**：回测结束后由 `service.run_review_account` 调用 `simulator.annotate_post_exit`（**不在 `AccountSimulator.run()` 内**，回测核心保持纯 as-of），对每条已平仓记录按 code 取一次日K计算 `post_exit_max_return` / `post_exit_max_drawdown` / `post_exit_window_days`，失败逐条降级为 NULL/0、不中断回测。窗口为清仓日**之后**的 5 根有效日K（停牌占位行不计入、不足按实际、无 bar 为 NULL/0 天），允许越出 `date_to`（事后评估）；最高盈利 =（窗口最高−卖出）/卖出×100，最大回撤 = min(0,（窗口最低−卖出）/卖出×100)。旧 run 不回溯，三列为 NULL。
- **买点统计**：`evaluation.entry_reason_stats(positions, initial_cash)` 按 `BUY_SIGNAL_CODES` 四类（开盘回踩MA5/弱转强/分歧买龙/龙回头，0 笔也输出）+ 未知 code + 合计行，输出笔数/盈利笔数/胜率/平均每笔收益/累计贡献；胜率与 `_result()["win_rate"]` 同源（`realized_return > 0`）。累计贡献为近似值 `Σ(realized_return/100×entry_price×qty)/initial_cash×100`（忽略买入佣金）。`GET /api/account/positions` 返回 `{"data": [...], "stats": {...}}`（只增键），旧 run 也可实时聚合。
- `/account` 前端：`AccountStats` 之后新增「买点统计」卡片；「单票收益汇总」新增「清仓5日内最高盈利」「清仓5日内最大回撤」两列（NULL 显示 `—`，窗口不足 5 根标注实际天数，表头提示可能含回测区间外数据）。

## 实盘辅助交易（buy / sell）

```bash
python -m dragon_quant buy --date 20260907 --at 10:00
python -m dragon_quant buy --json        # 完整 JSON（details[].checks 含四类买点诊断）
python -m dragon_quant sell --date 20260908 --at 14:55
python -m dragon_quant buy --force      # 非交易时段强制跑当日回放（09:30 前仍拒绝）
python -m dragon_quant sell --force
```
`buy/sell` 底层与 `review-account` 完全复用同一 `review_account` 策略与成交函数（`evaluate_buy`/`evaluate_sell`/`fill_price`/`fee`/`apply_sell_fill`/`track_peak` 等），只做信号记账、**不维护真实账户**：只要候选池标的触发买点就记一条买入（buy-all，不再择优取第一只）；`sell` 从 `buy_sell_signals` 筛出「买入日之前且 as-of 未平仓」的信号，回放当日行情判定卖出。策略调参只需改 `review_account` + 回测，`buy/sell` 自动跟随。`buy` 与 `review-account` 共用 P/A 三值排序与否决；输出 `ranking_mode`（`auction`/`auction_unverified`/`prep_only`/`legacy`）、`fallback_reason`，被否决但本可买入的标的在 `details` 中带 `vetoed_signal`。

- **四类买点诊断（checks）**：`buy` 对每个候选（买入与未买入）逐条输出 ① 开盘回踩MA5 / ② 弱转强 / ③ 分歧买龙 / ④ 龙回头 的状态与带数值原因；状态枚举 `passed` / `failed` / `pending` / `window_closed` / `unavailable` / `skipped`（不伪造失败）。判定窗口、阈值与取帧/去重语义见 [策略规则 §8](../../dragon_quant/review_account/strategy/STRATEGY.md)。产品契约：`checks` 为 detail 的只增字段，不写入 `buy_sell_signals`；`buy --json` 的 `details[].checks` 每项含 `rule` / `label` / `window` / `status` / `selected` / `reason`，detail 另带 `observed_at` / `phase`；事件级闸门（风控/已卖出）输出 `buy_eval_skipped` + 四条 `skipped`，买点命中但未成交（不可成交价/超时/预算不足/回放结束）仍展示四类并标 `selected`。

- `SignalEngine`（`review_account/live_trade/signal_engine.py`）复刻 `TradingEngine.step` 的「决策 → 下一可执行事件成交」两段式与峰值跟踪，去掉资金/仓位/次数限制；卖出数量用 `notional_quantity`（`initial_cash × max_position_pct`，与回测首仓同口径）落库冻结，保证 `break_even_price` 与回测精确一致；买入不足一手时与回测一样不建仓。卖出/半仓的仓位记账经共享 `apply_sell_fill`，峰值经共享 `track_peak`，两引擎不复制规则。
- **半仓记账**：`next_day_limit_up_half` 或 `first_day_stop_half` 触发时 `half_close_signal` 冻结 `half_exit_qty/price/reason_*`（`half_exit_reason_code` 区分止盈/止损减半）且保持 `status='open'`，剩余半仓继续跟踪；后续规则可再次触发 `close_signal` 全清。`took_profit_half` 是「该信号已完成一条半仓腿（不分盈亏）」的唯一性守卫，两条半仓原因码共用 → 同一信号不会出现两条半仓腿。
- **影子风控**：`review_account/live_trade/shadow.py` 从 `buy_sell_signals` 全量记录按回放时点（`as_of=target`）重建 `AccountState`，逐事件调用同一 `risk_governor`；日级熔断/连亏熔断跟随，`equity<=0` 额外触发破产闸；delevel 降仓在无 sizing 场景不适用。`build_shadow_state` 的 `cash` 是净值轧差项（不是现金主张），run 内买入同步扣减。
- 数据分级：buy/sell 的 `codes` 均为「候选 ∪ as-of 持仓」，任一分时缺失即整日走日K兜底；结果带 `data_quality` 与 `degraded` 提示。历史交易日停牌标的（当日日K缺行或双零量且无分时）按 code 跳过：停牌持仓冻结（不评估卖出、按最近可得收盘价保持净值），结果带 `suspended_codes`，`degraded` 增加停牌说明；当日不做停牌分类。
- 历史日期必须同时传 `--date YYYYMMDD --at HH:MM`（重放当日截至该时点的5分钟K，as-of 过滤也以该时点为准）；实时今日回放截至当前已完成的5分钟K。绝不使用当前腾讯报价。
- **非交易时段 `--force`**：`buy`/`sell` 均支持，放行「午间休市（11:30–13:00）」与「已收盘（≥15:06）」两个时段闸门；**09:30 前仍拒绝**（无完整5分钟K，强制无意义，保留原「当前尚未开盘」报错）。15:00–15:06 属既有收盘宽限窗口（本来就允许运行），`--force` 在该窗口不生效，日K兜底仍按收盘价成交。`--force` 不解除任何数据/日期校验（非交易日、历史日期缺 `--at`、无完整5分钟K 照常报错）；与 `--at` 同给时 `--at` 优先、force 静默无效。强制模式的事件流截断到最后一个已完成5分钟K（午休=11:30、收盘=15:00），影子状态 as-of 仍取真实当前时点，**不伪造收盘价成交**（日K兜底日的 15:00:00.001 收盘成交腿被截断，15:00 的 late 决策事件仍执行但不成交）。强制运行照常写 `buy_sell_signals`：`signal_json` 带 `"forced": true` 便于事后辨认，人读输出打印一行强制模式提示（交易时段内加 `--force` 不打印、不标记）。
- 信号表 `buy_sell_signals`：`insert_signal` 对同一未平仓 code 幂等（部分唯一索引 `idx_buy_sell_open`），返回 `(id, created)`；`list_open_signals(before_date=...)` 只取买入日早于目标日的持仓，`list_all_signals` 供影子重建；峰值经 `update_signal_peaks` 跨日写回供移动止盈/保本判定。
- 运维契约：峰值新鲜度依赖每个交易日至少运行一次 `buy` 或 `sell`（buy 也会写回既有持仓峰值）；历史重放会写库，幂等命中时输出标注「未落库」。
- 同输入/参数/信号状态下买卖决策与回测一致；实时延迟与成交条件仍可能导致结果不同，不保证收益。
- `buy` 输出首行显示**建议仓位**（来自盘后复盘快照 `sentiment.position_pct`，`load_market_sentiment` 读取，缺失时显示 `—`）。该值**仅供人工参考，不参与下单**：`SignalEngine` 不维护真实账户（仅有影子状态），只把它用于禁买判定（`sentiment_ban_enabled` 开启且 `position_pct==0` 时否决买入）。`sell` 不显示仓位。

## 账户侧与信号表契约

| 表 | 用途 | 关键点 |
|----|------|--------|
| `review_account_runs` | 账户级 review 批次 | 保存 UI 记录名称、策略参数、区间、初始资金、最终权益、收益率、最大回撤 |
| `review_account_snapshots` | 账户每日快照 | 保存现金、市值、总权益、收益曲线、当前持仓 |
| `review_account_trades` | 账户交割单 | 每笔买卖含 `reason_code` / `reason_text` / `signal_json` |
| `review_account_positions` | 已平仓持仓 | 保存买入/卖出价、退出原因、持有天数、实现收益；`post_exit_max_return` / `post_exit_max_drawdown` / `post_exit_window_days` 为清仓后 5 个交易日的窗口指标（事后评估，不参与交易决策；窗口可越出回测区间；旧记录三列为 NULL，不回溯重算） |
| `review_account_events` | 账户决策时间线 | 保存买入、卖出、持仓和空仓原因，供 UI 解释每日决策 |
| `buy_sell_signals` | 实盘辅助买卖信号 | buy 记录买入、sell 按规则平仓；未平仓 code 唯一（部分唯一索引 `idx_buy_sell_open`），含最高浮盈/价、`qty`（买入日冻结）、半仓腿（`half_exit_date/price/qty/reason_*/signal_json`）、平仓原因与持有天数 |
