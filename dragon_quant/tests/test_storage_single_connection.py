"""Task 5/6: 全仓只有两个 ``sqlite3.connect`` 调用点。

``dragon_quant`` 内（测试文件除外）只允许两处直接打开 SQLite：

- ``dragon_quant/infra/storage/connection.py``（dragon.db，canonical）
- ``dragon_quant/infra/assets/db.py``（assets.db，Task 6 迁入）

例外**精确到这两个文件**：任何新增/搬移连接点都必须同步修改本测试，不能放宽为
目录级或前缀级白名单。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _sqlite_connect_call_sites(root: Path) -> list[str]:
    """AST 找 ``sqlite3.connect(...)`` 调用，返回相对仓库根的路径（去重排序）。"""
    sites: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.relative_to(root).parts[:1] == ("tests",):
            # ``dragon_quant/tests`` 是 source-only 测试树，不属于生产包扫描面。
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "connect"
                and isinstance(func.value, ast.Name)
                and func.value.id == "sqlite3"
            ):
                sites.add(path.relative_to(ROOT).as_posix())
    return sorted(sites)


def test_only_two_sqlite_connection_sites():
    assert _sqlite_connect_call_sites(ROOT / "dragon_quant") == [
        "dragon_quant/infra/assets/db.py",
        "dragon_quant/infra/storage/connection.py",
    ]
