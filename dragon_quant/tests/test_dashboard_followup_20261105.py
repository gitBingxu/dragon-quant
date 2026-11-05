"""2026-11-05 dashboard 用户故障修复：后端回归（旧账户 / 研究窗口 / 回测作业）。

全部离线：fake 仓储 + 纯函数 + 注入的 Popen，不打开真实 dragon.db / assets.db。
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from dragon_quant.review_account import evaluation
from web_ui.backend import deps
from web_ui.backend.app import create_app
from web_ui.backend.presenters.scan_quality import build_scan_quality
from web_ui.backend.review_runner import (
    ReviewJobBusy,
    ReviewJobRunner,
    default_command,
    parse_review_summary,
)
from web_ui.backend.settings import DashboardSettings

BASE = "http://127.0.0.1"


def _override(value):
    def override():
        return value

    return override


def _make_client(**overrides) -> TestClient:
    app = create_app(DashboardSettings())
    overrides.setdefault("bar_calendar_loader", lambda *a, **k: [])
    for name, value in overrides.items():
        app.dependency_overrides[getattr(deps, name)] = _override(value)
    return TestClient(app, base_url=BASE)


# ─── KR3：旧 run 缺 mark 的期末快照不得 500 ───────────────────────────────


def _old_run() -> dict:
    # 旧增量 run：有期初持仓与 mark，但期末快照的 positions_json 没有 mark
    # （老版本尚未持久化该字段），且缺 start_equity / base_equity。
    return {
        "id": 8, "source": "v2", "initial_cash": 100000.0,
        "start_equity": None, "base_equity": None,
        "start_positions": [{
            "code": "600001", "qty": 1000, "cost": 10005.0,
            "entry_price": 10.0, "entry_date": "2026-08-01",
            "entry_reason_code": "buy_open_turn_strong",
            "mark": 12.0, "realized_pnl": 0.0,
        }],
        "start_state_recorded": True, "base_run_id": 7,
        "run_meta": {},
    }


def _old_snapshot() -> dict:
    return {
        "trade_date": "2026-09-01", "cash": 5000.0,
        "market_value": 7000.0, "total_equity": 12000.0,
        "positions": [{
            "code": "600001", "qty": 1000, "cost": 10005.0,
            "entry_price": 10.0, "entry_date": "2026-08-01",
            "entry_reason_code": "buy_open_turn_strong",
            # 旧数据：无 mark 字段（读回后 key 缺失 → None）。
        }],
    }


class _OldAccount:
    def get_review_account_run(self, run_id):
        return _old_run() if run_id == 8 else None

    def query_review_account_snapshots(self, run_id):
        return [_old_snapshot()] if run_id == 8 else []

    def query_review_account_trades(self, run_id):
        return []

    def query_review_account_positions(self, run_id):
        return []


def test_period_ledger_missing_end_mark_is_unknown_not_crash():
    ledger = evaluation.period_ledger(
        _old_run(), [_old_snapshot()], [])
    assert ledger["quality"] == "unknown"
    row = ledger["rows"][0]
    assert row["end_value"] is None
    assert row["unrealized_part"] is None
    assert row["period_pnl"] is None
    assert ledger["totals"]["ledger_period_pnl"] is None
    assert ledger["reconciliation"]["diff"] is None


def test_old_run_positions_and_summary_return_200():
    client = _make_client(account_repository=_OldAccount())
    positions = client.get("/api/account/positions?run_id=8")
    assert positions.status_code == 200, positions.text
    body = positions.json()
    assert body["data"] == []
    assert body["stats"]["contribution_basis"] == "unknown"
    assert body["stats"]["unknown_rows"] == 1

    summary = client.get("/api/account/summary?run_id=8")
    assert summary.status_code == 200, summary.text
    data = summary.json()["data"]
    # 不 fill 0：缺 mark 的期间金额保持 unknown。
    assert data["unrealized_amount"] is None
    assert data["period_pnl_amount"] is None
    assert data["unrealized_basis"] == "unknown"


# ─── KR4：主 horizon 缺失 + 多日通过/否决的 bootstrap 不 500 ──────────────


def _bar(day: str, open_px: float, close: float) -> dict:
    high = max(open_px, close) + 0.2
    low = min(open_px, close) - 0.2
    return {"date": day, "open": open_px, "high": high, "low": low,
            "close": close, "volume": 1000.0}


def _weekdays(start: str, count: int) -> list[str]:
    from datetime import date, timedelta

    day = date.fromisoformat(start)
    days: list[str] = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _scan_rows(day: str, codes: list[str], *, passed: bool) -> list[dict]:
    return [{
        "code": code, "name": code, "rank": index + 1,
        "composite_score": 80.0 - index,
        "is_true_dragon": passed,
        "reject_reason": None if passed else "gate",
        "scan_top_n": 20, "scan_id": f"v2_{day}",
        "scan_created_at": f"{day}T15:00:00",
    } for index, code in enumerate(codes)]


def test_scan_quality_partial_primary_horizon_keeps_other_samples():
    days = _weekdays("2026-09-01", 12)
    passed_codes = [f"60000{i}" for i in range(1, 10)]
    rejected_codes = [f"60001{i}" for i in range(1, 10)]
    with_primary = {passed_codes[6], passed_codes[7], passed_codes[8],
                    rejected_codes[6], rejected_codes[7], rejected_codes[8]}
    contributions = {
        days[-6]: _scan_rows(days[-6], passed_codes, passed=True)
        + _scan_rows(days[-6], rejected_codes, passed=False),
        days[-5]: _scan_rows(days[-5], passed_codes, passed=True)
        + _scan_rows(days[-5], rejected_codes, passed=False),
    }
    bars: dict[str, list[dict]] = {}
    missing_indexes = {days.index(days[-2]), days.index(days[-1])}
    for code in passed_codes + rejected_codes:
        rows: list[dict] = []
        for index, day in enumerate(days):
            # 1 日 horizon 的退出 bar 始终有效；部分 code 的 3 日退出 bar
            # 故意缺失（旧数据缺 bar），主 horizon 为 None 但 1 日仍有效。
            if index in missing_indexes and code not in with_primary:
                continue
            rows.append(_bar(day, 10.0 + index * 0.1, 10.1 + index * 0.1))
        bars[code] = rows

    payload = build_scan_quality(
        date_from=days[0], date_to=days[-1],
        contributions_by_date=contributions,
        calendar=days, bars=bars,
    )

    baseline = payload["baseline"]
    # 6 个 code（3 通过 + 3 否决）的 3 日 horizon 有效，各出现 2 天；
    # 其余记录主 horizon 为 None，但 1 日样本不能被丢弃。
    assert baseline["valid"] == 12
    assert baseline["by_horizon"]["1"]["valid"] == 36
    assert baseline["by_horizon"]["3"]["valid"] == 12
    passed = payload["groups"]["by_gate"]["passed"]
    assert passed["by_horizon"]["1"]["valid"] == 18
    assert passed["valid"] == 6
    # bootstrap 只接收主 horizon 有效有限收益，不把 None 当 0。
    spread = payload["spread"]
    assert spread is not None
    assert spread["bootstrap"]["status"] == "ok"
    assert spread["value"] is not None


def test_scan_quality_route_read_failure_is_explicit_not_empty():
    class BrokenScans:
        def list_scan_dates(self, *args, **kwargs):
            raise RuntimeError("db locked")

        def list_scan_stock_contributions_by_date(self, *args, **kwargs):
            return []

    client = _make_client(
        scan_repository=BrokenScans(),
        local_bars_loader=lambda *a, **k: {"rows": {}, "truncated": False},
    )
    response = client.get(
        "/api/research/scan-quality?date_from=2026-09-01&date_to=2026-09-30")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "insufficient_data"
    assert body["reason"] == "scan_read_error"
    assert any("db locked" in item for item in body["read_errors"])


def test_scan_quality_truncated_reason_is_explicit():
    payload = build_scan_quality(
        date_from="2026-09-01", date_to="2026-09-30",
        contributions_by_date={}, calendar=["2026-09-01"], bars={},
        read_errors=["local_bars_truncated"],
        empty_reason="local_bars_truncated",
    )
    assert payload["status"] == "insufficient_data"
    assert payload["reason"] == "local_bars_truncated"
    assert payload["read_errors"] == ["local_bars_truncated"]


def test_accuracy_group_stats_ignore_none_returns():
    from web_ui.backend.presenters.accuracy import _group_stats

    stats = _group_stats([
        {"code": "600001", "snapshot_date": "2026-09-01", "group": "x",
         "net_return_pct": 1.0},
        {"code": "600002", "snapshot_date": "2026-09-01", "group": "x",
         "net_return_pct": None},
    ])
    assert stats["samples"] == 2
    assert stats["mean_return_pct"] == 1.0
    assert stats["date_equal_mean_return_pct"] == 1.0


# ─── KR2：增量回测作业（命令 / 单飞 / 403 / 只读状态） ────────────────────


class _FakeProc:
    def __init__(self, gate: threading.Event | None = None) -> None:
        self.returncode = None
        self.gate = gate
        self.terminated = 0

    def communicate(self):
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if self.returncode is None:
            self.returncode = 0
        return "📊 重算完成: processed=2 completed=2", None

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = -15
        if self.gate is not None:
            self.gate.set()

    def kill(self):
        self.terminate()


def _wait_done(runner: ReviewJobRunner, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = runner.status()
        if status["state"] != "running":
            return status
        time.sleep(0.01)
    raise AssertionError("review runner 未在超时内完成")


def test_review_default_command_is_incremental_recompute_all():
    argv = default_command()
    assert argv[1:5] == ["-m", "dragon_quant", "review", "--source"]
    assert argv[5] == "v2"
    assert "--recompute-all" in argv
    assert "--force" not in argv


def test_review_runner_success_and_single_flight():
    gate = threading.Event()
    runner = ReviewJobRunner(popener=lambda *a, **k: _FakeProc(gate))
    snapshot = runner.start_incremental(trigger="manual")
    assert snapshot.mode == "incremental"
    with pytest.raises(ReviewJobBusy):
        runner.start_incremental(trigger="manual")
    gate.set()
    status = _wait_done(runner)
    assert status["state"] == "succeeded"
    assert status["last_run"]["exit_code"] == 0
    assert "重算完成" in status["last_run"]["output_tail"]


def test_review_job_routes_are_explicit_and_read_only_status():
    gate = threading.Event()
    runner = ReviewJobRunner(popener=lambda *a, **k: _FakeProc(gate))
    client = _make_client(review_runner=runner)

    # 只读状态绝不启动任务。
    status = client.get("/api/review/jobs/status")
    assert status.status_code == 200
    assert status.json()["state"] == "idle"

    created = client.post(
        "/api/review/jobs/incremental",
        headers={"origin": "http://localhost:8765"})
    assert created.status_code == 202, created.text
    assert created.json()["state"] == "running"

    busy = client.post("/api/review/jobs/incremental")
    assert busy.status_code == 409
    assert busy.json()["error"] == "job_running"
    assert "运行" in busy.json()["detail"]

    denied = client.post(
        "/api/review/jobs/incremental",
        headers={"origin": "https://evil.example"})
    assert denied.status_code == 403
    assert denied.json()["error"] == "invalid_origin"

    gate.set()
    _wait_done(runner)
    done = client.get("/api/review/jobs/status").json()
    assert done["state"] == "succeeded"


# ─── KR2：真实 run_review 的增量选择（partial / 严格缺失 / 已完成） ───────


def test_recompute_all_backfills_partial_and_missing_but_skips_completed(
        monkeypatch):
    from datetime import datetime, timezone, timedelta
    from types import SimpleNamespace

    from dragon_quant.review import main as review_main

    fixed_now = datetime(2026, 9, 11, 15, 30,
                         tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(review_main.clock, "now", lambda: fixed_now)
    calendar = {
        (datetime(2026, 7, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(90)
        if (datetime(2026, 7, 1) + timedelta(days=i)).weekday() < 5
        and (datetime(2026, 7, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        <= "2026-09-11"
    }
    monkeypatch.setattr(review_main, "_build_review_calendar",
                        lambda *a, **k: set(calendar))

    entries = [
        {"code": "600001", "name": "已完成", "trade_date": "2026-07-01",
         "source": "v2"},
        {"code": "600002", "name": "半成品", "trade_date": "2026-07-02",
         "source": "v2"},
        {"code": "600003", "name": "严格缺失", "trade_date": "2026-07-03",
         "source": "v2"},
    ]
    monkeypatch.setattr(review_main.dragon_store, "get_pending_dragons",
                        lambda **kwargs: list(entries))
    monkeypatch.setattr(
        review_main.strict_store, "list_statuses",
        lambda source="v2": {("2026-07-01", "600001"): "completed",
                             ("2026-07-02", "600002"): "partial"})
    saved: list[dict] = []
    monkeypatch.setattr(review_main.dragon_store, "save_dual_review",
                        lambda **kwargs: saved.append(kwargs))

    bars = []
    for offset in range(0, 60):
        day = (datetime(2026, 6, 20) + timedelta(days=offset))
        if day.weekday() >= 5:
            continue
        ts = int(day.replace(tzinfo=timezone.utc).timestamp() * 1000)
        price = 10.0 + offset * 0.02
        bars.append(SimpleNamespace(
            timestamp=ts, open=price, close=price + 0.2,
            high=price + 0.3, low=price - 0.1, volume=1e6, pct=1.0,
            chg=0.1, turnover=2.0, amount=1000))

    class _Provider:
        def get_kline(self, code, days=90, fq_type="normal"):
            return list(bars)

    results = review_main.run_review(
        recompute_all=True, verbose=False, provider=_Provider())

    # 已有非 partial 严格结果跳过；partial 与严格缺失都补算。
    assert sorted(item["code"] for item in results) == ["600002", "600003"]
    assert {call["code"] for call in saved} == {"600002", "600003"}
    assert all(call["strict"] is not None for call in saved)
    assert "--force" not in default_command()


@pytest.mark.parametrize("output, skipped", [
    ("✅ 没有需要重算的龙头记录（跳过已有严格 3 条）", 3),
    ("🔍 自动筛选：跳过 1 条（完整收益窗口尚未走完）\n✅ 没有待回测的龙头记录", None),
])
def test_review_empty_cli_output_is_a_known_zero_result(output, skipped):
    from web_ui.backend.review_runner import parse_review_summary

    summary = parse_review_summary(output)
    assert summary["processed"] == 0
    assert summary["completed"] == 0
    assert summary["failed"] == 0
    assert summary.get("skipped") == skipped
    assert parse_review_summary("命令正常退出但未取得业务结果") is None


def test_parse_review_summary_reads_existing_cli_block():
    output = (
        "[1/3] 600001 旧真龙A (入选 2026-07-01) ... 错误: K线拉取失败\n"
        "════════════════════════════════════════════════════════\n"
        "📊 重算完成: processed=3\n"
        "   completed=2\n"
        "   cannot_evaluate=0\n"
        "   failed=1\n"
        "   skipped=1（已有严格 0 / 未成熟 1）\n"
        "   汇总校验: processed == completed + cannot_evaluate + failed -> True\n"
    )
    summary = parse_review_summary(output)
    assert summary == {
        "processed": 3, "completed": 2, "cannot_evaluate": 0,
        "failed": 1, "skipped": 1, "skipped_existing": 0,
        "skipped_immature": 1,
    }
    assert parse_review_summary("") is None
    assert parse_review_summary("没有汇总输出") is None


def test_review_status_payload_exposes_business_summary():
    class _Runner:
        def status(self):
            return {
                "state": "succeeded",
                "last_run": {
                    "mode": "incremental", "trigger": "manual",
                    "started_at": "2026-11-05T22:00:00+08:00",
                    "finished_at": "2026-11-05T22:00:03+08:00",
                    "exit_code": 0,
                    "output_tail": "📊 重算完成: processed=2\n"
                                   "   completed=1\n   cannot_evaluate=0\n"
                                   "   failed=1\n   skipped=0\n",
                },
            }

    client = _make_client(review_runner=_Runner())
    payload = client.get("/api/review/jobs/status").json()
    assert payload["summary"]["processed"] == 2
    assert payload["summary"]["failed"] == 1


def test_run_review_provider_failure_is_not_reported_as_all_success(
        monkeypatch, capsys):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from dragon_quant.review import main as review_main

    fixed_now = datetime(2026, 9, 11, 15, 30,
                         tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(review_main.clock, "now", lambda: fixed_now)
    calendar = {
        (datetime(2026, 7, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(90)
        if (datetime(2026, 7, 1) + timedelta(days=i)).weekday() < 5
        and (datetime(2026, 7, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        <= "2026-09-11"
    }
    monkeypatch.setattr(review_main, "_build_review_calendar",
                        lambda *a, **k: set(calendar))
    entries = [
        {"code": "600001", "name": "失败票", "trade_date": "2026-07-01",
         "source": "v2"},
        {"code": "600002", "name": "成功票", "trade_date": "2026-07-02",
         "source": "v2"},
    ]
    monkeypatch.setattr(review_main.dragon_store, "get_pending_dragons",
                        lambda **kwargs: list(entries))
    monkeypatch.setattr(review_main.strict_store, "list_statuses",
                        lambda source="v2": {})
    monkeypatch.setattr(review_main.dragon_store, "save_dual_review",
                        lambda **kwargs: None)

    bars = []
    for offset in range(0, 60):
        day = datetime(2026, 6, 20) + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        ts = int(day.replace(tzinfo=timezone.utc).timestamp() * 1000)
        price = 10.0 + offset * 0.02
        bars.append(SimpleNamespace(
            timestamp=ts, open=price, close=price + 0.2,
            high=price + 0.3, low=price - 0.1, volume=1e6, pct=1.0,
            chg=0.1, turnover=2.0, amount=1000))

    class _Provider:
        def get_kline(self, code, days=90, fq_type="normal"):
            if code == "600001":
                raise RuntimeError("fixture provider failure")
            return list(bars)

    review_main.run_review(recompute_all=True, verbose=True,
                           provider=_Provider())
    output = capsys.readouterr().out
    summary = parse_review_summary(output)
    assert summary is not None
    assert summary["processed"] == 2
    assert summary["completed"] == 1
    assert summary["failed"] == 1
    # exit 0 的进程也可能有业务失败：不能把 succeeded 当全部成功。
    assert summary["failed"] > 0


# ─── 空区间 / 非法日期：正常空态与 400 ────────────────────────────────────


def test_empty_scan_range_is_normal_empty_not_error():
    class EmptyScans:
        def list_scan_dates(self, *args, **kwargs):
            return []

        def list_scan_stock_contributions_by_date(self, *args, **kwargs):
            return []

    client = _make_client(scan_repository=EmptyScans())
    response = client.get(
        "/api/research/scan-quality?date_from=2026-09-01&date_to=2026-09-30")
    assert response.status_code == 200
    assert response.json()["reason"] == "no_scan_in_range"
    invalid = client.get(
        "/api/research/scan-quality?date_from=2026-13-99&date_to=2026-09-30")
    assert invalid.status_code == 400
