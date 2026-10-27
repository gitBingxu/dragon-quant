"""M1C blacklist/dragons/vpa baseline: B1–B3, DR1–DR3, V1–V4.

All databases are synthetic and live inside the per-case sandbox.  Read-only
commands assert that no business rows or result files appear beyond the
documented import-time schema creation.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from dragon_quant.tests.e2e.harness.assertlib import (
    assert_exit,
    list_files,
    sqlite_count,
    sqlite_rows,
)
from dragon_quant.tests.e2e.harness.canonical import floats_close
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.seed import seed_db

E2E_ROOT = Path(__file__).resolve().parent
SCENARIO = E2E_ROOT / "fixtures" / "scenario_local.json"
GOLDEN = E2E_ROOT / "golden" / "local"
NOW = "2026-09-11T15:30:00+08:00"

DRAGON_COLUMNS = {
    "trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL", "name": "TEXT",
    "scan_id": "TEXT", "rank": "INTEGER", "composite_score": "REAL",
    "board_count": "INTEGER", "open_px": "REAL", "close_px": "REAL",
    "high_px": "REAL", "low_px": "REAL", "pct": "REAL",
    "turnover_rate": "REAL", "amount": "REAL", "market_cap": "REAL",
    "concepts_json": "TEXT", "report_text": "TEXT", "is_true_dragon": "INTEGER",
    "version": "TEXT", "created_at": "TEXT", "buy_date": "TEXT",
    "buy_price": "REAL", "max_return_5d": "REAL", "max_drawdown_5d": "REAL",
    "max_return_hold_days": "INTEGER", "review_status": "TEXT", "source": "TEXT",
}


def runner():
    from dragon_quant.tests.e2e.harness import runner as module

    return module


@contextmanager
def sandbox(case_id: str):
    box = EnvSandbox.for_case(case_id, now=NOW)
    try:
        yield box
    finally:
        box.cleanup()


def run_local(box: EnvSandbox, args: list[str], *, timeout: int = 120):
    return runner().run_cli(args, sandbox=box, timeout=timeout,
                            env_override={"DQ_E2E_SCENARIO": str(SCENARIO)})


def golden(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def _dragon_row(code: str, trade_date: str, name: str, rank: int,
                score: float, true: bool) -> dict:
    return {
        "trade_date": trade_date, "code": code, "name": name,
        "scan_id": f"v2_{trade_date.replace('-', '')}_5", "rank": rank,
        "composite_score": score, "board_count": 2 + rank,
        "open_px": 10.0 + rank, "close_px": 10.5 + rank,
        "high_px": 10.8 + rank, "low_px": 9.9 + rank, "pct": 5.0 + rank,
        "turnover_rate": 3.0, "amount": 10000000.0,
        "market_cap": 1000000000.0,
        "concepts_json": json.dumps(["测试", f"概念{rank}"], ensure_ascii=False),
        "report_text": f"报告{code}", "is_true_dragon": 1 if true else 0,
        "version": "0.7.2", "created_at": f"{trade_date} 15:30:00",
        "buy_date": None, "buy_price": None, "max_return_5d": None,
        "max_drawdown_5d": None, "max_return_hold_days": None,
        "review_status": "pending", "source": "v2",
    }


def dragons_seed() -> dict:
    return {"tables": {"dragons_v2": {
        "columns": DRAGON_COLUMNS,
        "primary_key": ["trade_date", "code"],
        "rows": [
            _dragon_row("600001", "2026-09-10", "甲公司", 1, 80.0, True),
            _dragon_row("600002", "2026-09-10", "乙公司", 2, 60.0, False),
            _dragon_row("600003", "2026-09-11", "丙公司", 1, 90.0, True),
            _dragon_row("600004", "2026-09-11", "丁公司", 2, 70.0, False),
            _dragon_row("600005", "2026-09-09", "戊公司", 1, 50.0, False),
        ],
    }}}


def assert_no_result_files(box: EnvSandbox) -> None:
    assert list_files(box.data_dir / "results") == []
    assert list_files(box.data_dir / "cache") == []
    assert not (box.data_dir / "assets.db").exists()


# ─── B1 list ────────────────────────────────────────────────────────────────


def test_b1_blacklist_list_empty_and_populated():
    with sandbox("b1") as box:
        empty = run_local(box, ["blacklist", "list"])
        assert_exit(empty, 0)
        assert empty.stdout == "黑名单为空\n"

        run_local(box, ["blacklist", "add", "次新股"])
        run_local(box, ["blacklist", "add", "ST板块"])
        listed = run_local(box, ["blacklist", "list"])
        assert_exit(listed, 0)
        lines = listed.stdout.splitlines()
        assert lines[0] == "板块黑名单（2 个）:"
        assert sorted(lines[1:]) == sorted(["  - 次新股", "  - ST板块"])


# ─── B2 add / remove ────────────────────────────────────────────────────────


def test_b2_blacklist_add_remove_is_idempotent_and_tolerates_missing():
    with sandbox("b2") as box:
        db = box.data_dir / "dragon.db"
        added = run_local(box, ["blacklist", "add", "次新股"])
        assert_exit(added, 0)
        assert added.stdout == "✅ 已加入黑名单: 次新股\n"
        assert [row["name"] for row in sqlite_rows(db, "sector_blacklist")] == ["次新股"]

        duplicate = run_local(box, ["blacklist", "add", "次新股"])
        assert_exit(duplicate, 0)
        assert duplicate.stdout == "✅ 已加入黑名单: 次新股\n"
        assert sqlite_count(db, "sector_blacklist") == 1

        removed = run_local(box, ["blacklist", "remove", "次新股"])
        assert_exit(removed, 0)
        assert removed.stdout == "✅ 已移除黑名单: 次新股\n"
        assert sqlite_count(db, "sector_blacklist") == 0

        missing = run_local(box, ["blacklist", "remove", "不存在板块"])
        assert_exit(missing, 0)
        assert missing.stdout == "✅ 已移除黑名单: 不存在板块\n"
        assert sqlite_count(db, "sector_blacklist") == 0


# ─── B3 group without action ────────────────────────────────────────────────


def test_b3_blacklist_without_action_equals_list():
    with sandbox("b3") as box:
        result = run_local(box, ["blacklist"])
        assert_exit(result, 0)
        assert result.stdout == "黑名单为空\n"
        assert result.stderr == ""


# ─── DR1 default latest date ────────────────────────────────────────────────


def test_dr1_default_exports_latest_trade_date_payload():
    expected = golden("dragons.json")["default"]
    with sandbox("dr1") as box:
        seed_db(box, dragons_seed())
        result = run_local(box, ["dragons"])
        assert_exit(result, 0)
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload == expected
        assert list(payload) == list(expected)
        assert_no_result_files(box)
        assert sqlite_count(box.data_dir / "dragon.db", "dragons_v2") == 5


# ─── DR2 --date / --true-only ───────────────────────────────────────────────


def test_dr2_date_formats_and_true_only_filter():
    expected = golden("dragons.json")
    with sandbox("dr2") as box:
        seed_db(box, dragons_seed())

        compact = run_local(box, ["dragons", "--date", "20260910"])
        assert_exit(compact, 0)
        assert json.loads(compact.stdout) == expected["date_0910"]

        dashed = run_local(box, ["dragons", "--date", "2026-09-10"])
        assert_exit(dashed, 0)
        assert dashed.stdout == compact.stdout

        true_only = run_local(
            box, ["dragons", "--date", "20260910", "--true-only"])
        assert_exit(true_only, 0)
        assert json.loads(true_only.stdout) == expected["true_only_0910"]

        latest_true = run_local(box, ["dragons", "--true-only"])
        assert_exit(latest_true, 0)
        assert json.loads(latest_true.stdout) == expected["true_only_latest"]


# ─── DR3 no data ────────────────────────────────────────────────────────────


def test_dr3_no_data_exits_one_with_error_json():
    with sandbox("dr3-empty") as box:
        result = run_local(box, ["dragons"])
        assert result.returncode == 1
        assert json.loads(result.stdout) == {"error": "无 v2 龙头记录"}

    with sandbox("dr3-date") as box:
        seed_db(box, dragons_seed())
        missing = run_local(box, ["dragons", "--date", "20260105"])
        assert missing.returncode == 1
        assert json.loads(missing.stdout) == {
            "error": "未找到 2026-01-05 的 v2 龙头记录"}

        filtered = run_local(
            box, ["dragons", "--date", "20260909", "--true-only"])
        assert filtered.returncode == 1
        assert json.loads(filtered.stdout) == {
            "error": "未找到 2026-09-09 的 v2 龙头记录"}


# ─── V1 vpa success ────────────────────────────────────────────────────────


ICON = {"bullish": "📈", "bearish": "📉", "neutral": "➖"}


def _factor_line(item: list) -> str:
    name, title, signal, score, note = item
    return f"- {ICON[signal]} {title}({score:.0f}): {note}"


def test_v1_vpa_success_renders_report_and_persists_row():
    expected = golden("vpa.json")
    with sandbox("v1") as box:
        result = run_local(box, ["vpa", "--code", "600001"])
        assert_exit(result, 0)
        assert result.stderr == ""
        lines = result.stdout.splitlines()
        assert lines[0] == expected["header"]
        assert lines[1] == "═" * 60
        factor_lines = [line for line in lines[2:] if line.startswith("- ")]
        assert factor_lines == [_factor_line(item) for item in expected["factors"]]
        evidence = [line for line in lines[2:] if line.startswith("    - ")]
        assert len(evidence) >= 8

        rows = sqlite_rows(box.data_dir / "dragon.db", "vpa_analysis")
        assert len(rows) == 1
        row = rows[0]
        assert row["trade_date"] == "2026-09-11"
        assert row["code"] == "600001"
        assert row["name"] == ""
        assert row["source"] == "xueqiu"
        assert floats_close(row["health_score"], expected["health_score"], rel=0, abs_tol=1e-12)
        assert row["signal"] == expected["signal"]
        assert row["summary"] == expected["summary"]
        assert row["version"] == "0.7.2"
        assert isinstance(row["created_at"], str)

        factors = json.loads(row["factors_json"])
        assert [item["name"] for item in factors] == [
            item[0] for item in expected["factors"]]
        assert [
            [item["name"], item["title"], item["signal"], item["score"], item["note"]]
            for item in factors
        ] == expected["factors"]
        assert list(factors[0]) == [
            "name", "title", "signal", "score", "note", "evidence", "details"]
        assert factors[0]["details"]["key_metric"] == "成交量/额"
        assert factors[0]["details"]["in_high_zone"] is False

        assert_no_result_files(box)


# ─── V2 --no-save ───────────────────────────────────────────────────────────


def test_v2_no_save_renders_but_does_not_write():
    expected = golden("vpa.json")
    with sandbox("v2") as box:
        result = run_local(box, ["vpa", "--code", "600001", "--no-save"])
        assert_exit(result, 0)
        assert result.stdout.splitlines()[0] == expected["header"]
        # B1: a read-only render never opens storage, so no dragon.db exists.
        assert not (box.data_dir / "dragon.db").exists()


# ─── V3 provider failure / insufficient data ────────────────────────────────


def test_v3_fallback_reports_keep_exit_zero_and_write_nothing():
    expected = golden("vpa.json")
    with sandbox("v3-error") as box:
        result = run_local(box, ["vpa", "--code", "600009"])
        assert_exit(result, 0)
        assert result.stdout.splitlines() == expected["fallback_600009"]
        # B1: fallback renders write nothing and create no database.
        assert not (box.data_dir / "dragon.db").exists()

    with sandbox("v3-short") as box:
        result = run_local(box, ["vpa", "--code", "600003"])
        assert_exit(result, 0)
        assert result.stdout.splitlines() == expected["fallback_600003"]
        assert not (box.data_dir / "dragon.db").exists()


# ─── V5 single runtime-clock seam (M9C) ─────────────────────────────────────


def test_v1b_vpa_cli_save_date_comes_from_the_runtime_clock():
    """CLI path: the persisted ``trade_date`` is the frozen runtime clock date."""
    with sandbox("v1b") as box:
        result = run_local(box, ["vpa", "--code", "600001"])
        assert_exit(result, 0)

        rows = sqlite_rows(box.data_dir / "dragon.db", "vpa_analysis")
        assert [row["trade_date"] for row in rows] == ["2026-09-11"]

        proof = json.loads((box.root / "preflight.json").read_text(encoding="utf-8"))
        assert proof["injection"]["clock_mode"] == "runtime"
        assert proof["injection"]["clock_impl"] == "runtime.clock"
        assert proof["datetime_is_replaced"] is False
        assert proof["time_is_replaced"] is False
        assert proof["datetime_isinstance_ok"] is True
        assert proof["runtime_clock_type"] == "FixedClock"


# ─── V4 missing --code ──────────────────────────────────────────────────────


def test_v4_missing_code_is_an_argparse_error():
    with sandbox("v4") as box:
        result = run_local(box, ["vpa"])
        assert result.returncode == 2
        assert result.stdout == ""
        assert "--code" in result.stderr
        assert "required" in result.stderr
