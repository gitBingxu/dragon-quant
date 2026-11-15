"""增量续算：按基准冻结策略重建运行时；缺失/不兼容明确拒绝。"""

from __future__ import annotations

import json

import pytest

from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.service import _merged_run_meta, _runtime_from_frozen_ref
from dragon_quant.workspace import config, main, snapshots


def _ref(tmp_path):
    root = tmp_path / "ws"
    main.init_workspace(str(root))
    selection = main.resolve_selection(str(root), "user:example_momentum")
    return root, main.freeze_strategy(selection)


def _base_run(ref):
    return {"run_meta": {"strategy_ref": ref}, "strategy_name": "user:example_momentum",
            "date_to": "2026-11-10", "initial_cash": 100000.0}


def test_legacy_run_without_ref_is_rejected():
    """M6：版本证据不足的旧 run 不能悄悄用当前规则增量续算。"""
    with pytest.raises(ValueError) as exc:
        _runtime_from_frozen_ref({"run_meta": {}}, StrategyConfig())
    assert "拒绝" in str(exc.value)
    assert "strategy_ref" in str(exc.value)


def test_valid_user_ref_rebuilds_runtime(tmp_path):
    root, ref = _ref(tmp_path)
    runtime, resolver, returned = _runtime_from_frozen_ref(_base_run(ref), StrategyConfig())
    assert runtime is not None
    assert runtime.strategy_id == "user:example_momentum"
    assert resolver is not None
    assert returned["snapshot_id"] == ref["snapshot_id"]


def test_missing_snapshot_rejected(tmp_path):
    root, ref = _ref(tmp_path)
    ref["snapshot_id"] = "deadbeefdeadbeef"
    with pytest.raises(ValueError) as exc:
        _runtime_from_frozen_ref(_base_run(ref), StrategyConfig())
    assert "快照" in str(exc.value)


def test_incompatible_sdk_rejected(tmp_path):
    root, ref = _ref(tmp_path)
    manifest_path = root / "snapshots" / ref["snapshot_id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sdk_version"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        _runtime_from_frozen_ref(_base_run(ref), StrategyConfig())
    assert "SDK" in str(exc.value)


def test_tampered_snapshot_rejected(tmp_path):
    root, ref = _ref(tmp_path)
    target = root / "snapshots" / ref["snapshot_id"] / "strategies" / "example_momentum" / "strategy.py"
    target.write_text("# changed\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        _runtime_from_frozen_ref(_base_run(ref), StrategyConfig())
    assert "哈希" in str(exc.value)


def test_builtin_version_mismatch_rejected():
    ref = snapshots.builtin_ref(strategy_id=config.BUILTIN_STRATEGY_ID, params={})
    ref["toolkit_version"] = "0.0.0"
    with pytest.raises(ValueError) as exc:
        _runtime_from_frozen_ref(_base_run(ref), StrategyConfig())
    assert "版本不一致" in str(exc.value)


def test_merged_run_meta_keeps_strategy_ref():
    base = {"run_meta": {"a": 1}, "strategy_name": "s", "initial_cash": 100000.0}
    ref = {"strategy_id": "user:x", "snapshot_id": "abc"}
    merged = _merged_run_meta(base, {"data_quality": "strict_5min"},
                             total_days=1, date_from="2026-11-11",
                             date_to="2026-11-11", strategy_ref=ref)
    assert merged["strategy_ref"] == ref
    assert merged["a"] == 1
