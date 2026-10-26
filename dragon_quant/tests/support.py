"""测试公共 helper：存储隔离 + S0 架构基线快照。

``storage.dragons.save_dragons`` 在主写 dragon.db 之后会 fan-out 双写
assets.db 镜像。只 patch dragon.db 连接缝的测试挡不住第二条连接，镜像写会落到
真实 assets.db（2026-10-23 的脏数据事故）。需要写 dragons 的用例一律用
``patch_storage_dbs`` 一次覆盖两个连接点（dragon.db canonical 缝 +
``infra.assets.db`` canonical 缝）。

后半部分（``*_schema_snapshot`` / ``factor_version_snapshot`` / ``golden_hashes``）
是只读基线快照 helper，供 ``dragon_quant/tests/test_architecture_baseline.py`` 使用：临时空库读
schema、遍历内置因子取版本指纹、对 E2E golden 逐文件取 sha256，绝不碰真实数据。
"""

import hashlib
import sqlite3
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def patch_storage_dbs(dragon_path, assets_path=None) -> ExitStack:
    """同时 patch dragon.db 与 assets.db 的 ``_connect``，返回已进入的 ExitStack。

    ``assets_path`` 缺省为 ``<dragon_path>.assets``（两库必须不同文件）。
    """
    dragon_path = str(dragon_path)
    assets_path = str(assets_path or (dragon_path + ".assets"))
    if Path(dragon_path).resolve() == Path(assets_path).resolve():
        raise ValueError("dragon.db 与 assets.db 必须指向不同文件，否则镜像写不可观测")

    def _dragon(*args, **kwargs):
        return sqlite3.connect(dragon_path)

    def _assets(create=True, path=None):
        target = str(path) if path is not None else assets_path
        if not create and not Path(target).exists():
            return None
        return sqlite3.connect(target)

    stack = ExitStack()
    stack.enter_context(
        patch("dragon_quant.infra.storage.connection._connect", side_effect=_dragon))
    stack.enter_context(
        patch("dragon_quant.infra.assets.db._connect", side_effect=_assets))
    return stack


# ─── S0 架构基线快照（只读） ───────────────────────────────────────────────
# 供 dragon_quant/tests/test_architecture_baseline.py 使用：在 tmp_path 下建临时空库读 schema，
# 遍历内置因子 spec 取版本指纹，对 E2E golden 逐文件取 sha256。
# 约束：绝不打开真实 dragon.db / assets.db；所有连接在内部关闭。


def _schema_snapshot(db_path: Path) -> dict[str, dict[str, list[str]]]:
    """读出 ``{表名: {"columns": [...], "indexes": [...]}}``（按名排序，含自动索引）。

    排除 ``sqlite_*`` 内部表（如 AUTOINCREMENT 产生的 ``sqlite_sequence``）。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            if not row[0].startswith("sqlite_")
        ]
        snapshot: dict[str, dict[str, list[str]]] = {}
        for table in tables:
            columns = [row[1] for row in conn.execute(
                f'PRAGMA table_info("{table}")')]
            indexes = sorted(
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                    (table,))
                if row[0] is not None
            )
            snapshot[table] = {"columns": columns, "indexes": indexes}
        return snapshot
    finally:
        conn.close()


def dragon_schema_snapshot(tmp_dir) -> dict[str, dict[str, list[str]]]:
    """在 ``tmp_dir/dragon.db`` 上跑 ``init_db()``，返回表/列/索引快照。"""
    from dragon_quant.infra.storage.connection import init_db

    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / "dragon.db"
    with patch_storage_dbs(path):
        init_db()
    return _schema_snapshot(path)


def assets_schema_snapshot(tmp_dir) -> dict[str, dict[str, list[str]]]:
    """在 ``tmp_dir/assets.db`` 上建 schema，返回表/列/索引快照。"""
    from dragon_quant.infra.assets import db as assets_db

    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / "assets.db"
    conn = assets_db._connect(path=path)
    try:
        assets_db._ensure_schema(conn)
    finally:
        conn.close()
    return _schema_snapshot(path)


def factor_version_snapshot() -> dict[str, list[str]]:
    """全部内置因子的 ``{name: [version, version_source]}``。"""
    from dragon_quant.infra.factor.builtin import all_specs
    from dragon_quant.infra.factor.registry import compute_version_info

    return {spec.name: list(compute_version_info(spec)) for spec in all_specs()}


def golden_hashes() -> dict[str, str]:
    """``dragon_quant/tests/e2e/golden/**`` 逐文件 sha256（相对 POSIX 路径为键）。"""
    root = Path(__file__).resolve().parent / "e2e" / "golden"
    hashes: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(
            path.read_bytes()).hexdigest()
    return hashes
