"""示例策略（仅演示 SDK API，无有效性宣称）。

回踩均线承接入场 + 移动保护退出；演示「可读持仓 + 账户状态」的决策。
"""

from dragon_quant.strategy_sdk import StrategyIntent


class MeanRevertGuard:
    descriptor = {"id": "user:example_meanrev", "sdk_version": 1}

    def __init__(self, params):
        params = dict(params or {})
        self.pullback_pct = float(params.get("pullback_pct", 3.0))
        self.trail_giveback = float(params.get("trail_giveback_pct", 4.0))

    def evaluate_entry(self, ctx):
        row = ctx.row
        if not row or ctx.phase != "open":
            return None
        open_px = row.get("open")
        # 开盘事件不暴露当日最终 MA5（未来信息）；用上一交易日已完成均线。
        prev = row.get("prev_row") or {}
        ma5 = prev.get("ma5")
        if not open_px or not ma5:
            return None
        distance = (open_px / ma5 - 1) * 100
        if not (-2.0 <= distance <= self.pullback_pct):
            return None
        return StrategyIntent(
            action="buy",
            reason_code="meanrev_pullback",
            reason_text=f"开盘贴近 MA5（距 {distance:+.1f}%）",
            priority=80,
            position_pct=40.0,
            evidence={"distance_to_ma5_pct": distance},
        )

    def evaluate_exit(self, ctx):
        pos, row = ctx.position, ctx.row
        if not pos or not row or ctx.phase == "close":
            return None
        close = row.get("bar_close")
        highest = pos.get("highest_price") or pos.get("entry_price")
        if not close or not highest:
            return None
        giveback = (highest - close) / highest * 100
        if giveback >= self.trail_giveback and close > (pos.get("entry_price") or 0):
            return StrategyIntent(action="sell", reason_code="meanrev_trail",
                                  reason_text="冲高后回吐，兑现利润", priority=80,
                                  exit_fraction=1.0)
        return None

    def explain(self, ctx):
        return [{"rule": "meanrev", "pullback_pct": self.pullback_pct,
                 "trail_giveback_pct": self.trail_giveback}]


def create_strategy(params):
    return MeanRevertGuard(params)
