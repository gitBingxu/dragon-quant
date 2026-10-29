"""M1E1 factor behavior baseline: F1–F16.

Every case runs a real ``python -m dragon_quant factor ...`` subprocess through the
M0 sandbox (fake providers, frozen clock, temporary data root).  Rows are
seeded only into ``<sandbox>/data``; the real data directory is watched by the
session sentinel.  Goldens under ``dragon_quant/tests/e2e/golden/factor`` are reviewed
constants: tests compare against them and never regenerate them.

Deviation discipline: assertions pin *actual* production behavior.  One
documented difference from the approved matrix is pinned as-is:

* F11 "非当日 exit 1" — the CLI routes the ``ValueError`` through the factor
  error path, so the real exit code is **2** (parameter error), not 1.

Factor versions are content hashes (``factors/registry.compute_version_info``),
so goldens normalise them to ``<VERSION>`` while every *relationship* (same
version across build/query/status, exact match under an explicit ``--version``)
is asserted strictly.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path

from dragon_quant.tests.e2e.fixtures import factor_builders as fb
from dragon_quant.tests.e2e.harness.assertlib import (
    assert_exit,
    sqlite_count,
    sqlite_rows,
    sqlite_tables,
)
from dragon_quant.tests.e2e.harness.canonical import assert_json_equal
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.seed import seed_db

E2E_ROOT = Path(__file__).resolve().parent
SCENARIO = E2E_ROOT / "fixtures" / "scenario_factor.json"
GOLDEN = E2E_ROOT / "golden" / "factor"
NOW = fb.NOW
VERSION_RE = re.compile(r"v[0-9a-f]{12}")

BUILD_KEYS = ["ok", "mode", "dates", "build_ids", "reports", "warnings"]
BUILD_REPORT_KEYS = [
    "schema_version", "trade_date", "scan", "factors", "sector_snapshot",
    "registry_inserted", "warnings", "timezone_warning", "started_at",
    "finished_at", "ok", "build_id",
]
BUILD_ENTRY_KEYS = [
    "name", "label", "version", "version_source", "status", "rows", "source",
    "available_at", "source_scan_id", "codes", "coverage", "invalid_value",
    "skipped_backfilled", "warnings", "reason", "meta", "elapsed_ms",
]
LIST_KEYS = ["ok", "factors"]
LIST_ITEM_KEYS = [
    "name", "label", "version", "version_source", "source", "frequency",
    "direction", "unit", "available_phase", "pit_rule", "lookback",
    "default_enabled", "depends_on", "valid_from", "deprecated_at",
    "description",
]
QUERY_DAY_KEYS = [
    "ok", "mode", "trade_date", "rows", "resolved_versions",
    "missing_versions", "values", "available_at", "source_scan_id",
]
QUERY_SERIES_KEYS = [
    "ok", "mode", "factor", "date_from", "date_to", "rows",
    "resolved_versions", "missing_versions",
]
STATUS_KEYS = [
    "ok", "factor_values", "factor_registry", "factor_sector_snapshots",
    "build_runs", "registered_versions", "factors",
]
EXPORT_KEYS = [
    "ok", "mode", "date_from", "date_to", "dates", "codes", "columns",
    "resolved_versions", "missing_versions", "clean", "missing_rate_by_factor",
    "coverage_by_date", "build_ids", "generated_at", "pipeline_version",
    "winsorize_skipped", "normalize_skipped", "winsorize_skipped_dates",
    "normalize_skipped_dates", "dropped_rows", "dropped_codes", "matrix",
    "out_path", "meta_path", "warnings",
]
SNAPSHOT_KEYS = [
    "ok", "mode", "status", "trade_date", "codes", "errors",
    "updated_dates", "captured_at",
]
BACKFILL_KEYS = [
    "ok", "mode", "periods", "window", "downloaded", "skipped", "inserted",
    "unchanged", "retracted", "observed_unmatched", "layouts", "failed",
]
FIN_STATUS_KEYS = [
    "ok", "observed", "report_files", "report_records", "calibrations", "xdxr",
    "periods", "latest_period", "next_deadline", "overdue", "verified_layouts",
    "pit_basis_mix", "dependencies", "schedule",
]


# ─── harness helpers ───────────────────────────────────────────────────────


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


def seed_scans(box: EnvSandbox) -> None:
    seed_db(box, fb.dragon_db_seed(), db_name="dragon.db")


def run(box: EnvSandbox, *args: str, timeout: int = 180, scenario: Path = SCENARIO,
        clock_mode: str = "runtime"):
    overrides = {"DQ_E2E_SCENARIO": str(scenario),
                 "DQ_E2E_CLOCK_MODE": clock_mode}
    return runner().run_cli(
        list(args), sandbox=box, timeout=timeout,
        env_override=overrides)


def golden_json(name: str):
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def golden_text(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8")


def write_scenario_variant(box: EnvSandbox, name: str, mutate) -> Path:
    payload = json.loads(SCENARIO.read_text(encoding="utf-8"))
    mutate(payload)
    path = box.root / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def call_records(box: EnvSandbox) -> list[dict]:
    if not box.call_log.exists():
        return []
    return [json.loads(line) for line in
            box.call_log.read_text(encoding="utf-8").splitlines() if line.strip()]


def transport_calls(box: EnvSandbox) -> list[tuple[str, str]]:
    return transport_since(box, 0)


def transport_since(box: EnvSandbox, offset: int) -> list[tuple[str, str]]:
    return [(record["provider"], record["method"])
            for record in call_records(box)[offset:]
            if record["method"].startswith("get_")
            or record["method"] == "batch_get_quotes"]


# ─── version normalisation (content hashes are not behavior) ───────────────


_VERSION_KEYS = {"version", "factor_version", "resolved_version"}


def _norm_versions(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in ("resolved_versions", "registered_versions") and isinstance(item, dict):
                out[key] = {name: "<VERSION>" for name in item}
            elif key in _VERSION_KEYS and isinstance(item, str) and VERSION_RE.fullmatch(item):
                out[key] = "<VERSION>"
            elif key == "calibrated_at":
                out[key] = "<CALIBRATED_AT>"
            else:
                out[key] = _norm_versions(item)
        return out
    if isinstance(value, list):
        return [_norm_versions(item) for item in value]
    return value


def _project_build(payload):
    projected = _norm_versions(payload)
    for report in projected.get("reports") or []:
        for entry in (report.get("factors") or {}).values():
            entry["elapsed_ms"] = 0.0
    return projected


def _project_export(payload):
    return _norm_versions(payload)


def _project_eval(payload):
    return _norm_versions(payload)


def _close(actual: float, expected: float, tol: float = 1e-9) -> bool:
    return abs(float(actual) - float(expected)) <= tol


def _factor_rows(box: EnvSandbox, **kwargs) -> list[dict]:
    return sqlite_rows(box.data_dir / "assets.db", "factor_values", **kwargs)


# ─── F1: list ──────────────────────────────────────────────────────────────


def test_scenario_fixture_matches_reviewed_builder():
    """The child-facing JSON must stay byte-equivalent to the reviewed data."""

    assert json.loads(SCENARIO.read_text(encoding="utf-8")) == fb.scenario_payload()
    assert set(fb.DEFAULT_ENABLED_NAMES) < set(fb.EXPECTED_FACTOR_NAMES)
    assert fb.SINGLE_DAY_FACTOR_ROWS == (
        sum(fb.FIVE_DIM_ROWS.values()) + sum(fb.VPA_ROWS.values()))


def test_f1_list_json_and_human_match_registry():
    with sandbox("f1") as box:
        result = run(box, "factor", "list", "--json")
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert list(payload) == LIST_KEYS
        assert payload["ok"] is True
        items = payload["factors"]
        assert [item["name"] for item in items] == list(fb.EXPECTED_FACTOR_NAMES)
        assert len(items) == 58
        assert list(items[0]) == LIST_ITEM_KEYS
        for item in items:
            assert VERSION_RE.fullmatch(item["version"]), item
            assert item["version_source"] == "source"
        assert {item["name"] for item in items if item["default_enabled"]} == \
            set(fb.DEFAULT_ENABLED_NAMES)
        by_name = {item["name"]: item for item in items}
        assert by_name["five_dim_composite"]["pit_rule"] == "recorded"
        assert by_name["five_dim_composite"]["available_phase"] == "close"
        assert by_name["five_dim_composite"]["source"] == "scan"
        assert by_name["fin_roe"]["pit_rule"] == "asof"
        assert by_name["fin_roe"]["available_phase"] == "prep"
        assert by_name["fin_roe"]["frequency"] == "1q"
        assert by_name["prep_t_pct"]["pit_rule"] == "derived"
        assert by_name["auction_strength"]["available_phase"] == "auction"

        human = run(box, "factor", "list")
        assert_exit(human, 0)
        lines = human.stdout.splitlines()
        assert lines[0].split() == ["因子", "版本", "源", "阶段", "PIT", "默认"]
        assert lines[1].startswith("------")
        assert len(lines) == 2 + 58
        assert lines[2].startswith("auction_dim_direction")
        assert re.fullmatch(
            r"five_dim_composite\s+v[0-9a-f]{12}\s+scan\s+close\s+recorded\s+开",
            next(line for line in lines if line.startswith("five_dim_composite")))
        assert lines[-1].startswith("vpa_vol_amount")
        assert lines[-1].endswith("开")


# ─── F2: build single day (PIT + audit + missing semantics) ────────────────


def test_f2_build_single_day_pit_rows_audit_and_reviewed_golden():
    with sandbox("f2") as box:
        seed_scans(box)
        result = run(box, "factor", "build", "--date", "20260911", "--json")
        assert_exit(result, 0)
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert list(payload) == BUILD_KEYS
        assert payload["mode"] == "date"
        assert payload["dates"] == ["2026-09-11"]
        assert payload["build_ids"] == [1]
        assert payload["warnings"] == []
        report = payload["reports"][0]
        assert list(report) == BUILD_REPORT_KEYS
        assert report["schema_version"] == "1"
        assert report["trade_date"] == "2026-09-11"
        assert report["scan"] == {
            "scan_id": "v2_20260911_5", "top_n": 5,
            "created_at": "2026-09-11 15:30:00", "candidates": 6}
        assert report["sector_snapshot"] == {
            "status": "ok", "rows": 6, "skipped": 0,
            "by_category": {"hy": 6}, "scan_id": "v2_20260911_5"}
        assert report["registry_inserted"] == 12
        assert report["warnings"] == []
        assert report["timezone_warning"] is None
        # A frozen clock makes both audit timestamps exact.
        assert report["started_at"] == "2026-09-11T15:30:00"
        assert report["finished_at"] == "2026-09-11T15:30:00"
        assert report["ok"] is True
        assert report["build_id"] == 1

        factors = report["factors"]
        assert list(factors) == list(fb.DEFAULT_ENABLED_NAMES)
        expected_rows = {**fb.FIVE_DIM_ROWS, **fb.VPA_ROWS}
        for name, rows in expected_rows.items():
            entry = factors[name]
            assert list(entry) == BUILD_ENTRY_KEYS
            assert entry["status"] == "ok"
            assert entry["rows"] == rows
            # ``codes`` is the source pool size, not the produced row count.
            assert entry["codes"] == (6 if name.startswith("five_dim_") else 4)
            assert entry["available_at"] == "2026-09-11T15:30:00"
            assert VERSION_RE.fullmatch(entry["version"])
            assert entry["version_source"] == "source"
        for name in fb.FIVE_DIM_ROWS:
            assert factors[name]["source"] == "scan"
            assert factors[name]["source_scan_id"] == "v2_20260911_5"
        for name in fb.VPA_ROWS:
            assert factors[name]["source"] == "vpa"
            assert factors[name]["source_scan_id"] is None
        assert factors["vpa_breakout"]["meta"] == {
            "skipped_backfilled": 1,
            "skipped_backfilled_samples": ["600005"],
            "missing": 1, "duplicate_name": 0, "malformed": 0,
            "factors_error": 0,
        }
        assert factors["five_dim_composite"]["meta"] == {
            "missing": 0, "scan_id": "v2_20260911_5"}

        # ── persisted rows: PIT, version consistency, missing = no row ────
        rows = _factor_rows(box, order_by="factor_name, code")
        assert len(rows) == fb.SINGLE_DAY_FACTOR_ROWS
        assert {row["factor_name"] for row in rows} == set(fb.DEFAULT_ENABLED_NAMES)
        assert {row["available_at"] for row in rows} == {"2026-09-11T15:30:00"}
        assert {row["trade_date"] for row in rows} == {"2026-09-11"}
        for row in rows:
            assert row["factor_version"] == factors[row["factor_name"]]["version"]
            expected_source = "vpa" if row["factor_name"].startswith("vpa_") else "scan"
            assert row["source"] == expected_source
        by_factor: dict[str, list[dict]] = {}
        for row in rows:
            by_factor.setdefault(row["factor_name"], []).append(row)

        assert [row["code"] for row in by_factor["five_dim_liquidity"]] == \
            ["600001", "600003", "600004", "600005", "600006"]
        assert [row["code"] for row in by_factor["five_dim_absorption"]] == \
            ["600001", "600002", "600003", "600004", "600006"]
        flag = {row["code"]: row["value"]
                for row in by_factor["five_dim_is_true_dragon"]}
        assert flag == {"600001": 1.0, "600002": 1.0, "600003": 0.0,
                        "600005": 1.0, "600006": 0.0}
        assert "600004" not in flag          # NULL is missing, never 0
        assert [row["code"] for row in by_factor["vpa_breakout"]] == \
            ["600001", "600003", "600004"]
        assert {row["source_scan_id"] for row in by_factor["five_dim_composite"]} == \
            {"v2_20260911_5"}

        # ── build-run audit ───────────────────────────────────────────────
        assert sqlite_count(box.data_dir / "assets.db", "factor_build_runs") == 1
        runs = sqlite_rows(box.data_dir / "assets.db", "factor_build_runs")
        assert runs[0]["trade_date"] == "2026-09-11"
        assert runs[0]["started_at"] == "2026-09-11T15:30:00"
        assert runs[0]["finished_at"] == "2026-09-11T15:30:00"
        stored = json.loads(runs[0]["report_json"])
        # build_id is assigned *after* the audit row is written, so the
        # stored report deliberately has no build_id key.
        assert "build_id" not in stored
        assert stored["ok"] is True
        assert list(stored["factors"]) == list(fb.DEFAULT_ENABLED_NAMES)

        assert_json_equal(_project_build(payload),
                          golden_json("build_single_day.json"))


# ─── F3: build range ───────────────────────────────────────────────────────


def test_f3_build_range_expands_only_scan_days():
    with sandbox("f3") as box:
        seed_scans(box)
        result = run(box, "factor", "build",
                     "--from", "20260907", "--to", "20260910", "--json")
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert payload["mode"] == "range"
        # 2026-09-07 has no scan -> not expanded, no empty report.
        assert payload["dates"] == ["2026-09-08", "2026-09-09", "2026-09-10"]
        assert payload["build_ids"] == [1, 2, 3]
        assert [report["trade_date"] for report in payload["reports"]] == \
            payload["dates"]
        for report in payload["reports"]:
            entry = report["factors"]["five_dim_composite"]
            assert entry["available_at"] == f"{report['trade_date']}T15:30:00"
            assert report["ok"] is True
        rows = _factor_rows(box)
        assert len(rows) == 39 * 3
        assert {row["trade_date"] for row in rows} == set(payload["dates"])

        empty = run(box, "factor", "build",
                    "--from", "20260907", "--to", "20260907", "--json")
        assert_exit(empty, 0)
        empty_payload = json.loads(empty.stdout)
        assert empty_payload["ok"] is True
        assert empty_payload["mode"] == "range"
        assert empty_payload["dates"] == []
        assert empty_payload["build_ids"] == []
        assert empty_payload["reports"] == []
        assert empty_payload["warnings"] == [
            "no_scans_in_range: 区间内没有扫描记录"]
        assert sqlite_count(box.data_dir / "assets.db", "factor_build_runs") == 3

        # Slash date format is normalised by the same service entry point.
        slash = run(box, "factor", "build", "--date", "2026/09/11", "--json")
        assert_exit(slash, 0)
        assert json.loads(slash.stdout)["dates"] == ["2026-09-11"]


# ─── F4: --only and default-off factors ────────────────────────────────────


def test_f4_only_builds_named_factors_and_default_off_stays_off():
    with sandbox("f4") as box:
        seed_scans(box)
        only = run(box, "factor", "build", "--date", "20260911",
                   "--only", "five_dim_composite,vpa_breakout", "--json")
        assert_exit(only, 0)
        report = json.loads(only.stdout)["reports"][0]
        assert list(report["factors"]) == ["five_dim_composite", "vpa_breakout"]
        assert report["registry_inserted"] == 2
        assert {row["factor_name"] for row in _factor_rows(box)} == {
            "five_dim_composite", "vpa_breakout"}
        assert len(_factor_rows(box)) == 6 + 3

        default = run(box, "factor", "build", "--date", "20260911", "--json")
        assert_exit(default, 0)
        default_report = json.loads(default.stdout)["reports"][0]
        assert list(default_report["factors"]) == list(fb.DEFAULT_ENABLED_NAMES)
        produced = {row["factor_name"] for row in _factor_rows(box)}
        assert not {"prep_t_pct", "auction_strength", "fin_roe"} & produced
        assert produced == set(fb.DEFAULT_ENABLED_NAMES)

        # An explicit --only is a user opt-in even for a default-off factor.
        # 退役因子不可再显式构建：未知因子 → exit 2（不静默成功）
        retired = run(box, "factor", "build", "--date", "20260911",
                      "--only", "prep_verdict", "--json")
        assert_exit(retired, 2)
        assert "未知因子" in retired.stdout


# ─── F5: --all / mutual exclusion / no fin data -> no transport ────────────


def test_f5_all_builds_every_registered_factor_and_stays_offline():
    with sandbox("f5") as box:
        seed_scans(box)
        result = run(box, "factor", "build", "--date", "20260910",
                     "--all", "--json")
        assert_exit(result, 0)
        report = json.loads(result.stdout)["reports"][0]
        assert list(report["factors"]) == list(fb.EXPECTED_FACTOR_NAMES)
        assert report["registry_inserted"] == 58
        statuses = {name: entry["status"] for name, entry in report["factors"].items()}
        # 2026-09-10 has no vpa_analysis / prep / auction snapshots.
        assert {name for name, status in statuses.items() if status == "ok"} == \
            set(fb.FIVE_DIM_ROWS)
        assert {name for name, status in statuses.items()
                if status == "missing_source"} == \
            {name for name in fb.EXPECTED_FACTOR_NAMES
             if name.startswith(("prep_", "auction_", "vpa_"))}
        assert {name for name, status in statuses.items()
                if status == "no_report_yet"} == \
            {name for name in fb.EXPECTED_FACTOR_NAMES if name.startswith("fin_")}
        assert report["warnings"] == []
        # No finance raw data -> fin factors fail closed and no calendar /
        # finance transport call happens (warn_if_unavailable is import-time).
        assert transport_calls(box) == []
        assert sqlite_count(box.data_dir / "assets.db", "fin_observed_snapshots") == 0
        assert sqlite_count(box.data_dir / "assets.db", "factor_values") == 39

        mutex = run(box, "factor", "build", "--date", "20260911",
                    "--all", "--only", "five_dim_composite", "--json")
        assert_exit(mutex, 2)
        assert json.loads(mutex.stdout) == {
            "ok": False, "error": "--all 与 --only 互斥，请只指定其中一个"}


# ─── F6: query (day / series / version) ────────────────────────────────────


def test_f6_query_day_series_and_version_resolution():
    with sandbox("f6") as box:
        seed_scans(box)
        built = run(box, "factor", "build",
                    "--from", "20260908", "--to", "20260911", "--json")
        assert_exit(built, 0)
        report = json.loads(built.stdout)["reports"][3]
        version = report["factors"]["five_dim_composite"]["version"]

        day = run(box, "factor", "query", "--date", "20260911", "--json")
        assert_exit(day, 0)
        payload = json.loads(day.stdout)
        assert list(payload) == QUERY_DAY_KEYS
        assert payload["rows"] == fb.SINGLE_DAY_FACTOR_ROWS
        assert set(payload["values"]) == set(fb.CODES)
        assert payload["resolved_versions"] == {
            name: factors["version"]
            for name, factors in report["factors"].items()}
        assert payload["missing_versions"] == []
        assert payload["values"]["600002"]["five_dim_composite"] == 20.0
        assert "five_dim_liquidity" not in payload["values"]["600002"]
        assert payload["available_at"]["600002"]["five_dim_composite"] == \
            "2026-09-11T15:30:00"
        assert payload["source_scan_id"]["600002"]["five_dim_composite"] == \
            "v2_20260911_5"
        assert "vpa_breakout" not in payload["source_scan_id"]["600002"]

        filtered = run(box, "factor", "query", "--date", "20260911",
                       "--name", "five_dim_composite", "--code", "600002", "--json")
        assert_exit(filtered, 0)
        filtered_payload = json.loads(filtered.stdout)
        assert filtered_payload["rows"] == 1
        assert filtered_payload["values"] == {"600002": {"five_dim_composite": 20.0}}
        assert filtered_payload["resolved_versions"] == {"five_dim_composite": version}

        explicit = run(box, "factor", "query", "--date", "20260911",
                       "--name", "five_dim_composite", "--version", version, "--json")
        assert_exit(explicit, 0)
        explicit_payload = json.loads(explicit.stdout)
        assert explicit_payload["rows"] == 6
        assert explicit_payload["resolved_versions"] == {"five_dim_composite": version}
        assert explicit_payload["missing_versions"] == []

        bogus = run(box, "factor", "query", "--date", "20260911",
                    "--name", "five_dim_composite",
                    "--version", "vdeadbeef0000", "--json")
        assert_exit(bogus, 0)
        bogus_payload = json.loads(bogus.stdout)
        assert bogus_payload["rows"] == 0
        assert bogus_payload["resolved_versions"] == {}
        assert bogus_payload["missing_versions"] == ["five_dim_composite"]

        series = run(box, "factor", "query",
                     "--from", "20260908", "--to", "20260910",
                     "--name", "five_dim_composite", "--json")
        assert_exit(series, 0)
        series_payload = json.loads(series.stdout)
        assert list(series_payload) == QUERY_SERIES_KEYS
        assert len(series_payload["rows"]) == 18
        assert series_payload["resolved_versions"] == {"five_dim_composite": version}
        assert series_payload["missing_versions"] == []
        assert series_payload["rows"][0] == {
            "trade_date": "2026-09-08", "code": "600001",
            "factor_version": version, "value": 10.0,
            "available_at": "2026-09-08T15:30:00", "source": "scan",
            "source_scan_id": "v2_20260908_5",
            "source_meta_json": '{"missing": 0, "scan_id": "v2_20260908_5"}',
        }
        assert [row["trade_date"] for row in series_payload["rows"][::6]] == [
            "2026-09-08", "2026-09-09", "2026-09-10"]

        unknown = run(box, "factor", "query",
                      "--from", "20260908", "--to", "20260910",
                      "--name", "not_a_factor", "--json")
        assert_exit(unknown, 2)
        assert "未知因子" in json.loads(unknown.stdout)["error"]


# ─── F7: status ────────────────────────────────────────────────────────────


def test_f7_status_counts_and_human_output():
    with sandbox("f7") as box:
        seed_scans(box)
        assert_exit(run(box, "factor", "build",
                        "--from", "20260908", "--to", "20260910"), 0)
        result = run(box, "factor", "status", "--json")
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert list(payload) == STATUS_KEYS
        assert payload["factor_values"] == {
            "rows": 117, "dates": 3, "factors": 7,
            "first_date": "2026-09-08", "last_date": "2026-09-10"}
        assert payload["factor_registry"] == {"rows": 12, "factors": 12}
        assert payload["factor_sector_snapshots"] == {
            "rows": 18, "dates": 3, "by_category": {"hy": 18}}
        runs = payload["build_runs"]
        assert runs["rows"] == 3
        assert runs["last"]["id"] == 3
        assert runs["last"]["trade_date"] == "2026-09-10"
        assert runs["last"]["started_at"] == "2026-09-11T15:30:00"
        # created_at is the SQLite default (real clock): type/format only.
        assert isinstance(runs["last"]["created_at"], str) and \
            runs["last"]["created_at"]
        registered = payload["registered_versions"]
        assert set(registered) == set(fb.DEFAULT_ENABLED_NAMES)
        for version in registered.values():
            assert VERSION_RE.fullmatch(version)
        factors = payload["factors"]
        assert len(factors) == 58
        assert {item["name"] for item in factors
                if item["registered_version"] is not None} == \
            set(fb.DEFAULT_ENABLED_NAMES)

        human = run(box, "factor", "status")
        assert_exit(human, 0)
        missing = ", ".join(
            name for name in fb.EXPECTED_FACTOR_NAMES
            if name not in set(fb.DEFAULT_ENABLED_NAMES))
        assert human.stdout == (
            "因子值：117 行 / 7 因子 / 3 交易日 (2026-09-08 ~ 2026-09-10)\n"
            "注册表快照：12 行 / 12 因子\n"
            "主板块快照：18 行 / 3 交易日 {'hy': 18}\n"
            "build 审计：3 次，最近 build_id=3 @ 2026-09-10\n"
            f"未注册（尚未 build）：{missing}\n")


# ─── F8: export ────────────────────────────────────────────────────────────


def test_f8_export_csv_with_sidecar_and_missing_rate():
    with sandbox("f8") as box:
        seed_scans(box)
        assert_exit(run(box, "factor", "build",
                        "--from", "20260910", "--to", "20260911"), 0)
        out = box.out / "matrix.csv"
        result = run(box, "factor", "export",
                     "--from", "20260910", "--to", "20260911",
                     "--name", "five_dim_composite",
                     "--name", "five_dim_liquidity",
                     "--out", str(out), "--json")
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert list(payload) == EXPORT_KEYS
        assert payload["dates"] == ["2026-09-10", "2026-09-11"]
        assert payload["codes"] == list(fb.CODES)
        assert payload["columns"] == ["five_dim_composite", "five_dim_liquidity"]
        assert set(payload["resolved_versions"]) == {
            "five_dim_composite", "five_dim_liquidity"}
        assert payload["missing_versions"] == []
        assert payload["clean"] == {
            "missing": "nan", "winsorize": "none",
            "limits": [0.05, 0.95], "normalize": "none", "max_stale": 0}
        assert payload["missing_rate_by_factor"] == {
            "five_dim_composite": 0.0, "five_dim_liquidity": 0.166667}
        assert payload["coverage_by_date"] == {
            "2026-09-10": {"five_dim_composite": 1.0,
                           "five_dim_liquidity": 0.833333},
            "2026-09-11": {"five_dim_composite": 1.0,
                           "five_dim_liquidity": 0.833333}}
        assert payload["build_ids"] == [1, 2]
        assert payload["generated_at"] == "2026-09-11T15:30:00"
        assert payload["pipeline_version"] == "factor-matrix/1"
        assert payload["winsorize_skipped"] is None
        assert payload["normalize_skipped"] is None
        assert payload["out_path"] == str(out)
        assert payload["meta_path"] == str(out) + ".meta.json"
        assert payload["warnings"] == []
        assert payload["matrix"]["2026-09-11"]["600006"] == {
            "five_dim_composite": 60.0, "five_dim_liquidity": 55.0}
        assert "five_dim_liquidity" not in payload["matrix"]["2026-09-11"]["600002"]

        assert out.read_text(encoding="utf-8") == golden_text("export_matrix.csv")
        meta = json.loads(Path(str(out) + ".meta.json").read_text(encoding="utf-8"))
        meta_keys = EXPORT_KEYS[4:21]
        assert list(meta) == sorted(meta_keys)
        assert_json_equal(_project_export(meta), golden_json("export_meta.json"))


def test_f8b_export_clean_mad_zero_and_missing_drop():
    with sandbox("f8b") as box:
        seed_scans(box)
        assert_exit(run(box, "factor", "build",
                        "--from", "20260910", "--to", "20260911"), 0)
        clean_out = box.out / "clean.json"
        clean = run(box, "factor", "export",
                    "--from", "20260911", "--to", "20260911",
                    "--name", "five_dim_anti_drop", "--clean",
                    "--format", "json", "--out", str(clean_out), "--json")
        assert_exit(clean, 0)
        clean_payload = json.loads(clean.stdout)
        # All six values are 75 -> MAD == 0 -> winsorize skipped, rank kept.
        assert clean_payload["winsorize_skipped"] == "mad_zero"
        assert clean_payload["winsorize_skipped_dates"] == ["2026-09-11"]
        assert clean_payload["normalize_skipped"] is None
        assert clean_payload["clean"] == {
            "missing": "nan", "winsorize": "mad",
            "limits": [0.05, 0.95], "normalize": "rank", "max_stale": 0}
        for code in fb.CODES:
            assert_json_equal(
                clean_payload["matrix"]["2026-09-11"][code],
                {"five_dim_anti_drop": 0.28833333333333344})
        written = json.loads(clean_out.read_text(encoding="utf-8"))
        assert written["columns"] == ["five_dim_anti_drop"]
        assert written["dates"] == ["2026-09-11"]

        drop_out = box.out / "drop.json"
        drop = run(box, "factor", "export",
                   "--from", "20260910", "--to", "20260911",
                   "--name", "five_dim_composite",
                   "--name", "five_dim_liquidity",
                   "--missing", "drop", "--format", "json",
                   "--out", str(drop_out), "--json")
        assert_exit(drop, 0)
        drop_payload = json.loads(drop.stdout)
        assert drop_payload["dropped_rows"] == 2
        assert drop_payload["dropped_codes"] == [
            "2026-09-10:600002", "2026-09-11:600002"]
        assert drop_payload["missing_rate_by_factor"] == {
            "five_dim_composite": 0.0, "five_dim_liquidity": 0.166667}
        dropped = json.loads(drop_out.read_text(encoding="utf-8"))
        assert "600002" not in dropped["values"]["2026-09-10"]
        assert "600002" not in dropped["values"]["2026-09-11"]
        assert dropped["values"]["2026-09-10"]["600001"] == {
            "five_dim_composite": 10.0, "five_dim_liquidity": 55.0}


# ─── F9: eval success ──────────────────────────────────────────────────────


def test_f9_eval_metrics_match_reviewed_numeric_golden():
    with sandbox("f9") as box:
        seed_scans(box)
        assert_exit(run(box, "factor", "build",
                        "--from", "20260908", "--to", "20260910"), 0)
        offset = len(call_records(box))
        result = run(box, "factor", "eval", "--name", "five_dim_composite",
                     "--from", "20260908", "--to", "20260910",
                     "--min-days", "2", "--min-cross-section", "3", "--json")
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["status"] == "evaluated"
        assert payload["reason"] is None
        assert payload["days"] == 2
        assert payload["observations"] == 12
        assert payload["effective_independent_days"] == 2
        assert payload["horizon"] == 1
        assert payload["nw_lag"] == 0
        assert payload["group_count"] == 2
        assert payload["coverage"] == {"mean": 1.0, "min": 1.0,
                                       "missing_rate": 0.0}
        assert payload["ic"]["mean"] == 0.8
        assert _close(payload["ic"]["std"], 0.28284271247461906)
        assert _close(payload["ic"]["icir"], 2.82842712474619)
        assert payload["ic"]["t_stat"] == 4.0
        assert _close(payload["ic"]["nw_t_stat"], 5.65685424949238)
        assert payload["ic"]["n_days"] == 2
        assert payload["ic"]["positive_ratio"] == 1.0
        groups = payload["groups"]
        assert [g["group"] for g in groups] == [1, 2]
        assert [g["n"] for g in groups] == [6, 6]
        assert _close(groups[0]["mean_return"], 2.5)
        assert _close(groups[1]["mean_return"], 4.5)
        assert [g["hit_rate"] for g in groups] == [1.0, 1.0]
        assert _close(payload["long_short"]["mean"], 2.0)
        assert payload["long_short"]["tradable"] is False
        assert _close(payload["long_short"]["pool_mean_return"], 3.5)
        assert payload["turnover"] == {
            "mean": 0.0, "definition": "group_membership_change",
            "basis": "consecutive_included_days"}
        assert payload["pit_policy"] == "strict"
        assert payload["pit_basis_mix"] == {
            "observed": 12, "statutory_backfilled": 0, "revised": 0}
        assert payload["retracted_count"] == 0
        assert payload["build_ids"] == [1, 2, 3]
        assert payload["warnings"] == [
            "label_missing: 6 条样本因停牌/数据不足无前向收益",
            "单日截面中位数 6 只，5% 双侧显著约需 |IC| 0.88"]
        assert payload["degraded"] == []
        assert_json_equal(_project_eval(payload),
                          golden_json("eval_success.json"))

        # The fake daily-K seam serves every code plus the index calendar.
        assert transport_since(box, offset) == [("xueqiu", "get_kline")] * 7


# ─── F10: eval insufficient_data ───────────────────────────────────────────


def test_f10_eval_insufficient_data_is_a_valid_conclusion():
    with sandbox("f10") as box:
        seed_scans(box)
        unbuilt = run(box, "factor", "eval", "--name", "five_dim_composite",
                      "--from", "20260908", "--to", "20260910",
                      "--min-cross-section", "3", "--json")
        assert_exit(unbuilt, 0)
        payload = json.loads(unbuilt.stdout)
        assert payload["ok"] is True
        assert payload["status"] == "insufficient_data"
        assert payload["resolved_version"] is None
        assert payload["reason"] == \
            "version_not_registered: 因子 five_dim_composite 尚未 build（无注册版本）"
        assert payload["build_ids"] == []
        assert list(payload)[:4] == ["ok", "status", "reason", "ic"]

        assert_exit(run(box, "factor", "build", "--date", "20260908"), 0)
        short = run(box, "factor", "eval", "--name", "five_dim_composite",
                    "--from", "20260908", "--to", "20260908",
                    "--min-days", "20", "--min-cross-section", "3", "--json")
        assert_exit(short, 0)
        short_payload = json.loads(short.stdout)
        assert short_payload["status"] == "insufficient_data"
        assert short_payload["ok"] is True
        assert short_payload["days"] == 1
        assert short_payload["reason"] == "有效交易日 1 < min_days 20"
        assert short_payload["ic"]["mean"] == 1.0

        human = run(box, "factor", "eval", "--name", "five_dim_composite",
                    "--from", "20260908", "--to", "20260908",
                    "--min-days", "20", "--min-cross-section", "3")
        assert_exit(human, 0)
        assert human.stdout.splitlines()[0].startswith(
            "评估 five_dim_composite@v")
        assert human.stdout.splitlines()[0].endswith(
            "：insufficient_data（有效交易日 1 < min_days 20）")


# ─── F11: fin sync snapshot ────────────────────────────────────────────────


def test_f11_fin_snapshot_is_same_day_only_and_persists_rows():
    with sandbox("f11") as box:
        seed_scans(box)
        result = run(box, "factor", "fin", "sync", "--snapshot",
                     "--date", "20260911", "--json")
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert list(payload) == SNAPSHOT_KEYS
        # Quirk pinned as-is: the service ``ok`` boolean is overwritten by the
        # sync result's processed-row count (6), not ``true``.
        assert payload["ok"] == 6
        assert payload["mode"] == "snapshot"
        assert payload["status"] == "ok"
        assert payload["trade_date"] == "2026-09-11"
        assert payload["codes"] == 6
        assert payload["errors"] == []
        assert payload["updated_dates"] == {"20260630": 5, "20260331": 1}
        assert payload["captured_at"] == "2026-09-11T15:30:00"

        rows = sqlite_rows(box.data_dir / "assets.db",
                           "fin_observed_snapshots", order_by="code")
        assert len(rows) == 6
        assert {row["code"] for row in rows} == set(fb.CODES)
        assert {row["trade_date"] for row in rows} == {"2026-09-11"}
        periods = {row["code"]: row["period"] for row in rows}
        assert periods["600005"] == "2026-03-31"
        assert {code: period for code, period in periods.items()
                if code != "600005"} == {code: "2026-06-30"
                                         for code in fb.CODES if code != "600005"}
        assert {row["captured_at"] for row in rows} == {"2026-09-11T15:30:00"}
        assert all(len(row["values_hash"]) == 64 for row in rows)

        human = run(box, "factor", "fin", "sync", "--snapshot",
                    "--date", "20260911")
        assert_exit(human, 0)
        assert human.stdout == (
            "财务观测 2026-09-11：候选 6 只，成功 6，状态 ok\n"
            "  updated_date=20260331: 1\n"
            "  updated_date=20260630: 5\n")

        # same_day_only: a historical date is rejected.  Actual exit code is 2
        # (ValueError -> factor parameter-error path), not the matrix's 1.
        past = run(box, "factor", "fin", "sync", "--snapshot",
                   "--date", "20260910", "--json")
        assert_exit(past, 2)
        assert json.loads(past.stdout) == {
            "ok": False,
            "error": "财务观测仅当日可采（same_day_only）：2026-09-10 != 今天 2026-09-11"}
        past_human = run(box, "factor", "fin", "sync", "--snapshot",
                         "--date", "20260910")
        assert_exit(past_human, 2)
        assert past_human.stdout == ""
        assert past_human.stderr == (
            "❌ 因子操作失败：财务观测仅当日可采（same_day_only）："
            "2026-09-10 != 今天 2026-09-11\n")


# ─── F12: fin backfill (md5 idempotence + vintage) ─────────────────────────


def test_f12_fin_backfill_is_idempotent_and_appends_vintages():
    with sandbox("f12") as box:
        seed_scans(box)
        assert_exit(run(box, "factor", "fin", "calibrate",
                        "--sample", "4", "--json"), 0)
        first = run(box, "factor", "fin", "sync", "--backfill", "--json")
        assert_exit(first, 0)
        first_payload = json.loads(first.stdout)
        assert list(first_payload) == BACKFILL_KEYS
        assert_json_equal(first_payload, golden_json("fin_backfill.json"))
        assert first_payload["inserted"] == 4
        assert first_payload["layouts"] == {"gpcw_10": "verified"}

        second = run(box, "factor", "fin", "sync", "--backfill", "--json")
        assert_exit(second, 0)
        second_payload = json.loads(second.stdout)
        assert second_payload["downloaded"] == 0
        assert second_payload["skipped"] == 1
        assert second_payload["inserted"] == 0
        assert second_payload["periods"] == []
        assert second_payload["layouts"] == {}

        def make_vintage(payload: dict) -> None:
            payload["providers"]["tdx"]["get_financial_files"][0]["md5"] = \
                "md5-q2-v2"
            records = payload["providers"]["tdx"]["get_financial_records"]
            target = next(r for r in records if r["code"] == "600001")
            target["fields"][1] = 5.75      # bps revised 5.5 -> 5.75

        scenario = write_scenario_variant(box, "scenario_vintage.json", make_vintage)
        third = run(box, "factor", "fin", "sync", "--backfill", "--json",
                    scenario=scenario)
        assert_exit(third, 0)
        third_payload = json.loads(third.stdout)
        assert third_payload["downloaded"] == 1
        assert third_payload["skipped"] == 0
        assert third_payload["inserted"] == 1
        assert third_payload["unchanged"] == 3
        assert third_payload["retracted"] == 0
        assert third_payload["layouts"] == {"gpcw_10": "verified"}

        records = sqlite_rows(box.data_dir / "assets.db",
                              "fin_report_records",
                              order_by="code, file_md5")
        assert len(records) == 5
        code1 = [row for row in records if row["code"] == "600001"]
        assert [row["file_md5"] for row in code1] == ["md5-q2-v1", "md5-q2-v2"]
        assert [row["pit_basis"] for row in code1] == [
            "statutory_backfilled", "revised"]
        assert [row["available_at"] for row in code1] == [
            "2026-08-31T23:59:59", "2026-09-11T15:30:00"]
        assert json.loads(code1[1]["values_json"])["meigujing_zichan"] == 5.75
        assert all(row["retracted_at"] is None for row in records)
        files = sqlite_rows(box.data_dir / "assets.db",
                            "fin_report_files", order_by="md5")
        assert [row["md5"] for row in files] == ["md5-q2-v1", "md5-q2-v2"]
        assert {row["status"] for row in files} == {"ok"}


# ─── F12b: fin backfill on the single runtime-clock seam (M9C) ─────────────


def test_f12b_fin_backfill_cli_runs_on_the_single_runtime_clock_seam():
    """CLI path: ``first_seen_at`` is the frozen runtime clock, not real time."""
    with sandbox("f12b") as box:
        seed_scans(box)
        assert_exit(run(box, "factor", "fin", "calibrate",
                        "--sample", "4", "--json"), 0)
        result = run(box, "factor", "fin", "sync", "--backfill", "--json")
        assert_exit(result, 0)

        files = sqlite_rows(box.data_dir / "assets.db",
                            "fin_report_files", order_by="md5")
        assert {row["first_seen_at"] for row in files} == {"2026-09-11T15:30:00"}

        proof = json.loads((box.root / "preflight.json").read_text(encoding="utf-8"))
        assert proof["injection"]["clock_mode"] == "runtime"
        assert proof["injection"]["clock_impl"] == "runtime.clock"
        assert proof["datetime_is_replaced"] is False
        assert proof["time_is_replaced"] is False
        assert proof["datetime_isinstance_ok"] is True
        assert proof["runtime_clock_type"] == "FixedClock"


# ─── F13: fin calibrate verified / unverified ──────────────────────────────


def test_f13_fin_calibrate_verified_and_unverified_exit_codes():
    with sandbox("f13") as box:
        seed_scans(box)
        verified = run(box, "factor", "fin", "calibrate", "--sample", "4", "--json")
        assert_exit(verified, 0)
        payload = json.loads(verified.stdout)
        assert payload["ok"] is True
        assert payload["mode"] == "calibrate"
        assert payload["status"] == "verified"
        assert payload["sample_n"] == 4
        assert payload["anchors"] == {"identity": True, "hierarchy": True}
        assert payload["layout_key"] == "gpcw_10"
        assert payload["num_fields"] == 10
        assert payload["report_period"] == "2026-06-30"
        assert payload["filename"] == fb.FIN_FILENAME
        assert payload["errors"] == []
        assert payload["mapping"]["meigujing_zichan"] == {"index": 1, "scale": 1.0}
        assert payload["mapping"]["zong_guben"] == {"index": 0, "scale": 1.0}
        assert payload["mapping"]["shuihou_lirun"] == {"index": 7, "scale": 1.0}
        assert_json_equal(_norm_versions(payload),
                          golden_json("fin_calibration.json"))

        human = run(box, "factor", "fin", "calibrate", "--sample", "4")
        assert_exit(human, 0)
        assert human.stdout.splitlines()[0] == (
            "字段校准 gpcw_10：verified (sample=4, period=2026-06-30)")
        assert human.stdout.splitlines()[1] == \
            "  锚定：恒等式=True 层级不等式=True"

        def break_evidence(payload: dict) -> None:
            for values in payload["providers"]["tdx"]["get_finance_info"]["by_code"].values():
                values["meigujing_zichan"] = 999999.0

        scenario = write_scenario_variant(
            box, "scenario_unverified.json", break_evidence)
        unverified = run(box, "factor", "fin", "calibrate", "--sample", "4",
                         "--json", scenario=scenario)
        assert_exit(unverified, 1)
        unverified_payload = json.loads(unverified.stdout)
        assert unverified_payload["ok"] is False
        assert unverified_payload["status"] == "unverified"
        assert unverified_payload["anchors"] == {
            "identity": False, "hierarchy": True}
        assert "meigujing_zichan" not in unverified_payload["mapping"]


# ─── F13b: fin factor cohort (ledger + observed fallback + exclusion) ─────


def test_f13b_fin_factor_build_applies_modal_cohort_and_pit_phase():
    with sandbox("f13b") as box:
        seed_scans(box)
        assert_exit(run(box, "factor", "fin", "calibrate",
                        "--sample", "4", "--json"), 0)
        assert_exit(run(box, "factor", "fin", "sync", "--backfill", "--json"), 0)
        offset = len(call_records(box))
        result = run(box, "factor", "build", "--date", "20260911",
                     "--only",
                     "fin_bps,fin_roe,fin_net_margin,fin_net_assets,"
                     "fin_net_profit_yoy", "--json")
        assert_exit(result, 0)
        report = json.loads(result.stdout)["reports"][0]
        assert report["warnings"] == [
            "fin_net_profit_yoy: empty（源可用但当日无值）"]
        factors = report["factors"]
        for name in ("fin_bps", "fin_roe", "fin_net_margin", "fin_net_assets"):
            entry = factors[name]
            assert entry["status"] == "ok"
            assert entry["rows"] == 5
            assert entry["available_at"] == "2026-09-11T16:40:00"
            assert entry["warnings"] == ["cohort_selection_bias"]
        yoy = factors["fin_net_profit_yoy"]
        assert yoy["status"] == "empty"
        assert yoy["rows"] == 0
        assert yoy["warnings"] == ["cohort_selection_bias", "empty_unexpected"]

        meta = factors["fin_bps"]["meta"]
        assert meta["cohort"] == {
            "modal_period": "2026-06-30",
            "n_total": 6,
            "n_cohort": 5,
            "n_mismatch": 1,
            "by_period": {"2026-03-31": 1, "2026-06-30": 5},
        }
        assert meta["cohort_policy"] == "modal"
        excluded = meta["cohort_excluded_profile"]
        assert excluded["count"] == 1
        assert excluded["codes"] == ["600005"]
        assert excluded["by_period"] == {"2026-03-31": 1}
        assert excluded["composite"] == {
            "n": 1, "min": 50.0, "median": 50.0, "max": 50.0}
        assert excluded["composite_percentile"] == {
            "n": 1, "min": 75.0, "median": 75.0, "max": 75.0}
        assert excluded["size_proxy"] is None
        assert excluded["true_dragon_ratio"] == 1.0

        # Snapshot auto-sync ran inside the build (no stored observation yet)
        # and the TDX calendar was loaded for the stale check.
        assert sqlite_count(box.data_dir / "assets.db",
                            "fin_observed_snapshots") == 6
        delta = transport_since(box, offset)
        assert delta.count(("tdx", "get_finance_info")) == 6
        assert delta.count(("tdx", "get_kline")) == 1

        query = run(box, "factor", "query", "--date", "20260911",
                    "--name", "fin_roe", "--json")
        assert_exit(query, 0)
        values = json.loads(query.stdout)["values"]
        expected_roe = {
            "600001": 3.0e8 / 5.5e9 * 200.0,
            "600002": 3.1e8 / 6.0e9 * 200.0,
            "600003": 3.2e8 / 6.5e9 * 200.0,
            "600004": 3.3e8 / 7.0e9 * 200.0,
            "600006": 4.0e8 / 9.6e9 * 200.0,
        }
        assert set(values) == set(expected_roe)
        for code, expected in expected_roe.items():
            assert abs(values[code]["fin_roe"] - expected) <= 1e-9

        bps = run(box, "factor", "query", "--date", "20260911",
                  "--name", "fin_bps", "--json")
        assert_exit(bps, 0)
        assert {code: row["fin_bps"]
                for code, row in json.loads(bps.stdout)["values"].items()} == {
            "600001": 5.5, "600002": 6.0, "600003": 6.5,
            "600004": 7.0, "600006": 8.0}
        margin = run(box, "factor", "query", "--date", "20260911",
                     "--name", "fin_net_margin", "--json")
        assert_exit(margin, 0)
        assert {row["fin_net_margin"]
                for row in json.loads(margin.stdout)["values"].values()} == {10.0}

        fin_rows = [row for row in _factor_rows(box)
                    if row["factor_name"].startswith("fin_")]
        assert len(fin_rows) == 20
        assert {row["available_at"] for row in fin_rows} == {"2026-09-11T16:40:00"}


# ─── F14: fin status ───────────────────────────────────────────────────────


def test_f14_fin_status_counts_after_backfill_and_vintage():
    with sandbox("f14") as box:
        seed_scans(box)
        assert_exit(run(box, "factor", "fin", "calibrate",
                        "--sample", "4", "--json"), 0)
        assert_exit(run(box, "factor", "fin", "sync", "--backfill", "--json"), 0)

        def make_vintage(payload: dict) -> None:
            payload["providers"]["tdx"]["get_financial_files"][0]["md5"] = \
                "md5-q2-v2"
            target = next(r for r in payload["providers"]["tdx"]["get_financial_records"]
                          if r["code"] == "600001")
            target["fields"][1] = 5.75

        scenario = write_scenario_variant(box, "scenario_vintage.json", make_vintage)
        assert_exit(run(box, "factor", "fin", "sync", "--backfill", "--json",
                        scenario=scenario), 0)

        result = run(box, "factor", "fin", "status", "--json")
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert list(payload) == FIN_STATUS_KEYS
        assert payload["observed"] == {"rows": 0, "dates": 0, "last_date": None}
        assert payload["report_files"] == {
            "rows": 2, "periods": 1, "by_status": {"ok": 2}}
        assert payload["report_records"] == {
            "rows": 5, "periods": 1, "codes": 4, "retracted": 0,
            "by_basis": {"revised": 1, "statutory_backfilled": 4}}
        assert payload["xdxr"] == {"rows": 0}
        assert payload["periods"] == ["2026-06-30"]
        assert payload["latest_period"] == "2026-06-30"
        assert payload["next_deadline"] == "2026-10-31T23:59:59"
        assert payload["overdue"] is False
        assert payload["verified_layouts"] == ["gpcw_10"]
        assert payload["pit_basis_mix"] == {
            "counts": {"revised": 1, "statutory_backfilled": 4},
            "observed_ratio": 0.0,
            "restatement_unverifiable": 4}
        assert payload["dependencies"]["pandas"] == "transitive(tmdx)"
        assert_json_equal(_norm_versions(payload),
                          golden_json("fin_status.json"))

        human = run(box, "factor", "fin", "status")
        assert_exit(human, 0)
        assert human.stdout == (
            "观测快照：0 行 / 0 日 (最近 None)\n"
            "期文件：2 行 / 1 期 {'ok': 2}\n"
            "账本：5 行 / 1 期 / 4 code / 撤销 0 "
            "{'revised': 1, 'statutory_backfilled': 4}\n"
            "PIT 口径：observed 占比 0.0，回填不可证伪 4 行\n"
            "校准布局：['gpcw_10']\n"
            "最近报告期：2026-06-30（下一截止日 2026-10-31T23:59:59，逾期=False）\n"
            "依赖：pandas=transitive(tmdx)\n")


# ─── F15: parameter errors ─────────────────────────────────────────────────


def test_f15_parameter_errors_exit_2_with_json_error():
    with sandbox("f15") as box:
        cases = [
            (["factor", "build"],
             "build 需要 --date，或同时指定 --from 与 --to"),
            (["factor", "build", "--date", "2026-13-40"],
             "无效日期: '2026-13-40'（支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD）"),
            (["factor", "query", "--from", "20260908", "--to", "20260910"],
             "区间 query 必须指定 --name（单因子时序）"),
            (["factor", "eval", "--name", "five_dim_composite",
              "--from", "20260908", "--to", "20260910", "--groups", "5"],
             "groups 只允许 (2, 3)（5 组样本过少，直接拒绝）"),
            (["factor", "export", "--from", "20260908", "--to", "20260910",
              "--out", str(box.out / "empty.csv")],
             "export 至少需要一个 --name"),
            (["factor", "fin", "sync"],
             "fin sync 需要且只能指定 --snapshot 或 --backfill"),
            (["factor", "fin", "sync", "--snapshot", "--backfill"],
             "fin sync 需要且只能指定 --snapshot 或 --backfill"),
            (["factor", "fin", "calibrate", "--sample", "0"],
             "--sample 必须 ≥ 1"),
        ]
        for args, message in cases:
            result = run(box, *args, "--json")
            assert_exit(result, 2)
            assert json.loads(result.stdout) == {"ok": False, "error": message}
            human = run(box, *args)
            assert_exit(human, 2)
            assert human.stdout == ""
            assert human.stderr == f"❌ 因子操作失败：{message}\n"


# ─── F16: group without action ─────────────────────────────────────────────


def test_f16_group_without_action_exits_2():
    with sandbox("f16") as box:
        factor = run(box, "factor")
        assert_exit(factor, 2)
        assert factor.stdout == ""
        assert factor.stderr == (
            "❌ 因子操作失败：未知 factor 子命令"
            "（build/list/query/status/export/eval/fin）\n")

        fin = run(box, "factor", "fin")
        assert_exit(fin, 2)
        assert fin.stdout == ""
        assert fin.stderr == (
            "❌ 因子操作失败：未知 factor fin 子命令（sync/calibrate/status）\n")


# ─── M9B2: runtime-clock mode drives factor timestamps ─────────────────────


def test_m9b2_runtime_clock_mode_drives_build_export_and_captured_at():
    """M9B2: build/export/fin snapshot timestamps come from ``runtime.clock``.

    The runtime mode does not freeze ``datetime``/``time``; the only frozen
    input is ``runtime.clock``.  Every audit timestamp must therefore be the
    sandbox instant, and the build report must still match the reviewed golden.
    """
    with sandbox("m9b2-runtime") as box:
        seed_scans(box)
        build = run(box, "factor", "build", "--date", "20260911", "--json",
                    clock_mode="runtime")
        assert_exit(build, 0)
        payload = json.loads(build.stdout)
        assert_json_equal(_project_build(payload),
                          golden_json("build_single_day.json"))
        report = payload["reports"][0]
        assert report["started_at"] == "2026-09-11T15:30:00"
        assert report["finished_at"] == "2026-09-11T15:30:00"
        for entry in report["factors"].values():
            assert entry["available_at"] == "2026-09-11T15:30:00"

        out = box.out / "runtime.csv"
        export = run(box, "factor", "export",
                     "--from", "20260911", "--to", "20260911",
                     "--name", "five_dim_composite",
                     "--out", str(out), "--json", clock_mode="runtime")
        assert_exit(export, 0)
        assert json.loads(export.stdout)["generated_at"] == "2026-09-11T15:30:00"

        snapshot = run(box, "factor", "fin", "sync", "--snapshot",
                       "--date", "20260911", "--json", clock_mode="runtime")
        assert_exit(snapshot, 0)
        assert json.loads(snapshot.stdout)["captured_at"] == "2026-09-11T15:30:00"


# ─── F17–F20: factor decision-review (prep/auction 决策结果回测) ────────────

DECISION_PREP_DATE = "2026-09-09"
DECISION_UNSUPPORTED_DATE = "2026-09-10"
DECISION_REVIEW_KEYS = [
    "schema_version", "ok", "status", "source", "date_from", "date_to",
    "universe", "coverage_basis", "config", "snapshots", "versions", "groups",
    "spreads", "exclusions", "caveats", "warnings",
]
_ASSETS_STANDARD_TABLES = {
    "prep_assets", "auction_assets", "prep_snapshots", "auction_snapshots",
    "dragons", "factor_values", "factor_build_runs", "factor_sector_snapshots",
    "factor_registry", "factor_model_snapshots", "fin_observed_snapshots",
    "fin_report_files", "fin_report_records", "fin_field_calibrations",
    "fin_xdxr_records",
}


def _prep_snapshot_row(day: str, payload: dict) -> dict:
    return {
        "trade_date": day,
        "source": "v2",
        "result_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        "created_at": f"{day} 17:02:11",
    }


def _auction_snapshot_row(day: str, code: str, analysis, *,
                          created_at="2026-09-09 09:25:00") -> dict:
    text = analysis if isinstance(analysis, str) else json.dumps(
        analysis, ensure_ascii=False, sort_keys=True)
    return {"trade_date": day, "code": code, "source": "v2",
            "analysis_json": text, "created_at": created_at}


def _prep_decision_seed() -> dict:
    supported = {
        "version": "0.7",
        "trade_date": DECISION_PREP_DATE,
        "review_candidates": [
            {"code": "600001", "name": "龙头A", "eligibility": "eligible",
             "position_pct": 4.0, "review_score": 70.0, "review_grade": "B"},
            {"code": "600002", "name": "跟风B", "eligibility": "not_qualified",
             "position_pct": 0.0, "review_score": 40.0, "review_grade": "D"},
            {"code": "600003", "name": "补涨C", "eligibility": "unknown",
             "position_pct": None, "review_score": None, "review_grade": None},
        ],
        "strategy_context": {
            "frozen_at": f"{DECISION_PREP_DATE}T16:40:00+08:00"},
    }
    unsupported = {"version": "0.6", "trade_date": DECISION_UNSUPPORTED_DATE,
                   "review_candidates": []}
    rows = [
        _prep_snapshot_row(DECISION_PREP_DATE, supported),
        _prep_snapshot_row(DECISION_UNSUPPORTED_DATE, unsupported),
    ]
    return {"tables": {"prep_snapshots": {
        "columns": {"trade_date": "TEXT NOT NULL", "source": "TEXT NOT NULL",
                    "result_json": "TEXT NOT NULL", "created_at": "TEXT"},
        "primary_key": ["trade_date", "source"],
        "rows": rows,
    }}}


def _auction_decision_seed() -> dict:
    day = DECISION_PREP_DATE

    def analysis(code, *, version="2", weak=False, strength=70.0, level="强",
                 unverified=False, status="ok"):
        return {"version": version, "code": code, "is_weak": weak,
                "strength": strength, "level": level, "unverified": unverified,
                "data_quality": {"status": status}}

    rows = [
        _auction_snapshot_row(day, "600001", analysis("600001", level="强")),
        _auction_snapshot_row(day, "600002", analysis(
            "600002", strength=50.0, level="中")),
        _auction_snapshot_row(day, "600003", analysis(
            "600003", strength=40.0, level="弱")),
        _auction_snapshot_row(day, "600004", analysis(
            "600004", weak=True, strength=30.0, level="弱")),
        _auction_snapshot_row(day, "600005", analysis(
            "600005", status="unavailable")),
        _auction_snapshot_row(day, "600006", analysis("600006", version="1")),
        _auction_snapshot_row(day, "600007", "not json"),
    ]
    return {"tables": {"auction_snapshots": {
        "columns": {"trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
                    "source": "TEXT NOT NULL", "analysis_json": "TEXT NOT NULL",
                    "created_at": "TEXT"},
        "primary_key": ["trade_date", "code", "source"],
        "rows": rows,
    }}}


def _merge_seed(*specs: dict) -> dict:
    tables: dict = {}
    for spec in specs:
        tables.update(spec["tables"])
    return {"tables": tables}


def _decision_scenario(box: EnvSandbox) -> Path:
    """复用 factor scenario 的行情，但把成交额抬到可成交门槛（≥2 亿）。

    评测 fixture 的日 K 成交额只有 1000 万（``factor eval`` 不看流动性），
    决策回测会按可成交口径剔除 ``illiquid``，因此这里只调整 amount/volume。
    """
    payload = json.loads(SCENARIO.read_text(encoding="utf-8"))
    by_code = payload["providers"]["xueqiu"]["get_kline"]["by_code"]
    for code, bars in by_code.items():
        if code == "SH000001":
            continue
        for bar in bars:
            bar["amount"] = 500_000_000.0
            bar["volume"] = 1_000_000.0
    path = box.root / "scenario_decision.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_f17_decision_review_prep_groups_coverage_and_golden():
    with sandbox("f17") as box:
        seed_db(box, _prep_decision_seed(), db_name="assets.db")
        scenario = _decision_scenario(box)
        result = run(box, "factor", "decision-review", "--source", "prep",
                     "--from", "20260907", "--to", "20260910",
                     "--min-days", "1", "--min-group-samples", "1", "--json",
                     scenario=scenario)
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert list(payload) == DECISION_REVIEW_KEYS
        assert payload["schema_version"] == "dr_v1"
        assert payload["ok"] is True
        assert payload["status"] == "ok"
        assert payload["source"] == "prep"
        assert payload["universe"] == "persisted_decision_snapshots"
        assert payload["coverage_basis"] == "persisted_snapshots"
        assert payload["config"] == {
            "horizons": [1, 3, 5], "cost_bps": 13.0, "min_days": 1,
            "min_group_samples": 1, "include_samples": False,
            "outcome_basis": "net_open_to_open_after_cost",
            "net_return_basis": "gross_minus_cost",
            "entry_rule": "available_at_first_tradable_open",
            "exit_rule": "nth_trading_day_open",
            "calendar_basis": "index_calendar",
        }
        assert payload["snapshots"] == {
            "requested_days": 4, "with_snapshot": 2, "missing": 2, "usable": 1,
            "unsupported_version": 1, "malformed": 0, "empty_decision": 0,
            "corrupt_rows": 0, "non_trading_snapshot_dates": 0,
        }
        assert payload["versions"] == {
            "accepted": ["0.7", "0.8"], "seen": {"0.6": 1, "0.7": 1},
            "unsupported_units": 1,
        }
        assert [group["group"] for group in payload["groups"]] == [
            "recommended", "not_recommended", "data_insufficient"]
        groups = {group["group"]: group for group in payload["groups"]}
        recommended = groups["recommended"]["horizons"]["1"]
        assert recommended["observations"] == 1
        assert recommended["valid"] == 1
        assert recommended["up"] == 1
        assert recommended["direction_hit_rate"] == 1.0
        assert _close(recommended["mean_net_return_pct"], 0.87)
        assert recommended["quality"] == "ok"
        assert recommended["above_reference"] is True
        assert groups["not_recommended"]["horizons"]["1"]["direction_hit_rate"] == 0.0
        assert groups["data_insufficient"]["horizons"]["1"]["direction_evaluable"] is False
        assert _close(payload["spreads"]["1"]["value"], -1.0)
        assert payload["spreads"]["3"]["value"] is None
        assert payload["exclusions"]["sample"] == {"insufficient_horizon": 6}
        assert payload["exclusions"]["snapshot"] == {
            "duplicate_code": 0, "missing_snapshot": 2,
            "unsupported_snapshot_version": 1, "unparsed_position_pct": 0,
        }
        assert any(warning.startswith("unsupported_snapshot_version: prep 1 日")
                   for warning in payload["warnings"])
        assert "snapshot_missing: 2/4 交易日无快照（仅统计库内持久化快照）" \
            in payload["warnings"]
        assert_json_equal(payload, golden_json("decision_review_prep.json"))

        human = run(box, "factor", "decision-review", "--source", "prep",
                    "--from", "20260907", "--to", "20260910",
                    "--min-days", "1", "--min-group-samples", "1",
                    scenario=scenario)
        assert_exit(human, 0)
        assert human.stdout.splitlines()[0].startswith(
            "决策回测 prep 2026-09-07~2026-09-10｜快照 1/4 日")
        assert "horizon=1" in human.stdout
        assert "差值 推荐−不推荐:" in human.stdout


def test_f18_decision_review_auction_priority_and_coverage_basis():
    with sandbox("f18") as box:
        seed_db(box, _auction_decision_seed(), db_name="assets.db")
        scenario = _decision_scenario(box)
        result = run(box, "factor", "decision-review", "--source", "auction",
                     "--from", "20260909", "--to", "20260909", "--horizon", "1",
                     "--min-days", "1", "--min-group-samples", "1", "--json",
                     "--include-samples", scenario=scenario)
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert list(payload) == DECISION_REVIEW_KEYS + ["samples"]
        assert payload["status"] == "ok"
        assert payload["coverage_basis"] == "persisted_snapshots"
        assert [group["group"] for group in payload["groups"]] == [
            "strong", "medium", "weak", "vetoed", "unavailable"]
        groups = {group["group"]: group for group in payload["groups"]}
        assert groups["strong"]["horizons"]["1"]["up"] == 1
        assert groups["medium"]["horizons"]["1"]["direction_evaluable"] is False
        assert groups["weak"]["horizons"]["1"]["direction_hit_rate"] == 0.0
        assert groups["vetoed"]["horizons"]["1"]["direction_hit_rate"] == 0.0
        assert payload["spreads"]["1"]["pair"] == ["strong", "weak"]
        assert _close(payload["spreads"]["1"]["value"], -2.0)
        assert payload["snapshots"] == {
            "requested_days": 1, "with_snapshot": 1, "missing": 0, "usable": 1,
            "unsupported_version": 1, "malformed": 0, "empty_decision": 0,
            "corrupt_rows": 1, "non_trading_snapshot_dates": 0,
        }
        assert payload["versions"]["seen"] == {"1": 1, "2": 5}
        assert payload["versions"]["unsupported_units"] == 1
        assert "coverage_basis=persisted_snapshots: 未持久化的空盘口/失败候选" \
               "不在分母内（无 run manifest）" in payload["warnings"]
        assert "corrupt_snapshot_rows: 1 行 JSON 损坏已跳过" in payload["warnings"]
        assert payload["caveats"][-1] == \
            "auction 分母仅为已持久化快照，不含未持久化的空盘口/失败候选"
        assert [sample["code"] for sample in payload["samples"]] == [
            "600001", "600002", "600003", "600004", "600005"]
        assert_json_equal(payload, golden_json("decision_review_auction.json"))


def test_f19_decision_review_param_errors_and_empty_db():
    with sandbox("f19") as box:
        missing_source = run(box, "factor", "decision-review",
                             "--from", "20260909", "--to", "20260909")
        assert_exit(missing_source, 2)
        assert missing_source.stdout == ""

        bad_horizon = run(box, "factor", "decision-review", "--source", "prep",
                          "--from", "20260909", "--to", "20260909",
                          "--horizon", "0", "--json")
        assert_exit(bad_horizon, 2)
        assert json.loads(bad_horizon.stdout) == {
            "ok": False, "error": "horizon 必须是 ≥ 1 的整数: 0"}

        bad_cost = run(box, "factor", "decision-review", "--source", "prep",
                       "--from", "20260909", "--to", "20260909",
                       "--cost-bps", "-1", "--json")
        assert_exit(bad_cost, 2)
        assert json.loads(bad_cost.stdout) == {
            "ok": False, "error": "cost_bps 必须是有限非负数: -1.0"}

        empty = run(box, "factor", "decision-review", "--source", "prep",
                    "--from", "20260907", "--to", "20260910", "--json")
        assert_exit(empty, 0)
        empty_payload = json.loads(empty.stdout)
        assert empty_payload["status"] == "insufficient_data"
        assert empty_payload["reason"] == "no_snapshot_in_range"
        assert empty_payload["snapshots"]["with_snapshot"] == 0
        assert not (box.data_dir / "assets.db").exists()


def test_f20_decision_review_is_read_only():
    with sandbox("f20") as box:
        seed_db(box, _merge_seed(_prep_decision_seed(), _auction_decision_seed()),
                db_name="assets.db")
        db_path = box.data_dir / "assets.db"
        scenario = _decision_scenario(box)
        before_tables = set(sqlite_tables(db_path))
        before_prep = sqlite_rows(db_path, "prep_snapshots", order_by="trade_date")
        before_auction = sqlite_rows(db_path, "auction_snapshots",
                                     order_by="trade_date, code")

        assert_exit(run(box, "factor", "decision-review", "--source", "prep",
                        "--from", "20260907", "--to", "20260910",
                        "--min-days", "1", "--min-group-samples", "1", "--json",
                        scenario=scenario), 0)
        assert_exit(run(box, "factor", "decision-review", "--source", "auction",
                        "--from", "20260909", "--to", "20260909", "--horizon", "1",
                        "--min-days", "1", "--min-group-samples", "1", "--json",
                        scenario=scenario), 0)

        assert sqlite_rows(db_path, "prep_snapshots",
                           order_by="trade_date") == before_prep
        assert sqlite_rows(db_path, "auction_snapshots",
                           order_by="trade_date, code") == before_auction
        assert sqlite_count(db_path, "factor_values") == 0
        after_tables = set(sqlite_tables(db_path))
        # 读路径可能按 BASE_SCHEMA 补齐标准空表；绝不新建业务表。
        assert (after_tables - before_tables) <= _ASSETS_STANDARD_TABLES
        assert after_tables <= _ASSETS_STANDARD_TABLES
