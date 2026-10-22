"""M4 CLI 测试：日期校验、退出码、脱敏、子命令帮助。"""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from dragon_quant import cli
from dragon_quant.assets_sync.credentials import AssetsConfig
from dragon_quant.assets_sync.errors import ConfigError, LockBusyError


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with patch("sys.argv", ["dragon-quant"] + argv), \
         redirect_stdout(out), redirect_stderr(err):
        with unittest.TestCase().assertRaises(SystemExit) as raised:
            cli.main()
    return raised.exception.code, out.getvalue(), err.getvalue()


def _config(secret="super-secret-value"):
    return AssetsConfig(
        endpoint="https://example.r2.cloudflarestorage.com",
        region="auto",
        bucket="bucket",
        access_key_id="access-key-id",
        secret_access_key=secret,
    )


class TestAssetsCliHelp(unittest.TestCase):
    def test_subcommand_help(self):
        for action in ("push", "pull", "status", "verify"):
            code, out, _ = _run(["assets", action, "-h"])
            self.assertEqual(code, 0)
            self.assertIn(f"assets {action}", out)


class TestAssetsCliValidation(unittest.TestCase):
    def test_invalid_date_exits_2_before_network(self):
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   side_effect=AssertionError("不应触网/读配置")):
            code, out, _ = _run(["assets", "push", "--from", "2026-13-01"])
        self.assertEqual(code, 2)
        self.assertIn("日期非法", json.loads(out)["failed"][0]["reason"])

    def test_future_date_exits_2(self):
        code, out, _ = _run(["assets", "push", "--from", "29990101"])
        self.assertEqual(code, 2)
        self.assertIn("未来日期", json.loads(out)["failed"][0]["reason"])

    def test_from_after_to_exits_2(self):
        code, out, _ = _run([
            "assets", "push", "--from", "2026-10-22", "--to", "2026-10-20"])
        self.assertEqual(code, 2)
        self.assertIn("不能晚于", json.loads(out)["failed"][0]["reason"])

    def test_pull_requires_range(self):
        code, out, _ = _run(["assets", "pull", "--apply"])
        self.assertEqual(code, 2)
        self.assertIn("--from", json.loads(out)["failed"][0]["reason"])

    def test_verify_deep_requires_range_or_samples(self):
        code, out, _ = _run(["assets", "verify", "--deep"])
        self.assertEqual(code, 2)
        self.assertIn("--samples", json.loads(out)["failed"][0]["reason"])

    def test_verify_range_must_be_paired(self):
        code, out, _ = _run(["assets", "verify", "--from", "2026-10-20"])
        self.assertEqual(code, 2)
        self.assertIn("成对", json.loads(out)["failed"][0]["reason"])


class TestAssetsCliExitCodes(unittest.TestCase):
    def _run_with_push(self, result, argv=None):
        argv = argv or ["assets", "push"]
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   return_value=_config()), \
             patch("dragon_quant.assets_sync.service.push", return_value=result):
            return _run(argv)

    def test_success_exit_0(self):
        code, out, err = self._run_with_push({
            "ok": True, "fatal": False, "failed": [],
            "summary": {"pushed": 1, "skipped": 0},
        })
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])
        self.assertIn("推送", err)

    def test_partial_failure_exit_1(self):
        code, _, _ = self._run_with_push({
            "ok": False, "fatal": False,
            "failed": [{"trade_date": "2026-10-21", "kind": "shrink"}],
            "summary": {"pushed": 0},
        })
        self.assertEqual(code, 1)

    def test_fatal_exit_2(self):
        code, _, _ = self._run_with_push({
            "ok": False, "fatal": True,
            "failed": [{"kind": "transport"}], "summary": {},
        })
        self.assertEqual(code, 2)

    def test_config_error_exit_2(self):
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   side_effect=ConfigError("缺少配置项: endpoint")):
            code, out, _ = _run(["assets", "status"])
        self.assertEqual(code, 2)
        self.assertIn("endpoint", json.loads(out)["failed"][0]["reason"])

    def test_lock_busy_exit_1(self):
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   return_value=_config()), \
             patch("dragon_quant.assets_sync.service.ProcessLock") as lock_cls:
            lock_cls.return_value.acquire.side_effect = LockBusyError("busy")
            code, out, _ = _run(["assets", "push"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["failed"][0]["kind"], "lock")

    def test_secret_not_leaked(self):
        code, out, err = self._run_with_push({
            "ok": True, "fatal": False, "failed": [],
            "summary": {"pushed": 1},
        })
        self.assertEqual(code, 0)
        self.assertNotIn("super-secret-value", out + err)
        self.assertNotIn("access-key-id", out + err)


class TestAssetsCliTargetDir(unittest.TestCase):
    def test_pull_target_dir_creates_parent_and_isolates(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "scratch"
            main_data = Path(tmp) / "main-data"
            captured = {}

            def fake_pull(backend, sources, **kwargs):
                captured.update(kwargs)
                captured["db_path"] = sources[0].db_path
                return {"ok": True, "fatal": False, "failed": [],
                        "summary": {"restored": 0}}

            with patch("dragon_quant.assets_sync.credentials.load_config",
                       return_value=_config()), \
                 patch("dragon_quant.storage.paths.DATA_DIR", main_data), \
                 patch("dragon_quant.assets_sync.service.pull",
                       side_effect=fake_pull):
                code, _, _ = _run([
                    "assets", "pull", "--from", "2026-10-21", "--to", "2026-10-21",
                    "--apply", "--target-dir", str(target)])
            self.assertEqual(code, 0)
            self.assertTrue(target.exists())
            self.assertEqual(captured["db_path"], target / "assets.db")
            self.assertEqual(captured["paths"].lock_path, target / "assets_sync.lock")
            self.assertEqual(captured["paths"].tmp_dir, target / "tmp" / "assets_sync")
            self.assertFalse((main_data / "assets_sync" / "state.json").exists())
            self.assertFalse((main_data / "assets_sync.lock").exists())


if __name__ == "__main__":
    unittest.main()
