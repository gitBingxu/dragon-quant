"""workspace 选择：revision CAS、路径优先级、失效登记与临时路径不改登记。"""

from __future__ import annotations

import pytest

from dragon_quant.workspace import config, main


def _init(tmp_path, name="ws", *, register=False):
    root = tmp_path / name
    main.init_workspace(str(root), register=register)
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
    registered = _init(tmp_path, "registered", register=True)
    other = _init(tmp_path, "other", register=True)
    # 注册的是第二个（init 会登记 active）
    assert config.read_active_workspace() == other
    monkeypatch.setenv("DQ_WORKSPACE", str(registered))
    assert config.resolve_workspace_path(None) == registered
    assert config.resolve_workspace_path(str(other)) == other
    monkeypatch.delenv("DQ_WORKSPACE")
    assert config.resolve_workspace_path(None) == other


def test_resolving_with_explicit_path_does_not_change_registry(tmp_path):
    first = _init(tmp_path, "first", register=True)
    second = _init(tmp_path, "second", register=True)
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


def test_registered_workspace_removed_falls_back_to_builtin(tmp_path, monkeypatch):
    """失效的本机登记（临时目录已删）不得阻断内置/默认解析，也不得被改写。"""
    stale = tmp_path / "tmp-ws" / "ws"
    stale.mkdir(parents=True)
    monkeypatch.setattr(config, "read_active_workspace", lambda: stale)
    monkeypatch.delenv("DQ_WORKSPACE", raising=False)
    stale.rmdir()

    for strategy_id in (None, config.BUILTIN_STRATEGY_ID):
        selection = main.resolve_selection(None, strategy_id)
        assert selection.strategy_id == config.BUILTIN_STRATEGY_ID
        assert selection.workspace is None
    # 不隐式 init、不改登记：路径仍不存在。
    assert not stale.exists()
    assert config.read_active_workspace() == stale


def test_env_workspace_missing_still_errors(tmp_path, monkeypatch):
    """DQ_WORKSPACE 是显式指定：路径缺失必须报错，不静默退回内建默认。"""
    monkeypatch.setattr(config, "read_active_workspace", lambda: None)
    missing = tmp_path / "missing-env"
    monkeypatch.setenv("DQ_WORKSPACE", str(missing))
    with pytest.raises(config.WorkspaceError):
        main.resolve_selection(None, config.BUILTIN_STRATEGY_ID)
    assert not missing.exists()


def test_registered_corrupt_workspace_still_errors(tmp_path, monkeypatch):
    """坏 workspace.json 不是「未初始化」：即使来自本机登记也显式失败。"""
    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "workspace.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(config, "read_active_workspace", lambda: corrupt)
    monkeypatch.delenv("DQ_WORKSPACE", raising=False)
    with pytest.raises(config.WorkspaceError) as exc:
        main.resolve_selection(None, config.BUILTIN_STRATEGY_ID)
    assert "无法解析" in str(exc.value)


def test_resolution_source_is_reported(tmp_path, monkeypatch):
    root = _init(tmp_path, "ws")
    assert config.resolve_workspace_path_with_source(str(root)) == (
        root, config.WORKSPACE_SOURCE_EXPLICIT)
    monkeypatch.setenv("DQ_WORKSPACE", str(root))
    assert config.resolve_workspace_path_with_source(None) == (
        root, config.WORKSPACE_SOURCE_ENV)
    monkeypatch.delenv("DQ_WORKSPACE")
    monkeypatch.setattr(config, "read_active_workspace", lambda: root)
    assert config.resolve_workspace_path_with_source(None) == (
        root, config.WORKSPACE_SOURCE_REGISTERED)
    monkeypatch.setattr(config, "read_active_workspace", lambda: None)
    assert config.resolve_workspace_path_with_source(None) == (None, None)
