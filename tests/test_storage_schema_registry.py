"""Task 5: ``infra.storage`` schema registry 与公共接口冻结。

- dragon.db 的表集合必须与 S0 基线（``tests/fixtures/architecture_baseline.json``）
  完全一致；
- canonical 模块必须暴露批准的分组边界：DDL 在 ``schema``、source/表名在
  ``identifiers``、连接/schema 守卫在 ``connection``、公共门面在 ``main``（无
  ``transaction``）。

真实数据库绝不打开：schema 快照只落 ``tmp_path``。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import dragon_schema_snapshot

BASELINE = Path(__file__).resolve().parent / "fixtures" / "architecture_baseline.json"


def test_dragon_schema_table_set_is_frozen(tmp_path):
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert set(dragon_schema_snapshot(tmp_path)) == set(baseline["dragon_schema"])


def test_canonical_storage_modules_expose_the_approved_boundaries():
    from dragon_quant.infra.storage import connection, identifiers, schema

    assert identifiers.VALID_SOURCES == {"v1", "v2"}
    assert identifiers._tables("v2") == {
        "scans": "scans_v2",
        "scan_stocks": "scan_stocks_v2",
        "scan_logs": "scan_logs_v2",
        "dragons": "dragons_v2",
    }
    for name in (
        "_connect",
        "init_db",
        "_ensure_schema",
        "_ensure_ready",
        "_lock",
        "_schema_lock",
        "_schema_ready",
    ):
        assert hasattr(connection, name), name
    for name in (
        "BASE_SCHEMA",
        "_ensure_schema",
        "_ensure_review_account_columns",
        "_ensure_signal_columns",
        "_ensure_dragon_columns",
        "_create_versioned_tables",
        "_seed_blacklist",
    ):
        assert hasattr(schema, name), name


def test_storage_main_exports_public_facade_without_transaction():
    from dragon_quant.infra.storage import main

    for name in ("init_db", "DataCache", "StorageManager"):
        assert hasattr(main, name), name
    assert not hasattr(main, "transaction")


def test_infra_storage_package_import_is_lazy(tmp_path):
    """``import dragon_quant.infra.storage`` 零 eager 子模块、零目录副作用（B1）。"""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    data_root = tmp_path / "data"
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "DQ_DATA_DIR": str(data_root),
        "HOME": str(tmp_path / "home"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    code = "\n".join([
        "import sys",
        "import dragon_quant.infra.storage as storage",
        "loaded = sorted(name for name in sys.modules if name.startswith(",
        "    'dragon_quant.infra.storage.'))",
        "assert loaded == [], loaded",
        "print('ok')",
    ])
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert not data_root.exists()


def test_connection_module_binds_db_path_at_import(tmp_path):
    """``connection`` 在导入期绑定 ``paths.DB_PATH``，调用期不重新解析。

    regression：若连接模块等到首次使用才读路径，测试先 patch
    ``infra.storage.paths.DB_PATH`` 再连接会让模块捕获临时值，teardown 后仍指向
    临时库；导入期绑定保证 patch 目标唯一（``connection.DB_PATH``）。
    """
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    data_root = tmp_path / "data"
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "DQ_DATA_DIR": str(data_root),
        "HOME": str(tmp_path / "home"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    code = "\n".join([
        "import dragon_quant.infra.storage.paths as paths",
        "import dragon_quant.infra.storage.connection as connection",
        "assert connection.DB_PATH is paths.DB_PATH",
        "original = connection.DB_PATH",
        "paths.DB_PATH = paths.DATA_DIR / 'other.db'",
        "assert connection.DB_PATH is original",
        "print('ok')",
    ])
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert not data_root.exists()


# ─── Task 9: scan.storage / vpa.storage table ownership ────────────────────


def test_scan_storage_owns_the_scan_and_dragon_tables():
    from dragon_quant.scan.storage import blacklist, dragons, logs, scans

    for name in (
        "save_scan", "get_scan", "get_scan_stocks", "get_scans_by_date",
        "list_scan_dates", "list_scan_stock_contributions_by_date",
        "delete_scans_by_date_topn",
    ):
        assert hasattr(scans, name), name
    for name in (
        "save_dragons", "get_dragons", "get_pending_dragons",
        "get_last_entry_with_rank", "update_dragon_review",
        "rebuild_dragons_for_date", "query_dragons", "get_review_summary",
    ):
        assert hasattr(dragons, name), name
    for name in (
        "save_scan_logs", "count_scan_logs", "get_scan_logs",
        "list_scan_log_folders", "log_summary", "delete_all_scan_logs",
        "delete_old_scan_logs",
    ):
        assert hasattr(logs, name), name
    for name in (
        "get_sector_blacklist", "add_sector_blacklist", "remove_sector_blacklist",
    ):
        assert hasattr(blacklist, name), name


def test_vpa_storage_owns_the_vpa_analysis_table():
    from dragon_quant.scan.storage import dragons
    from dragon_quant.vpa import storage as vpa_storage

    assert hasattr(vpa_storage, "upsert_vpa")
    assert hasattr(vpa_storage, "list_vpa_analysis")
    source = Path(vpa_storage.__file__).read_text(encoding="utf-8")
    assert "INSERT INTO vpa_analysis" in source
    assert "FROM vpa_analysis" in source
    dragon_source = Path(dragons.__file__).read_text(encoding="utf-8")
    assert "INSERT INTO vpa_analysis" not in dragon_source
    assert "FROM vpa_analysis" not in dragon_source


def test_scan_storage_main_exports_the_phase_f_contract():
    from dragon_quant.scan.storage import main

    for name in (
        "PersistResult", "persist_scan_result", "write_scan_report",
        "save_scan", "save_dragons", "save_scan_logs", "get_sector_blacklist",
    ):
        assert hasattr(main, name), name
    fields = {"log_count", "report_path", "saved_dragons",
              "skipped_dragons", "updated_dragons", "errors"}
    assert fields <= set(main.PersistResult.__dataclass_fields__)


def test_scan_storage_package_import_is_lazy(tmp_path):
    """``import dragon_quant.scan.storage`` 零 eager 子模块、零目录副作用（B1）。"""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    data_root = tmp_path / "data"
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "DQ_DATA_DIR": str(data_root),
        "HOME": str(tmp_path / "home"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    code = "\n".join([
        "import sys",
        "import dragon_quant.scan.storage as storage",
        "loaded = sorted(name for name in sys.modules if name.startswith(",
        "    'dragon_quant.scan.storage.'))",
        "assert loaded == [], loaded",
        "print('ok')",
    ])
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert not data_root.exists()
