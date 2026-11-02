"""premarket offline E2E（阶段 3 Task 5）。

真实 CLI 子进程 + 临时数据根 + 固定时钟 + 网络隔离。覆盖完整快照 golden、
缺 prep / 缺 auction、损坏、09:25 时点门禁、参数错误、空根零写入与 B1 help。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.seed import seed_db

E2E_ROOT = Path(__file__).resolve().parent
GOLDEN = E2E_ROOT / "golden" / "premarket" / "context.json"
NOW = "2026-10-30T09:26:00+08:00"
T = "2026-10-30"
T1 = "2026-10-29"

STATES = ("confirmed", "weakened", "invalidated", "unknown")
FROZEN = {
    "confirmed": ["execute_plan", "execute_partial"],
    "weakened": ["downgrade_half", "wait_pullback"],
    "invalidated": ["cancel_entry", "exit_via_sell_rules"],
    "unknown": ["observe_only"],
}


def runner():
    from dragon_quant.tests.e2e.harness import runner as module

    return module


@contextmanager
def sandbox(case_id: str, *, now: str = NOW):
    box = EnvSandbox.for_case(case_id, now=now)
    try:
        yield box
    finally:
        box.cleanup()


def _candidate(code: str, *, role: str = "follower", position=0.04,
               expectations=True) -> dict:
    return {
        "code": code,
        "name": f"股票{code}",
        "role": role,
        "mainline": {"board_name": "机器人", "rank": 1,
                     "state": "strengthening"},
        "review_score": 70.3,
        "review_grade": "B",
        "confidence": 0.7,
        "risk_level": "low",
        "eligibility": "eligible",
        "position_pct": position,
        "position_basis": ["factor", "value"],
        "reason_code": None,
        "gate_trace": [{"gate": "G1_hard_risk", "evaluated": True,
                        "passed": True, "reason_code": None,
                        "detail": "risk_level=low"}],
        "warnings": [],
        "expectations": ({"gap_low": -0.02, "gap_high": 0.04,
                          "ratio_low": 0.8, "seal_by": "10:00"}
                         if expectations else None),
    }


def _plan(code: str, *, expectations=True) -> dict:
    if not expectations:
        return {"code": code, "role": None, "position_pct": None,
                "expected": None, "conditions": [],
                "allowed_actions": ["observe_only"],
                "unknown_reason": "expectations_unavailable"}
    return {
        "code": code,
        "role": "leader",
        "position_pct": 0.04,
        "expected": {"gap_low": -0.02, "gap_high": 0.04,
                     "ratio_low": 0.8, "seal_by": "10:00"},
        "conditions": [
            {"state": state, "requires": ["condition"],
             "allowed_actions": list(FROZEN[state])}
            for state in STATES],
        "allowed_actions": list(FROZEN["confirmed"]),
        "unknown_reason": None,
    }


def prep_result() -> dict:
    candidates = [_candidate("600001", role="leader"),
                  _candidate("600002", role="vanguard", position=0.0)]
    return {
        "trade_date": T1,
        "version": "0.8",
        "sentiment": {"up_cnt": 2100, "down_cnt": 950, "limit_up_cnt": 62,
                      "limit_down_cnt": 3, "broken_rate": 0.016,
                      "yesterday_premium_pct": 5.67, "promotion_rate": 0.667,
                      "max_board_height": 4},
        "main_boards": [],
        "macro": {"available": False},
        "candidates": [
            {"code": "600001", "name": "股票600001",
             "facts": {"limit_up_type": "换手板", "open_num": 0}},
            {"code": "600002", "name": "股票600002",
             "facts": {"limit_up_type": "T字板", "open_num": 2}},
        ],
        "dragon_source": {"window_days": 5, "dates": []},
        "review": {
            "enabled": True,
            "review_version": "review_v1",
            "config_version": "review_baseline_v1",
            "config_hash": "cfg-hash",
            "market": {"state": "strong", "score": 72.0, "coeff": 1.0,
                       "evidence": ["涨停 62 家"], "unknown_reason": None},
            "mainlines": [{"board_name": "机器人", "state": "strengthening",
                           "role_map": {"leader": "600001",
                                        "vanguard": "600002"}}],
            "anchors": {
                "positive": [{"code": "600001", "reason": "市场最高 4 板"}],
                "negative": [{"code": "600010",
                              "reason": "昨日涨停今日 -8.0%"}],
                "risk": {"limit_down_cnt": 3, "broken_rate": 0.016},
            },
            "portfolio": {"sum_pct": 4.0, "warning": None},
            "degraded": [],
        },
        "review_candidates": candidates,
        "strategy_context": {
            "context_version": "sc_v1",
            "trade_date": T1,
            "frozen_at": f"{T1}T16:40:00+08:00",
            "positions": [],
            "feedback_plans": [_plan("600001"), _plan("600002")],
        },
    }


def auction_analysis(code: str) -> dict:
    return {
        "version": "2",
        "code": code,
        "is_weak": False,
        "strength": 69.9,
        "level": "中",
        "direction": "neutral",
        "rank": 1,
        "dims": {},
        "reject_reason": None,
        "unverified": False,
        "rank_reason": None,
        "pre_veto_strength": 69.9,
        "validation_status": "baseline_unvalidated",
        "config_version": "auction_v2_baseline",
        "config_hash": "auction-hash",
        "semantics": {"semantics_version": "auction_semantics_v1"},
        "data_quality": {"status": "ok", "completeness": 1.0,
                         "reason_codes": [], "reasons": []},
        "risk_events": [],
        "raw_factors": {"official_open_gap_pct": 4.0,
                        "auction_turnover_pct": 6.56},
        "evidence": {"last_time": "09:25:00", "official_open_px": 10.4},
        "reference_prices": {"prev_close": 10.0, "official_open": 10.4},
        "windows": {"core": ["09:20:00", "09:25:00"]},
        "actions": [{"kind": "await_confirmation", "requires": "post_open",
                     "text": "若…则观察…"}],
    }


def assets_spec(*, prep_rows=None, auction_rows=None, dragon_rows=None) -> dict:
    return {"tables": {
        "prep_snapshots": {
            "columns": {"trade_date": "TEXT NOT NULL",
                        "source": "TEXT NOT NULL",
                        "result_json": "TEXT NOT NULL",
                        "created_at": "TEXT"},
            "primary_key": ["trade_date", "source"],
            "rows": list(prep_rows or []),
        },
        "auction_snapshots": {
            "columns": {"trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
                        "source": "TEXT NOT NULL",
                        "analysis_json": "TEXT NOT NULL", "created_at": "TEXT"},
            "primary_key": ["trade_date", "code", "source"],
            "rows": list(auction_rows or []),
        },
        "dragons": {
            "columns": {"trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL"},
            "primary_key": ["trade_date", "code"],
            "rows": list(dragon_rows or []),
        },
    }}


def prep_row(result: dict | str = None) -> dict:
    text = result if isinstance(result, str) else json.dumps(
        prep_result() if result is None else result)
    return {"trade_date": T1, "source": "v2", "result_json": text,
            "created_at": f"{T1} 16:52:11"}


def auction_row(code: str, analysis: dict | str = None,
                created_at: str = "2026-10-30 09:26:01") -> dict:
    text = analysis if isinstance(analysis, str) else json.dumps(
        auction_analysis(code) if analysis is None else analysis)
    return {"trade_date": T, "code": code, "source": "v2",
            "analysis_json": text, "created_at": created_at}


def seed_full(box: EnvSandbox) -> None:
    seed_db(box, assets_spec(
        prep_rows=[prep_row()],
        auction_rows=[auction_row("600001", created_at="2026-10-30 09:26:01"),
                      auction_row("600002", created_at="2026-10-30 09:26:04"),
                      auction_row("600099", created_at="2026-10-30 09:26:05")],
        dragon_rows=[{"trade_date": T1, "code": "600001"}],
    ), db_name="assets.db")


def run_premarket(box: EnvSandbox, *args: str, timeout: int = 120):
    return runner().run_cli(["premarket", *args], sandbox=box,
                            timeout=timeout)


def _json(result) -> dict:
    return json.loads(result.stdout)


def _db_stats(box: EnvSandbox) -> tuple[list[str], dict[str, int]]:
    path = box.data_dir / "assets.db"
    if not path.exists():
        return [], {}
    conn = sqlite3.connect(str(path))
    try:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {table: int(conn.execute(
            f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}
        return tables, counts
    finally:
        conn.close()


# ─── cases ─────────────────────────────────────────────────────────────────


def test_pm1_full_context_matches_reviewed_golden():
    with sandbox("pm1") as box:
        seed_full(box)
        result = run_premarket(box, "--date", "20261030", "--json")
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        payload = _json(result)
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        assert payload == golden
        assert payload["context_version"] == "premarket_sc_v1"
        assert payload["prep"]["trade_date"] == T1
        assert payload["auction"]["state"] == "ready"
        assert payload["data_quality"]["status"] == "ok"
        codes = [row["code"] for row in payload["candidates"]]
        assert codes == ["600001", "600002"]
        assert payload["auction"]["unplanned_codes"] == ["600099"]
        assert payload["candidates"][0]["action_ceiling"]["status"] == "wait"
        assert payload["candidates"][1]["action_ceiling"]["status"] == \
            "no_new_position"


def test_pm2_missing_prep_degrades_exit_zero():
    with sandbox("pm2") as box:
        seed_db(box, assets_spec(
            prep_rows=[], auction_rows=[auction_row("600001")],
            dragon_rows=[{"trade_date": T1, "code": "600001"}],
        ), db_name="assets.db")
        before = _db_stats(box)
        result = run_premarket(box, "--date", "20261030", "--json")
        assert result.returncode == 0, result.stderr
        payload = _json(result)
        assert payload["prep"]["state"] == "missing"
        assert payload["prep"]["available"] is False
        assert payload["data_quality"]["plan_usable"] is False
        assert "prep_missing" in payload["data_quality"]["degraded"]
        assert payload["candidates"] == []
        assert _db_stats(box) == before


def test_pm3_missing_auction_degrades_exit_zero():
    with sandbox("pm3") as box:
        seed_db(box, assets_spec(
            prep_rows=[prep_row()], auction_rows=[],
            dragon_rows=[{"trade_date": T1, "code": "600001"}],
        ), db_name="assets.db")
        before = _db_stats(box)
        result = run_premarket(box, "--date", "20261030", "--json")
        assert result.returncode == 0, result.stderr
        payload = _json(result)
        assert payload["auction"]["state"] == "missing"
        assert payload["auction"]["available"] is False
        assert "auction_missing" in payload["data_quality"]["degraded"]
        by_code = {row["code"]: row for row in payload["candidates"]}
        assert by_code["600001"]["action_ceiling"]["status"] == "observe_only"
        assert by_code["600001"]["action_ceiling"]["blocked_by"] == \
            ["auction_unavailable"]
        assert _db_stats(box) == before


def test_pm4_corrupt_rows_are_structured_exit_zero():
    with sandbox("pm4") as box:
        seed_db(box, assets_spec(
            prep_rows=[prep_row("{not-json")],
            auction_rows=[auction_row("600001"),
                          auction_row("600002", "{bad-json")],
            dragon_rows=[],
        ), db_name="assets.db")
        before = _db_stats(box)
        result = run_premarket(box, "--date", "20261030", "--json")
        assert result.returncode == 0, result.stderr
        payload = _json(result)
        assert payload["prep"]["state"] == "corrupt"
        assert payload["auction"]["state"] == "partial"
        assert payload["auction"]["corrupt_codes"] == ["600002"]
        degraded = payload["data_quality"]["degraded"]
        assert "prep_corrupt" in degraded
        assert "auction_corrupt_rows" in degraded
        assert _db_stats(box) == before


def test_pm5_before_0925_not_ready_then_ready():
    with sandbox("pm5a", now="2026-10-30T09:10:00+08:00") as box:
        seed_full(box)
        early = run_premarket(box, "--date", "20261030", "--json")
        assert early.returncode == 0, early.stderr
        payload = _json(early)
        assert payload["auction"]["state"] == "not_ready"
        assert payload["auction"]["pre_ready_rows_present"] is True
        assert payload["data_quality"]["auction_usable"] is False
        assert "auction_not_ready_before_0925" in \
            payload["data_quality"]["degraded"]
        for candidate in payload["candidates"]:
            assert candidate["action_ceiling"]["status"] != "wait"
        by_code = {row["code"]: row for row in payload["candidates"]}
        assert by_code["600001"]["action_ceiling"]["status"] == "observe_only"
        assert by_code["600001"]["action_ceiling"]["blocked_by"] == \
            ["auction_unavailable"]

    with sandbox("pm5b", now="2026-10-30T09:26:00+08:00") as box:
        seed_full(box)
        late = run_premarket(box, "--date", "20261030", "--json")
        assert late.returncode == 0, late.stderr
        assert _json(late)["auction"]["state"] == "ready"


def test_pm6_invalid_and_future_dates_exit_two():
    with sandbox("pm6") as box:
        invalid = run_premarket(box, "--date", "20261301")
        assert invalid.returncode == 2
        assert invalid.stdout == ""
        assert "非法日期" in invalid.stderr

        future = run_premarket(box, "--date", "20261101", "--json")
        assert future.returncode == 2
        payload = _json(future)
        assert payload["ok"] is False
        assert "未来日期" in payload["error"]
        assert not (box.data_dir / "assets.db").exists()


def test_pm7_empty_root_reads_without_writes():
    with sandbox("pm7") as box:
        result = run_premarket(box, "--date", "20261030", "--json")
        assert result.returncode == 0, result.stderr
        payload = _json(result)
        assert payload["ok"] is True
        assert payload["prep"]["state"] == "missing"
        assert payload["auction"]["state"] == "missing"
        assert payload["data_quality"]["status"] == "unavailable"
        assert not (box.data_dir / "assets.db").exists()
        assert not (box.data_dir / "assets.db-wal").exists()
        assert not (box.data_dir / "assets.db-shm").exists()
        # 只允许 CLI 入口的 tmdx 可用性提示；不得有任何取数调用。
        if box.call_log.exists():
            records = [json.loads(line) for line in
                       box.call_log.read_text(encoding="utf-8").splitlines()
                       if line.strip()]
            assert all(record.get("method") == "warn_if_unavailable"
                       for record in records), records


def test_pm8_help_creates_nothing():
    with sandbox("pm8") as box:
        result = runner().run_cli(["premarket", "-h"], sandbox=box)
        assert result.returncode == 0, result.stderr
        for token in ("--date", "--json", "Usage:"):
            assert token in result.stdout
        assert not (box.data_dir / "assets.db").exists()
        assert not (box.data_dir / "dragon.db").exists()
        assert not box.call_log.exists()
