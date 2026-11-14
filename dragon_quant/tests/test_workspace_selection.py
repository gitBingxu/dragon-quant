"""workspace 选择：revision CAS、路径优先级、临时路径不改登记。"""

from __future__ import annotations

import pytest

from dragon_quant.workspace import config, main


def _init(tmp_path, name="ws"):
    root = tmp_path / name
    main.init_workspace(str(root))
    return root


def test_select_strategy_persists_and_bumps_revision(tmp_path):
    root = _init(tmp_path)
    first = main.select_strategy(str(root), "user:example_momentum")
    assert first["current"] == "user:example_momentum"
    assert first["revision"] >= 2
    second = main.select_strategy(str(root), "user:example_meanrev",
                                  expected_revision=first["revision"])
    assert second["revision"] == first["revision"] + 1
    assert main.current_selection(str(root))["strategy_id"] == "user:example_meanrev"


def test_revision_conflict_keeps_original_file(tmp_path):
    root = _init(tmp_path)
    current = main.current_selection(str(root))
    before = (root / "workspace.json").read_text(encoding="utf-8")
    with pytest.raises(config.WorkspaceConflict):
        main.select_strategy(str(root), "user:example_momentum",
                             expected_revision=current["revision"] + 5)
    after = (root / "workspace.json").read_text(encoding="utf-8")
    assert after == before


def test_explicit_path_beats_env_beats_registered(tmp_path, monkeypatch):
    registered = _init(tmp_path, "registered")
    other = _init(tmp_path, "other")
    # 注册的是第二个（init 会登记 active）
    assert config.read_active_workspace() == other
    monkeypatch.setenv("DQ_WORKSPACE", str(registered))
    assert config.resolve_workspace_path(None) == registered
    assert config.resolve_workspace_path(str(other)) == other
    monkeypatch.delenv("DQ_WORKSPACE")
    assert config.resolve_workspace_path(None) == other


def test_resolving_with_explicit_path_does_not_change_registry(tmp_path):
    first = _init(tmp_path, "first")
    second = _init(tmp_path, "second")
    assert config.read_active_workspace() == second
    main.resolve_selection(str(first), "user:example_momentum")
    assert config.read_active_workspace() == second


def test_uninitialized_explicit_workspace_errors(tmp_path):
    with pytest.raises(config.WorkspaceError):
        main.resolve_selection(str(tmp_path / "missing"), None)


def test_builtin_selection_without_workspace(monkeypatch):
    # 该用例显式隔离本机登记，避免同 session 其他 init 的影响。
    monkeypatch.setattr(config, "read_active_workspace", lambda: None)
    monkeypatch.delenv("DQ_WORKSPACE", raising=False)
    selection = main.resolve_selection(None, None)
    assert selection.strategy_id == config.BUILTIN_STRATEGY_ID
    assert selection.workspace is None
