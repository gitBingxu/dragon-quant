"""策略纯拆分：共享值对象与信号构造原语（Task 14b）。

``RuleResult`` / ``BuyRejected`` / ``BuyCheckContext`` 与 ``_safe_rule`` /
``_payload`` / ``_buy`` / ``_sell`` / ``_ctx`` 从原 ``strategy.py`` 原样搬入，
函数体与阈值零变化。

``BuyCheckContext`` 定义在此（而非 ``checks``）是为了让依赖图保持无环：
``buy`` 与 ``checks`` 都依赖上下文类型，``checks`` 又依赖 ``buy`` 的规则函数；
把它放在最底层的 ``common`` 可避免 ``common ↔ checks`` 环。``checks`` 仍以
re-export 保持文档化的模块面。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from dragon_quant.review_account.models import StrategyConfig


@dataclass(frozen=True)
class RuleResult:
    """单条买点规则的纯判定结果。``unavailable`` 表示数据不足而非条件不满足。"""

    ok: bool
    reason: str
    signal: Optional[dict] = None
    unavailable: bool = False


@dataclass(frozen=True)
class BuyCheckContext:
    candidate: dict
    row: dict
    cfg: StrategyConfig
    prev_row: Optional[dict] = None
    hist_rows: Optional[list] = None
    intraday_bars: Optional[list] = None


class BuyRejected(Exception):
    """买入否决 / 禁买的早退异常，携带原因码供 explain 精确回显（而非落入 no_buy_pattern）。

    ``signal`` 仅在「本可买入但被否决」时携带，供影子统计与决策记录使用。
    """

    def __init__(self, code: str, text: str, signal: Optional[dict] = None):
        self.code, self.text, self.signal = code, text, signal
        super().__init__(text)


def _safe_rule(fn, *args) -> RuleResult:
    """诊断用兜底：单条规则异常只降级该条，不影响其它规则与决策。"""
    try:
        return fn(*args)
    except Exception as exc:  # pragma: no cover - 由异常注入用例覆盖
        return RuleResult(False, f"诊断异常：{type(exc).__name__}", unavailable=True)


def _ctx(candidate: dict, row: dict, cfg: StrategyConfig,
         prev_row: Optional[dict] = None, hist_rows: Optional[list] = None,
         intraday_bars: Optional[list] = None) -> BuyCheckContext:
    return BuyCheckContext(candidate, row, cfg, prev_row, hist_rows, intraday_bars)


def _payload(row: dict) -> dict:
    return {key: row.get(key) for key in ("date", "phase", "observed_at", "open", "close", "ma5",
            "prev_close", "open_gap_pct", "bar_high", "bar_low", "bar_close", "limit_up", "limit_down",
            "data_quality")}


def _buy(candidate: dict, row: dict, code: str, text: str, priority: int) -> dict:
    signal = _payload(row)
    signal.update({"candidate_trade_date": candidate.get("trade_date"), "rank": candidate.get("rank"),
                   "composite_score": candidate.get("composite_score")})
    return {"action": "BUY", "code": code, "reason_text": text, "priority": priority, "signal": signal}


def _sell(code: str, text: str, row: dict) -> dict:
    return {"action": "SELL", "code": code, "reason_text": text, "signal": _payload(row)}
