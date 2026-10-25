"""M6: ``assets_sync.cli_service`` 应用服务 + 薄命令适配器。

这些用例只验证职责边界与契约，不重跑同步算法：

* backend 注入覆盖 doctor/push/pull/status/verify 全部远程动作；
* 默认路径仍构造带凭据的 ``S3Backend``；
* 缺凭据/日期非法在构造后端之前失败，锁冲突映射 exit 1；
* ``pull --target-dir`` 只装配目标目录的 sources/paths；
* ``backfill-dragons`` 纯本地，从不构造后端；
* 命令层只做参数/输出/退出码，不装配后端或调用同步函数。
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dragon_quant.assets_sync import cli_service
from dragon_quant.assets_sync.credentials import AssetsConfig
from dragon_quant.assets_sync.errors import ConfigError, LockBusyError, StorageError


def _config() -> AssetsConfig:
    return AssetsConfig(
        endpoint="https://example.invalid",
        region="auto",
        bucket="bucket",
        access_key_id="access-key-id",
        secret_access_key="super-secret-value",
    )


def _args(action, **overrides) -> SimpleNamespace:
    defaults = dict(
        assets_action=action,
        profile=None,
        timeout=30.0,
        from_date=None,
        to_date=None,
        samples=None,
        deep=False,
        show_md5=False,
        all_dates=False,
        dry_run=False,
        allow_shrink=False,
        apply=False,
        force=False,
        pick=None,
        target_dir=None,
        orphans=False,
        json=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _ok_result(**summary):
    payload = {"pushed": 1, "skipped": 0}
    payload.update(summary)
    return {"ok": True, "fatal": False, "failed": [], "summary": payload}


class TestDateCoordination(unittest.TestCase):
    def test_supported_formats_normalize_to_iso(self):
        self.assertEqual(
            cli_service.parse_asset_date("20261020", "--from"), "2026-10-20")
        self.assertEqual(
            cli_service.parse_asset_date("2026/10/20", "--from"), "2026-10-20")
        self.assertEqual(
            cli_service.parse_asset_date("2026-10-20", "--from"), "2026-10-20")
        self.assertIsNone(cli_service.parse_asset_date(None, "--from"))

    def test_future_and_malformed_dates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "不能是未来日期"):
            cli_service.parse_asset_date("29990101", "--from")
        with self.assertRaisesRegex(ValueError, "日期非法"):
            cli_service.parse_asset_date("2026-13-01", "--to")


class TestBackendInjection(unittest.TestCase):
    def test_injected_backend_covers_every_remote_action(self):
        cases = (
            ("push", {}, "push"),
            ("pull", {"from_date": "2026-10-20", "to_date": "2026-10-21"}, "pull"),
            ("status", {}, "status"),
            ("verify", {}, "verify"),
        )
        for action, overrides, service_name in cases:
            with self.subTest(action=action):
                backend = object()
                captured = {}

                def fake_service(backend_arg, sources, **kwargs):
                    captured["backend"] = backend_arg
                    return _ok_result()

                with patch(f"dragon_quant.assets_sync.service.{service_name}",
                           side_effect=fake_service), \
                     patch("dragon_quant.assets_sync.credentials.load_config",
                           side_effect=AssertionError("注入 backend 时不得读凭据")):
                    outcome = cli_service.run(
                        action, _args(action, **overrides), backend=backend)

                self.assertEqual(outcome.exit_code, 0)
                self.assertEqual(outcome.command, f"assets {action}")
                self.assertEqual(outcome.payload["ok"], True)
                self.assertIs(captured["backend"], backend)

        backend = object()
        captured = {}

        def fake_doctor(backend_arg, samples):
            captured["backend"] = backend_arg
            captured["samples"] = samples
            return {"ok": True, "errors": [], "cleanup_errors": []}

        with patch("dragon_quant.assets_sync.doctor.run_doctor",
                   side_effect=fake_doctor), \
             patch("dragon_quant.assets_sync.credentials.load_config",
                   side_effect=AssertionError("注入 backend 时不得读凭据")):
            outcome = cli_service.run(
                "doctor", _args("doctor", samples=4), backend=backend)

        self.assertEqual(outcome.exit_code, 0)
        self.assertIs(captured["backend"], backend)
        self.assertEqual(captured["samples"], 4)

    def test_default_backend_is_s3_with_loaded_credentials(self):
        created = {}

        class FakeS3:
            def __init__(self, config, *, timeout=60.0, **kwargs):
                created["config"] = config
                created["timeout"] = timeout

        config = _config()
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   return_value=config), \
             patch("dragon_quant.assets_sync.backends.s3.S3Backend", FakeS3), \
             patch("dragon_quant.assets_sync.service.push",
                   return_value=_ok_result()):
            outcome = cli_service.run("push", _args("push"))

        self.assertEqual(outcome.exit_code, 0)
        self.assertIs(created["config"], config)
        self.assertEqual(created["timeout"], 30.0)

    def test_missing_credentials_fail_before_backend_construction(self):
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   side_effect=ConfigError("缺少配置项: endpoint")), \
             patch("dragon_quant.assets_sync.backends.s3.S3Backend",
                   side_effect=AssertionError("缺凭据时不得构造后端")):
            outcome = cli_service.run("status", _args("status"))

        self.assertEqual(outcome.exit_code, 2)
        self.assertIn("endpoint", outcome.payload["failed"][0]["reason"])
        self.assertEqual(outcome.payload["summary"], {})


class TestErrorCoordination(unittest.TestCase):
    def test_invalid_dates_never_touch_configuration(self):
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   side_effect=AssertionError("日期非法时不得读凭据")):
            outcome = cli_service.run(
                "push", _args("push", from_date="2026-13-01"))

        self.assertEqual(outcome.exit_code, 2)
        self.assertIn("日期非法", outcome.payload["failed"][0]["reason"])

    def test_from_after_to_exits_2(self):
        outcome = cli_service.run(
            "push", _args("push", from_date="2026-10-22", to_date="2026-10-20"))
        self.assertEqual(outcome.exit_code, 2)
        self.assertIn("不能晚于", outcome.payload["failed"][0]["reason"])

    def test_lock_conflict_maps_to_exit_1(self):
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   return_value=_config()), \
             patch("dragon_quant.assets_sync.service.push",
                   side_effect=LockBusyError("进程锁被占用")):
            outcome = cli_service.run("push", _args("push"))

        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(outcome.payload["failed"][0]["kind"], "lock")
        self.assertIn("进程锁被占用", outcome.payload["failed"][0]["reason"])

    def test_assets_sync_error_maps_to_exit_2_with_kind(self):
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   return_value=_config()), \
             patch("dragon_quant.assets_sync.service.verify",
                   side_effect=StorageError("boom")):
            outcome = cli_service.run("verify", _args("verify"))

        self.assertEqual(outcome.exit_code, 2)
        self.assertEqual(outcome.payload["failed"][0]["kind"], "StorageError")

    def test_unexpected_error_is_reported_with_type(self):
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   return_value=_config()), \
             patch("dragon_quant.assets_sync.service.status",
                   side_effect=RuntimeError("kaput")):
            outcome = cli_service.run("status", _args("status"))

        self.assertEqual(outcome.exit_code, 2)
        self.assertIn("RuntimeError: kaput", outcome.payload["failed"][0]["reason"])

    def test_verify_argument_errors_do_not_construct_backend(self):
        cases = (
            (_args("verify", from_date="2026-10-20"), "成对"),
            (_args("verify", deep=True), "--deep"),
            (_args("verify", samples=0), "--samples"),
        )
        for args, needle in cases:
            with self.subTest(needle=needle):
                with patch("dragon_quant.assets_sync.credentials.load_config",
                           side_effect=AssertionError("参数错误不得读凭据")), \
                     patch("dragon_quant.assets_sync.backends.s3.S3Backend",
                           side_effect=AssertionError("参数错误不得构造后端")):
                    outcome = cli_service.run("verify", args)
                self.assertEqual(outcome.exit_code, 2)
                self.assertIn(needle, outcome.payload["failed"][0]["reason"])


class TestTargetDirIsolation(unittest.TestCase):
    def test_pull_target_dir_only_assembles_target_sources_and_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "restore"
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
                outcome = cli_service.run("pull", _args(
                    "pull", from_date="2026-10-21", to_date="2026-10-21",
                    apply=True, target_dir=str(target)))

            self.assertEqual(outcome.exit_code, 0)
            self.assertTrue(target.exists())
            self.assertEqual(captured["db_path"], target / "assets.db")
            self.assertEqual(captured["paths"].lock_path, target / "assets_sync.lock")
            self.assertEqual(captured["paths"].tmp_dir, target / "tmp" / "assets_sync")
            self.assertFalse((main_data / "assets_sync" / "state.json").exists())
            self.assertFalse((main_data / "assets_sync.lock").exists())


class TestDoctor(unittest.TestCase):
    def test_samples_validation_precedes_backend(self):
        with patch("dragon_quant.assets_sync.doctor.run_doctor",
                   side_effect=AssertionError("不得执行 doctor")), \
             patch("dragon_quant.assets_sync.credentials.load_config",
                   side_effect=AssertionError("不得读凭据")):
            outcome = cli_service.run("doctor", _args("doctor", samples=0))

        self.assertEqual(outcome.exit_code, 2)
        self.assertEqual(
            outcome.payload["failed"][0]["reason"], "--samples 必须 >= 1")

    def test_doctor_reports_errors_and_cleanup_failures(self):
        summary = {"ok": False, "samples": 2, "errors": [{"op": "io"}],
                   "cleanup_errors": ["delete failed"]}
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   return_value=_config()), \
             patch("dragon_quant.assets_sync.doctor.run_doctor",
                   return_value=summary):
            outcome = cli_service.run("doctor", _args("doctor", samples=2))

        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(outcome.payload["failed"], [
            {"op": "io"}, {"op": "cleanup", "error": "delete failed"}])
        self.assertIs(outcome.payload["summary"], summary)

    def test_doctor_assets_sync_error_exits_2(self):
        with patch("dragon_quant.assets_sync.credentials.load_config",
                   return_value=_config()), \
             patch("dragon_quant.assets_sync.doctor.run_doctor",
                   side_effect=StorageError("backend down")):
            outcome = cli_service.run("doctor", _args("doctor", samples=2))

        self.assertEqual(outcome.exit_code, 2)
        self.assertIn("backend down", outcome.payload["failed"][0]["reason"])


class TestBackfill(unittest.TestCase):
    def test_backfill_is_local_and_never_builds_a_backend(self):
        result = {"ok": True, "mode": "backfill-dragons", "dates": [],
                  "failed": [], "inserted": 0}
        with patch("dragon_quant.storage.dragons_mirror.backfill_dragons",
                   return_value=result) as backfill, \
             patch("dragon_quant.assets_sync.credentials.load_config",
                   side_effect=AssertionError("回填不得读凭据")), \
             patch("dragon_quant.assets_sync.backends.s3.S3Backend",
                   side_effect=AssertionError("回填不得构造后端")):
            outcome = cli_service.run(
                "backfill-dragons",
                _args("backfill-dragons", from_date="20261020",
                      dry_run=True, prune=True),
                backend=object())

        self.assertIs(outcome.payload, result)
        self.assertEqual(outcome.exit_code, 0)
        backfill.assert_called_once_with(
            date_from="2026-10-20", date_to=None, dry_run=True, prune=True)

    def test_backfill_exit_codes_distinguish_fatal_and_partial(self):
        fatal = {"ok": False, "dates": [], "failed": [
            {"date": None, "reason": "assets.db 不可用"}]}
        partial = {"ok": False, "dates": [], "failed": [
            {"date": "2026-10-20", "reason": "row bad"}]}
        for result, expected in ((fatal, 2), (partial, 1)):
            with self.subTest(expected=expected), \
                 patch("dragon_quant.storage.dragons_mirror.backfill_dragons",
                       return_value=result):
                outcome = cli_service.run(
                    "backfill-dragons", _args("backfill-dragons"))
            self.assertEqual(outcome.exit_code, expected)

    def test_backfill_value_error_is_a_parameter_error(self):
        with patch("dragon_quant.storage.dragons_mirror.backfill_dragons",
                   side_effect=ValueError("--from 不能晚于 --to")):
            outcome = cli_service.run(
                "backfill-dragons",
                _args("backfill-dragons", from_date="20261022",
                      to_date="20261020"))
        self.assertEqual(outcome.exit_code, 2)
        self.assertEqual(outcome.payload, {
            "ok": False, "error": "--from 不能晚于 --to"})


class TestUnknownAction(unittest.TestCase):
    def test_unknown_action_keeps_the_group_error_contract(self):
        outcome = cli_service.run("bogus", _args("bogus"))
        self.assertEqual(outcome.exit_code, 2)
        self.assertEqual(outcome.payload, {
            "ok": False,
            "command": "assets",
            "failed": [{"reason": "未知 assets 子命令"}],
            "summary": {},
        })


class TestThinCommand(unittest.TestCase):
    def _run_handle(self, args, outcome):
        from dragon_quant.commands import assets as command

        out, err = io.StringIO(), io.StringIO()
        with patch("dragon_quant.assets_sync.cli_service.run",
                   return_value=outcome), \
             redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                command.handle(args, object())
        return raised.exception.code, out.getvalue(), err.getvalue()

    def test_sync_success_prints_human_summary_then_json(self):
        outcome = cli_service.AssetsOutcome(
            "assets push",
            {"ok": True, "command": "assets push", "failed": [],
             "summary": {"pushed": 2, "skipped": 0,
                         "blobs_uploaded": 3, "manifests_uploaded": 1}},
            0)
        code, out, err = self._run_handle(_args("push"), outcome)

        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])
        self.assertIn("推送：pushed=2 skipped=0 blobs=3 manifests=1", err)
        self.assertIn("assets push 完成：ok=True fatal=False", err)

    def test_sync_error_outcome_prints_only_the_failure_line_to_stderr(self):
        outcome = cli_service.AssetsOutcome(
            "assets pull",
            {"ok": False, "command": "assets pull",
             "failed": [{"reason": "boom"}], "summary": {}},
            1, completed=False)
        code, out, err = self._run_handle(_args("pull"), outcome)

        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])
        self.assertIn("assets pull 失败：boom", err)
        self.assertNotIn("恢复：", err)

    def test_completed_sync_partial_failure_still_renders_summary(self):
        outcome = cli_service.AssetsOutcome(
            "assets pull",
            {"ok": False, "command": "assets pull",
             "failed": [{"reason": "conflict"}],
             "summary": {"restored": 0, "skipped": 0, "conflicts": [{}],
                         "manifest_source": "remote"}},
            1)
        code, out, err = self._run_handle(_args("pull"), outcome)

        self.assertEqual(code, 1)
        self.assertIn("已判定冲突=1", err)
        self.assertIn("assets pull 失败：conflict", err)

    def test_period_filter_hint_follows_the_human_summary(self):
        outcome = cli_service.AssetsOutcome(
            "assets verify",
            {"ok": True, "command": "assets verify", "failed": [],
             "summary": {"checked_dates": 1, "period_excluded": [
                 {"partition": "fin-2025-12-31"}]}},
            0)
        code, _, err = self._run_handle(_args("verify"), outcome)

        self.assertEqual(code, 0)
        self.assertLess(err.index("校验：dates=1"), err.index("提示："))

    def test_doctor_and_unknown_actions_keep_stderr_empty(self):
        for action, payload, expected in (
            ("doctor", {"ok": True, "command": "assets doctor",
                        "failed": [], "summary": {"ok": True}}, 0),
            (None, {"ok": False, "command": "assets",
                    "failed": [{"reason": "未知 assets 子命令"}],
                    "summary": {}}, 2),
        ):
            with self.subTest(action=action):
                outcome = cli_service.AssetsOutcome(
                    payload["command"], payload, expected)
                code, out, err = self._run_handle(_args(action), outcome)
                self.assertEqual(code, expected)
                self.assertEqual(json.loads(out), payload)
                self.assertEqual(err, "")

    def test_backfill_human_and_json_channels_are_unchanged(self):
        result = {"ok": True, "mode": "backfill-dragons", "dry_run": True,
                  "dates": ["2026-10-20"],
                  "rows_source": 2, "inserted": 2, "updated": 0,
                  "unchanged": 0, "missing_rows": 0, "orphan_rows": 0,
                  "pruned": 0, "failed": [], "warnings": []}
        outcome = cli_service.AssetsOutcome("assets backfill-dragons", result, 0)

        code, out, err = self._run_handle(
            _args("backfill-dragons", dry_run=True), outcome)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("体检（dry-run）：dates=1", err)

        code, out, err = self._run_handle(
            _args("backfill-dragons", dry_run=True, json=True), outcome)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), result)
        self.assertEqual(err, "")

    def test_backfill_failure_in_human_mode_reports_the_reason(self):
        outcome = cli_service.AssetsOutcome(
            "assets backfill-dragons",
            {"ok": False, "error": "assets.db 不可用"}, 2)
        code, out, err = self._run_handle(_args("backfill-dragons"), outcome)

        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("❌ assets backfill-dragons 失败：assets.db 不可用", err)


if __name__ == "__main__":
    unittest.main()
