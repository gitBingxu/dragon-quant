"""``dashboard`` 命令：自装引导状态机 + 端口策略（全部离线）。"""

from __future__ import annotations

import errno
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dragon_quant.commands import dashboard


def test_web_extra_status_true_in_dev_env():
    ok, reason = dashboard.web_extra_status()
    assert ok is True and reason is None


def test_ensure_web_extra_available_is_noop():
    with patch.object(dashboard, "web_extra_status", return_value=(True, None)):
        assert dashboard.ensure_web_extra() is True


def test_non_tty_failure_prints_manual_hint_without_running_pip(capsys):
    with patch.object(dashboard, "web_extra_status", return_value=(False, "boom")), \
            patch.object(dashboard, "_is_tty", return_value=False), \
            patch.object(dashboard, "_run_pip_install") as pip, \
            pytest.raises(SystemExit) as raised:
        dashboard.ensure_web_extra()
    assert raised.value.code == 1
    pip.assert_not_called()
    stderr = capsys.readouterr().err
    assert 'python -m pip install "dragon-quant[web]"' in stderr
    assert "pipx inject dragon-quant" in stderr
    assert "--break-system-packages" not in stderr


def test_tty_refusal_exits_without_installing(capsys):
    with patch.object(dashboard, "web_extra_status", return_value=(False, "boom")), \
            patch.object(dashboard, "_is_tty", return_value=True), \
            patch("builtins.input", return_value="n"), \
            patch.object(dashboard, "_run_pip_install") as pip, \
            pytest.raises(SystemExit) as raised:
        dashboard.ensure_web_extra()
    assert raised.value.code == 1
    pip.assert_not_called()
    assert "占用" not in capsys.readouterr().err


def test_tty_yes_installs_and_verifies(capsys):
    statuses = [(False, "boom"), (True, None)]
    with patch.object(dashboard, "web_extra_status", side_effect=statuses), \
            patch.object(dashboard, "_is_tty", return_value=True), \
            patch("builtins.input", return_value=""), \
            patch.object(dashboard, "_run_pip_install", return_value=True) as pip:
        assert dashboard.ensure_web_extra() is True
    pip.assert_called_once_with()
    out = capsys.readouterr().out
    assert "已安装 [web] 可选依赖" in out
    assert "pipx inject" in out


def test_yes_flag_and_env_skip_confirmation():
    statuses = [(False, "boom"), (True, None)]
    with patch.object(dashboard, "web_extra_status", side_effect=statuses), \
            patch("builtins.input") as ask, \
            patch.object(dashboard, "_run_pip_install", return_value=True):
        assert dashboard.ensure_web_extra(auto=True) is True
    ask.assert_not_called()

    statuses = [(False, "boom"), (True, None)]
    with patch.object(dashboard, "web_extra_status", side_effect=statuses), \
            patch.dict("os.environ", {"DQ_WEB_AUTO_INSTALL": "YES"}), \
            patch("builtins.input") as ask, \
            patch.object(dashboard, "_run_pip_install", return_value=True):
        assert dashboard.ensure_web_extra() is True
    ask.assert_not_called()


def test_pip_failure_and_import_failure_exit_one(capsys):
    with patch.object(dashboard, "web_extra_status", return_value=(False, "boom")), \
            patch.object(dashboard, "_is_tty", return_value=False), \
            patch.object(dashboard, "_run_pip_install", return_value=False), \
            patch.dict("os.environ", {"DQ_WEB_AUTO_INSTALL": "1"}), \
            pytest.raises(SystemExit) as raised:
        dashboard.ensure_web_extra()
    assert raised.value.code == 1
    assert "--break-system-packages" not in capsys.readouterr().err

    with patch.object(dashboard, "web_extra_status", return_value=(False, "boom")), \
            patch.object(dashboard, "_is_tty", return_value=False), \
            patch.object(dashboard, "_run_pip_install", return_value=True), \
            patch.dict("os.environ", {"DQ_WEB_AUTO_INSTALL": "1"}), \
            pytest.raises(SystemExit) as raised:
        dashboard.ensure_web_extra()
    assert raised.value.code == 1


def test_run_pip_install_never_breaks_system_packages():
    with patch("subprocess.run", side_effect=RuntimeError("blocked")) as run, \
            patch("sys.stderr"):
        assert dashboard._run_pip_install() is False
    argv = run.call_args.args[0]
    assert argv[:4] == [dashboard.sys.executable, "-m", "pip", "install"]
    assert "--break-system-packages" not in argv and "--user" not in argv


def test_implicit_port_conflict_retries_ephemeral_once(capsys):
    conflict = OSError(errno.EADDRINUSE, "Address already in use")
    args = SimpleNamespace(port=None, no_browser=True)
    with patch.object(dashboard, "_start_server", side_effect=[conflict, None]) as start:
        dashboard.start_ui(args)
    assert [call.args[0] for call in start.call_args_list] == [8765, 0]
    assert "占用" in capsys.readouterr().err


def test_explicit_port_conflict_exits_and_other_oserror_propagates(capsys):
    conflict = OSError(errno.EADDRINUSE, "Address already in use")
    args = SimpleNamespace(port=8799, no_browser=True)
    with patch.object(dashboard, "_start_server", side_effect=conflict), \
            pytest.raises(SystemExit) as raised:
        dashboard.start_ui(args)
    assert raised.value.code == 1
    stderr = capsys.readouterr().err
    assert "8799" in stderr and "--port" in stderr and "占用" in stderr

    denied = OSError(errno.EACCES, "Permission denied")
    with patch.object(dashboard, "_start_server", side_effect=denied), \
            pytest.raises(OSError) as raised:
        dashboard.start_ui(SimpleNamespace(port=None, no_browser=True))
    assert raised.value.errno == errno.EACCES


def test_handle_runs_bootstrap_before_start():
    args = SimpleNamespace(port=0, no_browser=True, yes=True)
    with patch.object(dashboard, "ensure_web_extra", return_value=True) as ensure, \
            patch.object(dashboard, "start_ui") as start:
        dashboard.handle(args, object())
    ensure.assert_called_once_with(auto=True)
    start.assert_called_once_with(args, default_page="review")
