"""显式一次取数：预算/时窗/取数/原子提交/汇总的薄编排。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from dragon_quant.infra import clock as runtime_clock
from dragon_quant.infra.assets import observations as store

from .collectors import collector_for
from .session_setup import (
    DEFAULT_BUDGET,
    DEFAULT_LEASE_TTL,
    _aggregate,
    _bar_intents,
    _hhmm,
    _observation_intents,
    _request_budget_plan,
    _scope_for,
    _topic_defaults,
    _worse_quality,
    new_worker_id,
    resolve_plan,
)
from .types import (
    QUALITY_RECOVERED,
    TOPIC_WINDOWS,
    CaptureError,
    CaptureRequest,
    window_state,
)


@dataclass
class _Run:
    plan: dict
    report: dict
    entries: list[dict]
    providers: object
    db_path: Optional[str]
    runtime: object
    start: str
    today: str
    deadline_at: Optional[str]
    allow_outside_window: bool
    lease_ttl: int
    worker: str
    selected: tuple = ()
    run_id: str = ""
    token: int = 0
    remaining: int = DEFAULT_BUDGET


def _new_report(*, plan, dry_run, phase, slot, start, run_id) -> dict:
    return {"ok": True, "command": "assets capture once",
            "dry_run": bool(dry_run), "plan_id": plan["plan_id"],
            "trade_date": plan["trade_date"], "phase": phase, "slot": slot,
            "cutoff": start, "run_id": run_id, "topics": [], "gaps": []}


def _entry(entries: list[dict], run_id: str, topic: str) -> dict:
    item = {"topic": topic, "status": "skipped", "expected": 0,
            "observed": 0, "errors": [], "gaps": [], "quality": None,
            "notes": [], "attempted": False, "run_id": run_id}
    entries.append(item)
    return item


def _precheck(run: _Run, topic: str, item: dict, now_moment,
              now_text: str) -> bool:
    state = window_state(topic, _hhmm(now_moment))
    if state == "before" and not run.allow_outside_window:
        item["reason"] = "window_not_open"
        item["detail"] = f"{topic} 时窗未开始；不提前采未生成的数据"
        return False
    if state == "after" and run.plan["trade_date"] != run.today:
        item["reason"] = "history_not_recoverable"
        item["detail"] = "该计划不是当日计划，过期窗口不可补采"
        return False
    if run.deadline_at and now_text >= run.deadline_at:
        item["reason"] = "deadline_passed"
        item["detail"] = "已在截止时间之后，不再发起新请求"
        return False
    return True


def _prepare(run: _Run, topic: str, item: dict) -> Optional[tuple]:
    scope = _scope_for(topic, run.plan)
    if not scope:
        item["reason"] = "scope_empty"
        item["detail"] = ("计划里没有该主题的采集范围"
                          "（sector_membership 需要 sectors 配置）")
        return None
    narrowed, budget_gap = _request_budget_plan(topic, scope, run.remaining)
    if budget_gap is not None:
        item["gaps"].append(budget_gap)
        run.report["gaps"].append(budget_gap)
    if not narrowed:
        item["reason"] = "budget_exhausted"
        return None
    run.remaining -= 1 if topic in ("quote", "sector_membership") else len(narrowed)
    return narrowed, budget_gap


def _topic_report(topic, result, quality, request_state, start,
                  received_at, late) -> dict:
    return {"topic": topic, "status": result.status,
            "expected": list(result.expected_scope),
            "observed": list(result.observed_scope),
            "errors": list(result.errors), "gaps": list(result.gaps),
            "quality": quality, "notes": list(result.notes),
            "window": request_state, "cutoff": start,
            "received_at": received_at, "late_after_deadline": late}


def _run_topic(run: _Run, topic: str, item: dict, narrowed: tuple,
               budget_gap) -> bool:
    item["attempted"] = True
    request_state = window_state(topic, _hhmm(run.runtime.now()))
    quality = (QUALITY_RECOVERED if request_state == "after"
               else "semantics_unverified")
    if run.allow_outside_window and request_state == "before":
        item["notes"].append("manual_outside_window")
    try:
        collector = collector_for(topic)
        provider = getattr(run.providers, collector.provider_name)
        request = CaptureRequest(
            plan_id=run.plan["plan_id"], topic=topic,
            trade_date=run.plan["trade_date"], scope=narrowed, cutoff=run.start,
            request_budget=run.remaining, cursor=None,
            window=TOPIC_WINDOWS.get(topic, ("00:00", "23:59")))
        result = collector.collect(request, provider=provider, plan=run.plan)
        received_at = run.runtime.now().isoformat()
        if run.deadline_at and received_at >= run.deadline_at:
            item["notes"].append("late_after_deadline")
            quality = _worse_quality(quality, "partial")
        renewed = store.acquire_lease(
            run.run_id, owner=run.worker, ttl_seconds=int(run.lease_ttl),
            now=received_at, db_path=run.db_path)
        if renewed is None:
            raise store.LeaseLostError(
                f"取数期间租约被抢占，拒绝落库: run={run.run_id}")
        run.token = renewed
        late = "late_after_deadline" in item["notes"]
        topic_report = _topic_report(topic, result, quality, request_state,
                                     run.start, received_at, late)
        counts = store.commit_topic(
            run_id=run.run_id, owner=run.worker, token=run.token,
            now=received_at,
            observations=_observation_intents(
                result, request=request, run_id=run.run_id,
                received_at=received_at),
            bars=_bar_intents(result, run_id=run.run_id,
                              received_at=received_at),
            topic_report=topic_report, db_path=run.db_path)
        status = result.status
        if budget_gap is not None and status == "complete":
            status = "partial"
        if late and status == "complete":
            status = "partial"
        item.update({
            "status": status, "expected": len(result.expected_scope),
            "observed": len(result.observed_scope),
            "scope": list(result.expected_scope),
            "observed_scope": list(result.observed_scope),
            "quality": quality, "window": request_state, "cutoff": run.start,
            "received_at": received_at, "writes": counts, "reason": None,
            "late_after_deadline": late})
        item["errors"].extend(result.errors)
        item["gaps"].extend(result.gaps)
        item["notes"].extend(result.notes)
        return True
    except CaptureError as exc:
        item["status"] = "failed"
        item["errors"].append(exc.as_dict())
    except store.LeaseLostError:
        raise
    except Exception as exc:  # noqa: BLE001 - 单主题失败不中断整轮
        item["status"] = "failed"
        item["errors"].append({"reason": "source_unavailable",
                               "message": f"{type(exc).__name__}: {exc}"})
    return False


def _dry_run(run: _Run) -> dict:
    for topic in run.selected:
        item = _entry(run.entries, run.report["run_id"], topic)
        if not _precheck(run, topic, item, run.runtime.now(), run.start):
            continue
        prepared = _prepare(run, topic, item)
        if prepared is None:
            continue
        narrowed, _ = prepared
        item.update({
            "status": "planned", "expected": len(narrowed),
            "scope": list(narrowed),
            "quality": ("same_day_recovered"
                        if window_state(topic, _hhmm(run.runtime.now())) == "after"
                        else "semantics_unverified"),
            "reason": "dry_run"})
    run.report["topics"] = run.entries
    run.report["status"] = "planned"
    return run.report


def _lease_failure(run: _Run, reason: str, detail: str) -> dict:
    for topic in run.selected:
        item = _entry(run.entries, run.run_id, topic)
        item["reason"] = reason
        item["detail"] = detail
    run.report["topics"] = run.entries
    run.report["status"] = "skipped"
    return run.report


def _execute(run: _Run, committed: list[bool]) -> dict:
    for topic in run.selected:
        item = _entry(run.entries, run.run_id, topic)
        if not _precheck(run, topic, item, run.runtime.now(), run.start):
            continue
        prepared = _prepare(run, topic, item)
        if prepared is None:
            continue
        narrowed, budget_gap = prepared
        if _run_topic(run, topic, item, narrowed, budget_gap):
            committed[0] = True
    status = _aggregate(run.entries)
    final_at = run.runtime.now().isoformat()
    try:
        store.finalize_run(
            run.run_id, owner=run.worker, token=run.token, status=status,
            report={"cutoff": run.start, "finished_at": final_at,
                    "slot": run.report["slot"], "phase": run.report["phase"],
                    "late_after_deadline": any(
                        "late_after_deadline" in (item.get("notes") or [])
                        for item in run.entries),
                    "topics": run.entries, "gaps": run.report["gaps"]},
            finished_at=final_at, db_path=run.db_path)
    except store.LeaseLostError as exc:
        return _lease_lost(run, committed[0], exc)
    run.report["topics"] = run.entries
    run.report["status"] = status
    run.report["ok"] = status in ("complete", "partial", "skipped")
    return run.report


def _lease_lost(run: _Run, committed: bool, exc) -> dict:
    run.report["topics"] = run.entries
    run.report["status"] = "partial" if committed else "failed"
    run.report["ok"] = committed
    run.report["error"] = {"reason": "lease_lost", "message": str(exc)}
    run.report["committed_topics"] = [
        item["topic"] for item in run.entries
        if (item.get("writes") or {}).get("observations") or
        (item.get("writes") or {}).get("bars")]
    return run.report


def collect_once(*, plan_id: Optional[str] = None,
                 trade_date: Optional[str] = None,
                 topics: Optional[Iterable[str]] = None,
                 phase: str = "manual", slot: Optional[str] = None,
                 providers=None, db_path=None, clock=None,
                 budget: int = DEFAULT_BUDGET,
                 deadline_at: Optional[str] = None, dry_run: bool = False,
                 allow_outside_window: bool = False,
                 lease_ttl: int = DEFAULT_LEASE_TTL) -> dict:
    """显式一次取数；返回结构化报告（不抛网络异常）。"""
    runtime = clock or runtime_clock.get_clock()
    start_moment = runtime.now()
    start = start_moment.isoformat()
    today = start_moment.date().isoformat()
    plan = resolve_plan(plan_id=plan_id, trade_date=trade_date, db_path=db_path)
    selected = _topic_defaults(plan, topics)
    slot_label = slot or _hhmm(start_moment)
    run = _Run(plan=plan, report={}, entries=[], providers=providers,
               db_path=db_path, runtime=runtime, start=start, today=today,
               deadline_at=deadline_at, allow_outside_window=allow_outside_window,
               lease_ttl=lease_ttl, worker=new_worker_id(),
               remaining=max(int(budget), 0))
    report = _new_report(
        plan=plan, dry_run=dry_run, phase=phase, slot=slot_label, start=start,
        run_id=f"{plan['plan_id']}:{phase}:{slot_label}")
    run.selected = selected
    run.report = report
    if dry_run:
        return _dry_run(run)
    if providers is None:
        for topic in selected:
            item = _entry(run.entries, report["run_id"], topic)
            item["status"] = "failed"
            item["errors"].append({"reason": "missing_api_key",
                                   "message": "取数需要 provider bundle"})
        report["topics"] = run.entries
        report["status"] = "failed"
        report["ok"] = False
        return report
    try:
        existing = store.ensure_run(
            plan_id=plan["plan_id"], trade_date=plan["trade_date"],
            phase=phase, slot=slot_label, deadline_at=deadline_at,
            created_at=start, db_path=db_path)
    except store.ObservationStoreError as exc:
        report["status"] = "failed"
        report["ok"] = False
        report["error"] = {"reason": exc.reason, "message": str(exc)}
        return report
    run.run_id = existing["run_id"]
    report["run_id"] = run.run_id
    if existing["status"] == "complete":
        return _lease_failure(run, "already_complete", "")
    token = store.acquire_lease(run.run_id, owner=run.worker,
                                ttl_seconds=int(lease_ttl), now=start,
                                db_path=db_path)
    if token is None:
        return _lease_failure(run, "lease_busy", "")
    run.token = token
    committed = [False]
    try:
        store.start_run(run.run_id, owner=run.worker, token=token,
                        started_at=start, db_path=db_path)
        return _execute(run, committed)
    except store.LeaseLostError as exc:
        return _lease_lost(run, committed[0], exc)
    except Exception as exc:  # noqa: BLE001 - 提交失败也要可见
        report["topics"] = run.entries
        report["status"] = "failed"
        report["ok"] = False
        report["error"] = {"reason": "source_unavailable",
                           "message": f"{type(exc).__name__}: {exc}"}
        return report
