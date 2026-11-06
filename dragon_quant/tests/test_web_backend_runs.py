"""dashboard 命令运行端点：spec / eligibility / status / POST（零真实库）。"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from dragon_quant.infra.clock import FixedClock
from web_ui.backend import deps
from web_ui.backend.app import create_app
from web_ui.backend.run_runner import RunJobRunner
from web_ui.backend.settings import DashboardSettings

BASE = "http://127.0.0.1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
ORIGIN = {"Origin": "http://localhost:8765"}


class _Calendar:
    def __init__(self, trading_day: bool = True, source: str = "tdx_calendar") -> None:
        self.trading_day = trading_day
        self.source = source

    def lookup(self, now):
        return self.trading_day, self.source


def _probe(*done: str):
    done_set = set(done)

    def probe(job: str, trade_date: str) -> bool:
        return job in done_set

    return probe


def make_client(*, at: datetime | None = None, calendar=None, done=(),
                runner: RunJobRunner | None = None) -> TestClient:
    app = create_app(DashboardSettings())
    moment = at or datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI)
    app.dependency_overrides[deps.clock_module] = lambda: FixedClock(moment)
    app.dependency_overrides[deps.trade_calendar_probe] = lambda: (
        calendar or _Calendar())
    app.dependency_overrides[deps.run_done_probe] = lambda: _probe(*done)
    app.dependency_overrides[deps.run_runner] = lambda: (
        runner or RunJobRunner(popener=_never_spawns))
    return TestClient(app, base_url=BASE)


def _never_spawns(argv, **kwargs):  # pragma: no cover - 只读用例不得启动任务
    raise AssertionError(f"只读请求不得启动子进程：{argv}")


class _FakeProc:
    def __init__(self, gate: threading.Event, output: str = "") -> None:
        self.gate = gate
        self.output = output
        self.returncode: int | None = None

    def communicate(self):
        self.gate.wait(timeout=5)
        if self.returncode is None:
            self.returncode = 0
        return self.output, None

    def terminate(self) -> None:
        self.gate.set()

    def kill(self) -> None:
        self.gate.set()

    def wait(self, timeout: float | None = None):
        self.gate.wait(timeout)
        return self.returncode


def _wait_state(client: TestClient, job: str, expected: str,
                timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{job}/status").json()
        if body["state"] == expected:
            return body
        time.sleep(0.01)
    raise AssertionError(f"未进入 {expected}: {client.get(f'/api/runs/{job}/status').json()}")


# ─── spec ──────────────────────────────────────────────────────────────────


def test_spec_for_three_jobs_and_unknown_job_404() -> None:
    client = make_client()
    for job in ("scan", "prep", "auction"):
        response = client.get(f"/api/runs/{job}/spec")
        assert response.status_code == 200
        assert response.json()["job"] == job

    missing = client.get("/api/runs/review/spec")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"


# ─── eligibility ───────────────────────────────────────────────────────────


def test_scan_eligible_after_close_without_record() -> None:
    client = make_client(at=datetime(2026, 11, 6, 15, 0, tzinfo=SHANGHAI))
    body = client.get("/api/runs/scan/eligibility").json()
    assert body["enabled"] is True
    assert body["blockers"] == []
    assert body["trade_date"] == "2026-11-06"
    assert body["trading_day"] is True


def test_scan_disabled_during_session_with_reason() -> None:
    client = make_client(at=datetime(2026, 11, 6, 14, 30, tzinfo=SHANGHAI))
    body = client.get("/api/runs/scan/eligibility").json()
    assert body["enabled"] is False
    assert body["blockers"][0]["code"] == "outside_window"
    assert "15:00" in body["blockers"][0]["text"]


def test_scan_disabled_when_today_already_scanned() -> None:
    client = make_client(done=("scan",))
    body = client.get("/api/runs/scan/eligibility").json()
    assert body["enabled"] is False
    assert body["already_done"] is True
    assert [item["code"] for item in body["blockers"]] == ["already_done"]


def test_weekend_disables_with_weekend_reason() -> None:
    client = make_client(
        at=datetime(2026, 11, 7, 16, 0, tzinfo=SHANGHAI),
        calendar=_Calendar(False, "weekend"))
    body = client.get("/api/runs/scan/eligibility").json()
    assert body["enabled"] is False
    assert body["blockers"][0]["code"] == "non_trading_day"
    assert "周末" in body["blockers"][0]["text"]


def test_prep_requires_scan_first() -> None:
    client = make_client(at=datetime(2026, 11, 6, 21, 30, tzinfo=SHANGHAI))
    body = client.get("/api/runs/prep/eligibility").json()
    assert body["enabled"] is False
    assert body["scan_done"] is False
    codes = [item["code"] for item in body["blockers"]]
    assert codes == ["missing_scan"]
    assert "请先点击「龙头扫描」" in body["blockers"][0]["text"]

    ready = make_client(
        at=datetime(2026, 11, 6, 21, 30, tzinfo=SHANGHAI), done=("scan",))
    assert ready.get("/api/runs/prep/eligibility").json()["enabled"] is True


def test_auction_window_starts_at_0925() -> None:
    early = make_client(at=datetime(2026, 11, 6, 9, 24, tzinfo=SHANGHAI))
    assert early.get("/api/runs/auction/eligibility").json()["enabled"] is False

    ready = make_client(at=datetime(2026, 11, 6, 9, 25, tzinfo=SHANGHAI))
    assert ready.get("/api/runs/auction/eligibility").json()["enabled"] is True


# ─── POST ──────────────────────────────────────────────────────────────────


def test_post_rejects_cross_site_origin() -> None:
    client = make_client()
    response = client.post(
        "/api/runs/scan", json={}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"] == "invalid_origin"


def test_post_rejects_invalid_params_before_spawning() -> None:
    client = make_client()
    for payload, hint in (({"top": 0}, "真龙数量"),
                          ({"workers": "many"}, "并发线程"),
                          ({"date": "20260101"}, "未知参数")):
        response = client.post("/api/runs/scan", json=payload, headers=ORIGIN)
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "invalid_request"
        assert hint in body["detail"]


def test_post_rechecks_eligibility_and_returns_blockers() -> None:
    client = make_client(at=datetime(2026, 11, 6, 10, 0, tzinfo=SHANGHAI))
    response = client.post("/api/runs/scan", json={}, headers=ORIGIN)
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "not_eligible"
    assert "15:00" in body["detail"]
    assert body["blockers"][0]["code"] == "outside_window"


def test_post_scan_202_spawns_exact_argv_and_status_reports_job() -> None:
    gate = threading.Event()
    seen: list[list[str]] = []

    def opener(argv, **kwargs):
        seen.append(list(argv))
        return _FakeProc(gate, output="scan done\n")

    runner = RunJobRunner(popener=opener)
    client = make_client(runner=runner)

    response = client.post("/api/runs/scan", json={"top": 7}, headers=ORIGIN)
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "running"
    assert body["job"] == "scan"
    assert body["params"] == {"top": 7, "candidates": 5, "workers": 2}
    assert seen[0][1:] == [
        "-m", "dragon_quant", "scan",
        "--top", "7", "--candidates", "5", "--workers", "2",
    ]

    gate.set()
    done = _wait_state(client, "scan", "succeeded")
    assert done["last_run"]["exit_code"] == 0
    assert done["last_run"]["output_tail"] == "scan done"


def test_shared_runner_blocks_other_jobs_with_reason() -> None:
    gate = threading.Event()
    runner = RunJobRunner(popener=lambda argv, **kwargs: _FakeProc(gate))
    client = make_client(
        at=datetime(2026, 11, 6, 21, 30, tzinfo=SHANGHAI),
        done=("scan",), runner=runner)

    assert client.post("/api/runs/auction", json={}, headers=ORIGIN).status_code == 202
    other = client.post("/api/runs/prep", json={}, headers=ORIGIN)
    gate.set()
    assert other.status_code == 409
    body = other.json()
    codes = [item["code"] for item in body["blockers"]]
    assert codes == ["job_running"]
    assert "集合竞价" in body["detail"]


def test_post_spawn_failure_is_500() -> None:
    def opener(argv, **kwargs):
        raise OSError("blocked by sandbox")

    client = make_client(runner=RunJobRunner(popener=opener))
    response = client.post("/api/runs/scan", json={}, headers=ORIGIN)
    assert response.status_code == 500
    assert response.json()["error"] == "spawn_failed"
    assert client.get("/api/runs/scan/status").json()["state"] == "failed"
