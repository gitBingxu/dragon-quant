"""M1D2 assets behavior baseline: AS1-AS18.

Every case runs the real ``python -m dragon_quant assets ...`` subprocess through
the M0 sandbox (frozen clock, net/proc guards, mandatory preflight).  The object
store is the M0 fake adapter backed by ``<sandbox>/remote`` while every SQLite
database lives in ``<sandbox>/data``; the real data directory is watched by the
session metadata sentinel in ``conftest.py``.

Goldens under ``dragon_quant/tests/e2e/golden/assets`` are reviewed constants.  Values that
are content-derived (manifest ids, blob hashes, uploaded/downloaded bytes) are
*recomputed* from the temporary remote instead of being trusted as stored
numbers, so the golden and the object layout must agree independently.

Deviation discipline: assertions pin actual production behavior.  ``assets
verify --from/--to`` expands every calendar day in the range, ``assets pull
--target-dir`` restores ``raw_blob`` through the NULL-fill path, and
``backfill-dragons --dry-run`` still creates missing schema (it reads the DB
with ``migrate=False``); each of these is asserted as observed, never weakened.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from dragon_quant.tests.e2e.fixtures import seed_builders as sb
from dragon_quant.tests.e2e.harness.assertlib import (
    assert_exit,
    assert_json_keys_in_order,
    assert_stderr_contains,
    list_files,
    sqlite_count,
    sqlite_rows,
    sqlite_tables,
)
from dragon_quant.tests.e2e.harness.canonical import (
    assert_json_equal,
    sha256_bytes,
    sha256_file,
)
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.seed import seed_db

REPO_ROOT = Path(__file__).resolve().parents[3]
E2E_ROOT = Path(__file__).resolve().parent
SCENARIO_PATH = E2E_ROOT / "fixtures" / "scenario_assets.json"
GOLDEN = E2E_ROOT / "golden" / "assets"
PACKAGE_VERSION = re.search(
    r'(?m)^version\s*=\s*"([^"]+)"',
    (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")).group(1)

SCENARIO = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
NOW = SCENARIO["now"]
NOW_ISO = NOW
TRADE_DATE = SCENARIO["trade_date"]
PERIOD = SCENARIO["report_period"]
LEGACY_PERIOD = SCENARIO["legacy_period"]
EXPECTED = SCENARIO["expected"]
PHYSICAL_TABLES = EXPECTED["physical_tables"]
SYNC_UNITS = EXPECTED["sync_units"]
PARTITIONS = EXPECTED["partitions"]
SHARDS = EXPECTED["shards_by_partition"]
ROW_COUNTS = EXPECTED["row_counts"]

# Deterministic bytes for the layout-unverified raw financial blob.
RAW_BLOB = b"\x1f\x8bE2E-RAW-BLOB"

PULL_ARGS = ["pull", "--from", "2026-01-01", "--to", "2026-10-21"]
BACKFILL_KEYS = [
    "ok", "mode", "dry_run", "from", "to", "dates", "rows_source",
    "inserted", "updated", "unchanged", "missing_rows", "orphan_rows",
    "pruned", "failed", "warnings",
]


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


def run_assets(box: EnvSandbox, args: list[str], *,
               env: dict[str, str] | None = None, timeout: int = 120):
    return runner().run_cli(
        ["assets", *args], sandbox=box, timeout=timeout,
        env_override=env or {})


def payload_of(result) -> dict:
    try:
        return json.loads(result.stdout)
    except ValueError as exc:  # pragma: no cover - assertion aid
        raise AssertionError(
            f"stdout 不是合法 JSON: {result.stdout!r}\nstderr: {result.stderr!r}"
        ) from exc


def assert_assets_payload(result, command: str, exit_code: int) -> dict:
    """Top-level contract shared by doctor/push/pull/status/verify."""

    assert_exit(result, exit_code)
    data = payload_of(result)
    assert_json_keys_in_order(data, ["ok", "command", "failed", "summary"])
    assert data["command"] == command
    return data


def call_log(box: EnvSandbox) -> list[dict]:
    path = box.call_log
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def backend_calls(box: EnvSandbox) -> list[dict]:
    """Only the object-store adapter's calls (provider warnings share the log)."""

    return [entry for entry in call_log(box)
            if entry.get("provider") == "assets_backend"]


def assert_no_data_warehouse(data_dir: Path) -> None:
    """The assets commands must not create the assets warehouses.

    ``dragon.db`` *is* created by the package's ``storage.db`` module import
    (production startup behavior), so only ``assets.db`` / legacy ``prep.db``
    are asserted here.
    """

    for name in ("assets.db", "prep.db"):
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(data_dir) / f"{name}{suffix}"
            if candidate.exists():
                raise AssertionError(f"不应出现 {name}: {candidate}")


def golden(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def exec_sql(db_path: Path, sql: str, params: tuple = ()) -> None:
    """Mutate a *temporary sandbox database* (test setup only)."""

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()


def write_secrets(box: EnvSandbox) -> Path:
    path = box.data_dir / "secrets" / "assets_sync.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(SCENARIO["secrets"], ensure_ascii=False),
                    encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def copy_remote(source: EnvSandbox, target: EnvSandbox) -> None:
    shutil.copytree(source.remote, target.remote, dirs_exist_ok=True)


# ─── synthetic assets.db (all 15 physical tables) ───────────────────────────


def assets_seed_spec(*, full: bool = True) -> dict:
    td = TRADE_DATE
    period = PERIOD
    prep = {
        "columns": {
            "trade_date": "TEXT NOT NULL",
            "asset_type": "TEXT NOT NULL",
            "payload_json": "TEXT NOT NULL",
            "created_at": "TEXT",
        },
        "primary_key": ["trade_date", "asset_type"],
        "rows": [
            {"trade_date": td, "asset_type": "dragons",
             "payload_json": json.dumps({"count": 3}, sort_keys=True),
             "created_at": f"{td} 17:00:00"},
            {"trade_date": td, "asset_type": "collection_report",
             "payload_json": json.dumps({"dragons": {"status": "ok"}},
                                        sort_keys=True),
             "created_at": f"{td} 17:00:01"},
        ],
    }
    if not full:
        return {"tables": {"prep_assets": prep}}

    tables = {
        "prep_assets": prep,
        "auction_assets": {
            "columns": {
                "trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
                "name": "TEXT", "items_json": "TEXT NOT NULL",
                "quote_json": "TEXT", "created_at": "TEXT",
            },
            "primary_key": ["trade_date", "code"],
            "rows": [{
                "trade_date": td, "code": "002881", "name": "美格智能",
                "items_json": json.dumps(
                    [{"time": "09:25:00", "price": 42.3}], sort_keys=True),
                "quote_json": json.dumps({"last": 42.3}, sort_keys=True),
                "created_at": f"{td} 09:26:00",
            }],
        },
        "prep_snapshots": {
            "columns": {
                "trade_date": "TEXT NOT NULL", "source": "TEXT NOT NULL",
                "result_json": "TEXT NOT NULL", "created_at": "TEXT",
            },
            "primary_key": ["trade_date", "source"],
            "rows": [{
                "trade_date": td, "source": "v2",
                "result_json": json.dumps({"sentiment": {"position_pct": 0.5}},
                                          sort_keys=True),
                "created_at": f"{td} 17:05:00",
            }],
        },
        "auction_snapshots": {
            "columns": {
                "trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
                "source": "TEXT NOT NULL", "analysis_json": "TEXT NOT NULL",
                "created_at": "TEXT",
            },
            "primary_key": ["trade_date", "code", "source"],
            "rows": [
                {"trade_date": td, "code": "002881", "source": "v2",
                 "analysis_json": json.dumps({"strength": 72.5}, sort_keys=True),
                 "created_at": f"{td} 09:26:01"},
                {"trade_date": td, "code": "600172", "source": "v2",
                 "analysis_json": json.dumps({"strength": 60.0}, sort_keys=True),
                 "created_at": f"{td} 09:26:02"},
            ],
        },
        "dragons": {
            "columns": sb.DRAGON_COLUMNS,
            "primary_key": ["trade_date", "code"],
            "rows": [sb.dragon_row(td, "600172", "黄河旋风", 1, 80.5,
                                   ["培育钻石"])],
        },
        "factor_values": {
            "columns": {
                "trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
                "factor_name": "TEXT NOT NULL", "factor_version": "TEXT NOT NULL",
                "value": "REAL NOT NULL", "available_at": "TEXT NOT NULL",
                "source": "TEXT NOT NULL", "source_scan_id": "TEXT",
                "source_meta_json": "TEXT", "computed_at": "TEXT",
            },
            "primary_key": ["trade_date", "code", "factor_name", "factor_version"],
            "rows": [
                {"trade_date": td, "code": "600172",
                 "factor_name": "five_dim_drive", "factor_version": "v1",
                 "value": 78.5, "available_at": f"{td}T15:05:00",
                 "source": "scan", "source_scan_id": "v2_20261021_25",
                 "source_meta_json": json.dumps({"coverage": 0.82}, sort_keys=True),
                 "computed_at": f"{td} 15:06:00"},
                {"trade_date": td, "code": "002881",
                 "factor_name": "five_dim_drive", "factor_version": "v1",
                 "value": 65.0, "available_at": f"{td}T15:05:00",
                 "source": "scan", "source_scan_id": None,
                 "source_meta_json": None, "computed_at": f"{td} 15:06:00"},
            ],
        },
        "factor_build_runs": {
            "columns": {
                "trade_date": "TEXT NOT NULL", "started_at": "TEXT",
                "finished_at": "TEXT", "report_json": "TEXT NOT NULL",
                "created_at": "TEXT",
            },
            "rows": [{
                "trade_date": td, "started_at": f"{td} 15:00:00",
                "finished_at": f"{td} 15:05:00",
                "report_json": json.dumps({"ok": True, "build_ids": [3]},
                                          sort_keys=True),
                "created_at": f"{td} 15:05:01",
            }],
        },
        "factor_sector_snapshots": {
            "columns": {
                "trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
                "industry_code": "TEXT", "industry_name": "TEXT",
                "category": "TEXT NOT NULL", "source": "TEXT",
                "scan_id": "TEXT", "created_at": "TEXT",
            },
            "primary_key": ["trade_date", "code", "category"],
            "rows": [{
                "trade_date": td, "code": "600172", "industry_code": "881101",
                "industry_name": "半导体", "category": "hy", "source": "scan",
                "scan_id": "v2_20261021_25", "created_at": f"{td} 15:05:00",
            }],
        },
        "factor_registry": {
            "columns": {
                "factor_name": "TEXT NOT NULL", "factor_version": "TEXT NOT NULL",
                "label": "TEXT", "frequency": "TEXT NOT NULL",
                "source": "TEXT NOT NULL", "depends_on_json": "TEXT",
                "lookback": "INTEGER", "direction": "TEXT NOT NULL",
                "unit": "TEXT", "available_phase": "TEXT NOT NULL",
                "pit_rule": "TEXT NOT NULL", "scope_json": "TEXT",
                "params_json": "TEXT", "default_enabled": "INTEGER",
                "valid_from": "TEXT", "deprecated_at": "TEXT", "owner": "TEXT",
                "description": "TEXT", "spec_hash": "TEXT NOT NULL",
                "created_at": "TEXT",
            },
            "primary_key": ["factor_name", "factor_version"],
            "rows": [{
                "factor_name": "five_dim_drive", "factor_version": "v1",
                "label": "带动性", "frequency": "daily", "source": "scan",
                "depends_on_json": json.dumps(["kline"], sort_keys=True),
                "lookback": 10, "direction": "higher", "unit": "score",
                "available_phase": "scan", "pit_rule": "derived",
                "scope_json": json.dumps(["hs300"], sort_keys=True),
                "params_json": json.dumps({"w": 0.3}, sort_keys=True),
                "default_enabled": 1, "valid_from": None, "deprecated_at": None,
                "owner": None, "description": None, "spec_hash": "hash1",
                "created_at": "2026-10-01 10:00:00",
            }],
        },
        "factor_model_snapshots": {
            "columns": {
                "model_name": "TEXT NOT NULL", "model_version": "TEXT NOT NULL",
                "params_json": "TEXT NOT NULL",
                "factor_versions_json": "TEXT NOT NULL", "created_at": "TEXT",
            },
            "primary_key": ["model_name", "model_version"],
            "rows": [{
                "model_name": "equal_rank_v1", "model_version": "1",
                "params_json": json.dumps({"k": 1}, sort_keys=True),
                "factor_versions_json": json.dumps({"five_dim_drive": "v1"},
                                                   sort_keys=True),
                "created_at": "2026-10-02 10:00:00",
            }],
        },
        "fin_observed_snapshots": {
            "columns": {
                "trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
                "updated_date": "INTEGER", "period": "TEXT",
                "values_json": "TEXT NOT NULL", "values_hash": "TEXT NOT NULL",
                "captured_at": "TEXT",
            },
            "primary_key": ["trade_date", "code"],
            "rows": [{
                "trade_date": td, "code": "600172", "updated_date": 20260630,
                "period": period,
                "values_json": json.dumps({"bps": 5.1}, sort_keys=True),
                "values_hash": "hash-observed", "captured_at": f"{td} 18:00:00",
            }],
        },
        "fin_report_files": {
            "columns": {
                "report_period": "TEXT NOT NULL", "filename": "TEXT NOT NULL",
                "md5": "TEXT NOT NULL", "filesize": "INTEGER",
                "first_seen_at": "TEXT NOT NULL", "status": "TEXT NOT NULL",
                "records_count": "INTEGER", "raw_blob": "BLOB", "error": "TEXT",
            },
            "primary_key": ["report_period", "md5"],
            "rows": [{
                "report_period": period, "filename": "gpcw.zip", "md5": "md5-a",
                "filesize": 1024, "first_seen_at": "2026-07-01 10:00:00",
                "status": "layout_unverified", "records_count": 2,
                "raw_blob": None, "error": None,
            }],
        },
        "fin_report_records": {
            "columns": {
                "report_period": "TEXT NOT NULL", "code": "TEXT NOT NULL",
                "file_md5": "TEXT NOT NULL", "layout_key": "TEXT NOT NULL",
                "values_json": "TEXT NOT NULL", "available_at": "TEXT NOT NULL",
                "pit_basis": "TEXT NOT NULL", "first_seen_at": "TEXT",
                "retracted_at": "TEXT",
            },
            "primary_key": ["report_period", "code", "file_md5"],
            "rows": [
                {"report_period": period, "code": "600172", "file_md5": "md5-a",
                 "layout_key": "gpcw_10",
                 "values_json": json.dumps({"roe": 12.3}, sort_keys=True),
                 "available_at": "2026-07-01T10:00:00", "pit_basis": "observed",
                 "first_seen_at": "2026-07-01 10:00:00", "retracted_at": None},
                {"report_period": period, "code": "002881", "file_md5": "md5-a",
                 "layout_key": "gpcw_10",
                 "values_json": json.dumps({"roe": 9.9}, sort_keys=True),
                 "available_at": "2026-07-01T10:00:00", "pit_basis": "observed",
                 "first_seen_at": "2026-07-01 10:00:00",
                 "retracted_at": "2026-09-01T10:00:00"},
            ],
        },
        "fin_field_calibrations": {
            "columns": {
                "layout_key": "TEXT NOT NULL", "num_fields": "INTEGER NOT NULL",
                "mapping_json": "TEXT NOT NULL",
                "confidence_json": "TEXT NOT NULL", "status": "TEXT NOT NULL",
                "sample_n": "INTEGER NOT NULL", "calibrated_at": "TEXT",
            },
            "primary_key": ["layout_key"],
            "rows": [{
                "layout_key": "gpcw_10", "num_fields": 10,
                "mapping_json": json.dumps({"roe": 1}, sort_keys=True),
                "confidence_json": json.dumps({"roe": 0.9}, sort_keys=True),
                "status": "verified", "sample_n": 5,
                "calibrated_at": "2026-07-02 10:00:00",
            }],
        },
        "fin_xdxr_records": {
            "columns": {
                "code": "TEXT NOT NULL", "ex_date": "TEXT NOT NULL",
                "category": "INTEGER NOT NULL", "payload_json": "TEXT NOT NULL",
                "created_at": "TEXT",
            },
            "primary_key": ["code", "ex_date", "category"],
            "rows": [{
                "code": "600172", "ex_date": TRADE_DATE, "category": 1,
                "payload_json": json.dumps({"bonus": 0.5}, sort_keys=True),
                "created_at": "2026-10-20 20:00:00",
            }],
        },
    }
    return {"tables": tables}


def seed_assets(box: EnvSandbox, *, full: bool = True) -> None:
    seed_db(box, assets_seed_spec(full=full), db_name="assets.db")
    if full:
        exec_sql(
            box.data_dir / "assets.db",
            "UPDATE fin_report_files SET raw_blob = ? "
            "WHERE report_period = ? AND md5 = ?",
            (RAW_BLOB, PERIOD, "md5-a"),
        )


def main_marker_seed() -> dict:
    return {"tables": {"prep_assets": {
        "columns": {
            "trade_date": "TEXT NOT NULL", "asset_type": "TEXT NOT NULL",
            "payload_json": "TEXT NOT NULL", "created_at": "TEXT",
        },
        "primary_key": ["trade_date", "asset_type"],
        "rows": [{
            "trade_date": "2026-10-19", "asset_type": "dragons",
            "payload_json": json.dumps({"marker": 1}, sort_keys=True),
            "created_at": "2026-10-19 17:00:00",
        }],
    }}}


def seed_local_factor_conflict(box: EnvSandbox) -> None:
    """One local row that differs from remote: dry-run must report it as pending."""

    seed_db(box, {"tables": {"factor_values": {
        "columns": {
            "trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
            "factor_name": "TEXT NOT NULL", "factor_version": "TEXT NOT NULL",
            "value": "REAL NOT NULL", "available_at": "TEXT NOT NULL",
            "source": "TEXT NOT NULL", "source_scan_id": "TEXT",
            "source_meta_json": "TEXT", "computed_at": "TEXT",
        },
        "primary_key": ["trade_date", "code", "factor_name", "factor_version"],
        "rows": [{
            "trade_date": TRADE_DATE, "code": "600172",
            "factor_name": "five_dim_drive", "factor_version": "v1",
            "value": 1.0, "available_at": f"{TRADE_DATE}T15:05:00",
            "source": "scan", "source_scan_id": None,
            "source_meta_json": None, "computed_at": f"{TRADE_DATE} 15:06:00",
        }],
    }}}, db_name="assets.db")


def backfill_source_rows() -> list[dict]:
    return [
        sb.dragon_row("2026-10-20", "600001", "龙头A", 1, 88.0, ["AI算力"]),
        sb.dragon_row("2026-10-20", "600002", "跟风B", 2, 62.0, ["机器人"]),
        sb.dragon_row("2026-10-21", "600003", "补涨C", 1, 90.0, ["机器人"]),
    ]


def seed_backfill_dbs(box: EnvSandbox) -> None:
    source_rows = backfill_source_rows()
    mirror_rows = [source_rows[0], sb.dragon_row(
        "2026-10-20", "600009", "孤儿Z", 3, 30.0, ["其他"])]
    seed_db(box, sb.dragon_db_seed(source_rows), db_name="dragon.db")
    seed_db(box, sb.mirror_db_seed(mirror_rows), db_name="assets.db")


# ─── remote helpers ─────────────────────────────────────────────────────────


def canonical_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def gzip_bytes(data: bytes) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=6, mtime=0) as handle:
        handle.write(data)
    return buffer.getvalue()


def manifest_body(box: EnvSandbox, partition: str) -> dict:
    return json.loads((box.remote / "manifests" / f"{partition}.json")
                      .read_text(encoding="utf-8"))


def recompute_manifest_id(body: dict) -> str:
    identity = {
        key: {field: entry[field] for field in
              ("source", "asset_type", "sha256", "md5", "size", "schema")}
        for key, entry in body["assets"].items()
    }
    return sha256_bytes(canonical_bytes(identity))


def tamper_manifest_metadata(box: EnvSandbox, partition: str,
                             manifest_id: str = "0" * 64) -> None:
    path = box.remote / ".meta" / "manifests" / f"{partition}.json.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["manifest-id"] = manifest_id
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8")


def write_manifestless_blob(box: EnvSandbox) -> str:
    """A valid blob with no manifest — the only way to reach orphan ``null``."""

    body = {
        "schema": 1, "table": "prep_assets", "partition": TRADE_DATE,
        "columns": ["trade_date", "asset_type", "payload_json", "created_at"],
        "rows": [[TRADE_DATE, "dragons", {"count": 3}, f"{TRADE_DATE} 17:00:00"]],
    }
    content = canonical_bytes(body)
    sha = sha256_bytes(content)
    blob = gzip_bytes(content)
    key = f"blobs/{TRADE_DATE}/prep_dragons.{sha}.json.gz"
    path = box.remote / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    meta_path = box.remote / ".meta" / f"{key}.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps({
        "sha256": sha, "md5": hashlib.md5(content).hexdigest(),
        "size": str(len(content)), "schema": "1",
        "blob-sha256": sha256_bytes(blob), "source": "prep",
        "asset-type": "dragons", "partition": TRADE_DATE,
    }, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return key


def write_partial_raw_remote(box: EnvSandbox) -> tuple[str, bytes]:
    """Legacy/partial remote: a ``fin:report_raw`` unit without its metadata unit.

    This is the documented rollback shape that reaches the restore ``deferred``
    branch: the raw row cannot be inserted until ``fin_report_files`` metadata
    exists locally.
    """

    raw_bytes = b"\x1f\x8blegacy-raw-payload"
    partition = f"finraw-{LEGACY_PERIOD}"
    body = {
        "schema": 1, "table": "fin_report_files", "partition": partition,
        "columns": ["report_period", "md5", "raw_blob"],
        "rows": [[LEGACY_PERIOD, "md5-legacy",
                  base64.b64encode(raw_bytes).decode("ascii")]],
    }
    content = canonical_bytes(body)
    sha = sha256_bytes(content)
    md5 = hashlib.md5(content).hexdigest()
    blob = gzip_bytes(content)
    blob_sha = sha256_bytes(blob)
    key = f"blobs/{partition}/fin_report_raw.{sha}.json.gz"
    path = box.remote / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)

    meta_path = box.remote / ".meta" / f"{key}.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps({
        "sha256": sha, "md5": md5, "size": str(len(content)), "schema": "1",
        "blob-sha256": blob_sha, "source": "fin",
        "asset-type": "report_raw", "partition": partition,
    }, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    entry = {
        "source": "fin", "asset_type": "report_raw", "sha256": sha, "md5": md5,
        "size": len(content), "blob": key, "blob_sha256": blob_sha,
        "blob_size": len(blob), "schema": 1,
    }
    identity = {"fin:report_raw": {
        field: entry[field]
        for field in ("source", "asset_type", "sha256", "md5", "size", "schema")
    }}
    manifest_id = sha256_bytes(canonical_bytes(identity))
    manifest = {
        "schema": 2, "manifest_id": manifest_id,
        "generated_at": "2026-03-31T18:00:00+08:00",
        "generator_version": PACKAGE_VERSION, "manifest_source": "push",
        "partition": partition, "partition_kind": "period_raw",
        "sort_date": LEGACY_PERIOD, "assets": {"fin:report_raw": entry},
    }
    (box.remote / "manifests").mkdir(parents=True, exist_ok=True)
    (box.remote / "manifests" / f"{partition}.json").write_bytes(
        canonical_bytes(manifest))
    metadata_dir = box.remote / ".meta" / "manifests"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / f"{partition}.json.json").write_text(json.dumps({
        "manifest-id": manifest_id, "schema": "2", "partition": partition,
    }, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return partition, raw_bytes


# ─── AS1 / AS2: doctor ──────────────────────────────────────────────────────


def test_as1_doctor_ok_uses_local_backend_and_leaves_no_objects():
    with sandbox("as1") as box:
        write_secrets(box)
        result = run_assets(
            box, ["doctor", "--profile", "e2e", "--samples", "3"])
        data = assert_assets_payload(result, "assets doctor", 0)
        assert data["ok"] is True
        assert data["failed"] == []
        summary = data["summary"]
        assert_json_equal(
            {key: summary[key] for key in golden("doctor_summary.json")},
            golden("doctor_summary.json"))
        assert set(summary["p95_ms"]) == {"put", "head", "get", "list"}
        assert all(isinstance(value, (int, float))
                   for value in summary["p95_ms"].values())
        # The doctor cleans its own probe objects (blobs and metadata).
        assert list_files(box.remote) == []
        assert not (box.data_dir / "assets_sync" / "state.json").exists()
        assert_no_data_warehouse(box.data_dir)
        entries = backend_calls(box)
        assert {entry["method"] for entry in entries} <= {
            "put", "head", "get", "list", "delete"}
        assert all(entry["key"].startswith("_doctor/") for entry in entries)
        assert result.stderr == ""
        assert "e2e-secret-should-not-leak" not in result.stdout + result.stderr


def test_as2_doctor_backend_failure_reports_failed_and_exits_1():
    with sandbox("as2") as box:
        write_secrets(box)
        result = run_assets(
            box, ["doctor", "--profile", "e2e", "--samples", "2"],
            env={"DQ_E2E_BACKEND_MODE": "error"})
        data = assert_assets_payload(result, "assets doctor", 1)
        assert data["ok"] is False
        summary = data["summary"]
        assert summary["ok"] is False
        assert summary["samples"] == 2
        assert summary["operations"] == 9
        assert summary["success_rate"] == 0.0
        assert [item["op"] for item in summary["errors"]] == ["io", "io", "list"]
        assert len(summary["cleanup_errors"]) == 2
        assert len(data["failed"]) == 5
        # The injected transport failure never reaches a remote operation.
        assert backend_calls(box) == []
        assert result.stderr == ""


# ─── AS3 / AS4 / AS5: push ─────────────────────────────────────────────────


def test_as3_push_full_layout_state_schema2_and_idempotence():
    with sandbox("as3") as box:
        write_secrets(box)
        seed_assets(box)
        result = run_assets(box, ["push"])
        data = assert_assets_payload(result, "assets push", 0)
        assert data["ok"] is True
        assert data["failed"] == []
        summary = data["summary"]
        expected_push = golden("push_summary.json")
        assert_json_equal(
            {key: summary[key] for key in expected_push}, expected_push)
        assert_stderr_contains(
            result, "推送：pushed=5 skipped=0 blobs=17 manifests=5")
        assert_stderr_contains(
            result, "assets push 完成：ok=True fatal=False")

        files = list_files(box.remote)
        blob_files = [name for name in files if name.startswith("blobs/")]
        manifest_files = [name for name in files if name.startswith("manifests/")]
        assert len(blob_files) == expected_push["blobs_uploaded"] == 17
        assert manifest_files == [f"manifests/{p}.json" for p in PARTITIONS]
        assert all(name.startswith(("blobs/", "manifests/", ".meta/"))
                   for name in files)
        blob_bytes = sum((box.remote / name).stat().st_size for name in blob_files)
        assert summary["bytes_uploaded"] == blob_bytes

        for partition in PARTITIONS:
            body = manifest_body(box, partition)
            assert sorted(body["assets"]) == SHARDS[partition]
            if partition == TRADE_DATE:
                assert body["schema"] == 1
                assert body["trade_date"] == partition
            else:
                assert body["schema"] == 2
                assert body["partition"] == partition
                assert body["partition_kind"] == (
                    "static" if partition.startswith("static-")
                    else "period_raw" if partition.startswith("finraw-")
                    else "period")
                assert body["sort_date"] == (
                    "" if partition.startswith("static-")
                    else partition.split("-", 1)[1])
            assert body["generated_at"] == NOW_ISO
            assert body["generator_version"] == PACKAGE_VERSION
            assert body["manifest_source"] == "push"
            assert recompute_manifest_id(body) == body["manifest_id"]
            metadata = json.loads(
                (box.remote / ".meta" / "manifests" /
                 f"{partition}.json.json").read_text(encoding="utf-8"))
            assert metadata["manifest-id"] == body["manifest_id"]
            assert metadata["partition"] == partition

            for key, entry in body["assets"].items():
                blob_path = box.remote / entry["blob"]
                blob = blob_path.read_bytes()
                content = gzip.decompress(blob)
                assert hashlib.sha256(content).hexdigest() == entry["sha256"]
                assert hashlib.md5(content).hexdigest() == entry["md5"]
                assert len(content) == entry["size"]
                assert hashlib.sha256(blob).hexdigest() == entry["blob_sha256"]
                assert len(blob) == entry["blob_size"]
                assert entry["blob"] == (
                    f"blobs/{partition}/{key.replace(':', '_')}."
                    f"{entry['sha256']}.json.gz")
                assert f"{entry['source']}:{entry['asset_type']}" == key
                blob_metadata = json.loads(
                    (box.remote / ".meta" / f"{entry['blob']}.json")
                    .read_text(encoding="utf-8"))
                assert blob_metadata["sha256"] == entry["sha256"]
                assert blob_metadata["partition"] == partition
                assert blob_metadata["source"] == entry["source"]

        state = json.loads(
            (box.data_dir / "assets_sync" / "state.json").read_text(
                encoding="utf-8"))
        assert state["schema"] == 2
        assert state["hostname"] == socket.gethostname()
        assert state["updated_at"] == NOW_ISO
        assert state["last_success_trade_date"] == TRADE_DATE
        assert set(state["partitions"]) == set(PARTITIONS)
        state_golden = golden("state.json")
        for partition, kind in state_golden["partition_kinds"].items():
            entry = state["partitions"][partition]
            assert entry["kind"] == kind
            assert entry["codec"] == state_golden["codec"] == 2
            assert entry["pushed_at"] == state_golden["pushed_at"]
            assert entry["manifest_id"] == manifest_body(box, partition)["manifest_id"]
            recorded = {name: item["entries"]
                        for name, item in entry["sources"].items()}
            assert recorded == state_golden["sources"][partition]
            for item in entry["sources"].values():
                assert item["watermark"].count("|") == 2
                assert item["max_created_at"] == item["watermark"].split("|", 1)[0]

        second = run_assets(box, ["push"])
        data = assert_assets_payload(second, "assets push", 0)
        summary = data["summary"]
        assert summary["pushed"] == 0
        assert summary["skipped"] == 5
        assert summary["blobs_uploaded"] == 0
        assert summary["manifests_uploaded"] == 0
        assert [entry["action"] for entry in summary["dates"]] == ["skip"] * 5
        assert_stderr_contains(
            second, "推送：pushed=0 skipped=5 blobs=0 manifests=0")
        assert list_files(box.remote) == files


def test_as4_push_dry_run_never_writes_remote_or_state():
    with sandbox("as4") as box:
        write_secrets(box)
        seed_assets(box)
        result = run_assets(box, ["push", "--dry-run"])
        data = assert_assets_payload(result, "assets push", 0)
        summary = data["summary"]
        assert summary["dry_run"] is True
        assert summary["pushed"] == 0
        assert summary["blobs_uploaded"] == 0
        assert summary["manifests_uploaded"] == 0
        assert summary["bytes_uploaded"] == 0
        assert summary["skipped"] == 0
        assert [entry["action"] for entry in summary["dates"]] == ["push"] * 5
        assert sorted(entry["partition"] for entry in summary["dates"]) == sorted(PARTITIONS)
        assert list_files(box.remote) == []
        assert not (box.data_dir / "assets_sync").exists()
        assert backend_calls(box) == []
        assert_stderr_contains(
            result, "推送：pushed=0 skipped=0 blobs=0 manifests=0")


def test_as5_push_shrink_requires_allow_flag_and_preserves_remote():
    with sandbox("as5") as box:
        write_secrets(box)
        seed_assets(box, full=False)
        first = run_assets(box, ["push"])
        data = assert_assets_payload(first, "assets push", 0)
        assert data["summary"]["pushed"] == 1
        assert data["summary"]["blobs_uploaded"] == 2
        assert data["summary"]["manifests_uploaded"] == 1
        body = manifest_body(box, TRADE_DATE)
        assert sorted(body["assets"]) == ["prep:collection_report", "prep:dragons"]
        state_path = box.data_dir / "assets_sync" / "state.json"
        recorded_id = json.loads(state_path.read_text(encoding="utf-8"))[
            "partitions"][TRADE_DATE]["manifest_id"]
        assert recorded_id == body["manifest_id"]

        exec_sql(box.data_dir / "assets.db",
                 "DELETE FROM prep_assets WHERE asset_type = 'collection_report'")

        rejected = run_assets(box, ["push"])
        data = assert_assets_payload(rejected, "assets push", 1)
        assert data["ok"] is False
        assert [item["kind"] for item in data["failed"]] == ["shrink"]
        assert data["failed"][0]["partition"] == TRADE_DATE
        assert data["failed"][0]["removed"] == ["prep:collection_report"]
        assert data["summary"]["shrunk"] == [{
            "partition": TRADE_DATE, "removed": ["prep:collection_report"]}]
        assert_stderr_contains(rejected, "新 manifest 是远端严格子集")
        assert sorted(manifest_body(box, TRADE_DATE)["assets"]) == [
            "prep:collection_report", "prep:dragons"]
        assert json.loads(state_path.read_text(encoding="utf-8"))[
            "partitions"][TRADE_DATE]["manifest_id"] == recorded_id

        allowed = run_assets(box, ["push", "--allow-shrink"])
        data = assert_assets_payload(allowed, "assets push", 0)
        assert data["summary"]["pushed"] == 1
        assert data["summary"]["shrunk"] == []
        body = manifest_body(box, TRADE_DATE)
        assert sorted(body["assets"]) == ["prep:dragons"]
        assert json.loads(state_path.read_text(encoding="utf-8"))[
            "partitions"][TRADE_DATE]["manifest_id"] == body["manifest_id"]


# ─── AS6 / AS7: pull ───────────────────────────────────────────────────────


def test_as6_pull_dry_run_plans_downloads_and_writes_nothing():
    with sandbox("as6-push") as source, sandbox("as6-pull") as target:
        write_secrets(source)
        seed_assets(source)
        assert_exit(run_assets(source, ["push"]), 0)
        write_secrets(target)
        copy_remote(source, target)
        # A local row with the same identity but a different value makes the
        # row-level conflict *undecidable* before downloading: it must surface
        # as merge_pending, never as a fabricated conflict.
        seed_local_factor_conflict(target)
        rows_before = sqlite_rows(target.data_dir / "assets.db", "factor_values")
        assert len(rows_before) == 1

        result = run_assets(target, PULL_ARGS)
        data = assert_assets_payload(result, "assets pull", 0)
        summary = data["summary"]
        gold = golden("pull_dry_run.json")
        assert summary["restored"] == gold["restored"] == 0
        assert summary["skipped"] == gold["skipped"] == 0
        assert summary["conflicts"] == gold["conflicts"] == []
        assert summary["failed"] == gold["failed"] == []
        assert summary["manifest_source"] == gold["manifest_source"]
        assert summary["degraded"] is gold["degraded"]
        assert summary["merge_pending"] == gold["merge_pending"] == 1

        dates = {entry["partition"]: entry for entry in summary["dates"]}
        assert set(dates) == set(PARTITIONS)
        for partition in PARTITIONS:
            entry = dates[partition]
            assert entry["action"] == "plan"
            assert entry["download"] == gold["downloads"][partition]
            assert entry["merge_pending"] == (
                gold["merge_pending_by_partition"][partition])
            assert entry["conflicts"] == 0
            assert entry["manifest_source"] == "remote"

        # Dry-run wrote no rows and no sync state (read-path schema bootstrap is
        # production behavior and is not asserted here).
        assert sqlite_rows(target.data_dir / "assets.db", "factor_values") == rows_before
        assert not (target.data_dir / "assets_sync").exists()
        assert not (target.data_dir / "assets_sync.lock").exists()
        assert "put" not in {entry["method"] for entry in backend_calls(target)}
        assert_stderr_contains(result, "待判定：1 个行级分片需 --apply")


def test_as7_pull_apply_target_dir_row_merge_conflict_force_and_null_fill():
    with sandbox("as7-remote") as source, sandbox("as7-target") as target:
        write_secrets(source)
        seed_assets(source)
        assert_exit(run_assets(source, ["push"]), 0)
        write_secrets(target)
        seed_db(target, main_marker_seed(), db_name="assets.db")
        main_db = target.data_dir / "assets.db"
        main_hash = sha256_file(main_db)
        assert sqlite_count(main_db, "prep_assets") == 1
        copy_remote(source, target)

        restore = target.out / "restore"
        target_db = restore / "assets.db"
        args = [*PULL_ARGS, "--apply", "--target-dir", str(restore)]
        result = run_assets(target, args)
        data = assert_assets_payload(result, "assets pull", 0)
        summary = data["summary"]
        gold = golden("pull_apply.json")
        # ``restored`` counts restore events, not physical rows: the two legacy
        # shard sources (prep/auction) report one event per asset row while the
        # 13 row-merge units report one event per row, and ``fin:report_raw``
        # adds a NULL-fill event on the metadata row inserted moments earlier.
        # Hence 20 events for 19 physical rows (asserted by pull_target_counts).
        assert summary["restored"] == gold["restored"] == 20
        assert summary["skipped"] == gold["skipped"] == 0
        assert summary["rows_inserted"] == gold["rows_inserted"] == 17
        assert summary["rows_skipped"] == gold["rows_skipped"] == 0
        assert summary["rows_filled"] == gold["rows_filled"] == 1
        assert summary["merge_pending"] == gold["merge_pending"] == 0
        assert summary["conflicts"] == []
        assert summary["deferred"] == []
        assert summary["failed"] == []
        assert summary["manifest_source"] == "remote"
        assert summary["degraded"] is False
        applied = {entry["partition"]: entry for entry in summary["dates"]}
        assert set(applied) == set(PARTITIONS)
        for partition, shards in gold["applied"].items():
            assert applied[partition]["action"] == "applied"
            assert applied[partition]["shards"] == shards
            assert applied[partition]["conflicts"] == 0
        remote_blob_bytes = sum(
            (target.remote / name).stat().st_size
            for name in list_files(target.remote) if name.startswith("blobs/"))
        assert summary["bytes_downloaded"] == remote_blob_bytes
        assert_stderr_contains(
            result, "恢复：restored=20 skipped=0 已判定冲突=0 source=remote")

        # Restored target database: 15 physical tables, same row counts, raw filled.
        assert sqlite_tables(target_db) == sorted(PHYSICAL_TABLES)
        for table, expected in golden("pull_target_counts.json").items():
            assert sqlite_count(target_db, table) == expected, table
        rows = sqlite_rows(target_db, "fin_report_files")
        assert len(rows) == 1
        assert rows[0]["raw_blob"] == RAW_BLOB
        assert sqlite_count(target_db, "dragons") == ROW_COUNTS["dragons"]

        # Isolation: the main temporary DB is not opened or written.
        assert sha256_file(main_db) == main_hash
        assert sqlite_count(main_db, "prep_assets") == 1
        assert sqlite_rows(main_db, "prep_assets")[0]["trade_date"] == "2026-10-19"
        assert not (target.data_dir / "assets_sync").exists()
        assert not (target.data_dir / "assets_sync.lock").exists()
        assert not (restore / "assets_sync").exists()
        assert not (restore / "assets_sync.lock").exists()
        assert {name for name in list_files(restore)} <= {
            "assets.db", "assets.db-wal", "assets.db-shm"}

        # Row-level conflict: same identity, different value → exit 1, unchanged.
        exec_sql(
            target_db,
            "UPDATE factor_values SET value = 1.0 WHERE code = '600172' "
            "AND factor_name = 'five_dim_drive' AND factor_version = 'v1'")
        conflict = run_assets(target, args)
        data = assert_assets_payload(conflict, "assets pull", 1)
        assert data["ok"] is False
        summary = data["summary"]
        assert len(summary["conflicts"]) == 1
        item = summary["conflicts"][0]
        assert item["key"] == "factor:values"
        assert item["reason"] == "content differs"
        assert item["columns"] == ["value"]
        assert item["partition"] == TRADE_DATE
        assert item["source"] == "factor"
        assert_stderr_contains(conflict, "已判定冲突=1")
        rows = {row["code"]: row["value"]
                for row in sqlite_rows(target_db, "factor_values")}
        assert rows["600172"] == 1.0

        forced = run_assets(target, [*args, "--force"])
        data = assert_assets_payload(forced, "assets pull", 0)
        assert data["summary"]["conflicts"] == []
        rows = {row["code"]: row["value"]
                for row in sqlite_rows(target_db, "factor_values")}
        assert rows["600172"] == 78.5

        # NULL-fill: local raw_blob NULL is refilled without --force.
        exec_sql(
            target_db,
            "UPDATE fin_report_files SET raw_blob = NULL "
            "WHERE report_period = ? AND md5 = 'md5-a'", (PERIOD,))
        filled = run_assets(target, args)
        data = assert_assets_payload(filled, "assets pull", 0)
        summary = data["summary"]
        assert summary["conflicts"] == []
        assert summary["rows_filled"] == 1
        assert summary["restored"] == 1
        assert sqlite_rows(target_db, "fin_report_files")[0]["raw_blob"] == RAW_BLOB
        assert sha256_file(main_db) == main_hash


def test_as7b_pull_reports_deferred_raw_when_metadata_row_is_missing():
    with sandbox("as7b") as box:
        write_secrets(box)
        partition, raw_bytes = write_partial_raw_remote(box)
        restore = box.out / "deferred"
        result = run_assets(box, [
            "pull", "--from", "2026-03-01", "--to", "2026-03-31",
            "--apply", "--target-dir", str(restore)])
        data = assert_assets_payload(result, "assets pull", 0)
        summary = data["summary"]
        assert summary["failed"] == []
        assert summary["conflicts"] == []
        assert summary["restored"] == 0
        assert summary["rows_inserted"] == 0
        assert len(summary["deferred"]) == 1
        item = summary["deferred"][0]
        assert item["key"] == "fin:report_raw"
        assert item["identity"] == [LEGACY_PERIOD, "md5-legacy"]
        assert item["reason"] == "元数据行缺失，raw 延后补齐"
        assert item["partition"] == partition
        assert item["trade_date"] == ""
        assert item["source"] == "fin"
        target_db = restore / "assets.db"
        assert target_db.is_file()
        assert sqlite_count(target_db, "fin_report_files") == 0
        assert not (box.data_dir / "assets.db").exists()
        assert_no_data_warehouse(box.data_dir)


# ─── AS8 / AS9: status ─────────────────────────────────────────────────────


def test_as8_status_full_coverage_clean_then_drift():
    with sandbox("as8") as box:
        write_secrets(box)
        seed_assets(box)
        assert_exit(run_assets(box, ["push"]), 0)

        result = run_assets(box, ["status"])
        data = assert_assets_payload(result, "assets status", 0)
        summary = data["summary"]
        assert summary["dates_missing"] == []
        assert summary["dates_mismatched"] == []
        assert summary["dates_drift"] == []
        assert summary["not_synced"] == {}
        assert {entry["partition"]: entry["status"]
                for entry in summary["dates"]} == {p: "ok" for p in PARTITIONS}

        tables = summary["tables"]
        gold = golden("status_clean.json")
        assert set(tables) == set(gold) == set(SYNC_UNITS)
        for unit, expected in gold.items():
            assert tables[unit] == expected, unit
        assert {item["table"] for item in tables.values()} == set(PHYSICAL_TABLES)
        assert tables["fin:report_files"]["table"] == (
            tables["fin:report_raw"]["table"]) == "fin_report_files"
        assert_stderr_contains(result, "同步覆盖：16/16 个同步单元有远端数据")
        assert_stderr_contains(result, "factor_* / fin_*: 已纳入同步")

        exec_sql(
            box.data_dir / "assets.db",
            "INSERT INTO factor_build_runs"
            "(trade_date, started_at, finished_at, report_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (TRADE_DATE, f"{TRADE_DATE} 16:00:00", f"{TRADE_DATE} 16:01:00",
             json.dumps({"ok": True, "build_ids": [99]}, sort_keys=True),
             f"{TRADE_DATE} 16:01:01"))
        drift = run_assets(box, ["status"])
        data = assert_assets_payload(drift, "assets status", 0)
        summary = data["summary"]
        assert summary["dates_mismatched"] == []
        assert summary["dates_drift"] == [{
            "partition": TRADE_DATE, "trade_date": TRADE_DATE,
            "keys": ["factor:build_runs"], "reason": "row_count_only"}]
        assert {entry["partition"]: entry["status"]
                for entry in summary["dates"]}[TRADE_DATE] == "drift"
        assert_stderr_contains(drift, "drift=1")


def test_as8b_status_reports_mismatched_and_exits_1():
    with sandbox("as8b") as box:
        write_secrets(box)
        seed_assets(box, full=False)
        assert_exit(run_assets(box, ["push"]), 0)
        exec_sql(
            box.data_dir / "assets.db",
            "UPDATE prep_assets SET payload_json = ? "
            "WHERE trade_date = ? AND asset_type = 'dragons'",
            (json.dumps({"count": 4}, sort_keys=True), TRADE_DATE))

        result = run_assets(box, ["status"])
        data = assert_assets_payload(result, "assets status", 1)
        assert data["ok"] is False
        summary = data["summary"]
        assert summary["dates_drift"] == []
        assert len(summary["dates_mismatched"]) == 1
        item = summary["dates_mismatched"][0]
        assert item["partition"] == TRADE_DATE
        assert item["keys"] == ["prep:dragons"]
        assert item["local_manifest_id"] != item["remote_manifest_id"]
        assert_stderr_contains(result, "状态：missing=0 mismatched=1 drift=0")
        assert_stderr_contains(result, "assets status 失败")


def test_as9_status_orphans_is_null_when_manifest_is_missing():
    with sandbox("as9") as box:
        write_secrets(box)
        seed_assets(box, full=False)
        blob_key = write_manifestless_blob(box)

        result = run_assets(box, ["status", "--orphans"])
        data = assert_assets_payload(result, "assets status", 1)
        summary = data["summary"]
        assert summary["orphan_blobs"] is None
        assert summary["orphan_reason"] == {
            "reason": "manifest_missing", "dates": [TRADE_DATE]}
        assert summary["dates_missing"] == [{
            "partition": TRADE_DATE, "trade_date": TRADE_DATE,
            "reason": "remote_missing"}]
        entry = summary["dates"][0]
        assert entry["status"] == "remote_missing"
        assert entry["orphans"] is None
        assert entry["orphan_reason"] == "manifest_missing"
        # The orphan blob is real; the status must not pretend it does not exist.
        assert (box.remote / blob_key).is_file()


# ─── AS10 / AS11 / AS12: verify ────────────────────────────────────────────


def test_as10_verify_manifest_self_consistency_and_tamper():
    with sandbox("as10") as box:
        write_secrets(box)
        seed_assets(box)
        assert_exit(run_assets(box, ["push"]), 0)

        clean = run_assets(box, ["verify"])
        data = assert_assets_payload(clean, "assets verify", 0)
        summary = data["summary"]
        assert summary["checked_dates"] == 5
        assert summary["checked_blobs"] == 0
        assert summary["mismatched"] == []
        assert summary["metadata_supported"] is True
        assert summary["deep"] is False
        assert summary["samples"] is None
        assert summary["local_absent"] == []
        assert summary["period_excluded"] == []
        assert_stderr_contains(clean, "校验：dates=5 blobs=0 mismatched=0")

        body_id = manifest_body(box, TRADE_DATE)["manifest_id"]
        tamper_manifest_metadata(box, TRADE_DATE)
        bad = run_assets(box, ["verify"])
        data = assert_assets_payload(bad, "assets verify", 1)
        assert data["ok"] is False
        summary = data["summary"]
        assert summary["checked_dates"] == 5
        assert len(summary["mismatched"]) == 1
        item = summary["mismatched"][0]
        assert item["partition"] == TRADE_DATE
        assert item["reason"] == "metadata_id_mismatch"
        assert item["metadata_id"] == "0" * 64
        assert item["body_id"] == body_id

        # Corrupt the manifest body: parsing must fail closed, not silently pass.
        (box.remote / "manifests" / f"{TRADE_DATE}.json").write_text(
            "{", encoding="utf-8")
        broken = run_assets(box, ["verify"])
        data = assert_assets_payload(broken, "assets verify", 1)
        reason = data["summary"]["mismatched"][0]["reason"]
        assert "manifest 解析失败" in reason


def test_as11_verify_deep_downloads_blobs_and_detects_corruption():
    with sandbox("as11") as box:
        write_secrets(box)
        seed_assets(box)
        assert_exit(run_assets(box, ["push"]), 0)

        deep = run_assets(
            box, ["verify", "--deep", "--samples", "17", "--md5"])
        data = assert_assets_payload(deep, "assets verify", 0)
        summary = data["summary"]
        assert summary["deep"] is True
        assert summary["samples"] == 17
        assert summary["checked_dates"] == 5
        assert summary["checked_blobs"] == 17
        assert summary["mismatched"] == []
        assert len(summary["md5"]) == 17
        assert all(len(item["md5"]) == 32 for item in summary["md5"])
        assert_stderr_contains(deep, "校验：dates=5 blobs=17 mismatched=0")

        body = manifest_body(box, TRADE_DATE)
        key = sorted(body["assets"])[0]
        blob_path = box.remote / body["assets"][key]["blob"]
        corrupted = bytearray(blob_path.read_bytes())
        corrupted[len(corrupted) // 2] ^= 0xFF
        blob_path.write_bytes(bytes(corrupted))

        bad = run_assets(box, ["verify", "--deep", "--samples", "17"])
        data = assert_assets_payload(bad, "assets verify", 1)
        summary = data["summary"]
        assert summary["checked_blobs"] == 16
        assert len(summary["mismatched"]) == 1
        reason = summary["mismatched"][0]["reason"]
        assert reason.startswith("blob_invalid:")
        assert "传输哈希不一致" in reason


def test_as12_verify_argument_errors_exit_2_without_credentials():
    with sandbox("as12") as box:
        single = run_assets(box, ["verify", "--from", "2026-10-21"])
        data = assert_assets_payload(single, "assets verify", 2)
        assert data["ok"] is False
        assert "成对" in data["failed"][0]["reason"]
        assert_stderr_contains(single, "assets verify 失败")

        deep_only = run_assets(box, ["verify", "--deep"])
        data = assert_assets_payload(deep_only, "assets verify", 2)
        assert "--deep" in data["failed"][0]["reason"]
        assert "--samples" in data["failed"][0]["reason"]

        assert backend_calls(box) == []
        assert_no_data_warehouse(box.data_dir)


# ─── AS13 / AS14 / AS15: backfill-dragons ──────────────────────────────────


def test_as13_backfill_dry_run_reports_diff_without_row_writes():
    with sandbox("as13") as box:
        seed_backfill_dbs(box)
        source_before = sqlite_rows(
            box.data_dir / "dragon.db", "dragons_v2", order_by="trade_date, code")
        mirror_before = sqlite_rows(
            box.data_dir / "assets.db", "dragons", order_by="trade_date, code")
        assert len(source_before) == 3
        assert len(mirror_before) == 2

        result = run_assets(box, [
            "backfill-dragons", "--dry-run", "--json",
            "--from", "20261020", "--to", "20261021"])
        assert_exit(result, 0)
        payload = payload_of(result)
        assert_json_keys_in_order(payload, BACKFILL_KEYS)
        assert payload["mode"] == "backfill-dragons"
        assert payload["dry_run"] is True
        assert payload["from"] == "2026-10-20"
        assert payload["to"] == "2026-10-21"
        expected = golden("backfill_dry_run.json")
        assert_json_equal(
            {key: payload[key] for key in expected}, expected)

        assert sqlite_rows(
            box.data_dir / "dragon.db", "dragons_v2",
            order_by="trade_date, code") == source_before
        assert sqlite_rows(
            box.data_dir / "assets.db", "dragons",
            order_by="trade_date, code") == mirror_before

        human = run_assets(box, [
            "backfill-dragons", "--dry-run",
            "--from", "20261020", "--to", "20261021"])
        assert_exit(human, 0)
        assert human.stdout == ""
        assert_stderr_contains(
            human, "体检（dry-run）：dates=2 rows_source=3 inserted=2 "
                   "updated=0 unchanged=1 missing=2 orphan=1")


def test_as14_backfill_run_fills_mirror_and_is_idempotent():
    with sandbox("as14") as box:
        seed_backfill_dbs(box)
        source_before = sqlite_rows(
            box.data_dir / "dragon.db", "dragons_v2", order_by="trade_date, code")

        result = run_assets(box, [
            "backfill-dragons", "--json",
            "--from", "20261020", "--to", "20261021"])
        assert_exit(result, 0)
        payload = payload_of(result)
        assert payload["dry_run"] is False
        expected = {key: value for key, value in
                    golden("backfill_dry_run.json").items() if key != "warnings"}
        for key, value in expected.items():
            assert payload[key] == value, key
        assert len(payload["warnings"]) == 1
        assert "orphan_rows=1" in payload["warnings"][0]
        assert "--prune" in payload["warnings"][0]

        mirror = sqlite_rows(
            box.data_dir / "assets.db", "dragons", order_by="trade_date, code")
        assert sorted(row["code"] for row in mirror) == [
            "600001", "600002", "600003", "600009"]
        assert sqlite_rows(
            box.data_dir / "dragon.db", "dragons_v2",
            order_by="trade_date, code") == source_before

        again = run_assets(box, [
            "backfill-dragons", "--json",
            "--from", "20261020", "--to", "20261021"])
        assert_exit(again, 0)
        payload = payload_of(again)
        assert payload["inserted"] == 0
        assert payload["updated"] == 0
        assert payload["unchanged"] == 3
        assert payload["missing_rows"] == 0
        assert payload["orphan_rows"] == 1


def test_as15_backfill_prune_deletes_only_mirror_orphans():
    with sandbox("as15") as box:
        seed_backfill_dbs(box)
        source_before = sqlite_rows(
            box.data_dir / "dragon.db", "dragons_v2", order_by="trade_date, code")
        assert_exit(run_assets(box, [
            "backfill-dragons", "--json",
            "--from", "20261020", "--to", "20261021"]), 0)
        mirror = sqlite_rows(
            box.data_dir / "assets.db", "dragons", order_by="trade_date, code")
        assert "600009" in {row["code"] for row in mirror}

        result = run_assets(box, [
            "backfill-dragons", "--json", "--prune",
            "--from", "20261020", "--to", "20261021"])
        assert_exit(result, 0)
        payload = payload_of(result)
        assert payload["ok"] is True
        assert payload["pruned"] == 1
        assert payload["orphan_rows"] == 1
        assert payload["inserted"] == 0
        assert payload["failed"] == []

        mirror = sqlite_rows(
            box.data_dir / "assets.db", "dragons", order_by="trade_date, code")
        assert [row["code"] for row in mirror] == ["600001", "600002", "600003"]
        # The source of truth is untouched: prune only deletes mirror-only rows.
        assert sqlite_rows(
            box.data_dir / "dragon.db", "dragons_v2",
            order_by="trade_date, code") == source_before


# ─── AS16 / AS17 / AS18: lock, credentials, group without action ───────────


def test_as16_lock_busy_second_process_exits_1_without_remote_calls():
    with sandbox("as16") as box:
        write_secrets(box)
        lock = box.data_dir / "assets_sync.lock"
        lock.mkdir()
        (lock / "owner.json").write_text(json.dumps({
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at": NOW,
        }, ensure_ascii=False, sort_keys=True), encoding="utf-8")

        result = run_assets(box, ["push"])
        data = assert_assets_payload(result, "assets push", 1)
        assert data["ok"] is False
        assert data["failed"][0]["kind"] == "lock"
        assert "进程锁被占用" in data["failed"][0]["reason"]
        assert_stderr_contains(result, "assets push 失败：进程锁被占用")
        assert lock.is_dir()
        assert backend_calls(box) == []
        assert not (box.data_dir / "assets_sync" / "state.json").exists()
        assert_no_data_warehouse(box.data_dir)


def test_as17_missing_credentials_exit_2_without_network():
    with sandbox("as17") as box:
        result = run_assets(box, ["status"])
        data = assert_assets_payload(result, "assets status", 2)
        assert data["ok"] is False
        reason = data["failed"][0]["reason"]
        assert "缺少配置项" in reason
        for field in ("endpoint", "region", "bucket",
                      "access_key_id", "secret_access_key"):
            assert field in reason
        assert_stderr_contains(result, "assets status 失败：缺少配置项")
        assert backend_calls(box) == []
        assert_no_data_warehouse(box.data_dir)
        assert not (box.data_dir / "secrets").exists()


def test_as18_group_without_action_emits_json_error_exit_2():
    with sandbox("as18") as box:
        result = runner().run_cli(["assets"], sandbox=box, timeout=60)
        assert_exit(result, 2)
        data = payload_of(result)
        assert_json_keys_in_order(data, ["ok", "command", "failed", "summary"])
        assert data["ok"] is False
        assert data["command"] == "assets"
        assert data["failed"][0]["reason"] == "未知 assets 子命令"
        assert data["summary"] == {}
        assert result.stderr == ""
        assert backend_calls(box) == []
        assert_no_data_warehouse(box.data_dir)
