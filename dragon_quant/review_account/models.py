"""账户级 review 数据模型。"""

from dataclasses import dataclass, field
from typing import Optional

# 卖出规则链的稳定规则名（sell_rule_toggles 的合法键）
SELL_RULE_NAMES = ("hard_stop", "trailing", "breakeven", "high_open_clear",
                   "soft_time_stop", "weak_close", "hard_time_stop", "half", "volume_spike")

# 买点类型的唯一来源：strategy._buy 的调用与 auction_veto_exempt_signals 校验共用。
# 改动/新增买点时必须同步此处，否则配置里的豁免名会静默 fail-open。
BUY_SIGNAL_MA5_PULLBACK = "buy_open_ma5_pullback"
BUY_SIGNAL_OPEN_TURN_STRONG = "buy_open_turn_strong"
BUY_SIGNAL_DIVERGENCE_BREAK = "buy_divergence_first_break"
BUY_SIGNAL_DRAGON_COMEBACK = "buy_dragon_comeback"
BUY_SIGNAL_CODES = (
    BUY_SIGNAL_MA5_PULLBACK,
    BUY_SIGNAL_OPEN_TURN_STRONG,
    BUY_SIGNAL_DIVERGENCE_BREAK,
    BUY_SIGNAL_DRAGON_COMEBACK,
)

# 当日行情不可用的稳定原因名（MarketEvent.unavailable 的 value）
UNAVAILABLE_SUSPENDED = "suspended"              # 停牌：无分时且当日日K缺行/有价无量
UNAVAILABLE_DAILY_MISSING = "daily_unavailable"  # 日K兜底模式下当日日K不可用（分时可用）

_UNAVAILABLE_TEXT = {
    UNAVAILABLE_SUSPENDED: "当日停牌无行情（无分时且日K缺行或有价无量），当日跳过评估",
    UNAVAILABLE_DAILY_MISSING: "当日日K不可用（日K兜底模式无法建行），当日跳过评估",
}


def unavailable_text(reason: str) -> str:
    """不可用原因的中文可读文案（未知原因有兜底）。"""
    return _UNAVAILABLE_TEXT.get(reason, "当日行情不可用，跳过评估")


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
    turn_strong_bar_turnover_min: float = 0.5
    max_open_gap: float = 7.0
    max_close_to_ma5: float = 12.0
    max_avg_amplitude: float = 0.0
    max_return_3d: float = 0.0
    drawdown_5d_floor: float = 0.0
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
    # ── 择时与仓位改造（全部 default-off / =现值，向后兼容）──
    sentiment_sizing_enabled: bool = False
    sentiment_ban_enabled: bool = False
    sell_rule_toggles: dict = field(default_factory=dict)
    max_daily_loss_pct: float = 0.0
    drawdown_delevel_pct: float = 0.0
    streak_delevel_enabled: bool = False
    soft_time_stop_days: int = 0
    soft_time_stop_floor_pct: float = 0.0
    tiered_trailing_enabled: bool = False
    trailing_tier2_activate_pct: float = 15.0
    trailing_tier2_drawdown_pct: float = 5.0
    trailing_tier3_activate_pct: float = 25.0
    trailing_tier3_drawdown_pct: float = 8.0
    dragon_comeback_enabled: bool = True
    dragon_comeback_min_rise_pct: float = 30.0
    dragon_comeback_min_boards: int = 2
    dragon_comeback_pullback_days_min: int = 3
    dragon_comeback_pullback_days_max: int = 10
    dragon_comeback_volume_ratio: float = 0.333
    dragon_comeback_confirm_pct: float = 3.0
    # ── P/A 优先级（默认关；见 docs/solutions/2026-10-21-auction-prep-buy-priority-design.md）──
    auction_ranking_enabled: bool = False     # A 维度参与排序
    auction_veto_enabled: bool = False        # D3 否决（verified ∧ is_weak）
    prep_verdict_filter_enabled: bool = True  # D4：verdict=ignore 出池（消融可关）
    auction_veto_exempt_signals: list = field(default_factory=list)

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
            elif isinstance(default, dict):
                if not isinstance(value, dict):
                    raise ValueError(f"{name} 必须是对象")
                for k, v in value.items():
                    if k not in SELL_RULE_NAMES:
                        raise ValueError(f"未知卖出规则开关: {k}")
                    if not isinstance(v, bool):
                        raise ValueError(f"卖出规则开关 {k} 必须是布尔值")
            elif isinstance(default, list):
                if not isinstance(value, list):
                    raise ValueError(f"{name} 必须是数组")
                bad = [v for v in value
                       if not isinstance(v, str) or v not in BUY_SIGNAL_CODES]
                if bad:
                    raise ValueError(
                        f"未知买点类型: {', '.join(map(str, bad))}；"
                        f"合法值: {', '.join(BUY_SIGNAL_CODES)}")
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
        for name in ("max_avg_amplitude", "max_return_3d"):
            if getattr(cfg, name) < 0:
                raise ValueError(f"{name} 不能为负（0 表示禁用）")
        if not -100 < cfg.drawdown_5d_floor <= 0:
            raise ValueError("drawdown_5d_floor 必须在 (-100, 0] 内（0 表示禁用）")
        return cfg

    def to_json_dict(self) -> dict:
        data = dict(self.__dict__)
        data["sell_rule_toggles"] = dict(self.sell_rule_toggles)
        data["auction_veto_exempt_signals"] = list(self.auction_veto_exempt_signals)
        return data


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
    # 清仓后窗口指标（事后评估，不参与任何交易决策）；旧记录为 None/0。
    post_exit_max_return: Optional[float] = None
    post_exit_max_drawdown: Optional[float] = None
    post_exit_window_days: int = 0


@dataclass
class MarketEvent:
    timestamp: int
    phase: str
    rows: dict[str, dict]
    candidates: list[dict] = field(default_factory=list)
    allow_buy: bool = True
    # 当日不可用（停牌/日K缺失）的 code → 原因；rows 中不含这些 code。
    unavailable: dict[str, str] = field(default_factory=dict)
    # 不可用 code 的估值价（停牌=前收，daily_unavailable=当日末根5分钟K收盘）；
    # 只用于 marks 估值，不参与买卖评估、不更新峰值。
    last_closes: dict[str, float] = field(default_factory=dict)

    @property
    def suspended(self) -> set[str]:
        """停牌子集（从 unavailable 派生，单一事实来源）。"""
        return {code for code, reason in self.unavailable.items()
                if reason == UNAVAILABLE_SUSPENDED}


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
    realized_today: float = 0.0
    consecutive_losses: int = 0
    sentiment_pct: Optional[float] = None
    peak_equity: float = 0.0

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AccountState":
        return cls(**{**data, "positions": [Position(**p) for p in data.get("positions", [])]})
