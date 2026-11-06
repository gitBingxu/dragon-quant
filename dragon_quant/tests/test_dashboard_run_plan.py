"""dashboard 可点击判定 + 交易日历探测 + 共享单飞作业（纯逻辑 / 注入替身）。"""

from __future__ import annotations

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from web_ui.backend.presenters.run_plan import decide, in_window
from web_ui.backend.run_runner import RunJobBusy, RunJobRunner
from web_ui.backend.run_specs import get_spec
from web_ui.backend.trade_calendar import TradeCalendarProbe

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _at(hour: int, minute: int, *, day: int = 6) -> datetime:
    # 2026-11-06 是周五。
    return datetime(2026, 11, day, hour, minute, tzinfo=SHANGHAI)


def _decide(job: str, now: datetime, **overrides) -> dict:
    context = {
        "trading_day": True,
        "trading_day_source": "tdx_calendar",
        "already_done": False,
        "scan_done": True,
        "running_job": None,
        "running_label": None,
    }
    context.update(overrides)
    return decide(spec=get_spec(job), now=now, **context)


# ─── 时间窗与前置 ──────────────────────────────────────────────────────────


def test_scan_window_is_15_00_to_23_55() -> None:
    spec = get_spec("scan")
    assert not in_window(_at(14, 59), spec)
    assert in_window(_at(15, 0), spec)
    assert in_window(_at(23, 54), spec)
    assert not in_window(_at(23, 55), spec)
    assert not in_window(_at(0, 30), spec)


def test_prep_requires_time_scan_and_no_snapshot() -> None:
    before = _decide("prep", _at(20, 59))
    assert before["enabled"] is False
    assert [item["code"] for item in before["blockers"]] == ["outside_window"]

    missing_scan = _decide("prep", _at(21, 30), scan_done=False)
    assert missing_scan["enabled"] is False
    assert [item["code"] for item in missing_scan["blockers"]] == ["missing_scan"]
    assert "龙头扫描" in missing_scan["blockers"][0]["text"]

    done = _decide("prep", _at(21, 30), already_done=True)
    assert done["enabled"] is False
    assert [item["code"] for item in done["blockers"]] == ["already_done"]

    ready = _decide("prep", _at(21, 30))
    assert ready["enabled"] is True
    assert ready["blockers"] == []


def test_auction_requires_0925() -> None:
    assert _decide("auction", _at(9, 24))["enabled"] is False
    assert _decide("auction", _at(9, 25))["enabled"] is True


def test_non_trading_day_blocks_every_job_with_source_text() -> None:
    weekend = _decide(
        "scan", _at(16, 0, day=7), trading_day=False,
        trading_day_source="weekend")
    assert weekend["enabled"] is False
    assert weekend["blockers"][0]["code"] == "non_trading_day"
    assert "周末" in weekend["blockers"][0]["text"]

    fallback = _decide(
        "scan", _at(16, 0), trading_day=False,
        trading_day_source="weekday_fallback")
    assert "日历不可用" in fallback["blockers"][0]["text"]


def test_running_job_blocks_all_jobs_and_names_the_job() -> None:
    blocked = _decide(
        "scan", _at(16, 0), running_job="prep", running_label="盘后复盘")
    assert blocked["enabled"] is False
    blocker = blocked["blockers"][0]
    assert blocker["code"] == "job_running"
    assert "盘后复盘" in blocker["text"]


def test_blockers_accumulate_for_hover_text() -> None:
    blocked = _decide(
        "prep", _at(9, 0), scan_done=False, already_done=True,
        running_job="scan", running_label="龙头扫描")
    codes = [item["code"] for item in blocked["blockers"]]
    assert codes == ["outside_window", "missing_scan", "already_done", "job_running"]


# ─── 交易日历探测 ──────────────────────────────────────────────────────────


def test_calendar_weekend_is_not_trading_day_without_probing() -> None:
    calls: list[int] = []

    def loader() -> list[str]:
        calls.append(1)
        return ["2026-11-05"]

    probe = TradeCalendarProbe(loader=loader)
    assert probe.lookup(_at(16, 0, day=7)) == (False, "weekend")
    assert calls == []  # 周末不联网


def test_calendar_confirms_today_when_present() -> None:
    probe = TradeCalendarProbe(loader=lambda: ["2026-11-05", "2026-11-06"])
    assert probe.lookup(_at(9, 25)) == (True, "tdx_calendar")


def test_calendar_before_1505_assumes_weekday_trading_day() -> None:
    probe = TradeCalendarProbe(loader=lambda: ["2026-11-05"])
    assert probe.lookup(_at(9, 25)) == (True, "weekday_assumed")
    assert probe.lookup(_at(15, 4)) == (True, "weekday_assumed")


def test_calendar_after_1505_confirms_holiday() -> None:
    probe = TradeCalendarProbe(loader=lambda: ["2026-11-05"])
    assert probe.lookup(_at(21, 0)) == (False, "tdx_calendar")


def test_calendar_absence_cached_only_until_1505() -> None:
    """盘前缓存的「今天缺席」必须在 15:05 后重新探测，否则交易日会被误判。"""
    calls: list[int] = []
    now = [0.0]

    def loader() -> list[str]:
        calls.append(1)
        # 第二次探测时 TDX 已发布当日日K（真实交易日）。
        return ["2026-11-05"] if len(calls) == 1 else ["2026-11-05", "2026-11-06"]

    probe = TradeCalendarProbe(loader=loader, mono=lambda: now[0])
    assert probe.lookup(_at(14, 0)) == (True, "weekday_assumed")
    assert len(calls) == 1

    # 14:00 → 15:05 是 3900s；+60s 缓冲后缓存必然过期。
    now[0] = 4000.0
    assert probe.lookup(_at(15, 6)) == (True, "tdx_calendar")
    assert len(calls) == 2


def test_calendar_failure_degrades_to_weekday_and_retries_after_ttl() -> None:
    calls: list[int] = []

    def loader() -> list[str]:
        calls.append(1)
        raise RuntimeError("tdx down")

    now = [0.0]
    probe = TradeCalendarProbe(loader=loader, mono=lambda: now[0])
    assert probe.lookup(_at(16, 0)) == (True, "weekday_fallback")
    assert probe.lookup(_at(16, 1)) == (True, "weekday_fallback")
    assert len(calls) == 1  # 失败后 5 分钟内不重试

    now[0] = 301.0
    probe.lookup(_at(16, 2))
    assert len(calls) == 2


def test_calendar_caches_success_for_the_whole_day() -> None:
    calls: list[int] = []

    def loader() -> list[str]:
        calls.append(1)
        return ["2026-11-06"]

    probe = TradeCalendarProbe(loader=loader, mono=lambda: 0.0)
    assert probe.lookup(_at(9, 25)) == (True, "tdx_calendar")
    assert probe.lookup(_at(21, 0)) == (True, "tdx_calendar")
    assert len(calls) == 1


# ─── 共享单飞作业 ──────────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, gate: threading.Event) -> None:
        self.gate = gate
        self.returncode: int | None = None

    def communicate(self):
        self.gate.wait(timeout=5)
        if self.returncode is None:
            self.returncode = 0
        return "done", None

    def terminate(self) -> None:
        self.gate.set()

    def kill(self) -> None:
        self.gate.set()

    def wait(self, timeout: float | None = None):
        self.gate.wait(timeout)
        return self.returncode


def test_runner_reports_job_and_params_and_blocks_other_jobs() -> None:
    gate = threading.Event()
    seen: list[list[str]] = []

    def opener(argv, **kwargs):
        seen.append(list(argv))
        return _FakeProc(gate)

    runner = RunJobRunner(popener=opener)
    runner.start(job="scan", label="龙头扫描",
                 argv=["python", "-m", "dragon_quant", "scan"],
                 params={"top": 5})

    status = runner.status()
    assert status["state"] == "running"
    assert status["job"] == "scan"
    assert status["label"] == "龙头扫描"
    assert status["params"] == {"top": 5}
    assert seen[0][-1] == "scan"

    with pytest.raises(RunJobBusy):
        runner.start(job="prep", label="盘后复盘",
                     argv=["python", "-m", "dragon_quant", "prep"],
                     params={"mode": "incremental"})
    # 忙时不得覆盖元数据。
    assert runner.status()["params"] == {"top": 5}

    gate.set()
    for _ in range(300):
        status = runner.status()
        if status["state"] != "running":
            break
        threading.Event().wait(0.01)
    assert status["state"] == "succeeded"
    assert status["last_run"]["exit_code"] == 0
    assert status["last_run"]["job"] == "scan"


def test_runner_spawn_failure_keeps_status_failed() -> None:
    def opener(argv, **kwargs):
        raise OSError("blocked")

    runner = RunJobRunner(popener=opener)
    with pytest.raises(OSError):
        runner.start(job="auction", label="集合竞价",
                     argv=["python", "-m", "dragon_quant", "auction"],
                     params={"pool": "prep"})
    status = runner.status()
    assert status["state"] == "failed"
    assert status["params"] is None
    assert "spawn_failed" in status["last_run"]["output_tail"]
