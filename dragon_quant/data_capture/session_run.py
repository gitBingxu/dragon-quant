"""有界采集会话（阶段循环，网络仍在事务外）。"""

from __future__ import annotations

import time
from typing import Optional

from dragon_quant.infra import clock as runtime_clock

from dragon_quant.infra.assets import observations as store

from .collect import collect_once
from .session_setup import DEFAULT_BUDGET, _hhmm, _worse, resolve_plan
from .types import CaptureError

def run_session(*, plan_id: Optional[str] = None,
                trade_date: Optional[str] = None,
                phase: str = "auction",
                providers=None, db_path=None, clock=None,
                interval_seconds: int = 60, max_slots: int = 3,
                budget: int = DEFAULT_BUDGET, grace_minutes: Optional[int] = None,
                sleep=None, dry_run: bool = False) -> dict:
    """有界会话：按计划时槽推进，错过的窗口显式记 ``missed``。"""
    runtime = clock or runtime_clock.get_clock()
    sleeper = sleep or time.sleep
    plan = resolve_plan(plan_id=plan_id, trade_date=trade_date, db_path=db_path)
    config = plan.get("task_config") or {}
    slots = [
        slot for slot in (config.get("slots") or [])
        if isinstance(slot, dict) and slot.get("phase") == phase
    ]
    if not slots:
        raise CaptureError("unknown_phase", f"计划没有该阶段的时槽: {phase}")
    now_moment = runtime.now()
    today = now_moment.date().isoformat()
    payload: dict = {
        "ok": True,
        "command": "assets capture session",
        "dry_run": bool(dry_run),
        "plan_id": plan["plan_id"],
        "trade_date": plan["trade_date"],
        "phase": phase,
        "slots": [],
        "stopped": None,
        "status": "complete",
    }
    if plan["trade_date"] != today and not dry_run:
        payload["ok"] = False
        payload["stopped"] = "history_not_recoverable"
        payload["status"] = "skipped"
        return payload

    performed = 0
    current_hhmm = _hhmm(now_moment)
    for slot in slots:
        slot_label = str(slot.get("slot") or "")
        grace = int(grace_minutes if grace_minutes is not None
                    else slot.get("grace_minutes") or 30)
        topics = slot.get("topics") or []
        item: dict = {"slot": slot_label, "topics": list(topics)}
        if current_hhmm < slot_label:
            item["status"] = "skipped"
            item["reason"] = "window_not_open"
            payload["slots"].append(item)
            payload["stopped"] = "window_not_open"
            break
        deadline_hhmm = _add_minutes(slot_label, grace)
        run_id = f"{plan['plan_id']}:{phase}:{slot_label}"
        try:
            existing = store.get_run(run_id, db_path=db_path)
        except store.StoreUninitialized:
            existing = None
        if existing is not None and existing["status"] == "complete":
            item["status"] = "skipped"
            item["reason"] = "already_complete"
            payload["slots"].append(item)
            continue
        if current_hhmm > deadline_hhmm:
            item["status"] = "missed"
            item["reason"] = "slot_window_passed"
            if not dry_run:
                run = store.ensure_run(
                    plan_id=plan["plan_id"], trade_date=plan["trade_date"],
                    phase=phase, slot=slot_label, deadline_at=None,
                    created_at=now_moment.isoformat(), db_path=db_path)
                store.mark_run(
                    run["run_id"], status="missed",
                    report={"reason": "slot_window_passed",
                            "slot": slot_label,
                            "note": "睡眠/停机导致错过；不可回补"},
                    finished_at=now_moment.isoformat(), db_path=db_path)
            payload["slots"].append(item)
            payload["status"] = "missed"
            continue
        if performed >= int(max_slots):
            payload["stopped"] = "max_slots"
            break
        result = collect_once(
            plan_id=plan["plan_id"], topics=topics, phase=phase,
            slot=slot_label, providers=providers, db_path=db_path,
            clock=runtime, budget=budget, dry_run=dry_run)
        item["status"] = result["status"]
        item["result"] = result
        payload["slots"].append(item)
        payload["status"] = _worse(payload["status"], result["status"])
        performed += 1
        if performed < int(max_slots):
            sleeper(max(int(interval_seconds), 0))
            now_moment = runtime.now()
            current_hhmm = _hhmm(now_moment)
            if now_moment.date().isoformat() != today:
                payload["stopped"] = "day_rolled_over"
                payload["status"] = _worse(payload["status"], "partial")
                break
    payload["ok"] = payload["status"] in ("complete", "partial", "skipped")
    return payload


def _add_minutes(hhmm: str, minutes: int) -> str:
    hour = int(hhmm[:2])
    minute = int(hhmm[2:])
    total = hour * 60 + minute + int(minutes)
    total = min(total, 23 * 60 + 59)
    return f"{total // 60:02d}{total % 60:02d}"