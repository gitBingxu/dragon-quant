# review_account 策略规则说明

> 账户级模拟交易 `review-account` 与实盘辅助 `buy/sell` 的共享策略规范。完整调研见《择时与仓位调研.md》，技术方案见《择时与仓位技术方案.md》。

## 1. 卖出规则链（顺序即优先级，命中即短路）

`strategy.evaluate_sell` 实现为 9 条 `SellRule` 的有序责任链（`strategy.py` `_SELL_RULES`）。`late_only=True` 的规则仅在 `phase == "late"` 评估（等价于原 `if phase != late: return []` 的阶段门）。

| # | name | reason_code | late_only | 默认 | 触发 |
|---|------|-------------|:---:|:---:|------|
| 1 | `hard_stop` | first_day_stop_loss / hard_stop_loss | ✗ | 开 | 已完成 K 最低触及止损线（首日 -3.5%，此后 -5%） |
| 2 | `trailing` | trailing_take_profit | ✗ | 开 | 峰值浮盈 ≥ 8% 后回撤 ≥ 3.5%（可分级/ATR 化），或 14:55 失守 MA5 |
| 3 | `breakeven` | profit_back_to_cost_take_profit | ✗ | 开 | 峰值浮盈 ≥ 6% 后最低触及完整成本线 |
| 4 | `high_open_clear` | high_open_7pct/5pct_no_limit_* | ✗ | 开 | 高开 ≥7%/5% 的 5/30 分钟窗口未封板 |
| 5 | `soft_time_stop` | soft_time_stop | ✓ | **关** | 买入后 N 日未创新高且未脱离成本区（逻辑证伪） |
| 6 | `weak_close` | next_day_close_below_open / close_below_open_stop / break_intraday_ma_stop | ✓ | 开 | 14:55 弱势（收<开且跌>1% 或破 MA5 或破昨收） |
| 7 | `hard_time_stop` | hard_time_stop | ✓ | **关** | 持有达 `max_hold_days` 仍退出，封板豁免 |
| 8 | `half` | next_day_limit_up_half | ✓ | 开 | 首个可卖日 14:55 仍封板 → 减半仓 |
| 9 | `volume_spike` | volume_spike_take_profit | ✓ | 开 | 14:55 累计量 ≥ 上日全天 ×1.3 且未封板 |

顺序要点：软时间止损（逻辑证伪）先于当日弱势；弱势先于硬时间止损（更具体归因）；硬时间止损封板豁免交由半仓规则。

## 2. 卖出规则开关（`sell_rule_toggles`）

- `StrategyConfig.sell_rule_toggles: dict[str, bool]`，键为规则 `name`，值为是否启用。
- **空 dict = 各规则默认值：存量规则（#1-#4/#6/#8/#9）开，新增规则（`soft_time_stop`/`hard_time_stop`）关**。
- 显式键覆盖对应规则。消融实验通过关闭单条规则实现。
- 未知键在 `StrategyConfig.from_dict` 校验期拒绝（`models.SELL_RULE_NAMES`）。

## 3. 择时与仓位新增字段（全部 default-off / =现值，向后兼容）

| 字段 | 默认 | 作用 |
|------|------|------|
| `sentiment_sizing_enabled` | False | 情绪→仓位：`budget_scale = sentiment_pct × delevel` |
| `sentiment_ban_enabled` | False | 情绪禁买：`position_pct==0` 时 `evaluate_buy` 抛 `BuyRejected("sentiment_ban")` |
| `max_daily_loss_pct` | 0 | 日级熔断（0=关）：当日已实现亏损 ≥ 净值×X% 停开新仓 |
| `drawdown_delevel_pct` | 0 | 净值回撤降仓（0=关）：-5%→×0.75，-10%→×0.5，-20%→×0.25 |
| `streak_delevel_enabled` | False | 连续亏损降仓：4-5 连亏×0.5，6+ 连亏停开新仓 |
| `soft_time_stop_days` / `soft_time_stop_floor_pct` | 0 / 0 | 软时间止损天数与「未脱离成本区」阈值 |
| `tiered_trailing_enabled` + `trailing_tier2/3_*` | False | 分级移动止盈：浮盈 ≥15%→容忍 5%、≥25%→容忍 8% |
| `dragon_comeback_*` | — | 龙回头/二波买点（`dragon_comeback_enabled=False` 默认） |

`AccountState` 新增：`realized_today`、`consecutive_losses`、`sentiment_pct`、`peak_equity`。

## 4. 组合风控（`risk.py`）

`risk_governor(cfg, state, equity) -> (delevel, allow_buy, fired_gate)` 只负责**账户维度**：

- `delevel = min(回撤系数, 连亏系数)`（默认 1.0；账户维度内部用 min 而非相乘，避免叠加至近零）。
- `allow_buy`：日级熔断 / 连亏停开新仓时 False。
- 市场维度（`sentiment_pct`）不在 RiskGovernor 内，由 engine 在开仓前做 `budget_scale = sentiment_pct × delevel`。

## 5. 回测 ↔ 实盘一致性（分叉声明）

| 机制 | 落层 | 回测 | 实盘 `buy/sell` |
|------|------|------|----------------|
| 情绪禁买 | 共享 `evaluate_buy`（`BuyRejected`） | 生效 | **自动跟随** ✓ |
| 竞价/其它否决 `entry_veto` | 共享 `evaluate_buy` 顶部 | 生效 | **自动跟随** ✓ |
| 情绪→仓位（sizing） | `buy_quantity` 的 `budget_scale` | 生效 | 不适用（SignalEngine 无真实预算） |
| 组合熔断 `allow_buy` | engine 层 `RiskGovernor` | 生效 | **跟随**：`live_trade/shadow.py` 从信号表按回放时点重建影子状态并调用同一 `risk_governor`；日级熔断分母为影子净值（buy-all 敞口可超 100%），`equity<=0` 额外触发破产闸 |
| 组合降仓 `delevel` | `buy_quantity` 的 `budget_scale` | 生效 | **不适用**：无 sizing，`streak_delevel` 行为 no-op（fired_gate 仍展示），`drawdown_delevel` 不触发 |
| 保本线口径 | `break_even_price`（依赖 qty/cost） | 首仓 `buy_quantity(initial_cash, initial_cash)` | **跟随**：`notional_quantity` 同口径落库冻结 |
| 半仓规则 | 共享 `_rule_half` + `apply_sell_fill` | `p.qty` 减半、`took_profit_half=True` | **跟随**：`half_close_signal` 冻结半仓腿，`status` 仍 open 供剩余半仓续跟踪 |
| 数据分级 | engine 外部 `codes = candidates ∪ positions` | 生效 | **跟随**：buy/sell 均纳入并集 |

`sentiment_pct` 装载点：`AccountSimulator.run`（回测）与 `LiveTrader._run_buy`（实盘）各自经共享 `load_market_sentiment` 读取 T-1 prep 快照；engine 只读 `state.sentiment_pct`。快照缺失/回退旧逻辑时显式降级为 sizing 1.0、禁买不生效（等效现状）。

## 6. 验证基建（`evaluation.py`）

- `drawdown_decomposition(result)`：按 reason_code 归因已实现盈亏（止损/止盈/其它三桶）。
- `plateau_scan(date_from, date_to, data, cfg, param, values, metric)`：**单参数**网格 + 高原判定（邻域 80% 以上视为高原；二维热力图矩阵为后续扩展）。
- `ablation(date_from, date_to, data, cfg, rule_names, metric)`：逐条关闭卖出规则，边际贡献。
- `walk_forward(date_from, date_to, data, cfg, n_windows=4, metric)`：固定 cfg 滚动样本外评估，报告相邻窗口 OOS/IS 效率比，负值或 <0.5 判「样本外衰减」红旗（参数寻优由 plateau_scan/ablation 承担）。
- `compare_strategies(..., variant_grid=None)`：声明式额外 variant 网格 + `trial_count`（试验次数账本；>20 时报告标注 `overfitting_risk`）。
