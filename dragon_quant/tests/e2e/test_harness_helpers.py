"""M1A harness helper contracts: ``canonical`` / ``assertlib`` / ``seed``.

The helpers are stdlib-only and operate exclusively on temporary databases and
files.  These tests live in the parent process and never import the
application.
"""

from __future__ import annotations

import sqlite3

import pytest

from dragon_quant.tests.e2e.harness import assertlib, canonical, seed
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox

NOW = "2026-09-11T15:30:00+08:00"


# ─── canonical ─────────────────────────────────────────────────────────────


def test_canonical_dumps_is_deterministic_and_rejects_nan():
    assert canonical.dumps({"b": 1, "a": [2]}) == '{"a":[2],"b":1}'
    assert canonical.dumps({"b": 1, "a": [2]}) == canonical.dumps({"a": [2], "b": 1})
    with pytest.raises(ValueError):
        canonical.dumps({"x": float("nan")})


def test_canonical_float_tolerance_and_structural_equality():
    canonical.assert_json_equal({"x": 1.0, "y": [2, "a"]}, {"x": 1.0 + 1e-12, "y": [2, "a"]})
    with pytest.raises(AssertionError, match=r"\$\.x"):
        canonical.assert_json_equal({"x": 1.0}, {"x": 1.1})
    with pytest.raises(AssertionError, match="缺失"):
        canonical.assert_json_equal({}, {"x": 1})
    with pytest.raises(AssertionError, match="多余"):
        canonical.assert_json_equal({"x": 1}, {})
    with pytest.raises(AssertionError, match="bool"):
        canonical.assert_json_equal({"x": 1}, {"x": True})


def test_canonical_normalizes_sandbox_paths(tmp_path):
    text = f"root={tmp_path / 'data'} done"
    normalized = canonical.normalize_paths(text, tmp_path)
    assert normalized == "root=<ROOT>/data done"
    assert canonical.normalize_newlines("a\r\nb\rc") == "a\nb\nc"


def test_canonical_hash_helpers(tmp_path):
    path = tmp_path / "payload.bin"
    path.write_bytes(b"payload")
    assert canonical.sha256_bytes(b"payload") == canonical.sha256_file(path)


# ─── seed ──────────────────────────────────────────────────────────────────

SMOKE_SPEC = {
    "tables": {
        "scans_v2": {
            "columns": {
                "scan_id": "TEXT PRIMARY KEY",
                "trade_date": "TEXT NOT NULL",
                "created_at": "TEXT",
            },
            "rows": [
                {
                    "scan_id": "v2_20260911_5",
                    "trade_date": "2026-09-11",
                    "created_at": "2026-09-11T15:30:00",
                }
            ],
        },
        "dragons_v2": {
            "columns": {
                "trade_date": "TEXT NOT NULL",
                "code": "TEXT NOT NULL",
                "payload": "TEXT",
            },
            "primary_key": ["trade_date", "code"],
            "rows": [
                {"trade_date": "2026-09-11", "code": "600172", "payload": '{"ok":true}'}
            ],
        },
    }
}


def test_seed_creates_only_the_target_temporary_database(tmp_path):
    target = seed.create_sqlite(tmp_path / "dragon.db", SMOKE_SPEC)
    assert target.is_file()
    assert assertlib.sqlite_tables(target) == ["dragons_v2", "scans_v2"]
    assert assertlib.sqlite_count(target, "scans_v2") == 1
    rows = assertlib.sqlite_rows(target, "dragons_v2", order_by="code")
    assert rows == [
        {"trade_date": "2026-09-11", "code": "600172", "payload": '{"ok":true}'}
    ]


def test_seed_refuses_overwrite_and_unsafe_identifiers(tmp_path):
    target = tmp_path / "dragon.db"
    seed.create_sqlite(target, SMOKE_SPEC)
    with pytest.raises(ValueError, match="已存在"):
        seed.create_sqlite(target, SMOKE_SPEC)

    with pytest.raises(ValueError, match="非法标识符"):
        seed.create_sqlite(tmp_path / "bad.db", {"tables": {"a; DROP": {"columns": {"x": "TEXT"}}}})
    with pytest.raises(ValueError, match="非法列类型"):
        seed.create_sqlite(tmp_path / "bad2.db", {"tables": {"t": {"columns": {"x": "TEXT; DROP"}}}})


def test_seed_db_stays_inside_the_sandbox():
    box = EnvSandbox.for_case("seed", now=NOW)
    try:
        target = seed.seed_db(box, "smoke")
        assert target == (box.data_dir / "dragon.db").resolve()
        assert assertlib.sqlite_count(target, "scans_v2") == 1
        with pytest.raises(ValueError, match="数据库名"):
            seed.seed_db(box, SMOKE_SPEC, db_name="../escape.db")
    finally:
        box.cleanup()


# ─── assertlib ─────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_assertlib_process_assertions():
    good = _Result(stdout='{"ok": true}\n')
    assertlib.assert_exit(good)
    assertlib.assert_stdout_contains(good, "ok")
    assertlib.assert_stderr_empty(good)
    assert assertlib.parse_stdout_json(good) == {"ok": True}

    with pytest.raises(AssertionError, match="exit"):
        assertlib.assert_exit(_Result(returncode=2), 0)
    with pytest.raises(AssertionError, match="stdout"):
        assertlib.assert_stdout_empty(_Result(stdout="x"))
    with pytest.raises(AssertionError, match="stderr"):
        assertlib.assert_stderr_empty(_Result(stderr="x"))


def test_assertlib_json_key_order(tmp_path):
    payload = {"ok": True, "items": []}
    assertlib.assert_json_keys_in_order(payload, ["ok", "items"])
    with pytest.raises(AssertionError, match="键顺序"):
        assertlib.assert_json_keys_in_order(payload, ["items", "ok"])


def test_assertlib_file_and_database_helpers(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = data_dir / "dragon.db"
    seed.create_sqlite(db, SMOKE_SPEC)

    assertlib.assert_tables(db, ["scans_v2", "dragons_v2"])
    assertlib.assert_row_count(db, "scans_v2", 1)
    assertlib.assert_no_business_db(tmp_path)

    with pytest.raises(AssertionError, match="业务数据库"):
        assertlib.assert_no_business_db(data_dir)

    payload = tmp_path / "out.json"
    payload.write_text('{"ok": true}', encoding="utf-8")
    assertlib.assert_file(payload, contains='"ok"')
    assertlib.assert_no_file(tmp_path / "missing.json")
    assert assertlib.list_files(tmp_path) == ["data/dragon.db", "out.json"]


def test_assertlib_missing_database_is_an_error(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        assertlib.sqlite_tables(tmp_path / "missing.db")
