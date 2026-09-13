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
    first_day_stop_loss_pct: float = -3.5
    weak_close_tolerance_pct: float = 1.0
    take_profit_pct: float = 12.0
    breakeven_activate_pct: float = 6.0
    trailing_activate_pct: float = 8.0
    trailing_drawdown_pct: float = 3.5
    volume_spike_pct: float = 30.0
    max_hold_days: int = 5
    buy_slippage: float = 0.002
    sell_slippage: float = 0.002
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.0005
    lot_size: int = 100
    max_position_pct: float = 100.0
    risk_per_trade_pct: float = 0.0
    ma5_min_distance_pct: float = -100.0
    require_rising_ma5: bool = False
    trailing_atr_multiple: float = 0.0
    next_day_half_enabled: bool = True
    max_daily_buys: int = 1
    min_commission: float = 5.0
    quote_max_age_seconds: int = 60

    @classmethod
    def from_dict(cls, values: dict) -> "StrategyConfig":
        import math
        from dataclasses import fields
        if not isinstance(values, dict):
            raise ValueError("策略配置必须是 JSON 对象")
        names = {f.name for f in fields(cls)}
        unknown = set(values) - names
        if unknown:
            raise ValueError(f"未知策略参数: {', '.join(sorted(unknown))}")
        cfg = cls(**values)
        defaults = cls()
        for name in names:
            value, default = getattr(cfg, name), getattr(defaults, name)
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ValueError(f"{name} 必须是布尔值")
            elif isinstance(default, (int, float)):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"{name} 必须是有限数值")
                if isinstance(default, int) and not isinstance(value, int):
                    raise ValueError(f"{name} 必须是整数")
        if cfg.source not in {"v1", "v2"}:
            raise ValueError("source 必须是 v1 或 v2")
        for name in ("initial_cash", "candidate_top_n", "candidate_lookback_days", "max_positions",
                     "lot_size", "max_daily_buys", "divergence_confirm_bars", "quote_max_age_seconds"):
            if getattr(cfg, name) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        if not 0 < cfg.max_position_pct <= 100 or not 0 <= cfg.risk_per_trade_pct <= 100:
            raise ValueError("仓位和风险预算百分比超出范围")
        for name in ("buy_slippage", "sell_slippage", "commission_rate", "stamp_tax_rate"):
            if not 0 <= getattr(cfg, name) < 1:
                raise ValueError(f"{name} 必须在 [0, 1) 内")
        if cfg.commission_rate + cfg.stamp_tax_rate >= 1:
            raise ValueError("卖出费率之和必须小于 1")
        if not -100 < cfg.stop_loss_pct < 0 or not -100 < cfg.first_day_stop_loss_pct < 0:
            raise ValueError("止损百分比必须在 (-100, 0) 内")
        if not isinstance(cfg.strategy_name, str) or not cfg.strategy_name.strip():
            raise ValueError("strategy_name 必须是非空字符串")
        for name in ("min_commission", "trailing_atr_multiple", "min_amount", "min_turnover",
                     "trailing_drawdown_pct", "weak_close_tolerance_pct", "breakeven_activate_pct"):
            if getattr(cfg, name) < 0:
                raise ValueError(f"{name} 不能为负")
        if not 1 <= cfg.divergence_confirm_bars <= 24 or cfg.divergence_min_boards < 1:
            raise ValueError("分歧窗口必须在上午交易时段内且连板门槛为正")
        if not 0 < cfg.divergence_break_open_ratio <= 1:
            raise ValueError("断板开盘比例必须在 (0,1] 内")
        if cfg.trailing_activate_pct < cfg.breakeven_activate_pct:
            raise ValueError("移动止盈激活阈值不能低于保本阈值")
        return cfg

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
    positions_json: str = "[]"


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


@dataclass
class MarketEvent:
    timestamp: int
    phase: str
    rows: dict[str, dict]
    candidates: list[dict] = field(default_factory=list)
    allow_buy: bool = True


@dataclass
class AccountState:
    cash: float
    positions: list[Position] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)
    last_event: int = 0
    trade_date: str = ""
    sold_today: bool = False
    buys_today: int = 0
    marks: dict[str, float] = field(default_factory=dict)
    evaluated: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AccountState":
        return cls(**{**data, "positions": [Position(**p) for p in data.get("positions", [])]})
