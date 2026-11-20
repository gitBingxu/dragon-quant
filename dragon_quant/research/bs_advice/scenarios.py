"""从本地已成熟日 K 构造共同历史场景（A1）。

只用**决策前已成熟**的窗口（窗口末日 < 决策日），联合推进候选价格；容量按
参与率从日 K 成交量派生。日 K 在这里只作**反事实情景**（研究/压力），不作为
盘中执行成交证据；执行仍由 ``events`` 的分钟观测承担。
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from dragon_quant.review_account.strategy.bs_policy import (
    SCENARIO_HISTORICAL, ScenarioSpec, historical_joint_paths,
)

from .universe import ExAnteUniverse

DEFAULT_PARTICIPATION = 0.10


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _moment(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _daily_by_code(snapshot, codes: Iterable[str]) -> dict[str, dict[str, dict]]:
    result: dict[str, dict[str, dict]] = {}
    for fact in snapshot.facts:
        if fact.capability != "daily_bars" or not fact.usable:
            continue
        if fact.code not in codes:
            continue
        payload = dict(fact.payload or {})
        day = str(fact.trade_date or "")[:10]
        close = _finite(payload.get("close"))
        if not day or close is None or close <= 0:
            continue
        volume = _finite(payload.get("volume"))
        result.setdefault(fact.code, {})[day] = {
            "close": close, "volume": volume,
            "at": f"{day}T15:00:00+08:00"}
    return result


def build_historical_scenarios(snapshot, universe: ExAnteUniverse, *,
                               decision_time: str, spec: ScenarioSpec,
                               participation: float = DEFAULT_PARTICIPATION,
                               max_windows: int = 8):
    """构造共同历史窗口；不足/不成熟返回 ``None``（纯核按缺场景受限）。"""
    decision = _moment(decision_time)
    if decision is None or spec.horizon_days < 1:
        return None
    data = _daily_by_code(snapshot, set(universe.codes))
    if not data:
        return None
    common = set.intersection(*(set(per_code) for per_code in data.values()))
    dates = sorted(common)
    if not dates:
        return None
    horizon = int(spec.horizon_days)
    # 共同相对时点：决策前最近 horizon 个已成熟交易日；价格来自各历史窗口，
    # 时点只作为**相对模拟步**（provenance 记录真实窗口），保证共同路径同形。
    common_days = [day for day in dates
                   if (_moment(f"{day}T15:00:00+08:00") or decision) < decision]
    common_days = common_days[-horizon:]
    if len(common_days) != horizon:
        return None
    windows: list[dict] = []
    for start in range(0, max(0, len(dates) - horizon + 1)):
        step_days = dates[start:start + horizon]
        if len(step_days) != horizon:
            break
        last = _moment(f"{step_days[-1]}T15:00:00+08:00")
        if last is None or last >= decision:
            continue
        steps = []
        for index, day in enumerate(step_days):
            prices, tradable, capacity = {}, {}, {}
            for code, per_code in data.items():
                row = per_code.get(day)
                if row is None:
                    continue
                prices[code] = row["close"]
                tradable[code] = True
                qty = int((row["volume"] or 0.0) * participation)
                if qty > 0:
                    capacity[code] = qty
            if not prices:
                continue
            at = f"{common_days[index]}T15:00:00+08:00"
            steps.append({"at": at, "prices": prices, "tradable": tradable,
                          "capacity": capacity, "trade_date": common_days[index]})
        if len(steps) == horizon:
            windows.append({"window_id": f"{step_days[0]}..{step_days[-1]}",
                            "start": steps[0]["at"], "steps": steps})
        if len(windows) >= max_windows:
            break
    if not windows:
        return None
    return historical_joint_paths(
        windows, decision_at=decision_time, spec=spec,
        kind=SCENARIO_HISTORICAL)


__all__ = ["build_historical_scenarios", "DEFAULT_PARTICIPATION"]
