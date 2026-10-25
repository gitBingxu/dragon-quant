"""
tests for dragon_quant.providers.cookie — Cookie 库与独立入口。

库职责（读写/获取）留在 ``cookie.py``；独立入口的 argparse/status/set/fetch
编排在 ``cookie_cli.py``，并允许注入 fetch 实现做离线测试。
"""
import inspect
import os
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

from dragon_quant.providers.cookie import get_xq, COOKIE_DIR


class TestCookiePaths(unittest.TestCase):

    def test_cookie_dir_exists(self):
        self.assertIsNotNone(COOKIE_DIR)

    def test_get_xq_returns_string_or_none(self):
        result = get_xq()
        self.assertTrue(result is None or isinstance(result, str))


# ─── Cookie CLI orchestration (cookie_cli) ─────────────────────────────────


def _cookie_cli():
    from dragon_quant.providers import cookie_cli

    return cookie_cli


def test_cookie_cli_status_reports_empty_and_present(monkeypatch, capsys):
    cli = _cookie_cli()

    monkeypatch.setattr(cli, "get_xq", lambda: "")
    assert cli.main(["status"]) is None
    assert capsys.readouterr().out.strip() == "雪球: ❌ (0字符)"

    monkeypatch.setattr(cli, "get_xq", lambda: "xq_a_token=abc; xq_is_login=1")
    assert cli.main(["status"]) is None
    assert capsys.readouterr().out.strip() == "雪球: ✅ (29字符)"


def test_cookie_cli_status_show_prints_a_single_fixed_prefix(monkeypatch, capsys):
    cli = _cookie_cli()

    monkeypatch.setattr(cli, "get_xq", lambda: "tok=1; u=2")
    assert cli.main(["status", "--show"]) is None
    out = capsys.readouterr().out
    assert "雪球: ✅ (10字符)" in out
    assert "雪球: tok=1; u=2..." in out

    monkeypatch.setattr(cli, "get_xq", lambda: "")
    assert cli.main(["status", "--show"]) is None
    assert capsys.readouterr().out == "雪球: ❌ (0字符)\n"


def test_cookie_cli_set_writes_only_when_a_value_is_given(monkeypatch, capsys):
    cli = _cookie_cli()
    written = []
    monkeypatch.setattr(cli, "set_xq", written.append)

    assert cli.main(["set"]) is None
    assert written == []
    assert capsys.readouterr().out == ""

    assert cli.main(["set", "--cookie", "tok=1; u=2"]) is None
    assert written == ["tok=1; u=2"]


def test_cookie_cli_fetch_uses_the_injected_implementation(capsys):
    cli = _cookie_cli()
    calls = []

    def fake_fetch() -> str:
        calls.append("fetch")
        return ""

    assert cli.main(["fetch"], fetch=fake_fetch) is None
    assert calls == ["fetch"]
    assert capsys.readouterr().out == ""


def test_cookie_cli_fetch_defaults_to_the_library_function():
    from dragon_quant.providers import cookie

    signature = inspect.signature(_cookie_cli().main)
    parameter = signature.parameters["fetch"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is cookie.fetch_xq


def test_cookie_cli_unknown_action_exits_two(capsys):
    cli = _cookie_cli()

    with pytest.raises(SystemExit) as raised:
        cli.main(["bogus"])

    assert raised.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "invalid choice" in err
    assert "bogus" in err


def test_independent_module_entry_still_prints_status(tmp_path):
    """``python -m dragon_quant.providers.cookie`` remains the real entry."""
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, DQ_DATA_DIR=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-m", "dragon_quant.providers.cookie", "status"],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "雪球: ❌ (0字符)"
