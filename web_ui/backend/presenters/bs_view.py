"""``/bs`` 视图投影（纯函数：无 I/O、无 SQL、无时钟）。

标签与阈值由调用方传入（来自 ``bs_eval.strategy_doc`` 的数据），本模块不
import ``bs_eval`` 实现；所有缺失指标原样输出 ``None``，前端显示 —。
"""

from __future__ import annotations

from typing import Optional

VERDICT_LABELS = {
    "flew": "卖飞",
    "good": "卖得对",
    "neutral": "中性",
    "partial": "窗口不足",
}

_ENGINE_FIELDS = (
    "trade_date", "code", "name", "eval_status", "eval_note",
    "data_quality", "replay_days", "fallback_days", "suspended_days",
    "fault_days",
)


def _engine_fields(row: dict) -> dict:
    return {key: row.get(key) for key in _ENGINE_FIELDS}


def _partial(row: dict, key: str) -> bool:
    return bool(row.get(key))


def _win(row: dict, prefix: str, thresholds: dict) -> Optional[bool]:
    if _partial(row, "buy_window_partial"):
        return None
    ret = row.get(f"buy_{prefix}_max_return_5d")
    drawdown = row.get(f"buy_{prefix}_max_drawdown_5d")
    if ret is None or drawdown is None:
        return None
    return (ret > thresholds["buy_win_return_gt"]
            and drawdown > thresholds["buy_win_drawdown_gt"])


def present_buy_row(row: dict, labels: dict) -> dict:
    thresholds = labels["thresholds"]
    return {
        **_engine_fields(row),
        "buy_date": row.get("buy_date"),
        "buy_rule_code": row.get("buy_rule_code"),
        "buy_rule_label": labels["buy"].get(row.get("buy_rule_code")),
        "buy_loose_price": row.get("buy_loose_price"),
        "buy_strict_price": row.get("buy_strict_price"),
        "buy_loose_max_return_5d": row.get("buy_loose_max_return_5d"),
        "buy_loose_max_drawdown_5d": row.get("buy_loose_max_drawdown_5d"),
        "buy_strict_max_return_5d": row.get("buy_strict_max_return_5d"),
        "buy_strict_max_drawdown_5d": row.get("buy_strict_max_drawdown_5d"),
        "buy_max_return_hold_days": row.get("buy_max_return_hold_days"),
        "buy_window_partial": _partial(row, "buy_window_partial"),
        "loose_win": _win(row, "loose", thresholds),
        "strict_win": _win(row, "strict", thresholds),
        "buy_data_quality": row.get("buy_data_quality"),
    }


def present_sell_row(row: dict, labels: dict, verdict: str) -> dict:
    labels_map = labels["sell_reason"]
    half_date = row.get("sell_half_date")
    half_exit = None
    if half_date:
        half_exit = {
            "date": half_date,
            "price": row.get("sell_half_price"),
            "qty": row.get("sell_half_qty"),
            "rule_code": row.get("sell_half_rule_code"),
            "rule_label": labels_map.get(row.get("sell_half_rule_code")),
        }
    return {
        **_engine_fields(row),
        "sell_date": row.get("sell_date"),
        "sell_rule_code": row.get("sell_rule_code"),
        "sell_rule_label": labels_map.get(row.get("sell_rule_code")),
        "sell_price": row.get("sell_price"),
        "sell_hold_days": row.get("sell_hold_days"),
        "sell_max_return_5d": row.get("sell_max_return_5d"),
        "sell_max_drawdown_5d": row.get("sell_max_drawdown_5d"),
        "sell_max_return_hold_days": row.get("sell_max_return_hold_days"),
        "sell_post_exit_window_days": row.get("sell_post_exit_window_days"),
        "sell_half_max_return_5d": row.get("sell_half_max_return_5d"),
        "sell_half_max_drawdown_5d": row.get("sell_half_max_drawdown_5d"),
        "sell_half_window_days": row.get("sell_half_window_days"),
        "sell_window_partial": _partial(row, "sell_window_partial"),
        "fault_days": row.get("fault_days"),
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS.get(verdict, verdict),
        "half_exit": half_exit,
        "sell_data_quality": row.get("sell_data_quality"),
    }


def present_overview(run: Optional[dict], strategy: dict, summary: dict,
                     stale_rows: int) -> dict:
    return {
        "run": run,
        "strategy": strategy,
        "summary": summary,
        "stale_rows": int(stale_rows or 0),
    }


def labels(strategy_doc) -> dict:
    """从 ``strategy_doc`` 数据模块构造投影标签（路由层唯一调用点）。"""

    return {
        "buy": strategy_doc.BUY_SIGNAL_LABELS,
        "sell_reason": strategy_doc.SELL_REASON_LABELS,
        "sell_rule": strategy_doc.SELL_RULE_LABELS,
        "thresholds": strategy_doc.THRESHOLDS,
    }


__all__ = [
    "VERDICT_LABELS",
    "labels",
    "present_buy_row",
    "present_overview",
    "present_sell_row",
]
