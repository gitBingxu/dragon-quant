"""策略说明卡的纯数据来源（无 I/O）。

四类买点与十条卖出规则的文案与常量一一对应；Web 层只序列化本模块，
**不**在运行时解析 ``STRATEGY.md``。数值阈值的唯一来源是
:mod:`dragon_quant.bs_eval.constants`，本模块只派生展示结构。
"""

from __future__ import annotations

from dragon_quant.bs_eval import constants
from dragon_quant.review_account.models import (
    BUY_SIGNAL_CODES,
    BUY_SIGNAL_DIVERGENCE_BREAK,
    BUY_SIGNAL_DRAGON_COMEBACK,
    BUY_SIGNAL_MA5_PULLBACK,
    BUY_SIGNAL_OPEN_TURN_STRONG,
    DEFAULT_OFF_SELL_RULES,
    SELL_RULE_NAMES,
)

STRATEGY_DOC_VERSION = "1.0"

#: Web 默认统计口径使用的评测器版本（由 constants 单一来源派生）。
EVALUATOR_VERSION = constants.EVALUATOR_VERSION

BUY_SIGNALS = [
    {
        "code": BUY_SIGNAL_MA5_PULLBACK,
        "label": "开盘回踩MA5",
        "window": "09:30 开盘",
        "summary": "开盘价刚好回到昨天的 5 日均线附近，且昨天量能/换手达标，视为回踩有承接。",
        "details": [
            "开盘距上日 MA5 不超过 +3%，且不低于配置的偏离下限",
            "开盘涨幅落在 (-3%, 7%] 区间",
            "上日成交额、换手率达标",
        ],
    },
    {
        "code": BUY_SIGNAL_OPEN_TURN_STRONG,
        "label": "弱转强",
        "window": "09:35 首根 5 分钟 K",
        "summary": "开盘高开并突破昨天最高价，第一根 5 分钟 K 明显放量换手，视为弱转强。",
        "details": [
            "开盘价高于上日最高价",
            "开盘涨幅在 [0, 5.5%] 区间",
            "首根 5 分钟 K 换手率达到门槛",
        ],
    },
    {
        "code": BUY_SIGNAL_DIVERGENCE_BREAK,
        "label": "分歧买龙",
        "window": "09:35–10:00（默认 6 根）",
        "summary": "连续缩量一字板后今天开板，前 6 根 5 分钟 K 没跌破昨收且收盘企稳，视为分歧换手。",
        "details": [
            "前两日以上连续一字板且持续缩量",
            "断板开盘未接近涨停",
            "承接窗口内最低价不破昨收，末根收盘不低于首根开盘",
        ],
    },
    {
        "code": BUY_SIGNAL_DRAGON_COMEBACK,
        "label": "龙回头",
        "window": "09:30 开盘（日 K 形态）",
        "summary": "第一波大涨后缩量回调 3–10 天，昨天出现涨停或阳线吞没，视为二波启动。",
        "details": [
            "近 15 日内存在一波涨幅充足、涨停数达标的上涨段",
            "回调深度落在第一波涨幅的 30%–50%，且不破 MA20",
            "回调期缩量，T-1 涨停或阳线吞没确认",
        ],
    },
]

_SELL_RULE_META = {
    "first_day_stop": {
        "label": "首日止损减半",
        "summary": "买入第一天盘中亏到 -3.5% 先卖一半；灾难线（默认关）直接清仓。",
        "trigger": "首日浮亏触及 -3.5%（或配置的灾难线）",
    },
    "hard_stop": {
        "label": "硬止损",
        "summary": "首日亏到 -3.5%，之后亏到 -5%，触及就清仓。",
        "trigger": "首日 -3.5%，后续 -5%",
    },
    "trailing": {
        "label": "移动止盈",
        "summary": "曾经赚过 8% 后，从最高点回撤 3.5% 卖出；或 14:55 跌破 5 日均线。",
        "trigger": "浮盈 ≥8% 后回撤 3.5% 或尾盘失守 MA5",
    },
    "breakeven": {
        "label": "保本止盈",
        "summary": "曾经赚过 6% 后跌回成本价，卖出保本。",
        "trigger": "浮盈 ≥6% 后触及完整成本线",
    },
    "high_open_clear": {
        "label": "高开未封板",
        "summary": "高开 7% 看 5 分钟、高开 5% 看 30 分钟，仍未涨停就清仓。",
        "trigger": "高开 7%/5% 后对应窗口内未触板",
    },
    "soft_time_stop": {
        "label": "软时间止损",
        "summary": "买入 N 天后既没创新高也没脱离成本区，说明逻辑没走出来，退出。",
        "trigger": "持有 N 日仍未创新高且未脱离成本区",
    },
    "weak_close": {
        "label": "弱势收盘",
        "summary": "14:55 收得弱（收<开且跌超 1%，或跌破 5 日均线/昨收）就清仓。",
        "trigger": "尾盘弱势或跌破 MA5/昨收",
    },
    "hard_time_stop": {
        "label": "硬时间止损",
        "summary": "持有到最大天数仍未封板，清仓。",
        "trigger": "持有达最大天数且未封板",
    },
    "half": {
        "label": "次日涨停减半",
        "summary": "买入后第一个可卖日 14:55 仍封涨停，先卖一半。",
        "trigger": "首个可卖日尾盘仍封板",
    },
    "volume_spike": {
        "label": "放量止盈",
        "summary": "14:55 成交量达到昨天 1.3 倍且未封板，清仓。",
        "trigger": "尾盘量能放大 30% 且未封板",
    },
}

# 顺序与 strategy/sell.py 的 _SELL_RULES 一致（models.SELL_RULE_NAMES 是唯一真值）。
SELL_RULES = [
    {
        "name": name,
        "label": _SELL_RULE_META[name]["label"],
        "default_enabled": name not in DEFAULT_OFF_SELL_RULES,
        "summary": _SELL_RULE_META[name]["summary"],
        "trigger": _SELL_RULE_META[name]["trigger"],
    }
    for name in SELL_RULE_NAMES
]

#: reason_code → 规则名；一条规则可产出多个 reason_code。
SELL_REASON_TO_RULE = {
    "first_day_stop_half": "first_day_stop",
    "first_day_disaster_stop": "first_day_stop",
    "first_day_stop_loss": "hard_stop",
    "hard_stop_loss": "hard_stop",
    "trailing_take_profit": "trailing",
    "profit_back_to_cost_take_profit": "breakeven",
    "high_open_7pct_no_limit_5m_clear": "high_open_clear",
    "high_open_5pct_no_limit_30m_clear": "high_open_clear",
    "soft_time_stop": "soft_time_stop",
    "next_day_close_below_open": "weak_close",
    "close_below_open_stop": "weak_close",
    "break_intraday_ma_stop": "weak_close",
    "hard_time_stop": "hard_time_stop",
    "next_day_limit_up_half": "half",
    "volume_spike_take_profit": "volume_spike",
}

THRESHOLDS = {
    "buy_window_days": constants.BUY_WINDOW_DAYS,
    "window_skip": constants.WINDOW_SKIP_DAYS,
    "window_size": constants.WINDOW_SIZE_DAYS,
    "buy_win_return_gt": constants.BUY_WIN_RETURN_GT,
    "buy_win_drawdown_gt": constants.BUY_WIN_DRAWDOWN_GT,
    "sell_good_drawdown_le": constants.SELL_GOOD_DRAWDOWN_LE,
    "sell_fly_return_ge": constants.SELL_FLY_RETURN_GE,
    "max_replay_trading_days": constants.MAX_REPLAY_TRADING_DAYS,
}

#: 四类买点代码（顺序与 models.BUY_SIGNAL_CODES 一致）。
BUY_SIGNAL_ALL_CODES = BUY_SIGNAL_CODES

#: rule name → label / reason_code → label 的投影表（Web 只读）。
SELL_RULE_LABELS = {item["name"]: item["label"] for item in SELL_RULES}
SELL_REASON_LABELS = {
    reason: SELL_RULE_LABELS[rule]
    for reason, rule in SELL_REASON_TO_RULE.items()
}
BUY_SIGNAL_LABELS = {item["code"]: item["label"] for item in BUY_SIGNALS}

__all__ = [
    "BUY_SIGNALS",
    "BUY_SIGNAL_ALL_CODES",
    "BUY_SIGNAL_LABELS",
    "EVALUATOR_VERSION",
    "SELL_REASON_LABELS",
    "SELL_REASON_TO_RULE",
    "SELL_RULES",
    "SELL_RULE_LABELS",
    "STRATEGY_DOC_VERSION",
    "THRESHOLDS",
]
