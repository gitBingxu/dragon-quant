"""账户级 review 数据模型。"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StrategyConfig:
    """账户模拟策略参数。"""

    source: str = "v2"
    strategy_name: str = "dragon_pullback_daily"
    initial_cash: float = 100_000.0
    candidate_top_n: int = 5
    candidate_lookback_days: int = 3
    max_positions: int = 5
    min_score: float = 50.0
    min_amount: float = 200_000_000.0
    min_turnover: float = 5.0
    strong_turnover_min: float = 8.0
    strong_turnover_max: float = 35.0
    max_open_gap: float = 7.0
    max_close_to_ma5: float = 12.0
    divergence_enabled: bool = True
    divergence_min_boards: int = 2
    divergence_require_shrinking_volume: bool = True
    divergence_confirm_bars: int = 6
    divergence_break_open_ratio: float = 0.998
    stop_loss_pct: float = -5.0
    take_profit_pct: float = 12.0
    breakeven_activate_pct: float = 6.0
    trailing_activate_pct: float = 6.0
    trailing_drawdown_pct: float = 3.5
    volume_spike_pct: float = 30.0
    max_hold_days: int = 5
    buy_slippage: float = 0.002
    sell_slippage: float = 0.002
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.0005
    lot_size: int = 100

    def to_json_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Position:
    code: str
    name: str
    qty: int
    entry_date: str
    entry_price: float
    cost: float
    entry_reason_code: str
    entry_reason_text: str
    entry_signal: dict = field(default_factory=dict)
    highest_return: float = 0.0
    highest_price: float = 0.0
    entry_day_low: Optional[float] = None
    initial_qty: int = 0
    initial_cost: float = 0.0
    realized_pnl: float = 0.0
    took_profit_half: bool = False

    def __post_init__(self):
        if self.initial_qty <= 0:
            self.initial_qty = self.qty
        if self.initial_cost <= 0:
            self.initial_cost = self.cost


@dataclass
class Trade:
    trade_date: str
    code: str
    name: str
    side: str
    price: float
    qty: int
    amount: float
    fee: float
    realized_pnl: float
    cash_after: float
    position_after: int
    reason_code: str
    reason_text: str
    signal: dict = field(default_factory=dict)


@dataclass
class TimelineEvent:
    event_date: str
    event_type: str
    title: str
    detail: str
    reason_code: str = ""
    code: str = ""
    name: str = ""
    cash: Optional[float] = None
    total_equity: Optional[float] = None
    signal: dict = field(default_factory=dict)


@dataclass
class Snapshot:
    trade_date: str
    cash: float
    market_value: float
    total_equity: float
    daily_return: float
    cumulative_return: float
    drawdown: float
    position_code: str = ""
    position_name: str = ""
    position_qty: int = 0
    position_cost: Optional[float] = None
    position_market_price: Optional[float] = None
    position_unrealized_return: Optional[float] = None


@dataclass
class ClosedPosition:
    code: str
    name: str
    entry_date: str
    entry_price: float
    qty: int
    entry_reason_code: str
    entry_signal: dict
    exit_date: str
    exit_price: float
    exit_reason_code: str
    exit_signal: dict
    realized_return: float
    hold_days: int
    status: str = "closed"
