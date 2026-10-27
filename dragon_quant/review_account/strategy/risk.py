"""账户维度组合风控：日级熔断 + 净值回撤降仓 + 连续亏损降仓。

只负责账户维度；市场维度（sentiment_pct）由 engine 在开仓前与 delevel 相乘
（budget_scale = sentiment_pct × delevel）。全部闸门 default-off，逐项消融。
"""

from __future__ import annotations


def risk_governor(cfg, state, equity: float) -> tuple[float, bool, list[str]]:
    """账户维度风控，返回 (delevel, allow_buy, fired_gate)。

    - delevel：账户维度仓位系数（min 聚合），默认 1.0；
    - allow_buy：是否允许开新仓（日级熔断 / 连亏停开）；
    - fired_gate：触发的闸门名列表，供 UI/归因消费。
    """
    delevel = 1.0
    allow_buy = True
    fired: list[str] = []

    # 日级熔断：当日已实现亏损 ≥ 净值×X% → 停止开新仓
    if cfg.max_daily_loss_pct and equity > 0 and state.realized_today < 0:
        if -state.realized_today / equity * 100 >= cfg.max_daily_loss_pct:
            allow_buy = False
            fired.append("daily_loss_circuit_breaker")

    # 净值回撤降仓：-5%→×0.75，-10%→×0.5，-20%→最小
    if cfg.drawdown_delevel_pct and equity > 0 and state.peak_equity > 0:
        dd = (equity / state.peak_equity - 1) * 100
        if dd <= -20:
            delevel = min(delevel, 0.25)
            fired.append("drawdown_delevel")
        elif dd <= -10:
            delevel = min(delevel, 0.5)
            fired.append("drawdown_delevel")
        elif dd <= -5:
            delevel = min(delevel, 0.75)
            fired.append("drawdown_delevel")

    # 连续亏损降仓：4-5 连亏×0.5，6+ 连亏停开新仓
    if cfg.streak_delevel_enabled:
        if state.consecutive_losses >= 6:
            allow_buy = False
            fired.append("streak_circuit_breaker")
        elif state.consecutive_losses >= 4:
            delevel = min(delevel, 0.5)
            fired.append("streak_delevel")

    return delevel, allow_buy, fired
