"""``assets capture`` 命令装配（薄层：参数 → 用例 → 输出）。

分层：``cli → commands/assets.py → data_capture.cli → session/plans/coverage``。
本模块只做参数归一、装配（持仓代码读取）与输出，不实现采集算法；provider 由
composition root 按 ``providers_needed`` 决定是否构造（``plan``/``coverage``/
``schedule``/``--dry-run`` 一律零联网）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import timedelta
from typing import Optional

from dragon_quant.infra import clock as runtime_clock

from . import plans, session
from .coverage import build_coverage
from .schedule import render_schedule
from .types import CaptureError


def _date_arg(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return plans.normalize_trade_date(text)


def _topics_arg(value) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = [item.strip() for item in str(value).split(",")]
    return [item for item in items if item]


def _codes_arg(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = str(value).split(",")
    return sorted({item.strip() for item in items if item.strip()})


def _read_holdings() -> tuple[list[str], Optional[dict]]:
    """只读读取最近一次账户回测的未平仓持仓代码；缺失即显式记缺口。

    经共享严格只读连接（``mode=ro``）打开 dragon.db：绝不因计划的读路径建库/
    建表/建 sidecar。账户金额与真实成交仍留在账户域，这里只取代码。
    """
    from dragon_quant.infra.storage import paths
    from dragon_quant.infra.storage.readonly import ReadonlyUnavailable, open_readonly

    try:
        conn = open_readonly(paths.DB_PATH)
    except ReadonlyUnavailable as exc:
        return [], {"pool": "holdings", "reason": "account_state_missing",
                    "detail": f"dragon.db 无法只读打开: {exc.reason}"}
    if conn is None:
        return [], {"pool": "holdings", "reason": "account_state_missing",
                    "detail": "dragon.db 不存在；不猜持仓"}
    try:
        row = conn.execute(
            "SELECT id FROM review_account_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return [], {"pool": "holdings", "reason": "account_state_missing",
                        "detail": "没有账户回测运行；不猜持仓"}
        codes = sorted({
            item[0]
            for item in conn.execute(
                "SELECT code FROM review_account_positions "
                "WHERE run_id = ? AND status = 'open'",
                (row[0],),
            )
            if item and item[0]
        })
        return codes, None
    except sqlite3.Error as exc:
        return [], {"pool": "holdings", "reason": "account_state_missing",
                    "detail": f"账户表不可读: {exc}"}
    finally:
        conn.close()


def _deadline_arg(args, clock) -> Optional[str]:
    minutes = getattr(args, "deadline_minutes", None)
    if not minutes:
        return None
    return (clock.now() + timedelta(minutes=int(minutes))).isoformat()


def _run_plan(args, services) -> tuple[dict, int]:
    clock = getattr(services, "clock", None) or runtime_clock.get_clock()
    now = clock.now().isoformat()
    trade_date = _date_arg(getattr(args, "date", None)) or clock.today().isoformat()
    if getattr(args, "no_holdings", False):
        holdings: list[str] = []
        holdings_gap = None
    else:
        holdings, holdings_gap = _read_holdings()
    payload = plans.build_plan_payload(
        trade_date=trade_date,
        now=now,
        codes=_codes_arg(getattr(args, "codes", None)),
        holdings_codes=holdings,
        holdings_gap=holdings_gap,
        recent_days=int(getattr(args, "recent_days", plans.DEFAULT_RECENT_DAYS)),
        track_days=int(getattr(args, "track_days", plans.DEFAULT_TRACK_DAYS)),
        topics=_topics_arg(getattr(args, "topics", None)),
        sectors=_codes_arg(getattr(args, "sectors", None)),
    )
    if getattr(args, "dry_run", False):
        return ({
            "ok": True,
            "command": "assets capture plan",
            "dry_run": True,
            "plan_id": None,
            "created": False,
            "revision": None,
            "plan": payload,
        }, 0)
    frozen = plans.freeze_plan(payload, now=now)
    plan = frozen["plan"]
    return ({
        "ok": True,
        "command": "assets capture plan",
        "dry_run": False,
        "plan_id": plan["plan_id"],
        "created": bool(frozen["created"]),
        "revision": plan["revision"],
        "plan": plan,
    }, 0)


def _run_once(args, services) -> tuple[dict, int]:
    clock = services.clock if services is not None else runtime_clock.get_clock()
    report = session.collect_once(
        plan_id=getattr(args, "plan_id", None),
        trade_date=_date_arg(getattr(args, "date", None)),
        topics=_topics_arg(getattr(args, "topics", None)),
        phase=str(getattr(args, "phase", None) or "manual"),
        slot=getattr(args, "slot", None),
        providers=getattr(services, "providers", None),
        clock=clock,
        budget=int(getattr(args, "budget", None) or session.DEFAULT_BUDGET),
        deadline_at=_deadline_arg(args, clock),
        dry_run=bool(getattr(args, "dry_run", False)),
        allow_outside_window=bool(getattr(args, "allow_outside_window", False)),
    )
    return report, 0 if report.get("ok") else 1


def _run_session(args, services) -> tuple[dict, int]:
    clock = services.clock if services is not None else runtime_clock.get_clock()
    report = session.run_session(
        plan_id=getattr(args, "plan_id", None),
        trade_date=_date_arg(getattr(args, "date", None)),
        phase=str(getattr(args, "phase", None) or "auction"),
        providers=getattr(services, "providers", None),
        clock=clock,
        interval_seconds=int(getattr(args, "interval_seconds", 60) or 0),
        max_slots=int(getattr(args, "max_slots", 3) or 1),
        budget=int(getattr(args, "budget", None) or session.DEFAULT_BUDGET),
        grace_minutes=getattr(args, "grace_minutes", None),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    return report, 0 if report.get("ok") else 1


def _run_coverage(args, services) -> tuple[dict, int]:
    clock = getattr(services, "clock", None) or runtime_clock.get_clock()
    payload = build_coverage(
        date_from=_date_arg(getattr(args, "from_date", None)),
        date_to=_date_arg(getattr(args, "to_date", None)),
        codes=_codes_arg(getattr(args, "codes", None)),
        topics=_topics_arg(getattr(args, "topics", None)),
        limit=int(getattr(args, "limit", None) or 1000),
        offset=int(getattr(args, "offset", None) or 0),
        clock=clock,
    )
    ok = payload.get("store_state") != "error"
    return ({
        "ok": ok,
        "command": "assets capture coverage",
        **payload,
    }, 0 if ok else 1)


def _run_schedule(args, services) -> tuple[dict, int]:
    try:
        payload = render_schedule(
            str(getattr(args, "kind", None) or "launchd"),
            workdir=getattr(args, "workdir", None),
        )
    except ValueError as exc:
        raise CaptureError("invalid_schedule_kind", str(exc)) from exc
    return ({"ok": True, "command": "assets capture schedule", **payload}, 0)


_DISPATCH = {
    "plan": _run_plan,
    "once": _run_once,
    "session": _run_session,
    "coverage": _run_coverage,
    "schedule": _run_schedule,
}


def _human(payload: dict) -> str:
    command = payload.get("command", "assets capture")
    if not payload.get("ok"):
        error = payload.get("error") or {}
        reason = error.get("message") if isinstance(error, dict) else None
        return f"❌ {command} 失败：{reason or payload.get('error') or '未知错误'}"
    if command.endswith("plan"):
        plan = payload.get("plan") or {}
        members = plan.get("members") or []
        gaps = ((plan.get("pool") or {}).get("gaps") or [])
        prefix = "计划体检（dry-run）" if payload.get("dry_run") else "计划"
        text = (f"{prefix}：trade_date={plan.get('trade_date')} "
                f"members={len(members)} pools="
                f"dragons={len((plan.get('pool') or {}).get('dragons', {}).get('dates') or [])} "
                f"gaps={len(gaps)}")
        if payload.get("plan_id"):
            text += f" plan_id={payload['plan_id']} revision={payload.get('revision')}"
        if payload.get("dry_run"):
            text += "\ndry-run 未写库；去掉 --dry-run 冻结计划"
        return text
    if command.endswith("once"):
        topics = payload.get("topics") or []
        lines = [f"一次取数：plan={payload.get('plan_id')} slot={payload.get('slot')} "
                 f"status={payload.get('status')}"]
        for item in topics:
            lines.append(
                f"- {item.get('topic')}: status={item.get('status')} "
                f"expected={item.get('expected')} observed={item.get('observed')} "
                f"writes={(item.get('writes') or {}).get('observations')}")
        return "\n".join(lines)
    if command.endswith("session"):
        slots = payload.get("slots") or []
        lines = [f"会话：phase={payload.get('phase')} slots={len(slots)} "
                 f"status={payload.get('status') or payload.get('stopped')}"]
        for item in slots:
            lines.append(f"- {item.get('slot')}: {item.get('status')}"
                         + (f" ({item['reason']})" if item.get("reason") else ""))
        return "\n".join(lines)
    if command.endswith("coverage"):
        text = (f"覆盖：state={payload.get('store_state')} "
                f"plans={len(payload.get('plans') or [])} "
                f"runs={len(payload.get('runs') or [])} "
                f"obs_groups={len(payload.get('observations') or [])} "
                f"bar_groups={len(payload.get('bar_groups') or [])} "
                f"intervals={len(payload.get('interval_coverage') or [])} "
                f"gaps={len(payload.get('gaps') or [])}")
        if payload.get("truncated"):
            text += "\n已截断：总量见 totals；严格研究不得把截断当完整"
        if payload.get("coverage_complete") is False:
            text += "\n覆盖不完整：缺口/截断已列出"
        return text
    if command.endswith("schedule"):
        return (f"调度模板：kind={payload.get('kind')} installed=False "
                f"（仅输出，不安装）")
    return f"{command} 完成"


def handle_capture(args, services) -> None:
    """``assets capture <action>`` 的唯一入口；总是以进程退出码结束。"""
    from dragon_quant.infra.assets.observations import ObservationStoreError

    action = getattr(args, "capture_action", None)
    runner = _DISPATCH.get(action)
    try:
        if runner is None:
            raise CaptureError(
                "unknown_action",
                f"未知 capture 动作: {action!r}（可选 {sorted(_DISPATCH)}）")
        payload, exit_code = runner(args, services)
    except CaptureError as exc:
        payload = {"ok": False, "command": f"assets capture {action}",
                   "error": exc.as_dict()}
        exit_code = 1
    except ObservationStoreError as exc:
        payload = {"ok": False, "command": f"assets capture {action}",
                   "error": {"reason": exc.reason, "message": str(exc)}}
        exit_code = 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(_human(payload), file=sys.stderr)
    sys.exit(exit_code)
