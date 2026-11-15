"""示例策略（仅演示 SDK API，无有效性宣称）。

高开承接入场 + 自定义止损/止盈退出。
"""

from dragon_quant.strategy_sdk import StrategyIntent


class MomentumBreakout:
    descriptor = {"id": "user:example_momentum", "sdk_version": 1}

    def __init__(self, params):
        params = dict(params or {})
        self.min_open_gap = float(params.get("min_open_gap_pct", 1.0))
        self.take_profit_pct = float(params.get("take_profit_pct", 10.0))
        self.stop_loss_pct = float(params.get("stop_loss_pct", -4.0))

    def evaluate_entry(self, ctx):
        row = ctx.row
        if not row or ctx.phase != "open":
            return None
        gap = row.get("open_gap_pct")
        if gap is None or gap < self.min_open_gap:
            return None
        return StrategyIntent(
            action="buy",
            reason_code="momentum_breakout",
            reason_text=f"高开 {gap:+.1f}% 高于阈值 {self.min_open_gap:.1f}%",
            priority=100,
            position_pct=50.0,
            evidence={"open_gap_pct": gap},
        )

    def evaluate_exit(self, ctx):
        pos, row = ctx.position, ctx.row
        if not pos or not row or ctx.phase == "close":
            return None
        entry = pos.get("entry_price")
        if not entry:
            return None
        low, close = row.get("bar_low"), row.get("bar_close")
        if low is not None and (low / entry - 1) * 100 <= self.stop_loss_pct:
            return StrategyIntent(action="sell", reason_code="momentum_stop",
                                  reason_text="跌破自定义止损线", priority=100,
                                  exit_fraction=1.0)
        if close is not None and (close / entry - 1) * 100 >= self.take_profit_pct:
            return StrategyIntent(action="sell", reason_code="momentum_take_profit",
                                  reason_text="达到自定义止盈线", priority=100,
                                  exit_fraction=1.0)
        return None

    def explain(self, ctx):
        return [{"rule": "momentum", "min_open_gap_pct": self.min_open_gap,
                 "take_profit_pct": self.take_profit_pct,
                 "stop_loss_pct": self.stop_loss_pct}]


def create_strategy(params):
    return MomentumBreakout(params)
