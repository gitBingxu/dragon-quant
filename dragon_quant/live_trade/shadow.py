"""影子状态：从 `buy_sell_signals` 全量记录 as-of 重建 notional 账户状态。

`live_trade` 不维护真实账户，但 `review_account.risk_governor` 只读 `AccountState` 的
`realized_today` / `consecutive_losses` / `peak_equity` 与 `equity` 入参。这里用信号表
（买入腿 + 半仓腿 + 平仓腿）确定性重建这些量，让**同一个** `risk_governor` 在 live 侧
零修改复用（不复制任何风控规则）。

净值口径（buy-all 敞口可超 100%，现金不是真实约束）：

    equity = cash + Σ qty×mark
           = initial_cash + Σ已实现盈亏 − Σ剩余成本 + Σ剩余qty×mark

其中 `cash = initial_cash + Σ已实现盈亏 − Σ剩余成本` 是**轧差塞项**（不是现金主张），
目的只是让引擎沿用与 `TradingEngine._equity` 完全相同的恒等式；run 内买入会在
`SignalEngine._fill_buy` 里同步扣减 cash，恒等式持续成立。
"""

from __future__ import annotations

from dragon_quant.review_account.execution import fee, notional_quantity
from dragon_quant.review_account.models import AccountState, Position, StrategyConfig


def _ts(payload: dict, key: str = "fill_timestamp"):
    value = (payload or {}).get(key)
    return int(value) if isinstance(value, (int, float)) else None


def _leg_active(leg_date: str | None, payload: dict, day: str, as_of: int | None) -> bool:
    """腿是否在回放时点已生效：早于当日恒真；当日的看成交时间戳。"""
    if not leg_date or leg_date > day:
        return False
    if leg_date < day or as_of is None:
        return True
    stamp = _ts(payload)
    return stamp is None or stamp <= as_of


def entry_active(signal: dict, day: str, as_of: int | None = None) -> bool:
    return _leg_active(signal.get("entry_date"), signal.get("entry_signal") or {}, day, as_of)


def half_active(signal: dict, day: str, as_of: int | None = None) -> bool:
    return bool(signal.get("took_profit_half")) and _leg_active(
        signal.get("half_exit_date"), signal.get("half_exit_signal") or {}, day, as_of)


def close_active(signal: dict, day: str, as_of: int | None = None) -> bool:
    return signal.get("status") == "closed" and _leg_active(
        signal.get("exit_date"), signal.get("exit_signal") or {}, day, as_of)


def _entry_cost(signal: dict, cfg: StrategyConfig) -> tuple[int, float]:
    """买入腿的冻结数量与成本；legacy 行（qty IS NULL）按 notional 口径兜底。

    脏数据（entry_price 为空）返回 (0, 0.0)，由 `position_as_of` 跳过、盈亏按 0 计，
    不中断 buy/sell（方案 §6 错误契约）。
    """
    price = signal.get("entry_price")
    qty = signal.get("qty") or 0
    if not price:
        return 0, 0.0
    if not qty:
        qty = notional_quantity(cfg, price)
    return qty, price * qty + fee(price * qty, "BUY", cfg)


def _leg_pnl(price: float | None, qty: int, cost: float, cfg: StrategyConfig) -> float:
    """卖出腿盈亏；缺价/零量按 0 计（脏数据不中断风控路径）。"""
    if not price or not qty:
        return 0.0
    amount = price * qty
    return amount - fee(amount, "SELL", cfg) - cost


def position_as_of(signal: dict, cfg: StrategyConfig, day: str,
                   as_of: int | None = None) -> Position | None:
    """单信号 as-of 投影：未买入、已平仓或缺买入价返回 None；半仓腿未生效时保持全仓。"""
    if not signal.get("entry_price"):
        return None
    if not entry_active(signal, day, as_of) or close_active(signal, day, as_of):
        return None
    qty, cost = _entry_cost(signal, cfg)
    if not qty:
        return None
    half = half_active(signal, day, as_of)
    if half:
        half_qty = signal.get("half_exit_qty") or 0
        if 0 < half_qty < qty:
            cost = cost * (qty - half_qty) / qty
            qty -= half_qty
    return Position(signal["code"], signal.get("name", ""), qty, signal["entry_date"],
                    signal["entry_price"], cost, signal.get("entry_reason_code", ""),
                    signal.get("entry_reason_text", ""), signal.get("entry_signal") or {},
                    highest_return=signal.get("highest_return") or 0.0,
                    highest_price=max(signal.get("highest_price") or 0.0, signal["entry_price"]),
                    took_profit_half=half)


def _signal_total_pnl(signal: dict, cfg: StrategyConfig, day: str,
                      as_of: int | None) -> float:
    """该信号截至 as-of 的累计已实现盈亏（半仓腿 + 平仓腿；成本按腿比例摊）。"""
    qty, cost = _entry_cost(signal, cfg)
    if not qty:
        return 0.0
    total = 0.0
    if half_active(signal, day, as_of):
        leg_qty = signal.get("half_exit_qty") or 0
        if 0 < leg_qty <= qty:
            total += _leg_pnl(signal.get("half_exit_price"), leg_qty, cost * leg_qty / qty, cfg)
    if close_active(signal, day, as_of):
        half_qty = (signal.get("half_exit_qty") or 0) if half_active(signal, day, as_of) else 0
        leg_qty = qty - half_qty
        if leg_qty > 0:
            total += _leg_pnl(signal.get("exit_price"), leg_qty, cost * leg_qty / qty, cfg)
    return total


def build_shadow_state(signals: list[dict], cfg: StrategyConfig, day: str,
                       as_of: int | None = None) -> AccountState:
    """按 as-of 重建影子账户状态；as_of 为回放时点（毫秒），None 表示整日。"""
    state = AccountState(0.0)
    positions, realized_total, realized_today = [], 0.0, 0.0
    sold_today = False
    closed = []
    for signal in signals:
        if close_active(signal, day, as_of):
            closed.append(signal)
        if entry_active(signal, day, as_of):
            half = half_active(signal, day, as_of)
            if half:
                leg_date = signal.get("half_exit_date")
                qty, cost = _entry_cost(signal, cfg)
                leg_qty = signal.get("half_exit_qty") or 0
                if 0 < leg_qty <= qty:
                    pnl = _leg_pnl(signal.get("half_exit_price"), leg_qty, cost * leg_qty / qty, cfg)
                    realized_total += pnl
                    if leg_date == day:
                        realized_today += pnl
                if leg_date == day:
                    sold_today = True
        if close_active(signal, day, as_of):
            qty, cost = _entry_cost(signal, cfg)
            half_qty = (signal.get("half_exit_qty") or 0) if half_active(signal, day, as_of) else 0
            leg_qty = qty - half_qty
            pnl = _leg_pnl(signal.get("exit_price"), leg_qty, cost * leg_qty / qty, cfg) if qty else 0.0
            realized_total += pnl
            if signal.get("exit_date") == day:
                realized_today += pnl
                sold_today = True
        position = position_as_of(signal, cfg, day, as_of)
        if position is not None:
            positions.append(position)
    # 连亏：按 (exit_date, fill_timestamp, id) 降序取亏损领头段（同日多笔按成交先后）。
    closed.sort(key=lambda s: (s.get("exit_date") or "", _ts(s.get("exit_signal") or {}) or 0,
                               s.get("id") or 0), reverse=True)
    streak = 0
    for signal in closed:
        if _signal_total_pnl(signal, cfg, day, as_of) < 0:
            streak += 1
        else:
            break
    remaining_cost = sum(p.cost for p in positions)
    state.cash = cfg.initial_cash + realized_total - remaining_cost
    state.positions = positions
    state.realized_today = realized_today
    state.sold_today = sold_today
    state.consecutive_losses = streak
    state.peak_equity = 0.0  # 回撤降仓在 live 无 sizing，永不触发（见 STRATEGY.md 分叉声明）
    return state
