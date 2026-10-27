"""策略纯拆分：卖出上下文、规则链与 ``evaluate_sell``（Task 14b）。

10 条 ``SellRule`` 的顺序、``late_only`` 阶段门、``_DEFAULT_OFF_RULES`` 默认
开启集与全部阈值从原 ``strategy.py`` 原样搬入；规则体零变化。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from dragon_quant.review_account.models import (
    DEFAULT_OFF_SELL_RULES,
    Position,
    StrategyConfig,
)
from dragon_quant.review_account.strategy.common import _sell
from dragon_quant.review_account.strategy.execution import break_even_price


@dataclass
class SellContext:
    position: Position
    row: dict
    hold_days: int
    cfg: StrategyConfig
    intraday_bars: Optional[list]


@dataclass
class SellRule:
    name: str
    late_only: bool
    evaluate: Callable[["SellContext"], Optional[dict]]


def _rule_first_day_stop(ctx: SellContext) -> Optional[dict]:
    """首日止损（新增规则，默认关）：盘中浮亏达减半线先卖一半，灾难线优先整清。

    - 仅 `hold_days == 1` 生效，首日之后交给 `hard_stop` 等既有规则。
    - 灾难段**优先且不带半仓守卫**：首次击穿即整仓退出（不得先卖一半），已减半后再次
      击穿则清掉剩余半仓。
    - `took_profit_half` 是「该信号已完成一条半仓腿」的唯一性守卫（与 `next_day_limit_up_half`
      共用，语义不分盈亏），因此同一信号只会有一条半仓腿。
    """
    row, pos, hold_days, cfg = ctx.row, ctx.position, ctx.hold_days, ctx.cfg
    if hold_days != 1:
        return None
    low_return = (row["bar_low"] / pos.entry_price - 1) * 100
    if cfg.first_day_disaster_stop_pct and low_return <= cfg.first_day_disaster_stop_pct:
        return _sell("first_day_disaster_stop",
                     f"首日触及灾难止损线{cfg.first_day_disaster_stop_pct:.1f}%，清仓", row)
    if pos.took_profit_half:  # 同一信号只允许一条半仓腿
        return None
    if low_return <= cfg.first_day_stop_loss_pct:
        return _sell("first_day_stop_half",
                     f"首日浮亏触及{cfg.first_day_stop_loss_pct:.1f}%止损线，"
                     "执行半仓腿（不足一手则全卖）", row)
    return None


def _rule_hard_stop(ctx: SellContext) -> Optional[dict]:
    row, pos, hold_days, cfg = ctx.row, ctx.position, ctx.hold_days, ctx.cfg
    if hold_days == 1 and cfg.first_day_stop_enabled():
        # 首日由 first_day_stop 接管；判定收在 StrategyConfig，规则之间不互相读 toggle。
        return None
    stop = cfg.first_day_stop_loss_pct if hold_days == 1 else cfg.stop_loss_pct
    low_return = (row["bar_low"] / pos.entry_price - 1) * 100
    if low_return <= stop:
        code = "first_day_stop_loss" if hold_days == 1 else "hard_stop_loss"
        return _sell(code, f"已完成K线触及{stop:.1f}%止损，等待可执行报价", row)
    return None


def _tiered_drawdown(highest_return: float, cfg: StrategyConfig, base: float) -> float:
    if highest_return >= cfg.trailing_tier3_activate_pct:
        return cfg.trailing_tier3_drawdown_pct
    if highest_return >= cfg.trailing_tier2_activate_pct:
        return cfg.trailing_tier2_drawdown_pct
    return base


def _rule_trailing(ctx: SellContext) -> Optional[dict]:
    row, pos, cfg = ctx.row, ctx.position, ctx.cfg
    if pos.highest_return < cfg.trailing_activate_pct:
        return None
    close = row["bar_close"]
    drawdown = cfg.trailing_drawdown_pct
    if cfg.tiered_trailing_enabled:
        drawdown = _tiered_drawdown(pos.highest_return, cfg, drawdown)
    if cfg.trailing_atr_multiple and row.get("atr"):
        drawdown = max(drawdown, row["atr"] / close * 100 * cfg.trailing_atr_multiple)
    retrace = (pos.highest_price - close) / pos.highest_price * 100
    if retrace >= drawdown or (row["phase"] == "late" and close < row["ma5"]):
        return _sell("trailing_take_profit", "已有浮盈后回撤或尾盘失守MA5，移动止盈", row)
    return None


def _rule_breakeven(ctx: SellContext) -> Optional[dict]:
    row, pos, cfg = ctx.row, ctx.position, ctx.cfg
    if pos.highest_return >= cfg.breakeven_activate_pct and row["bar_low"] <= break_even_price(pos, cfg):
        return _sell("profit_back_to_cost_take_profit", "已有浮盈后触及完整成本线，按随后可执行价保护退出", row)
    return None


def _rule_high_open_clear(ctx: SellContext) -> Optional[dict]:
    row = ctx.row
    bars = ctx.intraday_bars or []
    gap = row.get("open_gap_pct") or 0
    window = 1 if gap >= 7 else 6 if gap >= 5 else 0
    if window and len(bars) == window and not any(b.high >= row["limit_up"] * .999 for b in bars):
        minutes = window * 5
        code = "high_open_7pct_no_limit_5m_clear" if window == 1 else "high_open_5pct_no_limit_30m_clear"
        return _sell(code, f"高开后{minutes}分钟未触板，清仓", row)
    return None


def _rule_soft_time_stop(ctx: SellContext) -> Optional[dict]:
    row, pos, hold_days, cfg = ctx.row, ctx.position, ctx.hold_days, ctx.cfg
    if not cfg.soft_time_stop_days or hold_days < cfg.soft_time_stop_days:
        return None
    if pos.highest_return >= cfg.trailing_activate_pct:  # trailing 已激活，交给移动止盈
        return None
    if row["bar_close"] > pos.entry_price * (1 + cfg.soft_time_stop_floor_pct / 100):  # 已脱离成本区
        return None
    return _sell("soft_time_stop", f"买入后{hold_days}日未创新高且未脱离成本区，逻辑证伪退出", row)


def _rule_weak_close(ctx: SellContext) -> Optional[dict]:
    row, hold_days, cfg = ctx.row, ctx.hold_days, ctx.cfg
    weak = _weak_close_break(row, cfg)
    below_ma = row["bar_close"] < row["ma5"]
    if weak or below_ma:
        code = ("next_day_close_below_open" if hold_days == 1 and weak
                else "close_below_open_stop" if weak else "break_intraday_ma_stop")
        return _sell(code, "14:55已知数据转弱，清仓", row)
    return None


def _rule_hard_time_stop(ctx: SellContext) -> Optional[dict]:
    row, hold_days, cfg = ctx.row, ctx.hold_days, ctx.cfg
    if hold_days >= cfg.max_hold_days and row["bar_close"] < row["limit_up"] * .999:
        return _sell("hard_time_stop", f"持有达{cfg.max_hold_days}日仍未退出，时间止损（封板豁免）", row)
    return None


def _rule_half(ctx: SellContext) -> Optional[dict]:
    row, pos, hold_days, cfg = ctx.row, ctx.position, ctx.hold_days, ctx.cfg
    if (hold_days == 1 and cfg.next_day_half_enabled and not pos.took_profit_half
            and row["bar_close"] >= row["limit_up"] * .999):
        return _sell("next_day_limit_up_half", "首个可卖日14:55仍封板，减半仓", row)
    return None


def _rule_volume_spike(ctx: SellContext) -> Optional[dict]:
    row, cfg = ctx.row, ctx.cfg
    volume = row.get("prev_volume")
    if (volume and row["volume"] / volume >= 1 + cfg.volume_spike_pct / 100
            and row["bar_close"] < row["limit_up"] * .999):
        return _sell("volume_spike_take_profit", "14:55累计量较上日全天放大且未封板，清仓", row)
    return None


# 卖出规则链：代码顺序即优先级，命中即短路。late_only 承载「phase != late 即跳过」的阶段门。
_SELL_RULES = [
    SellRule("first_day_stop", False, _rule_first_day_stop),
    SellRule("hard_stop", False, _rule_hard_stop),
    SellRule("trailing", False, _rule_trailing),
    SellRule("breakeven", False, _rule_breakeven),
    SellRule("high_open_clear", False, _rule_high_open_clear),
    SellRule("soft_time_stop", True, _rule_soft_time_stop),
    SellRule("weak_close", True, _rule_weak_close),
    SellRule("hard_time_stop", True, _rule_hard_time_stop),
    SellRule("half", True, _rule_half),
    SellRule("volume_spike", True, _rule_volume_spike),
]
# 新增规则默认关；存量规则默认开。sell_rule_toggles 空 dict 时即此默认（与 models 同一真值来源）。
_DEFAULT_OFF_RULES = set(DEFAULT_OFF_SELL_RULES)


def evaluate_sell(position: Position, row: dict, hold_days: int,
                  cfg: StrategyConfig, intraday_bars: Optional[list] = None) -> list[dict]:
    if hold_days <= 0 or position.entry_date >= row["date"]:
        return []
    ctx = SellContext(position, row, hold_days, cfg, intraday_bars)
    for rule in _SELL_RULES:
        if rule.late_only and row["phase"] != "late":
            continue
        if not cfg.sell_rule_toggles.get(rule.name, rule.name not in _DEFAULT_OFF_RULES):
            continue
        sig = rule.evaluate(ctx)
        if sig:
            return [sig]
    return []


def _weak_close_break(row: dict, cfg: StrategyConfig) -> bool:
    if row["close"] >= row["open"]:
        return False
    drop = (row["open"] - row["close"]) / row["open"] * 100
    return (drop > cfg.weak_close_tolerance_pct or row["close"] < row["ma5"]
            or row["close"] < row["prev_close"])
