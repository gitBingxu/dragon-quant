"""BS 回放聚合口径（纯函数，CLI 与 Web 共用）。

所有比率/均值保留两位小数；比率使用百分数（0–100）；分母为 0 返回 ``None``
（页面显示 —），绝不伪造 0。
"""

from __future__ import annotations

from typing import Iterable, Optional

from dragon_quant.bs_eval import constants
from dragon_quant.bs_eval import strategy_doc


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 2)


def _rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 2)


def _partial(row: dict, key: str) -> bool:
    return bool(row.get(key))


def buy_win(row: dict, prefix: str) -> Optional[bool]:
    """买入胜利：窗口成熟且指标非空 → bool；否则 ``None``（窗口不足）。"""

    if _partial(row, "buy_window_partial"):
        return None
    ret = row.get(f"buy_{prefix}_max_return_5d")
    drawdown = row.get(f"buy_{prefix}_max_drawdown_5d")
    if ret is None or drawdown is None:
        return None
    return ret > constants.BUY_WIN_RETURN_GT and drawdown > constants.BUY_WIN_DRAWDOWN_GT


def sell_verdict(row: dict) -> str:
    """卖出判定（互斥，先卖飞再卖得对；窗口不足 → partial）。"""

    if _partial(row, "sell_window_partial"):
        return "partial"
    ret = row.get("sell_max_return_5d")
    drawdown = row.get("sell_max_drawdown_5d")
    if ret is None or drawdown is None:
        return "partial"
    if ret >= constants.SELL_FLY_RETURN_GE:
        return "flew"
    if drawdown <= constants.SELL_GOOD_DRAWDOWN_LE:
        return "good"
    return "neutral"


VERDICT_LABELS = {
    "flew": "卖飞",
    "good": "卖得对",
    "neutral": "中性",
    "partial": "窗口不足",
}


def _buy_samples(rows: list[dict], prefix: str) -> list[dict]:
    price_key = f"buy_{prefix}_price"
    ret_key = f"buy_{prefix}_max_return_5d"
    drawdown_key = f"buy_{prefix}_max_drawdown_5d"
    return [
        row for row in rows
        if row.get("buy_date")
        and row.get(price_key) is not None
        and not _partial(row, "buy_window_partial")
        and row.get(ret_key) is not None
        and row.get(drawdown_key) is not None
    ]


def _buy_side(rows: list[dict]) -> dict:
    strict = _buy_samples(rows, "strict")
    loose = _buy_samples(rows, "loose")

    def block(samples: list[dict], prefix: str) -> dict:
        wins = sum(
            1 for row in samples
            if row.get(f"buy_{prefix}_max_return_5d") > constants.BUY_WIN_RETURN_GT
            and row.get(f"buy_{prefix}_max_drawdown_5d") > constants.BUY_WIN_DRAWDOWN_GT
        )
        return {
            "samples": len(samples),
            "wins": wins,
            "win_rate": _rate(wins, len(samples)),
            "avg_return": _mean(row.get(f"buy_{prefix}_max_return_5d")
                                for row in samples),
            "avg_drawdown": _mean(row.get(f"buy_{prefix}_max_drawdown_5d")
                                  for row in samples),
        }

    triggered = sum(1 for row in rows if row.get("buy_date"))
    breakdown = [
        {
            "rule": item["code"],
            "label": item["label"],
            "count": sum(1 for row in rows
                         if row.get("buy_rule_code") == item["code"]),
        }
        for item in strategy_doc.BUY_SIGNALS
    ]
    return {
        "triggered": triggered,
        "strict": block(strict, "strict"),
        "loose": block(loose, "loose"),
        "rule_breakdown": breakdown,
    }


def _sell_side(rows: list[dict]) -> dict:
    samples = [
        row for row in rows
        if row.get("sell_date")
        and not _partial(row, "sell_window_partial")
        and row.get("sell_max_return_5d") is not None
        and row.get("sell_max_drawdown_5d") is not None
    ]
    verdicts = [sell_verdict(row) for row in samples]
    triggered = sum(1 for row in rows if row.get("sell_date"))
    breakdown = [
        {
            "rule": item["name"],
            "label": item["label"],
            "count": sum(
                1 for row in rows
                if strategy_doc.SELL_REASON_TO_RULE.get(row.get("sell_rule_code"))
                == item["name"]
            ),
        }
        for item in strategy_doc.SELL_RULES
    ]
    return {
        "triggered": triggered,
        "samples": len(samples),
        "good": verdicts.count("good"),
        "good_rate": _rate(verdicts.count("good"), len(samples)),
        "flew": verdicts.count("flew"),
        "flew_rate": _rate(verdicts.count("flew"), len(samples)),
        "neutral": verdicts.count("neutral"),
        "neutral_rate": _rate(verdicts.count("neutral"), len(samples)),
        "avg_return": _mean(row.get("sell_max_return_5d") for row in samples),
        "avg_drawdown": _mean(row.get("sell_max_drawdown_5d") for row in samples),
        "rule_breakdown": breakdown,
    }


def _status_counts(rows: list[dict]) -> dict:
    counts = {status: 0 for status in constants.STATUSES}
    for row in rows:
        status = row.get("eval_status")
        if status in counts:
            counts[status] += 1
    return counts


def summarize(rows: list[dict]) -> dict:
    """全量/筛选后的回放行 → 汇总结构（§4.8）。"""

    evaluable = sum(
        1 for row in rows
        if row.get("eval_status") in constants.TRIGGER_DENOMINATOR_STATUSES)
    buy = _buy_side(rows)
    buy["trigger_rate"] = _rate(buy["triggered"], evaluable)
    return {
        "rows_total": len(rows),
        "status_counts": _status_counts(rows),
        "buy": buy,
        "sell": _sell_side(rows),
    }


def empty_summary() -> dict:
    return summarize([])


def buy_summary(rows: list[dict]) -> dict:
    """``/api/bs/buys`` 的 buy 侧统计（不含触发率/状态分布）。"""

    buy = _buy_side(rows)
    strict = buy["strict"]
    return {
        "triggered": buy["triggered"],
        "strict": buy["strict"],
        "loose": buy["loose"],
        "rule_breakdown": buy["rule_breakdown"],
        # 便捷键：前端汇总卡默认展示严格口径。
        "samples": strict["samples"],
        "wins": strict["wins"],
        "win_rate": strict["win_rate"],
        "avg_return": strict["avg_return"],
        "avg_drawdown": strict["avg_drawdown"],
    }


__all__ = [
    "VERDICT_LABELS",
    "buy_summary",
    "buy_win",
    "empty_summary",
    "sell_verdict",
    "summarize",
]
