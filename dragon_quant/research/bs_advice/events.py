"""严格 PIT 的盘中执行事件（A2）：只用已校准、会话内可得的分钟观测。

日 K 收盘价**不能**充当成交事件：盘后发布的日 K 不是可交易报价。本模块只从
本地 ``market_bar_versions`` 取 **1min** 观测，要求：

- 行内 ``completeness=final`` / ``adjustment=none`` / ``derived=false``；
- 时间与单位语义 verified（来自版本化校准，未知即不执行）；
- ``available_at`` 在交易时段内、且不晚于 ``interval_end + max_lag``；
- 每个身份在整体 cutoff 下取最后可用 revision（``select_bars_asof``）。

容量用显式参与率对分钟成交量打折（保守上界，不代表本单队列）；未知容量不成交。
事后重建（retrospective）不在本模块伪装成严格 PIT。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Iterable, Mapping, Optional

from dragon_quant.infra.assets import observations
from dragon_quant.review_account.evidence import slice_snapshot
from dragon_quant.review_account.strategy.policy_replay import ReplayEvent

from .candidates import build_candidates
from .market import market_from_universe
from .scenarios import build_historical_scenarios
from .universe import ExAnteUniverse

_CST = timezone(timedelta(hours=8))
_SESSIONS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))
DEFAULT_PARTICIPATION = 0.10
DEFAULT_MAX_LAG_MS = 120_000
DEFAULT_MAX_EVENTS = 5_000


class ExecutionEvidenceError(ValueError):
    def __init__(self, message: str, *, code: str = "execution_evidence_missing"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class IntradayEvents:
    events: tuple
    trade_dates: tuple[str, ...]
    coverage: Mapping
    gaps: tuple[Mapping, ...]
    universe_snapshots: tuple[Mapping, ...] = ()


def _ms(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return int(moment.timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=_CST).isoformat()


def _in_session(moment: datetime) -> bool:
    clock = moment.timetz().replace(tzinfo=None)
    return any(start <= clock <= end for start, end in _SESSIONS)


def _executable_row(row: Mapping, *, max_lag_ms: int,
                    cutoff_ms: int) -> tuple[bool, str, Optional[int]]:
    """严格行门禁：时间/接收/校准/内容/单位全部通过才可执行。"""
    from dragon_quant.infra.calibration import verify_binding
    from dragon_quant.review_account.evidence import bar_payload_valid

    if row.get("completeness") != "final":
        return False, "not_final", None
    if row.get("adjustment") != "none":
        return False, "adjusted", None
    if row.get("derived"):
        return False, "derived", None
    if row.get("time_semantics") != "verified":
        return False, "time_unverified", None
    if row.get("unit_semantics") != "verified":
        return False, "unit_unverified", None
    binding = row.get("calibration") or {}
    spec = verify_binding(binding, source=str(row.get("source") or ""),
                          period=str(row.get("period") or ""))
    if spec is None:
        return False, "calibration_unverified", None
    end = _ms(row.get("interval_end"))
    available = _ms(row.get("available_at"))
    received = _ms(row.get("observed_at"))
    calibrated = _ms(binding.get("calibrated_at"))
    if None in (end, available, received, calibrated):
        return False, "time_missing", None
    if available < end or received < end:
        return False, "available_before_source", None
    known = max(int(end), int(available), int(received), int(calibrated))
    if known > int(cutoff_ms):
        return False, "received_after_cutoff", None
    if known - int(end) > max_lag_ms:
        return False, "available_too_late", None
    if not _in_session(datetime.fromtimestamp(known / 1000, tz=_CST)):
        return False, "after_hours", None
    if datetime.fromtimestamp(end / 1000, tz=_CST).date().isoformat() \
            != str(row.get("trade_date")):
        return False, "trade_date_mismatch", None
    payload = {"open": row.get("open"), "high": row.get("high"),
               "low": row.get("low"), "close": row.get("close"),
               "volume": row.get("volume"), "amount": row.get("amount")}
    if not bar_payload_valid(payload):
        return False, "invalid_bar_content", None
    return True, "", known


def _capacity(volume, participation: float) -> Optional[int]:
    try:
        qty = int(float(volume) * participation)
    except (TypeError, ValueError):
        return None
    return qty if qty > 0 else None


def _entry_version(account, universe) -> str:
    config = getattr(getattr(account, "policy", None), "config", None)
    if config is not None:
        return str(config.content_hash)
    policy_hash = getattr(account, "policy_hash", None)
    if policy_hash:
        return str(policy_hash)
    meta = dict(getattr(universe, "market_meta", {}) or {})
    return str(meta.get("policy_hash") or "local_evidence_v1")


def build_intraday_events(*, codes: Iterable[str], universe: ExAnteUniverse,
                          base_snapshot, date_from: str, date_to: str,
                          cutoff: str, db_path=None,
                          participation: float = DEFAULT_PARTICIPATION,
                          max_lag_ms: int = DEFAULT_MAX_LAG_MS,
                          max_events: int = DEFAULT_MAX_EVENTS,
                          scenario_spec=None,
                          universe_factory: Optional[Callable] = None,
                          account=None, calendar: Iterable[str] = (),
                          entry_version: str = "local_evidence_v1",
                          execution_facts: Optional[Mapping] = None,
                          ) -> IntradayEvents:
    rows = observations.query_bars(
        None, None, codes=sorted(set(codes)), periods=["1min"],
        db_path=db_path, strict=True)
    cutoff_ms = _ms(cutoff) or 0
    by_time: dict[int, dict[str, dict]] = {}
    accepted = 0
    rejected = 0
    gaps: list[dict] = []
    for row in rows:
        day = str(row.get("trade_date") or "")
        if not (date_from <= day <= date_to):
            continue
        ok, reason, known = _executable_row(
            row, max_lag_ms=max_lag_ms, cutoff_ms=cutoff_ms)
        if not ok or known is None:
            rejected += 1
            if len(gaps) < 50:
                gaps.append({"code": row.get("code"), "trade_date": day,
                             "reason": reason})
            continue
        accepted += 1
        bucket = by_time.setdefault(int(known), {})
        code = str(row["code"])
        current = bucket.get(code)
        if current is None or str(row["interval_end"]) >= str(
                current["interval_end"]):
            bucket[code] = row
    events: list[ReplayEvent] = []
    universe_snapshots: list[dict] = []
    cursor = -1
    grouped_days: set[str] = set()
    for timestamp in sorted(by_time):
        bucket = by_time[timestamp]
        quotes: dict[str, dict] = {}
        day = ""
        for code, row in bucket.items():
            capacity = _capacity(row.get("volume"), participation)
            if capacity is None:
                continue
            available = _ms(row.get("available_at"))
            day = str(row.get("trade_date") or day)
            quotes[code] = {
                "execution_price": row.get("close"),
                "execution_timestamp": timestamp,
                "observed_at": _ms(row.get("observed_at")),
                "available_at": available,
                "open": row.get("open"), "high": row.get("high"),
                "low": row.get("low"), "close": row.get("close"),
                "volume": row.get("volume"), "amount": row.get("amount"),
                "available_qty": capacity, "tradable": True, "stale": False,
            }
        if not quotes:
            continue
        if timestamp <= cursor:
            continue
        grouped_days.add(day)
        snapshot = slice_snapshot(base_snapshot, _iso(timestamp))
        event_universe = (universe_factory(day) if universe_factory is not None
                          else universe)
        universe_snapshots.append({
            "trade_date": day, "timestamp": timestamp,
            "coverage": dict(event_universe.coverage),
            "gaps": [dict(item) for item in event_universe.gaps],
            "items": [item.to_dict() for item in event_universe.items],
            "market_context": dict(event_universe.market_context),
            "market_meta": dict(event_universe.market_meta),
        })
        candidates = build_candidates(
            snapshot, event_universe, rows=quotes,
            entry_version=entry_version)
        market_view = market_from_universe(
            event_universe, cutoff=_iso(timestamp), snapshot=snapshot)
        from .facts import (
            checkpoints_from_account, market_facts, recovery_confirmations,
        )
        account_view = getattr(account, "account", account)
        facts = market_facts(
            snapshot, account_view, trade_date=day, calendar=calendar,
            prices={code: row.get("execution_price")
                    for code, row in quotes.items()},
            codes=[candidate.code for candidate in candidates],
            checkpoints=checkpoints_from_account(account) if account else {})
        recovery = recovery_confirmations(
            snapshot, market=market_view, rows=quotes,
            execution_facts=execution_facts,
            market_meta=event_universe.market_meta,
            market_context=event_universe.market_context)
        scenarios = (build_historical_scenarios(
            snapshot, event_universe, decision_time=_iso(timestamp),
            spec=scenario_spec, participation=participation)
            if scenario_spec is not None else None)
        events.append(ReplayEvent(
            timestamp=timestamp, trade_date=day, rows=quotes,
            candidates=candidates,
            decision_kwargs={
                "snapshot": snapshot, "market": market_view,
                "scenarios": scenarios,
                "trends": facts["trends"],
                "pressures": facts["pressures"],
                "themes": facts["themes"],
                "held_days_by_batch": facts["held_days_by_batch"],
                "recovery_confirmations": recovery,
                "trend_audit": facts["trend_audit"]},
            decide=True, phase="bar"))
        cursor = timestamp
        if len(events) >= max_events:
            gaps.append({"reason": "events_truncated", "detail": str(max_events)})
            break
    coverage = {
        "bars_read": len(rows), "executable_bars": accepted,
        "rejected_bars": rejected, "events": len(events),
        "trade_dates": sorted(grouped_days),
        "participation": participation, "max_lag_ms": max_lag_ms,
    }
    return IntradayEvents(events=tuple(events),
                          trade_dates=tuple(coverage["trade_dates"]),
                          coverage=coverage, gaps=tuple(gaps),
                          universe_snapshots=tuple(universe_snapshots))


__all__ = [
    "DEFAULT_PARTICIPATION", "DEFAULT_MAX_LAG_MS", "ExecutionEvidenceError",
    "IntradayEvents", "build_intraday_events",
]
