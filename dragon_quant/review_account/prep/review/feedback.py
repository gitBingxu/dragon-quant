"""T+1 反馈与计划复盘（纯函数，无 I/O）。

产品 §11–§12 / 技术方案 §5、§7、§9.2 的实现：

* **时点**：状态只在 T+1 **盘后计算一次**（数据来自已落库的 ``auction_feedback``、
  ``t1_intraday`` 与 T+1 日 K）。09:25 / 10:00 / 收盘是冻结 playbook 的对照刻度，
  本模块不声称做过实时判定。
* **冻结预期**：``expected_low`` / ``expected_high`` / ``expected_ratio_low`` /
  ``expected_seal_by`` 一律取 ``prev_plan.feedback_plans[].expected``；只有旧快照
  缺该键时才回退到 ``CONFIG.expectations`` 并显式标记 ``config_fallback``，
  绝不静默重算。
* **代码集合**：只用 ``prev_plan.positions[].code``；当日竞价池/涨停池里的其它代码
  不参与反馈（否则会用今天的池评估昨天的计划）。
* **缺失语义**：竞价缺失/未验证、T+1 行情缺失、预期缺失 → ``unknown``；
  绝不把缺失当作 confirmed / weakened，也不编造成交价或仓位。
* **纯函数**：入参只有普通 dict/list/标量；不读环境变量、不读时钟、不写文件、
  不发请求、不 import sqlite3。时间比较全部用入参日期/时间字符串。

设计模式：**策略（Strategy）**——四态判定与动作映射由配置表驱动（阈值来自
``ReviewConfig``，动作映射来自 §5 的强制表），判定算法本身不内嵌数字。
"""

from __future__ import annotations

from typing import Any, Mapping

from .config import CONFIG, ReviewConfig

# 技术方案 §5「状态与动作的强制映射」：Agent 只能引用，不能扩写。
STATE_ACTIONS: dict[str, tuple[str, ...]] = {
    "confirmed": ("execute_plan", "execute_partial"),
    "weakened": ("downgrade_half", "wait_pullback"),
    "invalidated": ("cancel_entry", "exit_via_sell_rules"),
    "unknown": ("observe_only",),
    "pending": ("wait",),
}

_STATE_ORDER = ("confirmed", "invalidated", "weakened", "unknown")
_MARKET_RANK = {"ice": 0, "weak": 1, "neutral": 2, "strong": 3}
# 判定必需的冻结预期字段：缺任一即 expectations_incomplete（稳定 reason_code）。
_REQUIRED_EXPECTATIONS = ("gap_low", "ratio_low")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, Mapping) else {}


def _list_of_dicts(value: Any) -> list[dict]:
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    return None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return _round((numerator / denominator - 1.0) * 100.0)


def _dedupe_codes(values: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values if isinstance(values, list) else []:
        code = value.get("code") if isinstance(value, Mapping) else value
        if isinstance(code, str) and code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _minutes(moment: Any) -> int | None:
    """``HH:MM[:SS]`` → 当日分钟数（严格递增比较用）；非法返回 None。"""

    if not isinstance(moment, str) or not moment:
        return None
    parts = moment.split(":")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None
    return hour * 60 + minute


def _time_le(left: Any, right: Any) -> bool | None:
    left_min, right_min = _minutes(left), _minutes(right)
    if left_min is None or right_min is None:
        return None
    return left_min <= right_min


# ---------------------------------------------------------------------------
# 输入归一化
# ---------------------------------------------------------------------------


def _plan_positions(prev_plan: Mapping[str, Any]) -> list[dict]:
    rows = _list_of_dicts(prev_plan.get("positions"))
    return [row for row in rows if isinstance(row.get("code"), str) and row["code"]]


def _plans_by_code(prev_plan: Mapping[str, Any]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in _list_of_dicts(prev_plan.get("feedback_plans")):
        code = row.get("code")
        if isinstance(code, str) and code and code not in out:
            out[code] = row
    return out


def _auction_rows(auction: Mapping[str, Any]) -> dict[str, dict]:
    return {code: row for code, row in _as_dict(auction.get("rows_by_code")).items()
            if isinstance(code, str) and isinstance(row, Mapping)}


def _intraday_rows(intraday: Mapping[str, Any] | None, code: str) -> list[dict]:
    payload = _as_dict(intraday)
    rows = _list_of_dicts(payload.get("rows", {}).get(code)
                          if isinstance(payload.get("rows"), Mapping) else None)
    return sorted(rows, key=lambda row: str(row.get("time") or ""))


def _daily_rows(daily: Mapping[str, Any], code: str) -> list[dict]:
    payload = _as_dict(daily)
    rows = payload.get("rows")
    rows = rows if isinstance(rows, Mapping) else {}
    return sorted(_list_of_dicts(rows.get(code)), key=lambda row: str(row.get("date") or ""))


def _daily_slice(rows: list[dict], trade_date: str) -> tuple[dict | None, dict | None]:
    """返回 (T-1 及更早的最后一根, T+1 当日最后一根)。"""

    previous: dict | None = None
    current: dict | None = None
    for row in rows:
        date = str(row.get("date") or "")
        if date < trade_date:
            previous = row
        elif date == trade_date:
            current = row
    return previous, current


def _expected_for(
    code: str, position: Mapping[str, Any], plans: Mapping[str, dict],
    config: ReviewConfig,
) -> tuple[dict | None, str | None]:
    """冻结预期优先；旧快照缺失才回退配置并显式标注来源。"""

    plan = plans.get(code) or {}
    expected = plan.get("expected")
    if isinstance(expected, Mapping) and expected.get("gap_low") is not None:
        return dict(expected), "prev_plan"
    role = plan.get("role") or position.get("role")
    fallback = config.expectations.get(role) if isinstance(role, str) else None
    if isinstance(fallback, Mapping):
        return dict(fallback), "config_fallback"
    return None, None


# ---------------------------------------------------------------------------
# 盘中/日线指标
# ---------------------------------------------------------------------------


def _limit_ratio(code: str, config: ReviewConfig) -> float:
    rules = config.feedback_rules
    if code.startswith(("300", "301", "688", "689")):
        return float(rules.get("limit_up_ratio_growth", 0.20))
    return float(rules.get("limit_up_ratio_main", 0.10))


def _seal_metrics(
    code: str, prev_close: float | None, intraday: list[dict], config: ReviewConfig
) -> tuple[str | None, int | None]:
    """5 分钟 K → (首次封板时间 ``HH:MM``, 炸板次数)；无 5 分钟数据 → (None, None)。"""

    if prev_close is None or prev_close <= 0 or not intraday:
        return None, None
    rules = config.feedback_rules
    tolerance = float(rules.get("limit_up_tolerance", 0.001))
    limit_price = prev_close * (1.0 + _limit_ratio(code, config))
    threshold = limit_price * (1.0 - tolerance)
    sealed_by: str | None = None
    broken = 0
    at_limit = False
    for row in intraday:
        close = _num(row.get("close"))
        if close is None:
            continue
        is_at_limit = close >= threshold
        if is_at_limit and sealed_by is None:
            sealed_by = str(row.get("time") or "") or None
        if at_limit and not is_at_limit:
            broken += 1
        at_limit = is_at_limit
    return sealed_by, broken


def _missing_expectation_fields(expected: Mapping[str, Any] | None) -> list[str]:
    """判定必需的冻结字段缺失清单（``seal_by=None`` 合法，不算缺失）。"""

    if not isinstance(expected, Mapping):
        return list(_REQUIRED_EXPECTATIONS)
    return [key for key in _REQUIRED_EXPECTATIONS
            if _num(expected.get(key)) is None]


def _auction_ratio(row: Mapping[str, Any]) -> float | None:
    """竞价额比 = 今日竞价额 / 昨日竞价额；昨日缺失用已有历史均值。

    ``prev_amounts`` 是 T-1..T-5 的**定长**列表（新→旧，缺失日为 ``None``）：
    首元素有效即「昨日」口径；否则对非空历史取均值；全部缺失 → ``None``
    （绝不填 1.0 伪造）。
    """

    amount = _num(row.get("amount"))
    if amount is None:
        return None
    raw = list(row.get("prev_amounts") or [])
    yesterday = _num(raw[0]) if raw else None
    if yesterday is not None and yesterday > 0:
        return _round(amount / yesterday, 4)
    history = [value for value in (_num(item) for item in raw)
               if value is not None and value > 0]
    if not history:
        return None
    return _round(amount / (sum(history) / len(history)), 4)


def _mainline_dropped(
    position: Mapping[str, Any], current_mainlines: Any
) -> bool | None:
    """所在主线是否跌出当日前 3；无 T 日主线信息 → None（不评估）。"""

    if current_mainlines is None:
        return None
    mainline = _as_dict(position.get("mainline"))
    board = mainline.get("board_name")
    if not isinstance(board, str) or not board:
        return None
    top = _list_of_dicts(current_mainlines)[:3]
    names = {row.get("board_name") for row in top}
    return board not in names


# ---------------------------------------------------------------------------
# 四态判定
# ---------------------------------------------------------------------------


def _classify(
    *,
    gap_pct: float | None,
    ratio: float | None,
    sealed_by: str | None,
    close_pct: float | None,
    below_prev: bool,
    broken_count: int | None,
    mainline_dropped: bool | None,
    expected: Mapping[str, Any],
    config: ReviewConfig,
) -> tuple[str, str | None]:
    """按 confirmed → invalidated → weakened 顺序判定，返回 (state, transition)。"""

    rules = config.feedback_rules
    low = _num(expected.get("gap_low"))
    ratio_low = _num(expected.get("ratio_low"))
    seal_by = expected.get("seal_by")
    if low is None or ratio_low is None:
        return "unknown", None

    gap_ok = gap_pct is not None and gap_pct >= low
    ratio_ok = ratio is not None and ratio >= ratio_low
    seal_ok = True if seal_by is None else bool(
        sealed_by is not None and _time_le(sealed_by, seal_by))
    close_strong = (close_pct is not None
                    and close_pct >= float(rules.get("confirm_close_pct_min", 3.0)))

    if gap_ok and ratio_ok and (seal_ok or close_strong):
        return "confirmed", None

    invalidated = (
        (gap_pct is not None
         and gap_pct < low - float(rules.get("invalidate_gap_margin_pct", 3.0)))
        or (close_pct is not None
            and close_pct < float(rules.get("invalidate_close_pct_max", -2.0)))
        or below_prev
        or (broken_count is not None
            and broken_count >= float(rules.get("invalidate_broken_count_min", 2)))
        or bool(mainline_dropped)
    )
    if invalidated:
        return "invalidated", None

    # weakened → confirmed：回踩后重新站上前收且量能回升（§11.3，限一次）。
    recover_ratio = float(rules.get("weakened_recover_ratio_min", 1.0))
    if (not below_prev
            and close_pct is not None and close_pct >= 0
            and ratio is not None and ratio >= recover_ratio
            and not gap_ok):
        return "confirmed", "weakened->confirmed"
    return "weakened", None


def _unknown_checks(
    row: Mapping[str, Any] | None,
    expected: dict | None,
    prev_close: float | None,
    t1_close: float | None,
) -> str | None:
    if expected is None:
        return "expectations_missing"
    if row is None:
        return "auction_missing"
    if not row.get("verified", False) or _num(row.get("price")) is None:
        return str(row.get("unknown_reason") or "auction_unverified")
    if prev_close is None or prev_close <= 0:
        return "prev_close_missing"
    if t1_close is None:
        return "t1_quote_missing"
    return None


def compute_feedback(
    prev_plan: dict,
    auction: dict,
    intraday: dict | None,
    daily: dict,
    trade_date: str,
    *,
    config: ReviewConfig = CONFIG,
    current_mainlines: list | None = None,
) -> dict:
    """T+1 盘后反馈（纯函数）。

    ``prev_plan`` = ``_collect_prev_plan`` 资产（T-1 快照复制）；
    ``auction`` = ``auction_feedback`` 资产；``intraday`` = ``t1_intraday``（可空）；
    ``daily`` = ``stock_daily`` 资产。``current_mainlines`` 只用于「主线跌出前 3」
    这一破坏条件，缺失即不评估（绝不猜）。
    """

    plan = _as_dict(prev_plan)
    positions = _plan_positions(plan)
    plans = _plans_by_code(plan)
    auction_rows = _auction_rows(_as_dict(auction))
    intraday_payload = _as_dict(intraday)

    items: list[dict] = []
    degraded: set[str] = set()
    has_intraday = any(_intraday_rows(intraday_payload, pos["code"])
                       for pos in positions)

    for position in positions:
        code = position["code"]
        row = auction_rows.get(code)
        expected, source = _expected_for(code, position, plans, config)
        if source == "config_fallback":
            degraded.add("expectations_config_fallback")
        previous_row, current_row = _daily_slice(_daily_rows(daily, code), trade_date)
        bars = _intraday_rows(intraday_payload, code)

        prev_close = _num(previous_row.get("close")) if previous_row else None
        t1_close = _num(current_row.get("close")) if current_row else None
        close_source = "daily"
        if t1_close is None and bars:
            t1_close = _num(bars[-1].get("close"))
            close_source = "intraday_5min" if t1_close is not None else "missing"
        if close_source == "intraday_5min":
            degraded.add("t1_daily_missing")

        sealed_by, broken_count = _seal_metrics(code, prev_close, bars, config)
        missing_fields = (
            _missing_expectation_fields(expected) if expected is not None else [])
        reason = _unknown_checks(row, expected, prev_close, t1_close)
        if reason is None and missing_fields:
            # 冻结预期缺判定必需字段：稳定 reason_code，绝不返回空 unknown_reason。
            reason = "expectations_incomplete"

        gap_pct = None
        ratio = None
        close_pct = None
        below_prev = False
        state = "unknown"
        transition = None
        mainline_dropped = _mainline_dropped(position, current_mainlines)

        if reason is None:
            gap_pct = _pct(_num(row.get("price")), prev_close)
            ratio = _auction_ratio(row)
            close_pct = _pct(t1_close, prev_close)
            below_prev = t1_close < prev_close
            state, transition = _classify(
                gap_pct=gap_pct, ratio=ratio, sealed_by=sealed_by,
                close_pct=close_pct, below_prev=below_prev,
                broken_count=broken_count, mainline_dropped=mainline_dropped,
                expected=expected, config=config)

        # 任何 unknown 都必须带稳定 reason_code（不允许空 unknown_reason）。
        if state == "unknown" and reason is None:
            reason = "expectations_incomplete"
        if reason is not None:
            degraded.add(reason)

        items.append({
            "code": code,
            "state": state,
            "gap_pct": gap_pct,
            "auction_ratio": ratio,
            "sealed_by": sealed_by,
            "close_pct": _round(close_pct),
            "allowed_actions": list(STATE_ACTIONS.get(state, ("observe_only",))),
            "unknown_reason": reason,
            "transition": transition,
            "expectations_source": source,
            "close_source": close_source,
            "evidence": {
                "below_prev": below_prev,
                "broken_count": broken_count,
                "mainline_dropped": mainline_dropped,
                "missing_expected_fields": missing_fields,
            },
        })

    if has_intraday is False and positions:
        degraded.add("t1_intraday_missing")

    if not positions:
        checkpoint = "none"
    elif has_intraday and auction_rows:
        # 09:25 竞价 + 盘中 10:00 刻度 + 收盘；缺失 5 分钟即降级为 close_only。
        checkpoint = "auction+close"
    else:
        checkpoint = "close_only"

    return {
        "source_plan_date": plan.get("source_trade_date"),
        "eval_date": trade_date,
        "checkpoint": checkpoint,
        "items": items,
        "degraded": sorted(degraded) if items else [],
    }


# ---------------------------------------------------------------------------
# 计划执行复盘
# ---------------------------------------------------------------------------


def _planned_action(position: Mapping[str, Any]) -> str | None:
    planned = _num(position.get("position_pct"))
    if planned is None:
        return "observe_only"
    return "execute_plan" if planned > 0 else "cancel_entry"


def _actual_position(code: str, holdings: Any) -> tuple[float | None, bool]:
    """返回 (actual_position, execution_known)。

    真实成交价/持仓规模不落库（§10.4）：只把「不在未平仓信号里」映射为
    收盘实际持仓 0；持仓中的规模未知 → ``None``，绝不填 0 或编造。
    """

    payload = _as_dict(holdings)
    codes = payload.get("codes")
    if not isinstance(codes, list) or payload.get("degraded"):
        return None, False
    return (None, False) if code in codes else (0.0, True)


def _outcome_pct(daily: Mapping[str, Any], code: str, trade_date: str) -> float | None:
    _, current = _daily_slice(_daily_rows(daily, code), trade_date)
    if not current:
        return None
    return _pct(_num(current.get("close")), _num(current.get("open")))


def compute_plan_review(
    prev_plan: dict,
    feedback: dict,
    intraday: dict | None,
    daily: dict,
    trade_date: str,
    *,
    config: ReviewConfig = CONFIG,
    holdings: dict | None = None,
    current_market: dict | None = None,
) -> dict:
    """计划执行复盘 + 四类归因（纯函数）。

    只写反馈自己的键：``actual_entry_price`` / ``actual_position`` 在缺少真实
    执行数据时保持 ``None``（``execution_known=false``），**不编造成交价/仓位**。
    """

    plan = _as_dict(prev_plan)
    rules = config.feedback_rules
    feedback_items = {
        item.get("code"): item
        for item in _list_of_dicts(_as_dict(feedback).get("items"))
        if isinstance(item.get("code"), str)
    }
    positions = _plan_positions(plan)

    items: list[dict] = []
    no_trade: list[dict] = []
    pattern: list[str] = []
    execution: list[str] = []
    data: list[str] = []
    degraded: set[str] = set()

    for position in positions:
        code = position["code"]
        entry = feedback_items.get(code) or {}
        state = entry.get("state") or "unknown"
        outcome = _outcome_pct(daily, code, trade_date)
        actual_position, execution_known = _actual_position(code, holdings)
        planned_position = _num(position.get("position_pct"))
        item = {
            "code": code,
            "planned_action": _planned_action(position),
            "planned_position": planned_position,
            "feedback_state": state,
            "actual_entry_price": None,
            "actual_position": actual_position,
            "execution_known": execution_known,
            "outcome_pct": _round(outcome),
        }
        items.append(item)

        if state == "confirmed" and outcome is not None and \
                outcome <= float(rules.get("pattern_outcome_max_pct", -3.0)):
            pattern.append(code)
        if state == "invalidated" and entry.get("close_pct") is not None and \
                entry["close_pct"] >= float(rules.get("pattern_limit_up_pct", 9.5)):
            pattern.append(code)
        if (planned_position is not None and planned_position > 0
                and state == "confirmed" and execution_known
                and actual_position == 0):
            execution.append(code)
        if state == "unknown" and entry.get("unknown_reason"):
            data.append(code)

        if planned_position == 0:
            if state == "invalidated":
                no_trade.append({"code": code, "judgement": "correct_avoidance"})
            elif state == "confirmed":
                no_trade.append({"code": code, "judgement": "missed_opportunity"})
            elif state == "unknown":
                no_trade.append({"code": code, "judgement": "data_gap"})

    if not isinstance(holdings, dict) or _as_dict(holdings).get("degraded"):
        degraded.add("execution_data_unavailable")

    market_evidence: list[str] = []
    prev_state = plan.get("market_state")
    if prev_state is None:
        prev_state = _as_dict(plan.get("market")).get("state")
    current_state = _as_dict(current_market).get("state")
    market_count = 0
    if prev_state not in _MARKET_RANK or current_state not in _MARKET_RANK:
        degraded.add("market_state_unavailable")
    else:
        shift = _MARKET_RANK[current_state] - _MARKET_RANK[prev_state]
        direction = 1 if shift > 0 else -1
        outcomes = [item["outcome_pct"] for item in items
                    if item["outcome_pct"] is not None]
        aligned = sum(1 for value in outcomes
                      if value != 0 and (value > 0) == (direction > 0))
        market_evidence.append(f"market_state:{prev_state}->{current_state}")
        if abs(shift) >= 1 and outcomes and aligned * 2 > len(outcomes):
            market_count = 1
            market_evidence.append(f"outcome_alignment:{aligned}/{len(outcomes)}")

    return {
        "plan_date": plan.get("source_trade_date"),
        "eval_date": trade_date,
        "items": items,
        "attribution": {
            "market": market_count,
            "pattern": len(pattern),
            "execution": len(execution),
            "data": len(data),
        },
        "attribution_evidence": {
            "market": market_evidence,
            "pattern": pattern,
            "execution": execution,
            "data": data,
        },
        "no_trade": no_trade,
        "degraded": sorted(degraded),
    }


__all__ = [
    "STATE_ACTIONS",
    "compute_feedback",
    "compute_plan_review",
]
