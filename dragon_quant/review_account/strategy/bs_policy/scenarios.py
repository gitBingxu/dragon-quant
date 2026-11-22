"""共同场景路径：验证、压力路径与历史联合路径适配（纯计算）。

纪律（方案 §7 / G2–G7）：

- 所有候选政策必须使用**同一** step 时点、同一代码价格/可成交状态的外部路径；
  禁止每个方案挑选自己的有利路径，也禁止引用未来高低点反推可成交价。
- 历史类比只接受**决策前已成熟且冻结**的联合片段；每个窗口内各代码共同推进
  （保持相关性），不按单股最优独立拼接。
- 压力路径显式标识为 ``pressure``，只作门禁，不产生概率或 EV。
- 历史样本不足返回 ``insufficiency=('insufficient_data',)``，不随机生成概率。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from .config import ScenarioSpec
from dragon_quant.review_account.evidence import thaw
from .base import (
    SCENARIO_HISTORICAL,
    SCENARIO_PRESSURE,
    SCENARIO_TEACHING,
)
from .scenario_contracts import ScenarioPath, ScenarioSet, ScenarioStep

CST = timezone(timedelta(hours=8))
SCENARIO_KINDS = (SCENARIO_HISTORICAL, SCENARIO_PRESSURE, SCENARIO_TEACHING)


class ScenarioError(ValueError):
    """场景集合不满足共同口径要求。"""

    def __init__(self, message: str, *, reason: str = "scenario_invalid") -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


def _moment(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=CST)


def _price(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def validate_scenarios(scenarios: ScenarioSet, spec: ScenarioSpec, *,
                       decision_time: Optional[str] = None) -> None:
    """所有路径共享时点集合/期限；交易日、来源成熟度与 fixture 标记显式。"""
    if not scenarios.paths:
        return
    if scenarios.insufficiency:
        return
    horizon = spec.horizon_days
    reference: Optional[tuple[tuple[int, str], ...]] = None
    seen: set[str] = set()
    for path in scenarios.paths:
        if not path.scenario_id or path.scenario_id in seen:
            raise ScenarioError(f"场景 ID 重复或为空: {path.scenario_id!r}")
        seen.add(path.scenario_id)
        if path.kind not in SCENARIO_KINDS:
            raise ScenarioError(f"未知场景类别: {path.kind!r}")
        if not path.steps:
            raise ScenarioError(f"场景 {path.scenario_id} 没有 step")
        shape = tuple((step.index, step.at) for step in path.steps)
        indices = [step.index for step in path.steps]
        if indices != sorted(indices) or indices != list(range(1, horizon + 1)):
            raise ScenarioError(
                f"场景 {path.scenario_id} 的 step 必须是从 1 到 horizon={horizon} 的连续时点")
        if not all(str(step.at).strip() for step in path.steps):
            raise ScenarioError(f"场景 {path.scenario_id} 的时点不能为空")
        if reference is None:
            reference = shape
        elif shape != reference:
            raise ScenarioError(
                f"场景 {path.scenario_id} 与共同路径的时点/期限不一致")
        for step in path.steps:
            if not isinstance(step.prices, Mapping) or not isinstance(
                    step.tradable, Mapping):
                raise ScenarioError(f"场景 {path.scenario_id} step {step.index} 结构非法")
            if step.trade_date and step.at:
                try:
                    step_day = datetime.fromisoformat(step.at).date().isoformat()
                except ValueError:
                    step_day = ""
                if step_day and step_day != step.trade_date:
                    raise ScenarioError(
                        f"场景 {path.scenario_id} step {step.index} 的 at 与 "
                        f"trade_date 不一致")
            for code in step.prices:
                if _price(step.prices[code]) is None:
                    raise ScenarioError(
                        f"场景 {path.scenario_id} step {step.index} 的非正价格: {code}")
            for code, flag in step.tradable.items():
                if not isinstance(flag, bool):
                    raise ScenarioError(
                        f"场景 {path.scenario_id} 的 tradable 必须是布尔值: {code}")
        previous_date: Optional[str] = None
        for step in path.steps:
            if step.trade_date:
                if previous_date and step.trade_date < previous_date:
                    raise ScenarioError(
                        f"场景 {path.scenario_id} 的 trade_date 必须非递减")
                previous_date = step.trade_date
        if path.kind == SCENARIO_HISTORICAL:
            if not path.mature:
                raise ScenarioError(
                    f"历史联合场景 {path.scenario_id} 未成熟，不能作为决策前证据")
            provenance = dict(path.provenance or {})
            if provenance.get("simulated_relative_steps") is not True:
                raise ScenarioError(
                    f"历史联合场景 {path.scenario_id} 必须标记为相对模拟路径")
            matured_at = provenance.get("matured_at") or provenance.get("window_end")
            if not matured_at:
                raise ScenarioError(
                    f"历史联合场景 {path.scenario_id} 必须记录来源窗成熟时间")
            if decision_time is not None:
                matured_moment = _moment(matured_at)
                decision = _moment(decision_time)
                if matured_moment is None or decision is None:
                    raise ScenarioError("历史成熟时间/决策时间非法")
                if matured_moment > decision:
                    raise ScenarioError(
                        f"历史联合场景 {path.scenario_id} 在决策时尚未成熟")
                window_end = _moment(provenance.get("window_end") or matured_at)
                if window_end is None or window_end > decision:
                    raise ScenarioError(
                        f"历史联合场景 {path.scenario_id} 的来源窗晚于决策")
        if path.kind == SCENARIO_TEACHING and not path.fixture:
            raise ScenarioError(
                f"教学场景 {path.scenario_id} 必须显式 fixture=True")
        if path.kind == SCENARIO_PRESSURE and path.mature and not path.basis:
            raise ScenarioError(
                f"压力场景 {path.scenario_id} 必须声明假设依据 basis")


def _record_steps(raw_steps: Any, *, scenario_id: str) -> tuple[ScenarioStep, ...]:
    if not isinstance(raw_steps, (list, tuple)) or not raw_steps:
        raise ScenarioError(f"场景 {scenario_id} 缺少 steps 数组")
    steps: list[ScenarioStep] = []
    for index, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, Mapping):
            raise ScenarioError(f"场景 {scenario_id} step 必须是对象")
        prices = raw.get("prices") or {}
        tradable = raw.get("tradable") or {}
        if not isinstance(prices, Mapping) or not isinstance(tradable, Mapping):
            raise ScenarioError(f"场景 {scenario_id} step 的 prices/tradable 必须是对象")
        evidence = raw.get("evidence_available") or ()
        if not isinstance(evidence, (list, tuple)):
            raise ScenarioError(
                f"场景 {scenario_id} step 的 evidence_available 必须是数组")
        capacity = raw.get("capacity") or {}
        if not isinstance(capacity, Mapping):
            raise ScenarioError(
                f"场景 {scenario_id} step 的 capacity 必须是对象")
        parsed_capacity = {}
        for code, value in capacity.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ScenarioError(
                    f"场景 {scenario_id} step 的 capacity 必须是非负整数: {code}")
            parsed_capacity[str(code)] = int(value)
        available_at = str(raw.get("available_at") or "")
        at = str(raw.get("at") or "")
        if available_at and at and available_at > at:
            raise ScenarioError(
                f"场景 {scenario_id} step 的 available_at 晚于事件时点")
        bar_highs = raw.get("bar_highs") or {}
        if not isinstance(bar_highs, Mapping):
            raise ScenarioError(
                f"场景 {scenario_id} step 的 bar_highs 必须是对象")
        parsed_highs = {}
        for code, price in bar_highs.items():
            value = _price(price)
            if value is None or value <= 0:
                continue          # 非正/非法 high 不作为买后峰值证据
            parsed_highs[str(code)] = value
        interval_start = raw.get("interval_start")
        interval_end = raw.get("interval_end")
        steps.append(ScenarioStep(
            index=int(raw.get("index", index)),
            at=at,
            prices={str(code): _price(price) or 0.0
                    for code, price in prices.items()},
            tradable={str(code): bool(flag) for code, flag in tradable.items()},
            trade_date=str(raw.get("trade_date") or ""),
            evidence_available=tuple(str(item) for item in evidence),
            capacity=parsed_capacity,
            available_at=available_at,
            bar_highs=parsed_highs,
            bar_complete=bool(raw.get("bar_complete", False)),
            interval_start=(None if interval_start is None
                            else int(interval_start)),
            interval_end=(None if interval_end is None else int(interval_end)),
        ))
    return tuple(steps)


def pressure_path(*, scenario_id: str = "pressure_common_drawdown",
                  base_prices: Mapping[str, float],
                  pressure_pct: Mapping[str, float],
                  spec: ScenarioSpec,
                  basis: str = "explicit stress assumption; not a probability",
                  tradable: Optional[Mapping[str, bool]] = None,
                  final_tradable: bool = True,
                  step_ats: Optional[Iterable[str]] = None,
                  trade_dates: Optional[Iterable[str]] = None,
                  tradable_by_step: Optional[Mapping[int, Mapping[str, bool]]] = None,
                  capacity_qty: Optional[int] = None,
                  fixture: bool = False) -> ScenarioPath:
    """按显式压力假设构造共同下跌路径（线性推进到期末压力价）。

    ``tradable`` 允许表达“期末跌停/停牌导致无法卖出”的路径；不会把触价当成成交。
    """
    if not base_prices:
        raise ScenarioError("压力路径需要 base_prices")
    tradable = dict(tradable or {})
    ats = list(step_ats) if step_ats is not None else [
        f"step+{index}" for index in range(1, spec.horizon_days + 1)]
    if len(ats) != spec.horizon_days:
        raise ScenarioError("pressure step_ats 必须与 horizon 一致")
    dates = list(trade_dates) if trade_dates is not None else [
        "" for _ in range(spec.horizon_days)]
    if len(dates) != spec.horizon_days:
        raise ScenarioError("pressure trade_dates 必须与 horizon 一致")
    steps: list[ScenarioStep] = []
    horizon = spec.horizon_days
    for index in range(1, horizon + 1):
        fraction = index / horizon
        prices = {}
        flags = {}
        for code, base in base_prices.items():
            start = _price(base)
            if start is None:
                raise ScenarioError(f"压力路径基准价非法: {code}")
            pct = pressure_pct.get(code)
            if pct is None:
                # 压力无法合理估计的代码保持未知：调用方必须禁止新增风险。
                raise ScenarioError(f"缺少 {code} 的压力估计")
            if not math.isfinite(float(pct)) or float(pct) < 0:
                raise ScenarioError(f"压力比例非法: {code}")
            prices[code] = round(start * (1 - float(pct) * fraction), 6)
            step_flags = (tradable_by_step or {}).get(index) or {}
            flags[code] = (bool(tradable.get(code, True))
                           and bool(step_flags.get(code, True))
                           and (final_tradable or index < horizon))
        caps = {code: int(capacity_qty if capacity_qty is not None
                          else 1_000_000) for code in prices}
        steps.append(ScenarioStep(index=index, at=ats[index - 1],
                                  prices=prices, tradable=flags,
                                  trade_date=dates[index - 1],
                                  capacity=caps))
    path = ScenarioPath(scenario_id=scenario_id, kind=SCENARIO_PRESSURE,
                        steps=tuple(steps), basis=basis, fixture=fixture,
                        calendar=tuple(day for day in dates if day),
                        provenance={"kind": "pressure", "basis": basis,
                                    "capacity_assumption":
                                        "explicit_stress_fixed_capacity"})
    validate_scenarios(ScenarioSet(paths=(path,)), spec)
    return path


def historical_joint_paths(windows: Iterable[Mapping], *,
                           decision_at: str,
                           spec: ScenarioSpec,
                           kind: str = SCENARIO_HISTORICAL,
                           fixture: bool = False
                           ) -> ScenarioSet:
    """从**决策前已成熟**的联合窗口构造共同路径。

    每个 window 形如::

        {"window_id": "2026-06-01..2026-06-05",
         "start": "2026-06-01T15:00:00+08:00",
         "steps": [{"at": "...", "prices": {"600001": 10.0}, "tradable": {...}}, ...]}

    要求：

    - step 数 == ``spec.horizon_days``；时点严格递增且全部不晚于决策时点；
    - 窗口起点 + horizon 天必须在 ``decision_at`` 之前（结果已成熟）；
    - 每个窗口内所有代码共同推进，保持相关性；不补零、不猜价。
    """
    decision = _moment(decision_at)
    if decision is None:
        raise ScenarioError(f"非法 decision_at: {decision_at!r}")
    paths: list[ScenarioPath] = []
    skipped: list[str] = []
    matured = 0
    for raw in windows:
        if not isinstance(raw, Mapping):
            raise ScenarioError("历史窗口必须是对象")
        window_id = str(raw.get("window_id") or raw.get("start") or "window")
        start = _moment(raw.get("start"))
        if start is None:
            skipped.append(f"{window_id}:invalid_start")
            continue
        steps = _record_steps(raw.get("steps"), scenario_id=window_id)
        if len(steps) != spec.horizon_days:
            skipped.append(f"{window_id}:horizon_mismatch")
            continue
        previous: Optional[datetime] = None
        invalid = False
        for step in steps:
            moment = _moment(step.at)
            if moment is None or moment > decision:
                invalid = True
                break
            if previous is not None and moment <= previous:
                invalid = True
                break
            previous = moment
        if invalid:
            skipped.append(f"{window_id}:invalid_time")
            continue
        matured += 1
        paths.append(ScenarioPath(
            scenario_id=f"hist:{window_id}",
            kind=kind,
            steps=steps,
            basis=f"frozen mature window starting {raw.get('start')}",
            mature=True,
            fixture=fixture,
            provenance={
                "window_id": window_id,
                "window_start": str(raw.get("start")),
                "window_end": steps[-1].at,
                "matured_at": steps[-1].at,
                "frozen_at": str(raw.get("frozen_at") or decision_at),
                "source": "historical_joint_window",
                "simulated_relative_steps": True,
            },
        ))
    if not paths:
        return ScenarioSet(
            paths=(),
            insufficiency=(f"insufficient_data:{','.join(skipped) or 'no_windows'}",
                           ))
    scenarios = ScenarioSet(paths=tuple(paths))
    validate_scenarios(scenarios, spec, decision_time=decision_at)
    return scenarios


def scenario_set_from_records(records: Iterable[Mapping], *, spec: ScenarioSpec,
                              decision_time: Optional[str] = None
                              ) -> ScenarioSet:
    """从已构造的路径字典还原并统一验证（研究/W4 冻结输入的重放入口）。"""
    paths: list[ScenarioPath] = []
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ScenarioError("路径必须是对象")
        scenario_id = str(raw.get("scenario_id") or "")
        if not scenario_id:
            raise ScenarioError("路径缺少 scenario_id")
        steps = _record_steps(raw.get("steps"), scenario_id=scenario_id)
        provenance = raw.get("provenance") or {}
        if not isinstance(provenance, Mapping):
            raise ScenarioError("路径 provenance 必须是对象")
        kind = str(raw.get("kind") or SCENARIO_HISTORICAL)
        if kind == SCENARIO_HISTORICAL:
            # 冻结记录重放：来源窗与“相对模拟路径”必须分开记录。
            provenance = dict(provenance)
            provenance.setdefault("window_id", scenario_id)
            provenance.setdefault("window_end", steps[-1].at)
            provenance.setdefault("matured_at", steps[-1].at)
            provenance.setdefault("source", "provided_frozen_records")
            provenance.setdefault("simulated_relative_steps", True)
        calendar = raw.get("calendar") or ()
        if not isinstance(calendar, (list, tuple)):
            raise ScenarioError("路径 calendar 必须是数组")
        fixture = bool(raw.get("fixture", False))
        paths.append(ScenarioPath(
            scenario_id=scenario_id,
            kind=kind,
            steps=steps,
            basis=str(raw.get("basis") or ""),
            mature=bool(raw.get("mature", True)),
            provenance=provenance,
            fixture=fixture,
            calendar=tuple(str(day) for day in calendar),
        ))
    scenarios = ScenarioSet(paths=tuple(paths))
    validate_scenarios(scenarios, spec, decision_time=decision_time)
    return scenarios


def common_scenario_basis(scenarios: ScenarioSet) -> dict:
    """审计用：共同口径的 ID/类别/期限，供报告与 W4 冻结引用。"""
    return {
        "scenario_ids": [path.scenario_id for path in scenarios.paths],
        "kinds": sorted({path.kind for path in scenarios.paths}),
        "comparable": [path.scenario_id for path in scenarios.comparable()],
        "pressure": [path.scenario_id for path in scenarios.pressure()],
        "insufficiency": list(scenarios.insufficiency),
        "provenance": {path.scenario_id: thaw(dict(path.provenance))
                       for path in scenarios.paths},
        "fixture": any(path.fixture for path in scenarios.paths),
    }


def require_comparable_scenarios(scenarios: Optional[ScenarioSet],
                                 spec: ScenarioSpec) -> None:
    """有路径但不可比较/口径不一致时明确报错（不静默按空结果处理）。"""
    if scenarios is None:
        return
    validate_scenarios(scenarios, spec)
