"""M1B review behavior baseline: R1–R8 (R9–R11 are Web/M1F).

The fixture seeds three pending ``dragons_v2`` rows (completed / no_entry /
error), runs the real ``review`` CLI against fake Xueqiu klines, and pins the
exact buy price / 5-day return / drawdown / hold days / VPA rows.  Web server
branches (``--ui`` / ``--ui-only``) and the write-failure warning (R11) belong
to M1F and are deliberately not implemented here.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from dragon_quant.tests.e2e.harness.assertlib import (
    assert_exit,
    sqlite_count,
    sqlite_rows,
)
from dragon_quant.tests.e2e.harness.canonical import assert_json_equal, floats_close
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.seed import seed_db

E2E_ROOT = Path(__file__).resolve().parent
SCENARIO = E2E_ROOT / "fixtures" / "scenario_review.json"
GOLDEN = E2E_ROOT / "golden" / "review"
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


def run_review(box: EnvSandbox, args: list[str], *,
               scenario: Path = SCENARIO, timeout: int = 120,
               clock_mode: str = "runtime"):
    overrides: dict[str, str] = {"DQ_E2E_SCENARIO": str(scenario),
                                 "DQ_E2E_CLOCK_MODE": clock_mode}
    return runner().run_cli(
        args, sandbox=box, timeout=timeout, env_override=overrides)


def golden(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def dragon_row(code: str, trade_date: str, *, status: str = "pending",
               source: str = "v2") -> dict:
    return {
        "trade_date": trade_date, "code": code, "name": f"名字{code}",
        "scan_id": f"v2_{trade_date.replace('-', '')}_5", "rank": 1,
        "composite_score": 70.0, "board_count": 2, "open_px": 10.0,
        "close_px": 10.5, "high_px": 10.8, "low_px": 9.9, "pct": 5.0,
        "turnover_rate": 3.0, "amount": 10_000_000.0,
        "market_cap": 1_000_000_000.0, "concepts_json": '["测试"]',
        "report_text": "report", "is_true_dragon": 1, "version": "0.7.2",
        "created_at": f"{trade_date} 15:30:00", "buy_date": None,
        "buy_price": None, "max_return_5d": None, "max_drawdown_5d": None,
        "max_return_hold_days": None, "review_status": status, "source": source,
    }


def base_seed(extra: list[dict] | None = None) -> dict:
    rows = [
        dragon_row("600001", "2026-08-17"),
        dragon_row("600002", "2026-08-21"),
        dragon_row("600003", "2026-08-28"),
    ]
    rows.extend(extra or [])
    return {"tables": {
        "dragons_v2": {
            "columns": DRAGON_COLUMNS, "primary_key": ["trade_date", "code"],
            "rows": rows,
        },
        "dragons_v1": {
            "columns": DRAGON_COLUMNS, "primary_key": ["trade_date", "code"],
            "rows": [dragon_row("600001", "2026-08-17")],
        },
    }}


def seed_sandbox(box: EnvSandbox, spec: dict) -> None:
    seed_db(box, spec)
    mirror = {"tables": {"dragons": {
        "columns": DRAGON_COLUMNS, "primary_key": ["trade_date", "code"],
        "rows": spec["tables"]["dragons_v2"]["rows"],
    }}}
    seed_db(box, mirror, db_name="assets.db")


def review_fields(box: EnvSandbox) -> list[dict]:
    rows = []
    for row in sqlite_rows(box.data_dir / "dragon.db", "dragons_v2",
                           order_by="trade_date"):
        rows.append({key: row[key] for key in (
            "trade_date", "code", "review_status", "buy_date", "buy_price",
            "max_return_5d", "max_drawdown_5d", "max_return_hold_days")})
    return rows


def mirror_fields(box: EnvSandbox) -> list[dict]:
    return [{key: row[key] for key in (
        "trade_date", "code", "review_status", "buy_date", "buy_price",
        "max_return_5d", "max_drawdown_5d", "max_return_hold_days")}
        for row in sqlite_rows(box.data_dir / "assets.db", "dragons",
                               order_by="trade_date")]


def vpa_fields(box: EnvSandbox) -> list[dict]:
    rows = []
    for row in sqlite_rows(box.data_dir / "dragon.db", "vpa_analysis",
                           order_by="trade_date"):
        factors = json.loads(row["factors_json"])
        rows.append({
            "trade_date": row["trade_date"], "code": row["code"],
            "source": row["source"], "signal": row["signal"],
            "health_score": row["health_score"],
            "factors": [[item["name"], item["signal"], item["score"]]
                        for item in factors],
        })
    return rows


def strict_fields(box: EnvSandbox) -> list[dict]:
    return [{key: row[key] for key in (
        "trade_date", "code", "buy_date", "buy_price", "max_return_5d",
        "max_drawdown_5d", "max_return_hold_days", "review_status",
        "reason")}
        for row in sqlite_rows(box.data_dir / "dragon.db",
                               "dragon_review_strict", order_by="trade_date")]


def assert_strict_fields(actual: list[dict], expected: list[dict]) -> None:
    assert len(actual) == len(expected), actual
    for got, want in zip(actual, expected):
        for key in ("trade_date", "code", "review_status", "reason"):
            assert got[key] == want[key], (key, got, want)
        for key in ("buy_price", "max_return_5d", "max_drawdown_5d"):
            if want[key] is None:
                assert got[key] is None, (key, got, want)
            else:
                assert floats_close(got[key], want[key], rel=0, abs_tol=1e-9)
        assert got["buy_date"] == want["buy_date"]
        assert got["max_return_hold_days"] == want["max_return_hold_days"]


def assert_review_fields(actual: list[dict], expected: list[dict]) -> None:
    assert len(actual) == len(expected), actual
    for got, want in zip(actual, expected):
        for key in ("trade_date", "code", "review_status"):
            assert got[key] == want[key], (key, got, want)
        for key in ("buy_price", "max_return_5d", "max_drawdown_5d"):
            if want[key] is None:
                assert got[key] is None, (key, got, want)
            else:
                assert floats_close(got[key], want[key], rel=0, abs_tol=1e-9)
        assert got["buy_date"] == want["buy_date"]
        assert got["max_return_hold_days"] == want["max_return_hold_days"]


# ─── R1 completed / no_entry / error in one run ─────────────────────────────


def test_r1_three_states_persist_exactly_and_write_vpa():
    expected = golden("db.json")
    with sandbox("r1") as box:
        seed_sandbox(box, base_seed())
        result = run_review(box, ["review"])
        assert_exit(result, 0)
        assert result.stderr == ""

        lines = result.stdout.splitlines()
        stdout_golden = golden("stdout_lines.json")
        assert lines[0] == stdout_golden["header"]
        for line in stdout_golden["progress"]:
            assert line in lines, line
        for line in stdout_golden["summary"]:
            assert line in lines, line

        assert_review_fields(review_fields(box), expected["dragons"])
        assert_json_equal(vpa_fields(box), expected["vpa"])
        assert_review_fields(mirror_fields(box), expected["mirror"])
        assert_strict_fields(strict_fields(box), expected["strict"])

        assert sqlite_count(box.data_dir / "dragon.db", "dragons_v2") == 3
        assert sqlite_count(box.data_dir / "dragon.db", "vpa_analysis") == 2
        assert sqlite_count(box.data_dir / "dragon.db",
                            "dragon_review_strict") == 2
        for table in ("buy_sell_signals", "review_account_runs"):
            assert sqlite_count(box.data_dir / "dragon.db", table) == 0


# ─── R2 no_entry ────────────────────────────────────────────────────────────


def test_r2_no_entry_keeps_null_contract():
    with sandbox("r2") as box:
        seed_sandbox(box, base_seed())
        result = run_review(box, ["review", "--date", "20260821"])
        assert_exit(result, 0)
        assert "共 1 条待处理" in result.stdout
        assert "无可介入日 ❌" in result.stdout
        rows = {row["code"]: row for row in review_fields(box)}
        no_entry = rows["600002"]
        assert no_entry["review_status"] == "no_entry"
        for key in ("buy_date", "buy_price", "max_return_5d",
                    "max_drawdown_5d", "max_return_hold_days"):
            assert no_entry[key] is None, key
        assert rows["600001"]["review_status"] == "pending"
        assert rows["600003"]["review_status"] == "pending"
        # 严格口径独立输出：600002 次日（08-24）一字涨停仍按开盘价 11.0 成交。
        strict = {row["code"]: row for row in strict_fields(box)}
        assert set(strict) == {"600002"}
        assert strict["600002"]["review_status"] == "completed"
        assert strict["600002"]["buy_date"] == "2026-08-24"
        assert floats_close(strict["600002"]["buy_price"], 11.0, rel=0,
                            abs_tol=1e-9)
        assert floats_close(strict["600002"]["max_return_5d"], 0.0, rel=0,
                            abs_tol=1e-9)
        assert floats_close(strict["600002"]["max_drawdown_5d"], 0.0, rel=0,
                            abs_tol=1e-9)
        assert strict["600002"]["max_return_hold_days"] == 2


# ─── R3 provider error is isolated ──────────────────────────────────────────


def test_r3_provider_error_isolates_one_stock():
    with sandbox("r3") as box:
        seed_sandbox(box, base_seed())
        result = run_review(box, ["review", "--date", "20260828"])
        assert_exit(result, 0)
        assert "K线拉取失败" in result.stdout
        assert "RuntimeError: 模拟K线故障" in result.stdout
        rows = {row["code"]: row for row in review_fields(box)}
        assert rows["600003"]["review_status"] == "error"
        assert rows["600003"]["buy_date"] is None
        assert rows["600001"]["review_status"] == "pending"
        assert rows["600002"]["review_status"] == "pending"
        # 传输失败不是 cannot_evaluate：不写严格结果。
        assert sqlite_count(box.data_dir / "dragon.db",
                            "dragon_review_strict") == 0


# ─── R4 no pending rows ─────────────────────────────────────────────────────


def test_r4_no_pending_dragons_writes_nothing():
    with sandbox("r4") as box:
        result = run_review(box, ["review"])
        assert_exit(result, 0)
        assert "✅ 没有待回测的龙头记录" in result.stdout
        assert sqlite_count(box.data_dir / "dragon.db", "dragons_v2") == 0
        assert sqlite_count(box.data_dir / "dragon.db", "vpa_analysis") == 0


# ─── R5 automatic 5–20 trading-day window ───────────────────────────────────


def test_r5_auto_window_skips_recent_and_processes_stale_rows():
    extra = [dragon_row("600004", "2026-09-10"),
             dragon_row("600005", "2026-07-01")]
    with sandbox("r5") as box:
        seed_sandbox(box, base_seed(extra))
        result = run_review(box, ["review"])
        assert_exit(result, 0)
        assert "🔍 自动筛选：跳过 1 条（距当日不足 5 个交易日）" in result.stdout
        assert "共 4 条待处理" in result.stdout
        rows = {row["code"]: row for row in review_fields(box)}
        assert rows["600001"]["review_status"] == "completed"
        assert rows["600002"]["review_status"] == "no_entry"
        assert rows["600003"]["review_status"] == "error"
        # 超过 20 个交易日的旧记录不再被丢弃（但 fixture 无 600005 K 线 → 传输错误）。
        assert rows["600005"]["review_status"] == "error"
        assert rows["600004"]["review_status"] == "pending"


# ─── R6 explicit --date ─────────────────────────────────────────────────────


def test_r6_date_filter_processes_only_that_day():
    with sandbox("r6") as box:
        seed_sandbox(box, base_seed())
        result = run_review(box, ["review", "--date", "20260821"])
        assert_exit(result, 0)
        assert "共 1 条待处理" in result.stdout
        rows = {row["code"]: row for row in review_fields(box)}
        assert rows["600002"]["review_status"] == "no_entry"
        assert rows["600001"]["review_status"] == "pending"
        assert rows["600003"]["review_status"] == "pending"


# ─── R7 --force recomputes a completed row ──────────────────────────────────


def test_r7_force_recomputes_completed_row():
    with sandbox("r7") as box:
        seed_sandbox(box, base_seed())
        assert_exit(run_review(box, ["review"]), 0)
        before = {row["code"]: row for row in review_fields(box)}
        strict_before = {row["code"]: row for row in strict_fields(box)}
        assert before["600001"]["max_return_5d"] == 8.0
        assert strict_before["600001"]["max_return_5d"] == 6.93

        scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
        # Raise the 08-21 high to 11.00 so max_return_5d becomes 10.00.
        for bar in scenario["providers"]["xueqiu"]["get_kline"]["by_code"]["600001"]:
            if bar["pct"] == 2.0 and bar["high"] == 10.8:
                bar["high"] = 11.0
                bar["close"] = 11.0
        variant = box.root / "scenario_review_force.json"
        variant.write_text(json.dumps(scenario, ensure_ascii=False),
                           encoding="utf-8")

        result = run_review(box, ["review", "--date", "20260817", "--force"],
                            scenario=variant)
        assert_exit(result, 0)
        after = {row["code"]: row for row in review_fields(box)}
        strict_after = {row["code"]: row for row in strict_fields(box)}
        assert after["600001"]["review_status"] == "completed"
        assert after["600001"]["buy_date"] == "2026-08-18"
        assert floats_close(after["600001"]["buy_price"], 10.0, rel=0,
                            abs_tol=1e-9)
        assert floats_close(after["600001"]["max_return_5d"], 10.0, rel=0,
                            abs_tol=1e-9)
        assert after["600001"]["max_return_hold_days"] == 3
        # --force 同时覆盖严格口径（开盘价 10.1，峰值 11.0）。
        assert strict_after["600001"]["buy_date"] == "2026-08-18"
        assert floats_close(strict_after["600001"]["buy_price"], 10.1, rel=0,
                            abs_tol=1e-9)
        assert floats_close(strict_after["600001"]["max_return_5d"], 8.91,
                            rel=0, abs_tol=1e-9)
        assert floats_close(strict_after["600001"]["max_drawdown_5d"], -4.95,
                            rel=0, abs_tol=1e-9)
        assert strict_after["600001"]["max_return_hold_days"] == 3
        assert strict_after["600002"] == strict_before["600002"]
        for code in ("600002", "600003"):
            assert after[code] == before[code]


# ─── R8 source v1 isolation ─────────────────────────────────────────────────


def test_r8_source_v1_updates_v1_only():
    with sandbox("r8") as box:
        seed_sandbox(box, base_seed())
        v2_before = review_fields(box)
        result = run_review(box, ["review", "--date", "20260817",
                                  "--source", "v1"])
        assert_exit(result, 0)
        assert "龙头回测 [v1] — 共 1 条待处理" in result.stdout
        v1_rows = sqlite_rows(box.data_dir / "dragon.db", "dragons_v1")
        assert len(v1_rows) == 1
        v1 = v1_rows[0]
        assert v1["review_status"] == "completed"
        assert v1["buy_date"] == "2026-08-18"
        assert floats_close(v1["buy_price"], 10.0, rel=0, abs_tol=1e-9)
        assert floats_close(v1["max_return_5d"], 8.0, rel=0, abs_tol=1e-9)
        assert review_fields(box) == v2_before
        # v1 不写严格口径。
        assert sqlite_count(box.data_dir / "dragon.db",
                            "dragon_review_strict") == 0


# ─── R12: single runtime-clock seam (M9C) ───────────────────────────────────


def test_r12_runtime_clock_mode_drives_the_auto_window():
    """Runtime-clock mode: the 5~20 window follows the injected clock, not real time."""
    extra = [dragon_row("600004", "2026-09-10"),
             dragon_row("600005", "2026-07-01")]
    with sandbox("r12") as box:
        seed_sandbox(box, base_seed(extra))
        result = run_review(box, ["review"], clock_mode="runtime")
        assert_exit(result, 0)
        assert result.stderr == ""

        payload = json.loads(
            (box.root / "preflight.json").read_text(encoding="utf-8"))
        marker = payload["injection"]
        assert marker["clock_mode"] == "runtime"
        assert marker["clock_impl"] == "runtime.clock"
        assert payload["datetime_is_replaced"] is False
        assert payload["datetime_class"] == "datetime.datetime"
        assert payload["time_is_replaced"] is False
        assert payload["time_function_name"] == "time"
        assert payload["datetime_isinstance_ok"] is True
        assert payload["runtime_clock_type"] == "FixedClock"
        assert payload["runtime_clock_now"].startswith("2026-09-11T15:30")

        assert "🔍 自动筛选：跳过 1 条（距当日不足 5 个交易日）" in result.stdout
        assert "共 4 条待处理" in result.stdout

        rows = {row["code"]: row for row in review_fields(box)}
        assert rows["600004"]["review_status"] == "pending"
        assert rows["600005"]["review_status"] == "error"
        expected = {row["code"]: row for row in golden("db.json")["dragons"]}
        ordered = sorted(expected)
        assert_review_fields([rows[code] for code in ordered],
                             [expected[code] for code in ordered])
        assert_json_equal(vpa_fields(box), golden("db.json")["vpa"])
        mirror = {row["code"]: row for row in mirror_fields(box)}
        assert_review_fields([mirror[code] for code in ordered],
                             [expected[code] for code in ordered])


# ─── R13: 严格口径 cannot_evaluate（次日零成交平盘占位）─────────────────────


def test_r13_strict_cannot_evaluate_on_flat_zero_volume_day():
    extra = [dragon_row("600006", "2026-08-26")]
    with sandbox("r13") as box:
        seed_sandbox(box, base_seed(extra))
        result = run_review(box, ["review", "--date", "20260826"])
        assert_exit(result, 0)

        loose = {row["code"]: row for row in review_fields(box)}
        assert loose["600006"]["review_status"] == "completed"
        assert loose["600006"]["buy_date"] == "2026-08-28"

        strict = {row["code"]: row for row in strict_fields(box)}
        assert set(strict) == {"600006"}
        assert strict["600006"]["review_status"] == "cannot_evaluate"
        assert strict["600006"]["reason"] == "停牌或数据缺失无法区分"
        assert strict["600006"]["buy_date"] is None
        assert strict["600006"]["buy_price"] is None


# ─── R14: --recompute-all --dry-run 只统计，不触网、不写库 ─────────────────


def provider_calls(box: EnvSandbox) -> list[dict]:
    if not box.call_log.exists():
        return []
    return [json.loads(line) for line in
            box.call_log.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_r14_recompute_all_dry_run_counts_without_fetch_or_write():
    with sandbox("r14") as box:
        seed_sandbox(box, base_seed())
        before = review_fields(box)
        result = run_review(box, ["review", "--recompute-all", "--dry-run"])
        assert_exit(result, 0)
        assert "候选记录: 3 条" in result.stdout
        assert "已有严格结果跳过: 0 条" in result.stdout
        assert "预计处理上界: 3 条" in result.stdout
        assert review_fields(box) == before
        assert sqlite_count(box.data_dir / "dragon.db",
                            "dragon_review_strict") == 0
        assert not [c for c in provider_calls(box)
                    if c.get("provider") == "xueqiu"
                    and c.get("method") == "get_kline"]


# ─── R15: --recompute-all --limit 只处理最老 N 条并可幂等续跑 ──────────────


def test_r15_recompute_all_limit_oldest_and_idempotent():
    with sandbox("r15") as box:
        seed_sandbox(box, base_seed())

        first = run_review(box, ["review", "--recompute-all", "--limit", "1"])
        assert_exit(first, 0)
        strict = strict_fields(box)
        assert [row["code"] for row in strict] == ["600001"]
        assert strict[0]["review_status"] == "completed"
        loose = {row["code"]: row for row in review_fields(box)}
        assert loose["600001"]["review_status"] == "completed"
        assert loose["600002"]["review_status"] == "pending"
        assert loose["600003"]["review_status"] == "pending"

        dry = run_review(box, ["review", "--recompute-all", "--dry-run"])
        assert_exit(dry, 0)
        assert "已有严格结果跳过: 1 条" in dry.stdout
        assert "预计处理上界: 2 条" in dry.stdout

        second = run_review(box, ["review", "--recompute-all", "--limit", "1"])
        assert_exit(second, 0)
        strict = strict_fields(box)
        assert [row["code"] for row in strict] == ["600001", "600002"]


# ─── R16: 非法参数组合退出 2 ───────────────────────────────────────────────


def test_r16_invalid_recompute_flag_combinations_exit_2():
    with sandbox("r16") as box:
        seed_sandbox(box, base_seed())
        cases = [
            ["review", "--dry-run"],
            ["review", "--limit", "1"],
            ["review", "--recompute-all", "--source", "v1"],
            ["review", "--recompute-all", "--date", "20260817"],
            ["review", "--recompute-all", "--top", "1"],
        ]
        for args in cases:
            result = run_review(box, args)
            assert_exit(result, 2)
            assert "review:" in result.stderr, args
        assert all(row["review_status"] == "pending"
                   for row in review_fields(box))


# ─── R17: recompute-all 聚合汇总区分 completed / failed / skipped ───────────


def test_r17_recompute_all_summary_separates_failed_and_skipped():
    with sandbox("r17") as box:
        seed_sandbox(box, base_seed())
        result = run_review(box, ["review", "--recompute-all"])
        assert_exit(result, 0)
        # 3 条全部处理：600001/600002 严格完成，600003 传输失败。
        assert "📊 重算完成: processed=3" in result.stdout
        assert "   completed=2" in result.stdout
        assert "   cannot_evaluate=0" in result.stdout
        assert "   failed=1" in result.stdout
        assert "   skipped=0" in result.stdout
        assert "汇总校验: processed == completed + cannot_evaluate + failed -> True" in result.stdout
        assert sqlite_count(box.data_dir / "dragon.db",
                            "dragon_review_strict") == 2
