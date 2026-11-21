"""只读覆盖视图：计划 / 运行 / 观测 / bar / 区间缺口 / 质量。

W0-M4/M5 纪律：

- 覆盖按 ``日期 × 代码 × 主题/周期`` 分开统计，绝不因「有一根」就判完整；
- 分钟按**已结束的预期区间**求缺口，同区间多 revision 只算一个区间；
- 未来时槽/尚未收盘的窗口不算漏采；窗口过去才可能 ``missed``；
- 语义未验证（time_semantics / quality）的单列统计，不能与可用证据混为一谈；
- 长历史读取全部走 SQL 聚合 + 显式 ``limit``/``offset``，返回准确总数与截断标记；
  截断时不得给出「完整」印象。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from dragon_quant.infra import clock as runtime_clock
from dragon_quant.infra.assets import observations as store

from .coverage_report import _error_payload, _report_payload
from .types import OBSERVATION_SCHEMA_VERSION

CST = timezone(timedelta(hours=8))
MAX_GAPS = 2000
MAX_GAP_SAMPLES = 20
MINUTE_PERIOD = "1min"
DAILY_PERIOD = "daily"
TOPIC_PERIODS = {"minute_bars": (MINUTE_PERIOD,), "daily_bars": (DAILY_PERIOD,)}


def _latest_plans(plans: list[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    for plan in plans:
        current = latest.get(plan["trade_date"])
        if current is None or plan["revision"] > current["revision"]:
            latest[plan["trade_date"]] = plan
    return [latest[key] for key in sorted(latest)]


def _minute_interval_ends(day: str) -> list[str]:
    """A 股 1 分钟 bar 的预期结束时刻（09:31–11:30 / 13:01–15:00）。"""
    ends: list[str] = []
    for start_minute, end_minute in ((9 * 60 + 31, 11 * 60 + 30),
                                     (13 * 60 + 1, 15 * 60)):
        for minute in range(start_minute, end_minute + 1):
            ends.append(f"{day}T{minute // 60:02d}:{minute % 60:02d}:00+08:00")
    return ends


def _parse_day(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _evaluation_cutoff(day: str, now: datetime) -> tuple[str, list[str]]:
    """返回 ``(state, expected_minute_ends)``。

    state: ``future``（计划日在未来）/ ``open``（当日未开始或未结束）/
    ``ready``（已结束或已收盘）。
    """
    parsed = _parse_day(day)
    if parsed is None:
        return "invalid", []
    if parsed > now.date():
        return "future", []
    if parsed < now.date():
        return "ready", _minute_interval_ends(day)
    close_moment = datetime(parsed.year, parsed.month, parsed.day, 15, 0,
                            tzinfo=CST)
    if now >= close_moment:
        return "ready", _minute_interval_ends(day)
    ends = [end for end in _minute_interval_ends(day)
            if datetime.fromisoformat(end) <= now]
    return ("open" if ends else "open"), ends


def _daily_window_state(day: str, now: datetime) -> str:
    close_moment = datetime.fromisoformat(f"{day}T15:00:00+08:00")
    parsed = _parse_day(day)
    if parsed is None:
        return "invalid"
    if parsed > now.date():
        return "future"
    return "ready" if now >= close_moment else "open"


def _slot_state(plan_date: str, hhmm: str, grace_minutes: int,
                now: datetime) -> str:
    parsed = _parse_day(plan_date)
    if parsed is None:
        return "invalid"
    try:
        hour, minute = int(hhmm[:2]), int(hhmm[2:])
    except (TypeError, ValueError):
        return "invalid"
    scheduled = datetime(parsed.year, parsed.month, parsed.day, hour, minute,
                         tzinfo=CST)
    if now < scheduled:
        return "future"
    if now <= scheduled + timedelta(minutes=int(grace_minutes)):
        return "open"
    return "passed"


def _status_from_counts(missing: int, unusable: int, expected: int,
                        window: str) -> str:
    if window == "future":
        return "future"
    if window == "invalid":
        return "invalid"
    if expected == 0 and window == "open":
        return "open"
    if missing or unusable:
        return "partial"
    return "complete"


def _truncation_gap(kind: str, shown: int, total: int) -> Optional[dict]:
    if total <= shown:
        return None
    return {"reason": "coverage_truncated", "kind": kind,
            "shown": shown, "total": total,
            "detail": "覆盖结果已截断；严格研究不得把截断当完整"}


def build_coverage(*, date_from: Optional[str] = None,
                   date_to: Optional[str] = None,
                   codes: Optional[Iterable[str]] = None,
                   topics: Optional[Iterable[str]] = None,
                   limit: int = 1000, offset: int = 0,
                   interval_code_limit: int = 100,
                   db_path=None, clock=None) -> dict:
    """只读覆盖；库/表缺失时返回 ``store_state`` 而非伪造空池错误。"""
    now = (clock or runtime_clock.get_clock()).now()
    codes_list = sorted({str(code) for code in codes or () if str(code)}) or None
    topics_list = [str(topic) for topic in topics or ()] or None
    periods = (None if not topics_list else
               [period for topic in topics_list for period in TOPIC_PERIODS.get(topic, ())])

    try:
        state = store.store_state(db_path)
    except store.ObservationStoreError as exc:
        return _error_payload(exc)
    if state != store.READY:
        return {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "store_state": state,
            "date_from": date_from,
            "date_to": date_to,
            "plans": [],
            "runs": [],
            "observations": [],
            "bar_groups": [],
            "interval_coverage": [],
            "gaps": [],
            "totals": {"plans": 0, "observation_groups": 0, "bar_groups": 0},
            "truncated": False,
            "gaps_truncated": False,
            "coverage_complete": False,
        }

    try:
        plans = _latest_plans(store.list_plans(
            date_from, date_to, limit=limit, offset=offset, db_path=db_path))
        observations, total_obs = store.observation_groups(
            date_from, date_to, codes=codes_list, topics=topics_list,
            limit=limit, offset=offset, db_path=db_path)
        bars, total_bars = store.bar_groups(
            date_from, date_to, codes=codes_list, periods=periods or None,
            limit=limit, offset=offset, db_path=db_path)
        runs = store.list_runs(date_from, date_to, limit=limit, offset=offset,
                               db_path=db_path)
    except store.ObservationStoreError as exc:
        return _error_payload(exc)

    gaps, interval_coverage, interval_skipped = _plan_gaps(
        plans, observations, bars, runs, topics_list=topics_list,
        codes_list=codes_list, now=now,
        interval_code_limit=interval_code_limit, db_path=db_path)
    truncation_gaps = [
        gap for gap in (
            _truncation_gap("plans", len(plans),
                            store.count_plans(date_from, date_to,
                                              db_path=db_path)),
            _truncation_gap("observation_groups", len(observations), total_obs),
            _truncation_gap("bar_groups", len(bars), total_bars),
            _truncation_gap("runs", len(runs),
                            store.count_runs(date_from, date_to,
                                             db_path=db_path)),
        ) if gap is not None
    ]
    gaps.extend(truncation_gaps)
    if interval_skipped:
        gaps.append({"reason": "interval_coverage_truncated",
                     "skipped_codes": interval_skipped,
                     "limit": int(interval_code_limit)})
    gaps_truncated = len(gaps) > MAX_GAPS
    return _report_payload(
        plans=plans, runs=runs, observations=observations, bars=bars,
        interval_coverage=interval_coverage, gaps=gaps,
        truncation_gaps=truncation_gaps, gaps_truncated=gaps_truncated,
        date_from=date_from, date_to=date_to, total_obs=total_obs,
        total_bars=total_bars, interval_skipped=interval_skipped,
        db_path=db_path)



def _plan_gaps(plans, observations, bars, runs, *, topics_list, codes_list,
               now, interval_code_limit, db_path):
    obs_index = {(item["trade_date"], item["code"], item["obs_type"]): item
                 for item in observations}
    bar_index = {(item["trade_date"], item["code"], item["period"],
                  item["adjustment"]): item for item in bars}
    runs_index = {(run["plan_id"], run["phase"], run["slot"]): run
                  for run in runs}
    gaps: list[dict] = []
    interval_coverage: list[dict] = []
    interval_skipped = 0

    for plan in plans:
        plan_date = plan["trade_date"]
        config = plan.get("task_config") or {}
        plan_topics = topics_list or list(config.get("topics") or ())
        members = [item.get("code") for item in plan.get("members") or []
                   if isinstance(item, dict) and item.get("code")]
        if codes_list is not None:
            members = [code for code in members if code in codes_list]
        for gap in (plan.get("pool") or {}).get("gaps") or []:
            gaps.append({"trade_date": plan_date, "topic": "pool", **gap})

        slots = [slot for slot in (config.get("slots") or [])
                 if isinstance(slot, dict)]
        for slot in slots:
            slot_topics = [topic for topic in (slot.get("topics") or [])
                           if topic in plan_topics]
            if not slot_topics:
                continue
            slot_label = str(slot.get("slot") or "")
            grace = int(slot.get("grace_minutes") or 30)
            state = _slot_state(plan_date, slot_label, grace, now)
            if state != "passed":
                continue
            run = runs_index.get((plan["plan_id"], slot.get("phase")
                                  or "manual", slot_label))
            for topic in slot_topics:
                if topic in TOPIC_PERIODS:
                    continue
                for code in members:
                    if (plan_date, code, topic) in obs_index:
                        continue
                    gaps.append({"trade_date": plan_date, "code": code,
                                 "topic": topic, "slot": slot_label,
                                 "reason": "slot_not_collected",
                                 "run_status": (run or {}).get("status")})

        for topic in plan_topics:
            if topic == "minute_bars":
                window, expected = _evaluation_cutoff(plan_date, now)
                if window == "future":
                    continue
                for index, code in enumerate(members):
                    if index >= int(interval_code_limit):
                        interval_skipped = len(members) - index
                        break
                    states = store.bar_interval_states(
                        plan_date, code, MINUTE_PERIOD, db_path=db_path)
                    present = {item["interval_end"] for item in states}
                    usable = {
                        item["interval_end"] for item in states
                        if item["completeness"] == "final"
                        and item["time_semantics"] == "verified"
                    }
                    missing = [end for end in expected if end not in present]
                    unusable = sorted(present - usable)
                    revised = [item for item in states
                               if int(item.get("revisions") or 1) > 1]
                    entry = {
                        "trade_date": plan_date,
                        "code": code,
                        "topic": topic,
                        "period": MINUTE_PERIOD,
                        "window": window,
                        "expected_intervals": len(expected),
                        "present_intervals": len(present),
                        "usable_intervals": len(usable),
                        "missing_count": len(missing),
                        "missing_sample": missing[:MAX_GAP_SAMPLES],
                        "unusable_count": len(unusable),
                        "unusable_sample": unusable[:MAX_GAP_SAMPLES],
                        "revised_intervals": len(revised),
                        "status": _status_from_counts(
                            len(missing), len(unusable), len(expected), window),
                    }
                    interval_coverage.append(entry)
                    if missing:
                        gaps.append({"trade_date": plan_date, "code": code,
                                     "topic": topic,
                                     "reason": "interval_missing",
                                     "missing_count": len(missing),
                                     "sample": missing[:MAX_GAP_SAMPLES]})
                    if unusable:
                        gaps.append({"trade_date": plan_date, "code": code,
                                     "topic": topic,
                                     "reason": "semantics_unverified",
                                     "count": len(unusable)})
            elif topic == "daily_bars":
                window = _daily_window_state(plan_date, now)
                if window != "ready":
                    continue
                for code in members:
                    group = bar_index.get((plan_date, code, DAILY_PERIOD, "none"))
                    if group is None:
                        gaps.append({"trade_date": plan_date, "code": code,
                                     "topic": topic,
                                     "reason": "daily_bar_missing"})
                        continue
                    if (group["unusable_versions"] >= group["versions"]
                            or group["distinct_intervals"] == 0):
                        gaps.append({"trade_date": plan_date, "code": code,
                                     "topic": topic,
                                     "reason": "daily_bar_unusable",
                                     "unusable_versions": group["unusable_versions"]})
            else:
                for code in members:
                    if (plan_date, code, topic) in obs_index:
                        continue
                    gaps.append({"trade_date": plan_date, "code": code,
                                 "topic": topic, "reason": "not_collected"})

    return gaps, interval_coverage, interval_skipped
