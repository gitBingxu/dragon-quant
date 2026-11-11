"""workspace 策略目录：清单/校验不导入用户代码、重名与版本错误显式失败。"""

from __future__ import annotations

import json

import pytest

from dragon_quant.workspace import catalog, config, main


def _init(tmp_path):
    root = tmp_path / "ws"
    main.init_workspace(str(root))
    return root


def test_init_creates_two_templates_and_builtin(tmp_path):
    root = _init(tmp_path)
    data = main.list_strategies(str(root))
    ids = [item["id"] for item in data["strategies"]]
    assert ids == ["builtin:dragon_pullback_daily",
                   "user:example_momentum", "user:example_meanrev"]
    assert data["current"] == config.BUILTIN_STRATEGY_ID
    assert (root / "AGENTS.md").is_file()
    assert (root / "strategies" / "example_momentum" / "strategy.py").is_file()


def test_init_does_not_overwrite_user_files(tmp_path):
    root = _init(tmp_path)
    strategy = root / "strategies" / "example_momentum" / "strategy.py"
    strategy.write_text("# user edit\n", encoding="utf-8")
    main.init_workspace(str(root))
    assert strategy.read_text(encoding="utf-8") == "# user edit\n"


def test_list_never_imports_user_code(tmp_path):
    root = _init(tmp_path)
    (root / "strategies" / "example_momentum" / "strategy.py").write_text(
        "raise RuntimeError('must not import')\n", encoding="utf-8")
    data = main.list_strategies(str(root))
    entry = next(item for item in data["strategies"] if item["id"] == "user:example_momentum")
    assert entry["valid"] is True  # 只读 descriptor
    with pytest.raises(config.WorkspaceError):
        main.validate_strategy("user:example_momentum", str(root))


def test_duplicate_strategy_id_is_reported(tmp_path):
    root = _init(tmp_path)
    cfg = config.load_workspace(root)
    clone = root / "strategies" / "clone"
    clone.mkdir()
    (clone / "strategy.py").write_text("def create_strategy(params):\n    return None\n")
    (clone / "strategy.json").write_text(json.dumps({
        "id": "user:example_momentum", "label": "dup", "sdk_version": 1,
    }), encoding="utf-8")
    cfg.strategies.append("strategies/clone/strategy.json")
    config.write_workspace(cfg)
    descriptors = catalog.list_descriptors(config.load_workspace(root))
    dup = next(d for d in descriptors if d.path and d.path.parent.name == "clone")
    assert any("重复" in err for err in dup.errors)


def test_incompatible_sdk_version_fails(tmp_path):
    root = _init(tmp_path)
    descriptor = root / "strategies" / "example_meanrev" / "strategy.json"
    data = json.loads(descriptor.read_text(encoding="utf-8"))
    data["sdk_version"] = 99
    descriptor.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(config.WorkspaceError):
        main.resolve_selection(str(root), "user:example_meanrev")


def test_unknown_strategy_lists_known_ids(tmp_path):
    root = _init(tmp_path)
    with pytest.raises(config.WorkspaceError) as exc:
        main.resolve_selection(str(root), "user:nope")
    assert "未知策略" in str(exc.value)
    assert "builtin:dragon_pullback_daily" in str(exc.value)


def test_workspace_id_mismatch_rejected(tmp_path):
    root = _init(tmp_path)
    path = root / "workspace.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["workspace_id"] = "ws_deadbeef0000"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(config.WorkspaceError):
        config.load_workspace(root)


def test_parameter_schema_validates_type_range_and_enum(tmp_path):
    from dragon_quant.workspace.loader import merge_parameters

    descriptor = catalog.builtin_descriptor()
    descriptor.parameters = {
        "threshold": {"type": "number", "default": 1.0, "min": 0.0, "max": 5.0},
        "flag": {"type": "bool", "default": False},
        "mode": {"type": "string", "default": "a", "enum": ["a", "b"]},
        "count": {"type": "int", "default": 1},
    }
    descriptor.id = "user:schema"
    assert merge_parameters(descriptor, {"threshold": 2.5})["threshold"] == 2.5
    with pytest.raises(config.WorkspaceError):
        merge_parameters(descriptor, {"threshold": "x"})
    with pytest.raises(config.WorkspaceError):
        merge_parameters(descriptor, {"threshold": 9})
    with pytest.raises(config.WorkspaceError):
        merge_parameters(descriptor, {"flag": 1})
    with pytest.raises(config.WorkspaceError):
        merge_parameters(descriptor, {"mode": "c"})
    with pytest.raises(config.WorkspaceError):
        merge_parameters(descriptor, {"count": 1.5})
