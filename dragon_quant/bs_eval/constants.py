"""bs_eval 数值/状态常量的唯一来源（纯数据，无 I/O）。"""

from __future__ import annotations

from dragon_quant.review.strict_metrics import WINDOW_SIZE, WINDOW_SKIP

#: 评测器版本：任何语义变化都必须递增，触发下次运行的自动 force。
EVALUATOR_VERSION = "bs_eval/1"

#: 买入评估窗口：入选日之后的第 1~5 个交易日（独立于 StrategyConfig 默认值）。
BUY_WINDOW_DAYS = 5

#: 买入后最多回放 60 个交易日；仍未全清按 open/hold_cap_reached 终态处理。
MAX_REPLAY_TRADING_DAYS = 60

#: 引擎记账用名义资金：让高价股也能至少成交一手（不模拟资金/仓位）。
BS_EVAL_NOTIONAL_CASH = 10_000_000.0

#: 收益/回撤窗口（与 review 严格口径共享同一组常量）。
WINDOW_SKIP_DAYS = WINDOW_SKIP
WINDOW_SIZE_DAYS = WINDOW_SIZE

#: 买入胜利：5 日最大收益 > 0 且最大回撤 > -5%。
BUY_WIN_RETURN_GT = 0.0
BUY_WIN_DRAWDOWN_GT = -5.0

#: 卖出判定阈值（互斥，先卖飞后卖得对）。
SELL_FLY_RETURN_GE = 5.0
SELL_GOOD_DRAWDOWN_LE = -3.0

#: eval_status 取值。
STATUS_PENDING = "pending"
STATUS_NO_SIGNAL = "no_signal"
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_CANNOT_EVALUATE = "cannot_evaluate"

STATUSES = (
    STATUS_PENDING,
    STATUS_NO_SIGNAL,
    STATUS_OPEN,
    STATUS_CLOSED,
    STATUS_CANNOT_EVALUATE,
)

#: 可参与触发率分母的状态（窗口已成熟，可买或已买）。
TRIGGER_DENOMINATOR_STATUSES = frozenset(
    {STATUS_NO_SIGNAL, STATUS_OPEN, STATUS_CLOSED})

#: eval_note 稳定原因码。
NOTE_BUY_WINDOW_NOT_READY = "buy_window_not_ready"
NOTE_WARMUP_INSUFFICIENT = "warmup_insufficient"
NOTE_DAILY_MISSING = "daily_missing"
NOTE_HOLD_CAP_REACHED = "hold_cap_reached"
NOTE_STRATEGY_OR_EVALUATOR_CHANGED = "strategy_or_evaluator_changed_auto_force"

__all__ = [
    "BS_EVAL_NOTIONAL_CASH",
    "BUY_WINDOW_DAYS",
    "BUY_WIN_DRAWDOWN_GT",
    "BUY_WIN_RETURN_GT",
    "EVALUATOR_VERSION",
    "MAX_REPLAY_TRADING_DAYS",
    "NOTE_BUY_WINDOW_NOT_READY",
    "NOTE_DAILY_MISSING",
    "NOTE_HOLD_CAP_REACHED",
    "NOTE_STRATEGY_OR_EVALUATOR_CHANGED",
    "NOTE_WARMUP_INSUFFICIENT",
    "SELL_FLY_RETURN_GE",
    "SELL_GOOD_DRAWDOWN_LE",
    "STATUSES",
    "STATUS_CANNOT_EVALUATE",
    "STATUS_CLOSED",
    "STATUS_NO_SIGNAL",
    "STATUS_OPEN",
    "STATUS_PENDING",
    "TRIGGER_DENOMINATOR_STATUSES",
    "WINDOW_SIZE_DAYS",
    "WINDOW_SKIP_DAYS",
]
