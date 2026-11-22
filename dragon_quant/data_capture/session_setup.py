"""采集会话的准备辅助：计划解析、范围/预算与观测意图。"""

from __future__ import annotations

import os
import socket
import threading
import uuid
from typing import Iterable, Optional

from dragon_quant.infra.assets import observations as store

from . import plans
from .collectors import collector_for
from .types import (
    CaptureError,
    CaptureRequest,
    CaptureResult,
)

DEFAULT_BUDGET = 200
DEFAULT_LEASE_TTL = 120
_STATUS_RANK = {"complete": 0, "skipped": 1, "partial": 2, "failed": 3,
                "missed": 4}

def resolve_plan(*, plan_id: Optional[str] = None,
                 trade_date: Optional[str] = None, db_path=None) -> dict:
    """按 ``plan_id`` 或交易日解析计划；缺失/未初始化/损坏分别显式失败。"""
    try:
        if plan_id:
            plan = store.get_plan(plan_id, db_path=db_path)
            if plan is None:
                raise CaptureError("plan_not_found", f"计划不存在: {plan_id}")
            return plan
        if not trade_date:
            raise CaptureError("plan_not_found", "需要 --plan-id 或 --date")
        plan = store.latest_plan(plans.normalize_trade_date(trade_date),
                                 db_path=db_path)
        if plan is None:
            raise CaptureError(
                "plan_not_found",
                f"该交易日没有已冻结计划: {trade_date}；先执行 assets capture plan")
        return plan
    except store.ObservationStoreError as exc:
        raise CaptureError(exc.reason, str(exc)) from exc


def new_worker_id() -> str:
    """唯一 worker 身份：同进程同线程的两次调用也不相同。"""
    return (f"{socket.gethostname()}:{os.getpid()}:"
            f"{threading.get_ident()}:{uuid.uuid4().hex[:12]}")


def _hhmm(moment) -> str:
    return moment.strftime("%H%M")


def _topic_defaults(plan: dict, topics: Optional[Iterable[str]]) -> tuple[str, ...]:
    if topics:
        return plans.normalize_topics(topics)
    config = plan.get("task_config") or {}
    configured = config.get("topics")
    if configured:
        return plans.normalize_topics(configured)
    return plans.normalize_topics(None)


def _scope_for(topic: str, plan: dict) -> tuple[str, ...]:
    return collector_for(topic).resolve_scope(plan)


def _request_budget_plan(topic: str, scope: tuple[str, ...],
                         remaining: int) -> tuple[tuple[str, ...], Optional[dict]]:
    if remaining <= 0:
        return (), {"topic": topic, "reason": "budget_exhausted",
                    "expected": len(scope), "dropped": len(scope)}
    if topic in ("quote", "sector_membership"):
        return scope, None
    if len(scope) <= remaining:
        return scope, None
    return scope[:remaining], {"topic": topic, "reason": "budget_exhausted",
                               "expected": len(scope),
                               "dropped": len(scope) - remaining}


def _observation_intents(result: CaptureResult, *, request: CaptureRequest,
                         run_id: str, received_at: str) -> list[dict]:
    intents = []
    for record in result.observations:
        intents.append({
            "trade_date": request.trade_date,
            "market": record.market,
            "code": record.code,
            "obs_type": record.obs_type,
            "source": "tdx",
            "payload": record.payload,
            "received_at": received_at,
            "available_at": received_at,
            "units": record.units,
            "quality": record.quality,
            "source_event_time": record.source_event_time,
            "valid_until": record.valid_until,
            "semantic_key": record.semantic_key,
            "run_id": run_id,
        })
    return intents


def _bar_intents(result: CaptureResult, *, run_id: str,
                 received_at: str) -> list[dict]:
    from dragon_quant.infra.calibration import binding_for

    intents = []
    for bar in result.bars:
        calibration = (binding_for(bar.calibration_id, calibrated_at=received_at,
                                   source="tdx", period=bar.period)
                       if bar.calibration_id else None)
        intents.append({
            "trade_date": bar.interval_end[:10],
            "market": bar.market,
            "code": bar.code,
            "period": bar.period,
            "interval_start": bar.interval_start,
            "interval_end": bar.interval_end,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "amount": bar.amount,
            "source": "tdx",
            "observed_at": received_at,
            "available_at": received_at,
            "completeness": bar.completeness,
            "time_semantics": bar.time_semantics,
            "unit_semantics": bar.unit_semantics,
            "derived": bar.derived,
            "run_id": run_id,
            "calibration": calibration,
        })
    return intents


def _worse_quality(left: str, right: str) -> str:
    rank = {"semantics_verified": 0, "same_day_recovered": 1, "partial": 2}
    return left if rank.get(left, 0) >= rank.get(right, 0) else right


def _worse(left: str, right: str) -> str:
    return left if _STATUS_RANK.get(left, 0) >= _STATUS_RANK.get(right, 0) else right


def _aggregate(topics: list[dict]) -> str:
    attempted = [item for item in topics if item.get("attempted")]
    if not attempted:
        return "skipped"
    worst = "complete"
    for item in attempted:
        worst = _worse(worst, item["status"])
    return worst
