"""prep 单日效果投影（方案 §7.2，只读 Facade）。

- 板块：T 快照真主线 + T/T+1 ``ths_block_top`` 的 ``code`` / ``change``；
- 逐股：推荐 / 不推荐 / 数据不足分组，入场与窗口完全调用
  :func:`dragon_quant.review.strict_metrics.evaluate_strict`；
- 行情只读调用方注入的本地日 K，不触网、不写库、不补算。
"""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Sequence

from dragon_quant.review.strict_metrics import (
    PARTIAL_WINDOW_REASON,
    evaluate_strict,
    is_valid_bar,
)

from .local_market import LOCAL_CAVEATS

__all__ = ["build_day_effect"]

GROUP_LABELS = {
    "recommended": "推荐",
    "not_recommended": "不推荐",
    "data_insufficient": "数据不足",
}
GROUP_ORDER = ("recommended", "not_recommended", "data_insufficient")
PREP_ADVICE_BUILD = ("重仓", "正常", "轻仓")


def _as_dict(value: Any) -> dict | None:
    return value if isinstance(value, Mapping) else None


def _as_list(value: Any) -> list | None:
    return value if isinstance(value, list) else None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _rows(payload: Any) -> list[dict] | None:
    data = _as_dict(payload)
    rows = _as_list((data or {}).get("rows"))
    if rows is None:
        return None
    return [row for row in rows if isinstance(row, dict)]


def _first_by_code(rows: Sequence[Mapping[str, Any]],
                   code: str) -> Mapping[str, Any] | None:
    return next((row for row in rows
                 if _text(row.get("code")) == code), None)


def _first_by_name(rows: Sequence[Mapping[str, Any]],
                   name: str) -> list[Mapping[str, Any]]:
    return [row for row in rows if _text(row.get("name")) == name]


def _mean(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def _median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


# ─── 板块 ────────────────────────────────────────────────────────────────


def _board_source(snapshot: Mapping[str, Any]) -> tuple[list | None, str | None,
                                                       list[str]]:
    review = _as_dict(snapshot.get("review")) or {}
    reviewed = _as_list(review.get("mainlines"))
    if reviewed is not None:
        return ([row for row in reviewed
                 if isinstance(row, dict) and row.get("is_mainline") is True],
                "review_mainlines", [])
    legacy = _as_list(snapshot.get("main_boards"))
    if legacy is not None:
        return ([row for row in legacy if isinstance(row, dict)],
                "main_boards", ["legacy_main_boards"])
    return None, None, []


def _board_row(item: Mapping[str, Any], *, trade_date: str,
               next_trade_date: str | None,
               t_rows: Sequence[Mapping[str, Any]] | None,
               next_rows: Sequence[Mapping[str, Any]] | None,
               next_asset_present: bool) -> dict:
    name = _text(item.get("board_name")) or _text(item.get("name"))
    category = _text(item.get("category"))
    warnings: list[str] = []
    reason: str | None = None
    match: str | None = None
    board_code: str | None = None
    t_change: float | None = None
    next_change: float | None = None

    t_row = None
    if t_rows is not None and name:
        matches = _first_by_name(t_rows, name)
        t_row = matches[0] if matches else None
    if t_row is not None:
        board_code = _text(t_row.get("code"))
        t_change = _num(t_row.get("change"))
        if board_code is None:
            reason = "board_code_missing"
        elif t_change is None:
            reason = "change_missing"
    else:
        reason = "board_code_missing"

    if next_trade_date is None:
        reason = reason or "next_trading_day_unavailable"
    elif not next_asset_present:
        reason = reason or "next_board_asset_missing"
    elif next_rows is not None:
        matched = _first_by_code(next_rows, board_code) if board_code else None
        if matched is not None:
            match = "code"
        elif name:
            named = _first_by_name(next_rows, name)
            if len(named) == 1:
                matched = named[0]
                match = "name"
                warnings.append("board_name_fallback")
            elif len(named) > 1:
                reason = reason or "ambiguous_board_name"
            else:
                reason = reason or "next_board_missing"
        else:
            reason = reason or "next_board_missing"
        if matched is not None:
            next_change = _num(matched.get("change"))
            if next_change is None:
                reason = reason or "change_missing"

    return {
        "rank": item.get("rank"),
        "board_name": name,
        "category": category,
        "is_mainline": True,
        "board_code": board_code,
        "t_change_pct": t_change,
        "next_change_pct": next_change,
        "match": match,
        "exclusion_reason": reason,
        "warnings": warnings,
    }


def _build_boards(*, trade_date: str, next_trade_date: str | None,
                  snapshot: Mapping[str, Any], blocks: Any,
                  next_blocks: Any) -> tuple[dict, list[str]]:
    rows, source, warnings = _board_source(snapshot)
    if rows is None:
        return _boards_unavailable("mainline_missing"), warnings
    t_rows = _rows(blocks)
    if t_rows is None:
        return _boards_unavailable("board_asset_missing"), warnings
    next_rows = _rows(next_blocks)
    next_present = next_rows is not None

    built: list[dict] = []
    for item in rows:
        built.append(_board_row(
            item, trade_date=trade_date, next_trade_date=next_trade_date,
            t_rows=t_rows, next_rows=next_rows,
            next_asset_present=next_present))

    evaluated_changes = [row["next_change_pct"] for row in built
                         if row["next_change_pct"] is not None]
    return ({
        "available": True,
        "reason": None,
        "source": source,
        "rows": built,
        "summary": {
            "evaluated": len(evaluated_changes),
            "missing": len(built) - len(evaluated_changes),
            "mean_change_pct": _mean(evaluated_changes),
        },
    }, warnings)


def _boards_unavailable(reason: str) -> dict:
    return {
        "available": False,
        "reason": reason,
        "source": None,
        "rows": [],
        "summary": {"evaluated": 0, "missing": 0, "mean_change_pct": None},
    }


# ─── 逐股 ────────────────────────────────────────────────────────────────


def _group_for(*, signal_missing: bool, eligibility: Any,
               position_pct: float | None) -> str:
    if signal_missing or position_pct is None:
        return "data_insufficient"
    if position_pct == 0:
        return "not_recommended"
    if eligibility == "eligible" and position_pct > 0:
        return "recommended"
    return "data_insufficient"


def _stock_result(*, bars_for_code: list[dict], calendar: list[str],
                  trade_date: str, next_trade_date: str | None) -> dict:
    base = {
        "status": "pending",
        "buy_date": None,
        "buy_price": None,
        "max_return_5d": None,
        "max_drawdown_5d": None,
        "max_return_hold_days": None,
        "partial_window": False,
        "reason": None,
    }
    if next_trade_date is None:
        base["reason"] = "next_trading_day_not_available"
        return base

    buy_bar = next((bar for bar in bars_for_code
                    if _text(bar.get("date")) == next_trade_date), None)
    if buy_bar is None:
        base.update(status="cannot_evaluate", reason="missing_bar")
        return base
    if not is_valid_bar(buy_bar):
        base.update(status="cannot_evaluate", reason="invalid_bar")
        return base

    strict = evaluate_strict(bars_for_code, set(calendar), trade_date)
    if strict["review_status"] == "partial":
        # 窗口已开始但未走完：展示为 partial（可看，不入正式分母）。
        return {
            "status": "completed",
            "buy_date": strict["buy_date"],
            "buy_price": strict["buy_price"],
            "max_return_5d": strict["max_return_5d"],
            "max_drawdown_5d": strict["max_drawdown_5d"],
            "max_return_hold_days": strict["max_return_hold_days"],
            "partial_window": True,
            "reason": PARTIAL_WINDOW_REASON,
        }
    if strict["review_status"] != "completed":
        # 买入 bar 有效但窗口尚未开始：展示 pending，而不是失败。
        base["reason"] = "window_not_started"
        return base

    return {
        "status": "completed",
        "buy_date": strict["buy_date"],
        "buy_price": strict["buy_price"],
        "max_return_5d": strict["max_return_5d"],
        "max_drawdown_5d": strict["max_drawdown_5d"],
        "max_return_hold_days": strict["max_return_hold_days"],
        "partial_window": strict["reason"] == PARTIAL_WINDOW_REASON,
        "reason": None,
    }


def _build_stocks(*, trade_date: str, next_trade_date: str | None,
                  snapshot: Mapping[str, Any], calendar: list[str],
                  bars: Mapping[str, list[dict]]) -> tuple[dict, list[str]]:
    candidates = _as_list(snapshot.get("review_candidates"))
    if candidates is None:
        return ({
            "available": False,
            "reason": "review_candidates_missing",
            "rows": [],
            "summary": [],
        }, [])

    sentiment = _as_dict(snapshot.get("sentiment")) or {}
    position_pct = _num(sentiment.get("position_pct"))
    advice = _text(sentiment.get("position_advice"))
    signal_missing = (position_pct is None
                      and advice not in PREP_ADVICE_BUILD
                      and advice != "空仓")

    rows: list[dict] = []
    warnings: list[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        code = _text(item.get("code"))
        if not code:
            continue
        item_position = _num(item.get("position_pct"))
        eligibility = item.get("eligibility")
        group = _group_for(signal_missing=signal_missing,
                           eligibility=eligibility,
                           position_pct=item_position)
        if group == "data_insufficient" and not signal_missing \
                and item_position is not None and item_position > 0:
            warnings.append(
                f"inconsistent_group: {code} position_pct>0 "
                "但不在推荐集（资格/日级冲突）")

        result = _stock_result(
            bars_for_code=list(bars.get(code) or []), calendar=calendar,
            trade_date=trade_date, next_trade_date=next_trade_date)
        rows.append({
            "code": code,
            "name": _text(item.get("name")),
            "group": group,
            "role": item.get("role"),
            "review_score": _num(item.get("review_score")),
            "eligibility": eligibility,
            "position_pct": item_position,
            **result,
        })

    index = {group: order for order, group in enumerate(GROUP_ORDER)}
    rows.sort(key=lambda row: (index.get(row["group"], 9), row["code"]))
    summary = _group_summary(rows)
    return ({
        "available": True,
        "reason": None,
        "rows": rows,
        "summary": summary,
    }, warnings)


def _group_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    summary: list[dict] = []
    for group in GROUP_ORDER:
        members = [row for row in rows if row["group"] == group]
        completed = [row for row in members if row["status"] == "completed"
                     and not row.get("partial_window")]
        partial = sum(1 for row in members if row.get("partial_window"))
        returns = [row["max_return_5d"] for row in completed
                   if row["max_return_5d"] is not None]
        drawdowns = [row["max_drawdown_5d"] for row in completed
                     if row["max_drawdown_5d"] is not None]
        summary.append({
            "group": group,
            "label": GROUP_LABELS[group],
            "valid": len(completed),
            "partial": partial,
            "cannot_evaluate": sum(1 for row in members
                                   if row["status"] == "cannot_evaluate"),
            "pending": sum(1 for row in members
                           if row["status"] == "pending"),
            "mean_max_return_5d": _mean(returns),
            "median_max_return_5d": _median(returns),
            "mean_max_drawdown_5d": _mean(drawdowns),
            "median_max_drawdown_5d": _median(drawdowns),
        })
    return summary


# ─── 入口 ────────────────────────────────────────────────────────────────


def build_day_effect(
    *,
    trade_date: str,
    snapshot: Mapping[str, Any] | None,
    calendar: Sequence[str],
    blocks: Any,
    next_blocks: Any,
    bars: Mapping[str, Sequence[Mapping[str, Any]]] | None,
    next_trade_date: str | None = None,
    fatal_reason: str | None = None,
    extra_warnings: Sequence[str] = (),
) -> dict:
    """构建 ``GET /api/prep/{trade_date}/effect`` 响应主体。

    ``fatal_reason`` 用于日 K 截断无法拆分等「不得输出部分统计」的场景。
    """

    days = sorted({str(day) for day in (calendar or []) if day})
    if next_trade_date is None:
        next_trade_date = next((day for day in days if day > trade_date), None)
    bars_by_code = {str(code): list(items)
                    for code, items in (bars or {}).items()}

    if fatal_reason:
        return _payload(
            trade_date=trade_date, next_trade_date=next_trade_date,
            snapshot_version=(str(snapshot.get("version"))
                              if isinstance(snapshot, Mapping)
                              and snapshot.get("version") is not None else None),
            calendar=days,
            boards=_boards_unavailable(fatal_reason),
            stocks={"available": False, "reason": fatal_reason,
                    "rows": [], "summary": []},
            warnings=list(extra_warnings), status="insufficient_data",
            reason=fatal_reason)

    if not isinstance(snapshot, Mapping):
        return _payload(
            trade_date=trade_date, next_trade_date=next_trade_date,
            snapshot_version=None, calendar=days,
            boards=_boards_unavailable("snapshot_missing"),
            stocks={"available": False, "reason": "snapshot_missing",
                    "rows": [], "summary": []},
            warnings=list(extra_warnings), status="insufficient_data",
            reason="snapshot_missing")

    boards, board_warnings = _build_boards(
        trade_date=trade_date, next_trade_date=next_trade_date,
        snapshot=snapshot, blocks=blocks, next_blocks=next_blocks)
    stocks, stock_warnings = _build_stocks(
        trade_date=trade_date, next_trade_date=next_trade_date,
        snapshot=snapshot, calendar=days, bars=bars_by_code)
    warnings = list(extra_warnings) + board_warnings + stock_warnings

    boards_done = any(row["next_change_pct"] is not None
                      for row in boards["rows"])
    stocks_done = any(row["status"] == "completed"
                      for row in stocks["rows"])
    if boards_done or stocks_done:
        status, reason = "ok", None
    else:
        status = "insufficient_data"
        if not bars_by_code:
            reason = "local_bars_missing"
        elif next_trade_date is None:
            reason = "next_trading_day_unavailable"
        else:
            reason = "no_signal"

    return _payload(
        trade_date=trade_date, next_trade_date=next_trade_date,
        snapshot_version=(str(snapshot.get("version"))
                          if snapshot.get("version") is not None else None),
        calendar=days, boards=boards, stocks=stocks, warnings=warnings,
        status=status, reason=reason)


def _payload(*, trade_date: str, next_trade_date: str | None,
             snapshot_version: str | None, calendar: Sequence[str],
             boards: Mapping[str, Any], stocks: Mapping[str, Any],
             warnings: Sequence[str], status: str, reason: str | None) -> dict:
    return {
        "status": status,
        "reason": reason,
        "trade_date": trade_date,
        "next_trade_date": next_trade_date,
        "snapshot_version": snapshot_version,
        "data_source": "local_prep_assets+local_stock_daily",
        "calendar_basis": "local_stock_daily_bars_union",
        "boards": dict(boards),
        "stocks": dict(stocks),
        "coverage": {
            "boards_total": len(boards.get("rows") or []),
            "stocks_total": len(stocks.get("rows") or []),
            "bar_dates": len(calendar),
        },
        "warnings": list(warnings),
        "caveats": list(LOCAL_CAVEATS),
    }
