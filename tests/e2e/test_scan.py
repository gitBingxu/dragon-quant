"""M1B scan behavior baseline: G4/G5 and S1–S10.

Every case runs the real ``python -m dragon_quant`` subprocess through the M0
sandbox with fake providers, a frozen clock and a temporary data root.  The
golden files under ``tests/e2e/golden/scan`` are reviewed constants: tests only
compare against them and never regenerate or widen a tolerance.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path

from tests.e2e.harness.assertlib import (
    assert_exit,
    assert_file,
    assert_json_keys_in_order,
    assert_no_file,
    list_files,
    sqlite_count,
    sqlite_rows,
)
from tests.e2e.harness.canonical import assert_json_equal, floats_close
from tests.e2e.harness.sandbox import EnvSandbox
from tests.e2e.harness.seed import seed_db

REPO_ROOT = Path(__file__).resolve().parents[2]
E2E_ROOT = Path(__file__).resolve().parent
SCENARIO = E2E_ROOT / "fixtures" / "scenario_scan.json"
GOLDEN = E2E_ROOT / "golden" / "scan"
NOW = "2026-09-11T15:30:00+08:00"
INTRADAY = "2026-09-11T10:00:00+08:00"
BASE_ARGS = ["scan", "--top", "5", "--candidates", "3", "--workers", "1"]


def runner():
    from tests.e2e.harness import runner as module

    return module


@contextmanager
def sandbox(case_id: str, *, now: str = NOW):
    box = EnvSandbox.for_case(case_id, now=now)
    try:
        yield box
    finally:
        box.cleanup()


def run_scan(box: EnvSandbox, args: list[str], *, scenario: Path = SCENARIO,
             timeout: int = 180, clock_mode: str = "runtime"):
    overrides: dict[str, str] = {"DQ_E2E_SCENARIO": str(scenario),
                                 "DQ_E2E_CLOCK_MODE": clock_mode}
    return runner().run_cli(
        args, sandbox=box, timeout=timeout, env_override=overrides)


def golden(name: str) -> dict | str:
    path = GOLDEN / name
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return path.read_text(encoding="utf-8")


def normalize_stdout(text: str, root: Path) -> str:
    text = text.replace(str(root), "<ROOT>")
    return re.sub(r"\(\d+s\)", "(<ELAPSED>s)", text)


def normalize_report(text: str) -> str:
    return re.sub(r"耗时: [\d.]+s", "耗时: <ELAPSED>s", text)


def project_raw(raw: dict) -> dict:
    projected = {
        key: raw[key]
        for key in ("timestamp", "source", "params", "sectors",
                    "sector_cache_miss", "ranking", "report_text", "log_count")
    }
    stats = raw["api_stats"]
    projected["api_stats"] = {
        "total": stats["total"], "ok": stats["ok"], "error": stats["error"],
        "by_provider": {
            name: {"count": value["count"]}
            for name, value in stats["by_provider"].items()
        },
    }
    path = Path(raw["report_path"])
    projected["report_basename"] = path.name
    projected["report_parent"] = path.parent.name
    return projected


def scan_raw(box: EnvSandbox) -> dict:
    rows = sqlite_rows(box.data_dir / "dragon.db", "scans_v2")
    assert len(rows) == 1, rows
    return json.loads(rows[0]["raw_output"])


def projected_db(box: EnvSandbox) -> dict:
    dragons = []
    for row in sqlite_rows(box.data_dir / "dragon.db", "dragons_v2",
                           order_by="code"):
        dragons.append({
            "trade_date": row["trade_date"], "code": row["code"],
            "name": row["name"], "scan_id": row["scan_id"], "rank": row["rank"],
            "composite_score": row["composite_score"],
            "board_count": row["board_count"], "open_px": row["open_px"],
            "close_px": row["close_px"], "high_px": row["high_px"],
            "low_px": row["low_px"], "pct": row["pct"],
            "turnover_rate": row["turnover_rate"], "amount": row["amount"],
            "market_cap": row["market_cap"],
            "concepts": json.loads(row["concepts_json"] or "[]"),
            "report_text": row["report_text"],
            "is_true_dragon": row["is_true_dragon"], "version": row["version"],
            "buy_date": row["buy_date"], "buy_price": row["buy_price"],
            "max_return_5d": row["max_return_5d"],
            "max_drawdown_5d": row["max_drawdown_5d"],
            "max_return_hold_days": row["max_return_hold_days"],
            "review_status": row["review_status"], "source": row["source"],
        })
    stocks = []
    for row in sqlite_rows(box.data_dir / "dragon.db", "scan_stocks_v2",
                           order_by="code"):
        stocks.append({
            "scan_id": row["scan_id"], "code": row["code"], "name": row["name"],
            "rank": row["rank"], "composite_score": row["composite_score"],
            "board_count": row["board_count"],
            "concepts": json.loads(row["concepts_json"] or "[]"),
            "dim_drive": row["dim_drive"],
            "dim_leadership": row["dim_leadership"],
            "dim_anti_drop": row["dim_anti_drop"],
            "dim_liquidity": row["dim_liquidity"],
            "dim_absorption": row["dim_absorption"],
            "is_true_dragon": row["is_true_dragon"],
            "reject_reason": row["reject_reason"],
            "report_text": row["report_text"], "open_px": row["open_px"],
            "close_px": row["close_px"], "high_px": row["high_px"],
            "low_px": row["low_px"], "pct": row["pct"],
            "turnover_rate": row["turnover_rate"], "amount": row["amount"],
            "market_cap": row["market_cap"], "source": row["source"],
        })
    logs = sorted(
        f"{row['category']}|{row['level']}"
        for row in sqlite_rows(box.data_dir / "dragon.db", "scan_logs_v2")
    )
    mirror = []
    for row in sqlite_rows(box.data_dir / "assets.db", "dragons",
                           order_by="code"):
        mirror.append({
            "trade_date": row["trade_date"], "code": row["code"],
            "rank": row["rank"],
            "composite_score": row["composite_score"], "version": row["version"],
            "is_true_dragon": row["is_true_dragon"],
            "review_status": row["review_status"],
        })
    return {"dragons": dragons, "scan_stocks": stocks,
            "log_categories": logs, "mirror": mirror}


def call_methods(box: EnvSandbox) -> list[str]:
    if not box.call_log.exists():
        return []
    return [json.loads(line)["method"]
            for line in box.call_log.read_text(encoding="utf-8").splitlines()]


def transport_calls(box: EnvSandbox) -> list[str]:
    return [m for m in call_methods(box)
            if m.startswith("get_") or m == "batch_get_quotes"]


def scan_seed(raw_output: dict) -> dict:
    return {"tables": {"scans_v2": {
        "columns": {
            "id": "TEXT PRIMARY KEY", "scan_date": "TEXT NOT NULL",
            "elapsed_s": "REAL", "top_n": "INTEGER", "candidates_n": "INTEGER",
            "workers": "INTEGER", "raw_output": "TEXT", "source": "TEXT",
            "created_at": "TEXT",
        },
        "rows": [{
            "id": "v2_20260911_5", "scan_date": "2026-09-11",
            "elapsed_s": 1.5, "top_n": 5, "candidates_n": 3, "workers": 1,
            "raw_output": json.dumps(raw_output, ensure_ascii=False),
            "source": "v2", "created_at": "2026-09-11 15:30:00",
        }],
    }}}


# ─── S1 successful scan (full golden) ───────────────────────────────────────


def test_s1_success_matches_reviewed_golden():
    with sandbox("s1") as box:
        result = run_scan(box, BASE_ARGS)
        assert_exit(result, 0)
        assert result.stderr == ""

        raw = scan_raw(box)
        assert raw["timestamp"] == "20260911_153000"
        assert isinstance(raw["elapsed_s"], float)
        assert 0 <= raw["elapsed_s"] < 60
        assert round(raw["elapsed_s"], 1) == raw["elapsed_s"]
        assert_json_equal(project_raw(raw), golden("raw_output.json"))

        assert normalize_stdout(result.stdout, box.root) == golden("stdout.txt")

        report_path = Path(raw["report_path"])
        assert report_path.parent == box.data_dir / "results"
        assert report_path.name == "scan_report_v2_20260911_153000.txt"
        assert_file(report_path)
        assert normalize_report(
            report_path.read_text(encoding="utf-8")) == golden("report.txt")

        assert_json_equal(projected_db(box), golden("db.json"))

        scans = sqlite_rows(box.data_dir / "dragon.db", "scans_v2")
        assert scans[0]["id"] == "v2_20260911_5"
        assert scans[0]["scan_date"] == "2026-09-11"
        assert scans[0]["top_n"] == 5
        assert scans[0]["candidates_n"] == 3
        assert scans[0]["workers"] == 1
        assert scans[0]["source"] == "v2"
        assert floats_close(scans[0]["elapsed_s"], raw["elapsed_s"],
                            rel=0, abs_tol=0.1)

        assert sqlite_count(box.data_dir / "dragon.db", "scan_stocks_v2") == 4
        assert sqlite_count(box.data_dir / "dragon.db", "dragons_v2") == 3
        assert sqlite_count(box.data_dir / "dragon.db", "scan_logs_v2") == raw["log_count"]
        assert sqlite_count(box.data_dir / "assets.db", "dragons") == 3
        for table in ("vpa_analysis", "sector_blacklist", "review_account_runs",
                      "buy_sell_signals", "scans_v1", "dragons_v1"):
            assert sqlite_count(box.data_dir / "dragon.db", table) == 0

        provider_files = list_files(box.data_dir / "cache" / "provider")
        assert len(provider_files) == 40
        assert all(name.startswith("2026-09-11/") for name in provider_files)

        # Fixture diversity: >=2 true dragons, >=1 rejection, all five dims
        # exercised away from their neutral fallbacks at least once.
        true_dragons = [r for r in raw["ranking"] if r["is_true_dragon"]]
        rejected = [r for r in raw["ranking"] if not r["is_true_dragon"]]
        assert len(true_dragons) >= 2
        assert len(rejected) >= 1
        assert rejected[0]["reject_reason"] == "drive=25.0 < floor 40"
        assert {r["code"] for r in true_dragons} == {"600001", "600003", "600006"}
        for dim in ("drive", "leadership", "anti_drop", "liquidity"):
            scores = {r["dimensions"][dim]["score"] for r in raw["ranking"]}
            assert len(scores) > 1, f"{dim} 退化成了单一分数: {scores}"
        # absorption has one shared cross-sector event in this fixture: every
        # candidate must leave the 50 neutral fallback, with the event visible.
        absorption = [r["dimensions"]["absorption"] for r in raw["ranking"]]
        assert all(dim["score"] != 50.0 for dim in absorption)
        assert all(dim["details"]["event_count"] == 1 for dim in absorption)
        assert {r["code"] for r in raw["ranking"]} == {
            "600001", "600002", "600003", "600006"}


def test_g4_scan_without_options_uses_default_parameters():
    """The intended G4 path: ``scan`` with no options uses documented defaults."""

    with sandbox("g4-defaults") as box:
        result = run_scan(box, ["scan"])
        assert_exit(result, 0)
        raw = scan_raw(box)
        assert raw["params"] == {"top_n": 25, "candidates_n": 5,
                                 "workers": 2, "scorers": "v2"}
        assert sqlite_rows(box.data_dir / "dragon.db", "scans_v2")[0]["id"] == \
            "v2_20260911_25"
        assert_json_equal(raw["ranking"], golden("raw_output.json")["ranking"])
        assert "龙头战法扫描完成" in result.stdout


# ─── G4 default scan / G5 hidden alias ──────────────────────────────────────


def test_g4_no_argument_invocation_prints_help_without_side_effects():
    """Baseline fact: ``python -m dragon_quant`` with no argv prints help.

    ``parser.set_defaults(command="scan")`` is overwritten by
    ``add_subparsers(dest="command")``, so the historical "no args == scan"
    documentation does not match current behavior.  M1B pins the real
    behavior; it is not a production fix.
    """

    with sandbox("g4") as box:
        result = run_scan(box, [])
        assert_exit(result, 0)
        assert "Usage:" in result.stdout
        assert "Commands:" in result.stdout
        assert result.stderr == ""
        assert transport_calls(box) == []
        # B1: no-argument help is side-effect free — importing the package no
        # longer creates dragon.db (previously an empty schema-only file).
        assert not (box.data_dir / "dragon.db").exists()
        assert not (box.data_dir / "assets.db").exists()
        assert list_files(box.data_dir / "results") == []
        assert list_files(box.data_dir / "cache" / "provider") == []


def test_g5_hidden_scan_v2_alias_equals_scan_history():
    raw_output = {"timestamp": "20260911_153000", "source": "v2",
                  "params": {"top_n": 5}, "ranking": [], "report_text": "seed"}
    with sandbox("g5") as box:
        seed_db(box, scan_seed(raw_output))
        scan = run_scan(box, ["scan", "--date", "20260911", "--top", "5"])
        alias = run_scan(box, ["scan_v2", "--date", "20260911", "--top", "5"])
        assert_exit(scan, 0)
        assert_exit(alias, 0)
        assert alias.stdout == scan.stdout
        assert json.loads(scan.stdout) == raw_output


# ─── S2 database cache ──────────────────────────────────────────────────────


def test_s2_second_run_serves_database_cache_without_writes():
    with sandbox("s2") as box:
        assert_exit(run_scan(box, BASE_ARGS), 0)
        db_path = box.data_dir / "dragon.db"
        counts_before = {table: sqlite_count(db_path, table) for table in (
            "scans_v2", "scan_stocks_v2", "dragons_v2", "scan_logs_v2")}
        raw_before = scan_raw(box)
        results_before = list_files(box.data_dir / "results")
        calls_before = transport_calls(box)

        cached = run_scan(box, BASE_ARGS)
        assert_exit(cached, 0)
        assert cached.stderr == ""
        assert "缓存读取" in cached.stdout
        assert "直接从数据库读取 v2 缓存 (ID: v2_20260911_5)" in cached.stdout
        assert normalize_stdout(cached.stdout, box.root) == golden(
            "stdout_cached.txt")

        counts_after = {table: sqlite_count(db_path, table) for table in counts_before}
        assert counts_after == counts_before
        assert scan_raw(box) == raw_before
        assert list_files(box.data_dir / "results") == results_before
        assert transport_calls(box) == calls_before


# ─── S3 / S4 time gate ──────────────────────────────────────────────────────


def test_s3_intraday_without_force_is_intercepted():
    with sandbox("s3", now=INTRADAY) as box:
        result = run_scan(box, BASE_ARGS)
        assert_exit(result, 0)
        assert "拦截" in result.stdout
        assert "尚未收盘" in result.stdout
        assert "使用 --force 参数强制执行" in result.stdout
        assert result.stderr == ""
        # B1: the intraday interception returns before any storage access, so
        # importing the package no longer leaves an empty schema-only DB.
        assert not (box.data_dir / "dragon.db").exists()
        assert_no_file(box.data_dir / "assets.db")
        assert list_files(box.data_dir / "results") == []
        assert list_files(box.data_dir / "cache" / "provider") == []
        assert transport_calls(box) == []


def test_s4_intraday_force_runs_but_skips_provider_disk_cache():
    with sandbox("s4", now=INTRADAY) as box:
        result = run_scan(box, BASE_ARGS + ["--force"])
        assert_exit(result, 0)
        raw = scan_raw(box)
        assert raw["timestamp"] == "20260911_100000"
        assert raw["api_stats"]["total"] == 40
        assert_json_equal(raw["ranking"], golden("raw_output.json")["ranking"])
        assert list_files(box.data_dir / "cache" / "provider") == []
        results = list_files(box.data_dir / "results")
        assert results == ["scan_report_v2_20260911_100000.txt"]
        assert sqlite_count(box.data_dir / "dragon.db", "scans_v2") == 1
        assert sqlite_count(box.data_dir / "dragon.db", "dragons_v2") == 3


# ─── S5–S7 historical queries ───────────────────────────────────────────────


def test_s5_history_prints_pinned_raw_output_key_order():
    raw_output = {
        "timestamp": "20260911_153000", "source": "v2",
        "params": {"top_n": 5, "candidates_n": 3, "workers": 1,
                   "scorers": "v2"},
        "sectors": {"up": [], "down": [], "degraded": [], "sources": {},
                    "collisions": []},
        "ranking": [{"code": "600001", "name": "龙头A"}],
        "report_text": "seed",
    }
    with sandbox("s5") as box:
        seed_db(box, scan_seed(raw_output))
        result = run_scan(box, ["scan", "--date", "20260911", "--top", "5"])
        assert_exit(result, 0)
        assert result.stderr == ""
        assert result.stdout == json.dumps(raw_output, ensure_ascii=False,
                                           indent=2) + "\n"
        parsed = json.loads(result.stdout, object_pairs_hook=lambda pairs: pairs)
        assert_json_keys_in_order(dict(parsed), list(raw_output))
        assert transport_calls(box) == []


def test_s6_history_missing_date_returns_error_json():
    with sandbox("s6") as box:
        result = run_scan(box, ["scan", "--date", "20260910", "--top", "5"])
        assert_exit(result, 0)
        assert json.loads(result.stdout) == {
            "error": "未找到 2026-09-10 的 v2 扫描记录"}
        assert result.stderr == ""


def test_s7_history_bad_date_format_uses_stderr():
    with sandbox("s7") as box:
        result = run_scan(box, ["scan", "--date", "2026-09-11", "--top", "5"])
        assert_exit(result, 0)
        assert result.stdout == ""
        assert result.stderr == "错误: --date 格式应为 YYYYMMDD\n"


# ─── S8 parameter mapping / S9 no-cache / S10 degraded ─────────────────────


def test_s8_parameters_map_to_output_and_scan_id():
    with sandbox("s8") as box:
        result = run_scan(box, ["scan", "--top", "7", "--candidates", "4",
                                "--workers", "3"])
        assert_exit(result, 0)
        raw = scan_raw(box)
        assert raw["params"] == {"top_n": 7, "candidates_n": 4, "workers": 3,
                                 "scorers": "v2"}
        assert sqlite_rows(box.data_dir / "dragon.db", "scans_v2")[0]["id"] == \
            "v2_20260911_7"
        assert_json_equal(raw["ranking"], golden("raw_output.json")["ranking"])
        assert "扫描完成" in result.stdout


def test_s9_no_cache_refetches_and_keeps_ranking():
    with sandbox("s9") as box:
        assert_exit(run_scan(box, BASE_ARGS), 0)
        first = scan_raw(box)
        warm_call_count = len(transport_calls(box))

        assert_exit(run_scan(box, BASE_ARGS + ["--force"]), 0)
        warm = scan_raw(box)
        # Every provider response is served from the trade-date disk cache.
        assert warm["api_stats"]["total"] == 0
        assert_json_equal(warm["ranking"], first["ranking"])

        assert_exit(run_scan(box, BASE_ARGS + ["--force", "--no-cache"]), 0)
        cold = scan_raw(box)
        assert cold["api_stats"]["total"] == 40
        assert len(transport_calls(box)) > warm_call_count

        assert_json_equal(cold["ranking"], first["ranking"])
        assert_json_equal(first["ranking"], golden("raw_output.json")["ranking"])


def test_s10_missing_dimension_degrades_without_changing_scores():
    with sandbox("s10") as box:
        scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
        scenario["providers"]["tdx"]["get_sector_ranking"]["by_category"]["dq"] = []
        variant = box.root / "scenario_scan_degraded.json"
        variant.write_text(json.dumps(scenario, ensure_ascii=False),
                           encoding="utf-8")
        result = run_scan(box, BASE_ARGS, scenario=variant)
        assert_exit(result, 0)
        raw = scan_raw(box)
        assert raw["sectors"]["degraded"] == ["dq"]
        assert raw["sectors"]["sources"]["dq"] == "unavailable"
        assert "维度不可用" in result.stderr
        assert "地区" in result.stderr
        assert_json_equal(raw["ranking"], golden("raw_output.json")["ranking"])
        assert sqlite_count(box.data_dir / "dragon.db", "dragons_v2") == 3


def test_s10b_tdx_ranking_failure_falls_back_to_ths():
    with sandbox("s10b") as box:
        scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
        scenario["providers"]["tdx"]["get_sector_ranking"]["by_category"]["dq"] = []
        scenario["providers"]["ths"]["get_sector_ranking"]["by_category"]["dq"] = [{
            "code": "880201", "name": "江苏", "pct": 2.5, "amplitude": 3.0,
            "turnover_rate": 1.0, "category": "dq", "source": "ths",
        }]
        variant = box.root / "scenario_scan_fallback.json"
        variant.write_text(json.dumps(scenario, ensure_ascii=False),
                           encoding="utf-8")
        result = run_scan(box, BASE_ARGS, scenario=variant)
        assert_exit(result, 0)
        raw = scan_raw(box)
        assert raw["sectors"]["sources"]["dq"] == "ths"
        assert raw["sectors"]["degraded"] == []
        assert "ths" in raw["api_stats"]["by_provider"]
        assert_json_equal(raw["ranking"], golden("raw_output.json")["ranking"])


# ─── S11 / S12: single runtime-clock seam (M9C) ─────────────────────────────


def preflight_payload(box: EnvSandbox) -> dict:
    return json.loads((box.root / "preflight.json").read_text(encoding="utf-8"))


def assert_runtime_clock_mode(payload: dict, minute: str) -> None:
    marker = payload["injection"]
    assert marker["clock_mode"] == "runtime"
    assert marker["clock_impl"] == "runtime.clock"
    assert payload["datetime_is_replaced"] is False
    assert payload["datetime_class"] == "datetime.datetime"
    assert payload["time_is_replaced"] is False
    assert payload["time_function_name"] == "time"
    assert payload["datetime_isinstance_ok"] is True
    assert payload["runtime_clock_type"] == "FixedClock"
    assert payload["runtime_clock_now"].startswith(minute)
    assert payload["app_feature_imported"] == []


def test_s11_runtime_clock_mode_drives_scan_time_semantics_and_golden():
    """Runtime-clock mode: no global freeze, yet scan keeps every golden value."""
    with sandbox("s11") as box:
        result = run_scan(box, BASE_ARGS, clock_mode="runtime")
        assert_exit(result, 0)
        assert result.stderr == ""

        assert_runtime_clock_mode(preflight_payload(box), "2026-09-11T15:30")

        raw = scan_raw(box)
        assert raw["timestamp"] == "20260911_153000"
        assert_json_equal(project_raw(raw), golden("raw_output.json"))
        assert Path(raw["report_path"]).name == "scan_report_v2_20260911_153000.txt"
        assert sqlite_rows(box.data_dir / "dragon.db", "scans_v2")[0]["id"] == \
            "v2_20260911_5"
        assert_json_equal(projected_db(box), golden("db.json"))


def test_s12_runtime_clock_mode_intraday_gate_uses_the_runtime_clock():
    with sandbox("s12", now=INTRADAY) as box:
        result = run_scan(box, BASE_ARGS, clock_mode="runtime")
        assert_exit(result, 0)
        assert "拦截" in result.stdout
        assert "尚未收盘" in result.stdout

        assert_runtime_clock_mode(preflight_payload(box), "2026-09-11T10:00")

        assert not (box.data_dir / "dragon.db").exists()
        assert_no_file(box.data_dir / "assets.db")
        assert transport_calls(box) == []
