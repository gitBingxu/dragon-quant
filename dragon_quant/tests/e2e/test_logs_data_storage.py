"""M1C local logs/data/storage baseline: L1–L7, D1–D10, ST1–ST5.

Every case runs the real ``python -m dragon_quant`` subprocess inside the M0
sandbox (temporary ``DQ_DATA_DIR``/``HOME``, frozen clock, fake providers,
blocked network).  Read-only commands assert that only import-time schema
creation happens; destructive commands touch the temporary root only.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from dragon_quant.tests.e2e.harness.assertlib import (
    assert_exit,
    list_files,
    sqlite_count,
    sqlite_tables,
)
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.seed import seed_db

E2E_ROOT = Path(__file__).resolve().parent
SCENARIO = E2E_ROOT / "fixtures" / "scenario_local.json"
GOLDEN = E2E_ROOT / "golden" / "local"
NOW = "2026-09-11T15:30:00+08:00"

# Frozen-clock epoch seconds (Asia/Shanghai) used by the seeded log rows.
TS = {
    "sep01": 1788228000.0,
    "sep02": 1788314400.0,
    "sep09": 1788919200.0,
    "sep09b": 1788919500.0,
    "sep11": 1789092000.0,
    "sep11b": 1789092060.0,
}
CUTOFF_SEP04 = 1788507000.0

LOG_COLUMNS = {
    "id": "INTEGER PRIMARY KEY",
    "scan_id": "TEXT NOT NULL",
    "ts": "REAL",
    "category": "TEXT",
    "level": "TEXT",
    "message": "TEXT",
    "code": "TEXT",
    "data_json": "TEXT",
    "source": "TEXT",
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


def run_local(box: EnvSandbox, args: list[str], *,
              env: dict[str, str] | None = None, timeout: int = 120,
              clock_mode: str = "runtime"):
    overrides = {"DQ_E2E_SCENARIO": str(SCENARIO),
                 "DQ_E2E_CLOCK_MODE": clock_mode}
    overrides.update(env or {})
    return runner().run_cli(args, sandbox=box, timeout=timeout,
                            env_override=overrides)


def run_python_local(box: EnvSandbox, code: str, *, timeout: int = 60,
                     clock_mode: str = "runtime"):
    overrides = {"DQ_E2E_SCENARIO": str(SCENARIO),
                 "DQ_E2E_CLOCK_MODE": clock_mode}
    return runner().run_python(code, sandbox=box, timeout=timeout,
                               env_override=overrides)


def golden(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def call_records(box: EnvSandbox) -> list[dict]:
    if not box.call_log.exists():
        return []
    return [json.loads(line) for line in
            box.call_log.read_text(encoding="utf-8").splitlines() if line.strip()]


def transport_methods(box: EnvSandbox) -> list[str]:
    return [record["method"] for record in call_records(box)
            if record["method"].startswith("get_") or record["method"] == "batch_get_quotes"]


def json_lines(result) -> list[dict]:
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def json_tail(stdout: str) -> dict:
    """Parse the indented JSON object that follows an optional warning line."""

    lines = stdout.splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == "{")
    return json.loads("\n".join(lines[start:]))


def assert_no_business_side_effects(box: EnvSandbox) -> None:
    """Read-only commands must not materialise result/cache files or assets.db."""

    for key in ("results", "cache", "shared"):
        assert list_files(box.data_dir / key) == []
    assert not (box.data_dir / "assets.db").exists()


def _log_row(scan_id: str, ts: float, category: str, level: str,
             message: str, code: str, data: dict, source: str = "v2") -> dict:
    return {
        "scan_id": scan_id, "ts": ts, "category": category, "level": level,
        "message": message, "code": code,
        "data_json": json.dumps(data, ensure_ascii=False), "source": source,
    }


def logs_seed() -> dict:
    v2_rows = [
        _log_row("v2_20260911_5", TS["sep11b"], "scorer:liquidity", "error",
                 "流动性异常", "600002", {"error": "x"}),
        _log_row("v2_20260911_5", TS["sep11"], "phase:F", "info", "完成", "", {}),
        _log_row("v2_20260910_5", TS["sep09b"], "scorer:drive", "info",
                 "drive ok", "600001", {"score": 72.5}),
        _log_row("v2_20260910_5", TS["sep09"], "api:tdx", "info", "TDX ok", "",
                 {"ok": True, "elapsed_ms": 30}),
        _log_row("v2_20260910_5", TS["sep02"], "api:ths", "warn", "THS 403", "",
                 {"ok": False, "elapsed_ms": 120}),
        _log_row("v2_20260910_5", TS["sep01"], "phase:A", "info", "板块排行", "",
                 {"degraded": []}),
    ]
    v1_rows = [
        _log_row("v1_20260910_5", TS["sep09"], "phase:A", "info", "v1 日志", "",
                 {}, source="v1"),
    ]
    return {"tables": {
        "scan_logs_v2": {"columns": LOG_COLUMNS, "rows": v2_rows},
        "scan_logs_v1": {"columns": LOG_COLUMNS, "rows": v1_rows},
    }}


# ─── L1 tail ────────────────────────────────────────────────────────────────


def test_l1_tail_empty_and_seeded_with_line_limit():
    expected = golden("logs.json")
    with sandbox("l1-empty") as box:
        result = run_local(box, ["logs", "tail"])
        assert_exit(result, 0)
        assert result.stdout == ""
        assert result.stderr == ""

    with sandbox("l1") as box:
        seed_db(box, logs_seed())
        result = run_local(box, ["logs", "tail"])
        assert_exit(result, 0)
        assert result.stderr == ""
        assert json_lines(result) == expected["tail"]

        limited = run_local(box, ["logs", "tail", "-n", "2"])
        assert_exit(limited, 0)
        assert json_lines(limited) == expected["tail"][:2]


# ─── L2 query filters ───────────────────────────────────────────────────────


def test_l2_query_filters_by_date_category_level_code_and_tail():
    expected = golden("logs.json")
    with sandbox("l2") as box:
        seed_db(box, logs_seed())

        by_date = run_local(box, ["logs", "query", "--date", "20260910"])
        assert_exit(by_date, 0)
        assert json_lines(by_date) == expected["query_date_0910"]

        by_phase = run_local(box, ["logs", "query", "--category", "phase"])
        assert_exit(by_phase, 0)
        assert json_lines(by_phase) == expected["query_phase"]

        no_error = run_local(
            box, ["logs", "query", "--date", "20260910", "--level", "error"])
        assert_exit(no_error, 0)
        assert no_error.stdout == ""

        by_code = run_local(box, ["logs", "query", "--code", "600001"])
        assert_exit(by_code, 0)
        assert json_lines(by_code) == expected["query_code"]

        limited = run_local(box, ["logs", "query", "--tail", "1"])
        assert_exit(limited, 0)
        assert json_lines(limited) == expected["tail"][:1]


# ─── L3 clear ───────────────────────────────────────────────────────────────


def test_l3_clear_removes_old_v2_rows_and_keeps_v1_and_kept_minus_one():
    with sandbox("l3") as box:
        seed_db(box, logs_seed())
        db = box.data_dir / "dragon.db"
        assert sqlite_count(db, "scan_logs_v2") == 6
        assert sqlite_count(db, "scan_logs_v1") == 1

        result = run_local(box, ["logs", "clear", "--days", "7"])
        assert_exit(result, 0)
        assert result.stderr == ""
        # Current contract: the retained count is a literal -1 (not computed).
        assert result.stdout == "清除日志: 2 条记录\n保留: -1 条记录\n"
        # Sep 01 and Sep 02 rows are older than the frozen cutoff (Sep 04 15:30).
        assert sqlite_count(db, "scan_logs_v2") == 4
        assert sqlite_count(db, "scan_logs_v1") == 1

        remaining = run_local(box, ["logs", "tail"])
        assert_exit(remaining, 0)
        assert [entry["ts"] for entry in json_lines(remaining)] == [
            TS["sep11b"], TS["sep11"], TS["sep09b"], TS["sep09"]]


# ─── L4 list ────────────────────────────────────────────────────────────────


def test_l4_list_empty_and_grouped_table():
    expected = golden("logs.json")
    with sandbox("l4-empty") as box:
        result = run_local(box, ["logs", "list"])
        assert_exit(result, 0)
        assert result.stdout == "(无日志记录)\n"

    with sandbox("l4") as box:
        seed_db(box, logs_seed())
        result = run_local(box, ["logs", "list"])
        assert_exit(result, 0)
        lines = result.stdout.splitlines()
        assert lines[0].startswith("扫描ID")
        assert lines[1] == "-" * 32
        assert [[row[0], int(row[1])] for row in
                (line.split() for line in lines[2:])] == expected["list_rows"]


# ─── L5 summary ─────────────────────────────────────────────────────────────


def test_l5_summary_empty_and_seeded_payload():
    expected = golden("logs.json")["summary"]
    with sandbox("l5-empty") as box:
        result = run_local(box, ["logs", "summary"])
        assert_exit(result, 0)
        assert json.loads(result.stdout) == {"error": "无日志"}

    with sandbox("l5") as box:
        seed_db(box, logs_seed())
        result = run_local(box, ["logs", "summary"])
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert payload == expected
        assert list(payload) == list(expected)
        assert list(payload["api_stats"]["by_provider"]) == ["tdx", "ths"]
        assert_no_business_side_effects(box)


# ─── L6 source v1 ───────────────────────────────────────────────────────────


def test_l6_source_v1_reads_v1_only():
    with sandbox("l6") as box:
        seed_db(box, logs_seed())
        db = box.data_dir / "dragon.db"
        v2_before = sqlite_count(db, "scan_logs_v2")

        tail = run_local(box, ["logs", "--source", "v1", "tail"])
        assert_exit(tail, 0)
        entries = json_lines(tail)
        assert len(entries) == 1
        assert entries[0]["scan_id"] == "v1_20260910_5"
        assert entries[0]["source"] == "v1"

        summary = run_local(box, ["logs", "--source", "v1", "summary"])
        assert_exit(summary, 0)
        assert json.loads(summary.stdout)["source"] == "v1"

        assert sqlite_count(db, "scan_logs_v2") == v2_before


# ─── L7 group without action ────────────────────────────────────────────────


def test_l7_logs_without_action_is_silent_and_writes_nothing():
    with sandbox("l7") as box:
        result = run_local(box, ["logs"])
        assert_exit(result, 0)
        assert result.stdout == ""
        assert result.stderr == ""
        # B1: a group without an action performs no storage access at all, so
        # dragon.db is no longer created by importing the package.
        assert not (box.data_dir / "dragon.db").exists()
        assert_no_business_side_effects(box)


# ─── D1 sector ──────────────────────────────────────────────────────────────


def test_d1_sector_ranking_desc_and_asc_order():
    expected = golden("data.json")
    with sandbox("d1") as box:
        desc = run_local(box, ["data", "sector"])
        assert_exit(desc, 0)
        assert desc.stderr == ""
        assert desc.stdout.splitlines() == expected["sector_desc"]
        assert transport_methods(box) == ["get_sector_ranking"]

        asc = run_local(box, ["data", "sector", "--asc"])
        assert_exit(asc, 0)
        assert asc.stdout.splitlines() == expected["sector_asc"]

        assert_no_business_side_effects(box)
        # B1: pure data reads never open storage, so no dragon.db exists at all.
        assert not (box.data_dir / "dragon.db").exists()


# ─── D2 components ──────────────────────────────────────────────────────────


def test_d2_components_requires_sector_and_lists_rows():
    expected = golden("data.json")
    with sandbox("d2") as box:
        result = run_local(box, ["data", "components", "--sector", "881001"])
        assert_exit(result, 0)
        assert result.stdout.splitlines() == expected["components"]
        assert transport_methods(box) == ["get_sector_components"]

        missing = run_local(box, ["data", "components"])
        assert missing.returncode == 2
        assert missing.stdout == ""
        assert "--sector" in missing.stderr
        assert "required" in missing.stderr
        assert_no_business_side_effects(box)


# ─── D3 kline ───────────────────────────────────────────────────────────────


def test_d3_kline_sources_and_empty_result():
    expected = golden("data.json")
    with sandbox("d3") as box:
        xq = run_local(box, ["data", "kline", "--code", "600010"])
        assert_exit(xq, 0)
        assert json_lines(xq) == expected["kline"]
        assert transport_methods(box) == ["get_kline"]

        tencent = run_local(
            box, ["data", "kline", "--code", "600010", "--source", "tencent"])
        assert_exit(tencent, 0)
        # Tencent gtimg has no daily K line and production returns an empty list.
        assert tencent.stdout == ""
        tencent_calls = [record for record in call_records(box)
                         if record["provider"] == "tencent"
                         and record["method"] == "get_kline"]
        assert [record["args"]["code"] for record in tencent_calls] == ["600010"]

        empty = run_local(box, ["data", "kline", "--code", "600020"])
        assert_exit(empty, 0)
        assert empty.stdout == ""
        assert_no_business_side_effects(box)


# ─── D4 minute ──────────────────────────────────────────────────────────────


def test_d4_minute_xueqiu_and_tencent_bars():
    expected = golden("data.json")
    with sandbox("d4") as box:
        xq = run_local(box, ["data", "minute", "--code", "600010"])
        assert_exit(xq, 0)
        assert json_lines(xq) == expected["minute"]
        assert transport_methods(box) == ["get_minute_kline"]

        tencent = run_local(
            box, ["data", "minute", "--code", "600012", "--source", "tencent"])
        assert_exit(tencent, 0)
        assert json_lines(tencent) == expected["five_min"]
        assert "get_5min_kline" in transport_methods(box)
        assert_no_business_side_effects(box)


# ─── D5 quote ───────────────────────────────────────────────────────────────


def test_d5_quote_success_and_failure_message():
    expected = golden("data.json")["quote"]
    with sandbox("d5") as box:
        ok = run_local(box, ["data", "quote", "--code", "600010"])
        assert_exit(ok, 0)
        payload = json.loads(ok.stdout)
        assert payload == expected
        assert list(payload) == list(expected)

        missing = run_local(box, ["data", "quote", "--code", "600002"])
        assert_exit(missing, 0)
        assert missing.stdout == "获取行情失败: 600002\n"
        assert_no_business_side_effects(box)


# ─── D6 batch quote ─────────────────────────────────────────────────────────


def test_d6_batch_quote_prints_every_code_in_one_call():
    expected = golden("data.json")["batch_quotes"]
    with sandbox("d6") as box:
        result = run_local(
            box, ["data", "batch-quote", "--codes", "600010,600002"])
        assert_exit(result, 0)
        assert json_lines(result) == expected
        batch_calls = [record for record in call_records(box)
                       if record["method"] == "batch_get_quotes"]
        assert len(batch_calls) == 1
        assert batch_calls[0]["args"]["codes"]["len"] == 2
        assert_no_business_side_effects(box)


# ─── D7 cookie status ───────────────────────────────────────────────────────


def test_d7_cookie_status_empty_and_present():
    expected = golden("data.json")["cookie_status_empty"]
    with sandbox("d7-empty") as box:
        result = run_local(box, ["data", "cookie-status"])
        assert_exit(result, 0)
        assert json.loads(result.stdout) == expected
        assert transport_methods(box) == []

    with sandbox("d7") as box:
        cookie = box.data_dir / "cookies" / "xueqiu"
        cookie.parent.mkdir(parents=True, exist_ok=True)
        cookie.write_text("xq_a_token=synthetic; u=1", encoding="utf-8")
        result = run_local(box, ["data", "cookie-status"])
        assert_exit(result, 0)
        assert json.loads(result.stdout) == {
            "xueqiu": {"ok": True, "length": len("xq_a_token=synthetic; u=1")}}
        assert cookie.read_text(encoding="utf-8") == "xq_a_token=synthetic; u=1"


# ─── D8 cookie set ──────────────────────────────────────────────────────────


def test_d8_cookie_set_writes_only_the_temporary_cookie_file():
    with sandbox("d8") as box:
        result = run_local(
            box, ["data", "cookie-set", "--cookie", "xq_a_token=abc; u=1"])
        assert_exit(result, 0)
        cookie = box.data_dir / "cookies" / "xueqiu"
        assert result.stdout == f"✅ 雪球 Cookie -> {cookie}\n"
        assert cookie.read_text(encoding="utf-8") == "xq_a_token=abc; u=1"
        assert box.root in cookie.resolve().parents
        assert transport_methods(box) == []

        missing = run_local(box, ["data", "cookie-set"])
        assert missing.returncode == 2
        assert missing.stdout == ""
        assert "--cookie" in missing.stderr


# ─── D9 cookie fetch (fake playwright) ─────────────────────────────────────


def test_d9_cookie_fetch_success_writes_temp_cookie_via_fake_playwright():
    with sandbox("d9-ok") as box:
        result = run_local(box, ["data", "cookie-fetch"],
                           env={"DQ_E2E_COOKIE_FETCH": "ok"})
        assert_exit(result, 0)
        assert result.stderr == ""
        cookie = box.data_dir / "cookies" / "xueqiu"
        assert cookie.read_text(encoding="utf-8") == (
            "xq_a_token=e2e-synthetic-token; u=e2e-synthetic-user")
        lines = result.stdout.splitlines()
        assert lines[0] == f"✅ 雪球 Cookie -> {cookie}"
        assert json.loads("\n".join(lines[1:])) == {
            "xueqiu": {"ok": True, "length": 52}}
        gotos = [record for record in call_records(box)
                 if record["method"] == "page.goto"]
        assert [record["url"] for record in gotos] == ["https://xueqiu.com/"]


def test_d9_cookie_fetch_empty_and_error_never_write():
    with sandbox("d9-empty") as box:
        result = run_local(box, ["data", "cookie-fetch"],
                           env={"DQ_E2E_COOKIE_FETCH": "empty"})
        assert_exit(result, 0)
        assert "⚠️ 雪球 Cookie 获取失败" in result.stdout
        assert json_tail(result.stdout) == {
            "xueqiu": {"ok": False, "length": 0}}
        assert not (box.data_dir / "cookies" / "xueqiu").exists()
        assert [record["method"] for record in call_records(box)
                if record["method"] == "context.cookies"] == ["context.cookies"]

    with sandbox("d9-error") as box:
        result = run_local(box, ["data", "cookie-fetch"],
                           env={"DQ_E2E_COOKIE_FETCH": "error"})
        assert_exit(result, 0)
        assert "❌ E2E fake playwright failure" in result.stderr
        assert json.loads(result.stdout) == {
            "xueqiu": {"ok": False, "length": 0}}
        assert not (box.data_dir / "cookies" / "xueqiu").exists()
        launches = [record for record in call_records(box)
                    if record["method"] == "chromium.launch"]
        assert len(launches) == 1
        # The fake abort happens before any browser context exists.
        assert not any(record["method"] == "browser.new_context"
                       for record in call_records(box))


# ─── D10 group without action ───────────────────────────────────────────────


def test_d10_data_without_action_is_silent():
    with sandbox("d10") as box:
        result = run_local(box, ["data"])
        assert_exit(result, 0)
        assert result.stdout == ""
        assert result.stderr == ""
        assert transport_methods(box) == []
        assert_no_business_side_effects(box)


# ─── ST1 status ─────────────────────────────────────────────────────────────


def test_st1_status_fresh_layout_and_missing_databases():
    with sandbox("st1") as box:
        result = run_local(box, ["storage", "status"])
        assert_exit(result, 0)
        assert result.stderr == ""
        lines = result.stdout.splitlines()
        assert lines[0] == f"数据根目录: {box.data_dir}"
        assert lines[1] == f"{'目录':10s} {'文件数':>6s} {'大小':>8s}"
        assert lines[2] == "-" * 28
        # B1: importing the package and reading storage metadata no longer
        # creates the data directories or dragon.db.
        for index, key in enumerate(("cookies", "cache", "logs", "results", "shared")):
            assert lines[3 + index] == f"{key:10s}  (不存在)", lines[3 + index]
        assert lines[8] == f"{'数据库':10s} {'':>6s} {'大小':>8s}"
        assert lines[9] == "-" * 28
        assert lines[10] == f"{'dragon_db':10s}  (不存在)", lines[10]
        assert lines[11] == f"{'assets_db':10s}  (不存在)"
        assert len(lines) == 12  # no legacy prep.db line on a fresh root

        # Status is read-only and B1-lazy: no database file is materialised.
        assert not (box.data_dir / "dragon.db").exists()

    with sandbox("st1-all-dbs") as box:
        # ``status`` only stats the database files, so plain stand-ins are
        # enough to pin the three-database and legacy-note output contract.
        (box.data_dir / "assets.db").write_bytes(b"x" * 100)
        (box.data_dir / "prep.db").write_bytes(b"x" * 50)
        result = run_local(box, ["storage", "status"])
        assert_exit(result, 0)
        lines = result.stdout.splitlines()
        assert re.fullmatch(r"assets_db\s+\S+", lines[11]), lines[11]
        assert "(不存在)" not in lines[11]
        assert lines[12].startswith(
            "legacy_prep_db (历史库，不再读写，确认 assets.db 有数据后可删除) ")
        assert lines[12].endswith("50B")


# ─── ST2 size ───────────────────────────────────────────────────────────────


def _write_bytes(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_st2_size_sums_each_temporary_directory():
    with sandbox("st2") as box:
        _write_bytes(box.data_dir / "cache" / "a.bin", 1024)
        _write_bytes(box.data_dir / "cache" / "b.bin", 1024)
        _write_bytes(box.data_dir / "logs" / "a.log", 512)
        _write_bytes(box.data_dir / "shared" / "s.bin", 2048)

        result = run_local(box, ["storage", "size"])
        assert_exit(result, 0)
        assert result.stdout == (
            "总占用: 4.5K\n"
            "  cookies: 0B\n"
            "  cache: 2.0K\n"
            "  logs: 512B\n"
            "  results: 0B\n"
            "  shared: 2.0K\n"
        )
        assert list_files(box.data_dir / "cache") == ["a.bin", "b.bin"]


# ─── ST3 clear single item ─────────────────────────────────────────────────


def test_st3_clear_cache_only_removes_cache_files():
    with sandbox("st3-cache") as box:
        _write_bytes(box.data_dir / "cache" / "a.bin", 10)
        _write_bytes(box.data_dir / "results" / "r.txt", 10)
        result = run_local(box, ["storage", "clear", "--cache"])
        assert_exit(result, 0)
        assert result.stdout == "  清理 cache: 1 个文件\n"
        assert list_files(box.data_dir / "cache") == []
        assert list_files(box.data_dir / "results") == ["r.txt"]


def test_st3_clear_results_respects_days_against_frozen_clock():
    with sandbox("st3-results") as box:
        old = box.data_dir / "results" / "old.txt"
        fresh = box.data_dir / "results" / "fresh.txt"
        _write_bytes(old, 10)
        _write_bytes(fresh, 10)
        os.utime(old, (CUTOFF_SEP04 - 60, CUTOFF_SEP04 - 60))

        result = run_local(box, ["storage", "clear", "--results", "--days", "7"])
        assert_exit(result, 0)
        assert result.stdout == (
            "  清理 results: 1 个文件\n"
            "    (保留最近 7 天)\n"
        )
        assert not old.exists()
        assert fresh.exists()


def test_st3_clear_logs_removes_files_and_both_log_tables():
    with sandbox("st3-logs") as box:
        seed_db(box, logs_seed())
        _write_bytes(box.data_dir / "logs" / "a.log", 10)
        _write_bytes(box.data_dir / "logs" / "b.log", 10)
        db = box.data_dir / "dragon.db"

        result = run_local(box, ["storage", "clear", "--logs"])
        assert_exit(result, 0)
        # 2 files + 6 v2 rows + 1 v1 row
        assert result.stdout == "  清理 logs: 9 个文件\n"
        assert list_files(box.data_dir / "logs") == []
        assert sqlite_count(db, "scan_logs_v2") == 0
        assert sqlite_count(db, "scan_logs_v1") == 0


# ─── ST4 clear all ──────────────────────────────────────────────────────────


def test_st4_clear_all_empties_directories_and_protects_databases():
    with sandbox("st4") as box:
        seed_db(box, logs_seed())
        _write_bytes(box.data_dir / "cache" / "a.bin", 10)
        _write_bytes(box.data_dir / "results" / "r.txt", 10)
        _write_bytes(box.data_dir / "logs" / "a.log", 10)
        _write_bytes(box.data_dir / "shared" / "s.bin", 10)
        db = box.data_dir / "dragon.db"
        tables_before = sqlite_tables(db)

        result = run_local(box, ["storage", "clear", "--all"])
        assert_exit(result, 0)
        assert result.stdout == (
            "  清理 cache: 1 个文件\n"
            "  清理 results: 1 个文件\n"
            "  清理 logs: 8 个文件\n"  # 1 file + 6 v2 rows + 1 v1 row
            "  清理 shared: 1 个文件\n"
            "  保留数据库（不清理）:\n"
            f"    {db}\n"
        )
        for key in ("cache", "results", "logs", "shared"):
            assert list_files(box.data_dir / key) == []
        assert db.is_file()
        # ``storage clear`` never drops databases: the seeded log tables still
        # exist (import-time creation of the remaining schema is an M8 item).
        assert set(tables_before) <= set(sqlite_tables(db))


# ─── ST5 group without action ───────────────────────────────────────────────


def test_st5_storage_without_action_is_silent():
    with sandbox("st5") as box:
        result = run_local(box, ["storage"])
        assert_exit(result, 0)
        assert result.stdout == ""
        assert result.stderr == ""
        assert_no_business_side_effects(box)


# ─── M9B2: runtime-clock mode drives logs/storage/cache wall time ──────────


def test_m9b2_runtime_clock_mode_drives_logs_clear_cutoff():
    with sandbox("m9b2-logs-runtime") as box:
        seed_db(box, logs_seed())
        db = box.data_dir / "dragon.db"
        result = run_local(box, ["logs", "clear", "--days", "7"],
                           clock_mode="runtime")
        assert_exit(result, 0)
        assert result.stdout == "清除日志: 2 条记录\n保留: -1 条记录\n"
        assert sqlite_count(db, "scan_logs_v2") == 4
        assert sqlite_count(db, "scan_logs_v1") == 1


def test_m9b2_runtime_clock_mode_drives_storage_clear_days():
    with sandbox("m9b2-storage-runtime") as box:
        old = box.data_dir / "results" / "old.txt"
        fresh = box.data_dir / "results" / "fresh.txt"
        _write_bytes(old, 10)
        _write_bytes(fresh, 10)
        os.utime(old, (CUTOFF_SEP04 - 60, CUTOFF_SEP04 - 60))

        result = run_local(box, ["storage", "clear", "--results", "--days", "7"],
                           clock_mode="runtime")
        assert_exit(result, 0)
        assert result.stdout == (
            "  清理 results: 1 个文件\n"
            "    (保留最近 7 天)\n"
        )
        assert not old.exists()
        assert fresh.exists()


CACHE_PROBE = r"""
import json
import os
from pathlib import Path

from dragon_quant.infra.storage.cache import CacheEntry, DataCache, _today_end
from dragon_quant.infra import clock

root = Path(os.environ["DQ_DATA_DIR"]) / "cache-probe"
root.mkdir(parents=True, exist_ok=True)
now = clock.now().timestamp()
cache = DataCache(cache_dir=root)
cache.set("k", [1, 2, 3])
fresh = DataCache(cache_dir=root)
loaded = fresh.load_persisted("k", max_age=3600)
(root / "stale.json").write_text(
    json.dumps({"data": 1, "ts": now - 7200}), encoding="utf-8")
stale = fresh.load_persisted("stale", max_age=3600)
print(json.dumps({
    "today_end": _today_end(),
    "expired_past": CacheEntry("x", int(now) - 1).expired,
    "expired_future": CacheEntry("x", int(now) + 60).expired,
    "loaded": loaded,
    "stale": stale,
    "stale_file_exists": (root / "stale.json").exists(),
}))
"""


def test_m9b2_runtime_clock_mode_drives_cache_ttl_and_persisted_age():
    expected = {
        "today_end": 1789142399,  # 2026-09-11 23:59:59 +08:00
        "expired_past": True,
        "expired_future": False,
        "loaded": [1, 2, 3],
        "stale": None,
        "stale_file_exists": False,
    }
    with sandbox("m9b2-cache-runtime") as box:
        runtime = run_python_local(box, CACHE_PROBE, clock_mode="runtime")
        assert_exit(runtime, 0)
        legacy = run_python_local(box, CACHE_PROBE)
        assert_exit(legacy, 0)
    assert runtime.stdout == legacy.stdout
    assert json.loads(runtime.stdout) == expected
