"""策略冻结快照：内容哈希、原子发布、篡改检测、路径逃逸拒绝。"""

from __future__ import annotations

import pytest

from dragon_quant.workspace import config, main, snapshots


def _init(tmp_path):
    root = tmp_path / "ws"
    main.init_workspace(str(root))
    return root


def _publish(root, strategy_id="user:example_momentum", params=None):
    selection = main.resolve_selection(str(root), strategy_id)
    selection.parameters = params or selection.parameters
    return main.freeze_strategy(selection)


def test_publish_snapshot_records_files_and_params(tmp_path):
    root = _init(tmp_path)
    ref = _publish(root, params={"min_open_gap_pct": 1.0})
    assert ref["snapshot_id"]
    assert ref["workspace"].endswith("ws")
    assert ref["params"] == {"min_open_gap_pct": 1.0}
    assert "strategies/example_momentum/strategy.py" in ref["files"]
    assert ref["files"]["strategies/example_momentum/strategy.py"]
    manifest = snapshots.read_snapshot(config.load_workspace(root), ref["snapshot_id"])
    assert manifest["strategy_id"] == "user:example_momentum"


def test_helpers_change_produces_new_snapshot(tmp_path):
    root = _init(tmp_path)
    first = _publish(root)
    (root / "helpers" / "shared.py").write_text("VALUE = 1\n", encoding="utf-8")
    second = _publish(root)
    assert first["snapshot_id"] != second["snapshot_id"]
    (root / "helpers" / "shared.py").write_text("VALUE = 2\n", encoding="utf-8")
    third = _publish(root)
    assert third["snapshot_id"] != second["snapshot_id"]


def test_verify_detects_tampering(tmp_path):
    root = _init(tmp_path)
    ref = _publish(root)
    workspace = config.load_workspace(root)
    ok = snapshots.verify_snapshot(workspace, ref["snapshot_id"])
    assert ok["ok"] is True
    target = root / "snapshots" / ref["snapshot_id"] / "strategies" / "example_momentum" / "strategy.py"
    target.write_text("# tampered\n", encoding="utf-8")
    result = snapshots.verify_snapshot(workspace, ref["snapshot_id"])
    assert result["ok"] is False
    assert "strategies/example_momentum/strategy.py" in result["mismatched"]


def test_snapshot_dir_rejects_traversal(tmp_path):
    root = _init(tmp_path)
    workspace = config.load_workspace(root)
    with pytest.raises(config.WorkspaceError):
        snapshots.snapshot_dir(workspace, "../escape")


def test_builtin_ref_is_toolkit_fingerprint():
    ref = snapshots.builtin_ref(strategy_id=config.BUILTIN_STRATEGY_ID,
                                params={"min_score": 50.0})
    assert ref["snapshot_id"].startswith("builtin:")
    assert ref["reproducibility"] == "toolkit_fingerprint"
    again = snapshots.builtin_ref(strategy_id=config.BUILTIN_STRATEGY_ID,
                                  params={"min_score": 50.0})
    assert again["snapshot_id"] == ref["snapshot_id"]
    changed = snapshots.builtin_ref(strategy_id=config.BUILTIN_STRATEGY_ID,
                                    params={"min_score": 60.0})
    assert changed["snapshot_id"] != ref["snapshot_id"]
