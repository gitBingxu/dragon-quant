"""S0 架构基线快照：dragon/assets schema、因子版本、E2E golden 哈希。

本测试是第二轮 repository-wide 重构（human-friendly architecture）的行为护栏：
搬迁只允许改变代码位置，不允许改变 DB schema、因子 `compute_version_info()`
或 E2E golden 内容。基线由 `DQ_BASELINE_WRITE=1` 的 pytest 用例生成，之后逐项
精确比对。

所有数据库都在 `tmp_path` 下的临时库中构建，绝不读写真实 `dragon.db` /
`assets.db`；golden 只做只读 sha256。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.support import (
    assets_schema_snapshot,
    dragon_schema_snapshot,
    factor_version_snapshot,
    golden_hashes,
)

BASELINE = Path(__file__).resolve().parent / "fixtures" / "architecture_baseline.json"


def _collect(tmp_path: Path) -> dict:
    """组合四个只读 helper，形状与 fixture 顶层键一致。"""
    return {
        "dragon_schema": dragon_schema_snapshot(tmp_path),
        "assets_schema": assets_schema_snapshot(tmp_path),
        "factor_versions": factor_version_snapshot(),
        "golden_sha256": golden_hashes(),
    }


def test_write_baseline_when_requested(tmp_path):
    if os.environ.get("DQ_BASELINE_WRITE") != "1":
        pytest.skip("baseline write is opt-in")
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(
        json.dumps(_collect(tmp_path), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_schema_factor_and_golden_match_baseline(tmp_path):
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert dragon_schema_snapshot(tmp_path) == baseline["dragon_schema"]
    assert assets_schema_snapshot(tmp_path) == baseline["assets_schema"]
    assert factor_version_snapshot() == baseline["factor_versions"]
    assert golden_hashes() == baseline["golden_sha256"]
