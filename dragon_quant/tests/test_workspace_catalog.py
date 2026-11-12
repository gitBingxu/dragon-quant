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


def _add_descriptor(root, slug, parameters):
    strategy_dir = root / "strategies" / slug
    strategy_dir.mkdir()
    (strategy_dir / "strategy.py").write_text(
        "def create_strategy(params):\n    return None\n", encoding="utf-8")
    (strategy_dir / "strategy.json").write_text(json.dumps({
        "id": f"user:{slug}", "label": slug, "sdk_version": 1,
        "parameters": parameters,
    }), encoding="utf-8")
    cfg = config.load_workspace(root)
    cfg.strategies.append(f"strategies/{slug}/strategy.json")
    config.write_workspace(cfg)


def test_parameter_display_metadata_is_validated(tmp_path):
    root = _init(tmp_path)
    _add_descriptor(root, "badmeta", {
        "bad_label": {"type": "number", "default": 1.0, "label": 5},
        "bad_help": {"type": "number", "default": 1.0, "help": ["x"]},
        "bad_unit": {"type": "number", "default": 1.0, "unit": 3},
        "bad_step": {"type": "number", "default": 1.0, "step": 0},
        "bad_range": {"type": "number", "default": 1.0, "min": 5, "max": 1},
        "bad_enum": {"type": "string", "default": "a", "enum": ["a", 2]},
        "bad_enum_default": {"type": "string", "default": "z", "enum": ["a", "b"]},
        "bad_default": {"type": "int", "default": 1.5},
    })
    descriptors = catalog.list_descriptors(config.load_workspace(root))
    entry = next(d for d in descriptors if d.id == "user:badmeta")
    joined = "\n".join(entry.errors)
    assert "bad_label 的 label 必须是字符串" in joined
    assert "bad_help 的 help 必须是字符串" in joined
    assert "bad_unit 的 unit 必须是字符串" in joined
    assert "bad_step 的 step 必须是正数" in joined
    assert "bad_range 的 min 不能大于 max" in joined
    assert "bad_enum 的 enum 值类型必须与 type 相容" in joined
    assert "bad_enum_default 的默认值必须在 enum 内" in joined
    assert "bad_default 的默认值类型必须是 int" in joined


def test_valid_display_metadata_is_kept(tmp_path):
    root = _init(tmp_path)
    spec = {"type": "number", "default": 1.0, "min": 0, "max": 5,
            "step": 0.5, "label": "阈值", "help": "低于该值不参与", "unit": "%"}
    _add_descriptor(root, "goodmeta", {"threshold": spec})
    descriptors = catalog.list_descriptors(config.load_workspace(root))
    entry = next(d for d in descriptors if d.id == "user:goodmeta")
    assert entry.valid is True, entry.errors
    assert entry.parameters == {"threshold": spec}


def test_builtin_and_templates_expose_display_metadata(tmp_path):
    root = _init(tmp_path)
    data = main.list_strategies(str(root))
    for item in data["strategies"]:
        for name, spec in item["parameters"].items():
            assert spec.get("label"), f"{item['id']} / {name} 缺少 label"
            assert spec.get("help"), f"{item['id']} / {name} 缺少 help"
    builtin = catalog.builtin_descriptor()
    assert "兼容" in builtin.parameters["take_profit_pct"]["help"]


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
