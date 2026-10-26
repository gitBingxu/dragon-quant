"""Task 6: ``infra.assets`` schema registry 与公共接口冻结。

- assets.db 的 15 张表必须与 S0 基线（``tests/fixtures/architecture_baseline.json``）
  完全一致；
- ``schema.TABLE_NAMES`` 必须与同步注册表 / 两个既有适配器一致（防 DDL 与
  ``assets_sync`` 漂移）；
- canonical 模块必须暴露批准边界：DDL/迁移常量在 ``schema``，连接/守卫/资产仓储
  在 ``db``，公共门面在 ``main``。

真实数据库绝不打开：schema 快照只落 ``tmp_path``。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.support import assets_schema_snapshot

BASELINE = Path(__file__).resolve().parent / "fixtures" / "architecture_baseline.json"


def test_assets_schema_table_set_is_frozen(tmp_path):
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    snapshot = assets_schema_snapshot(tmp_path)
    assert set(snapshot) == set(baseline["assets_schema"])
    assert len(snapshot) == 15


def test_schema_table_names_match_sync_specs_and_legacy_adapters():
    from dragon_quant.infra.assets import schema
    from dragon_quant.infra.assets.sync import tables

    spec_tables = {spec.table for spec in tables.TABLE_SPECS}
    assert len(schema.TABLE_NAMES) == 15
    assert spec_tables <= set(schema.TABLE_NAMES)
    # prep_assets / auction_assets 由两个既有适配器同步（无 TableSpec）。
    assert spec_tables | {"prep_assets", "auction_assets"} == set(schema.TABLE_NAMES)


def test_canonical_assets_modules_expose_the_approved_boundaries():
    from dragon_quant.infra.assets import db, schema

    for name in (
        "TABLE_NAMES",
        "BASE_SCHEMA",
        "_REQUIRED_COLUMNS",
        "_WRITE_REQUIRED_COLUMNS",
        "_COLUMN_MIGRATIONS",
    ):
        assert hasattr(schema, name), name
    for name in (
        "_connect",
        "_ensure_schema",
        "_lock",
        "save_prep_assets",
        "get_prep_assets",
        "save_prep_snapshot",
        "get_prep_snapshot",
        "save_auction_asset",
        "list_auction_assets",
        "replace_auction_snapshots",
        "list_auction_snapshots",
        "get_auction_snapshot",
        "AssetsDbSchemaError",
    ):
        assert hasattr(db, name), name


def test_assets_main_exports_public_facade():
    from dragon_quant.infra.assets import main

    assert set(main.__all__) == {
        "run", "SYNC_ACTIONS", "db", "dragons_mirror", "save_prep_assets",
        "get_prep_assets", "get_prep_snapshot", "save_prep_snapshot",
        "save_auction_asset", "list_auction_assets", "replace_auction_snapshots",
        "list_auction_snapshots", "get_auction_snapshot", "diff_dragons",
        "backfill_dragons",
    }
