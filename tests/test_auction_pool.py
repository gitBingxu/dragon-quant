"""auction 候选池：prep 推荐优先 / 空推荐集回退 legacy / 空池正常结束。"""

import io
import json
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

DAY = "2026-10-20"
PREV = "2026-10-19"
ITEMS = [{"time": "09:25:00", "price": 10.0, "matched": 100, "unmatched": 0}]


class TestAuctionPool(unittest.TestCase):
    def _run(self, *, pool="prep", days=None, snapshot=None,
             snapshot_error=None, calendar=None, calendar_error=None,
             dragons=None, legacy_dates=None):
        # 先导入 cli：cli_commands 在模块底部反向引用 cli（既有循环规避设计），
        # 直接先导入 cli_commands 会在独立运行该测试文件时触发循环导入。
        from dragon_quant import cli  # noqa: F401
        from dragon_quant import cli_commands

        dragons = dragons or []
        legacy_dates = legacy_dates if legacy_dates is not None else (
            [DAY] if dragons else [])
        calendar = calendar if calendar is not None else [PREV, DAY]

        buf, err = io.StringIO(), io.StringIO()
        exit_code = None
        calls = {"auction": [], "quotes": 0, "snapshot_reads": 0}
        saved = []

        def fake_auction(code):
            calls["auction"].append(code)
            return list(ITEMS)

        def fake_batch(codes, source="tdx"):
            calls["quotes"] += 1
            return []

        def fake_snapshot(date, source="v2"):
            calls["snapshot_reads"] += 1
            if snapshot_error is not None:
                raise snapshot_error
            return snapshot

        def fake_calendar(tdx, days=120):
            if calendar_error is not None:
                raise calendar_error
            return list(calendar)

        with ExitStack() as stack:
            stack.enter_context(patch.object(
                cli_commands, "_resolve_trade_date", return_value=DAY))
            stack.enter_context(patch(
                "dragon_quant.utils.trading.tdx_trade_dates",
                side_effect=fake_calendar))
            stack.enter_context(patch(
                "dragon_quant.providers.tdx.TdxProvider", return_value=object()))
            stack.enter_context(patch(
                "dragon_quant.storage.assets_db.get_prep_snapshot",
                side_effect=fake_snapshot))
            stack.enter_context(patch(
                "dragon_quant.storage.db.list_dragon_trade_dates",
                return_value=legacy_dates))
            stack.enter_context(patch(
                "dragon_quant.storage.db.get_dragons", return_value=dragons))
            stack.enter_context(patch(
                "dragon_quant.storage.db.get_dragons_by_date",
                return_value=dragons))
            stack.enter_context(patch(
                "dragon_quant.data.get_auction", side_effect=fake_auction))
            stack.enter_context(patch(
                "dragon_quant.data.batch_get_quotes", side_effect=fake_batch))
            stack.enter_context(patch(
                "dragon_quant.storage.assets_db.save_auction_asset",
                side_effect=lambda *a, **k: saved.append(a[1] if len(a) > 1 else k.get("code"))))
            stack.enter_context(patch(
                "dragon_quant.storage.assets_db.list_auction_assets",
                return_value=[]))
            stack.enter_context(patch(
                "dragon_quant.storage.assets_db.replace_auction_snapshots",
                return_value=0))
            stack.enter_context(redirect_stdout(buf))
            stack.enter_context(redirect_stderr(err))
            try:
                cli_commands._cmd_auction(SimpleNamespace(pool=pool, days=days))
            except SystemExit as exc:
                exit_code = exc.code
        out = json.loads(buf.getvalue()) if buf.getvalue().strip() else None
        return out, err.getvalue(), exit_code, calls, saved

    def test_prep_pool_uses_recommended_codes(self):
        snapshot = {"candidates": [
            {"code": "A", "name": "甲", "composite_score": 80,
             "is_true_dragon": True, "verdict": "strong"},
            {"code": "B", "name": "乙", "composite_score": 70,
             "is_true_dragon": True, "verdict": "light"},
            {"code": "C", "name": "丙", "composite_score": 90,
             "is_true_dragon": True, "verdict": "ignore"},
        ]}
        out, err, code, calls, saved = self._run(snapshot=snapshot, dragons=[])
        self.assertIsNone(code)
        self.assertEqual(out["pool_source"], "prep")
        self.assertIsNone(out["fallback_reason"])
        self.assertEqual(out["prep_snapshot_date"], PREV)
        self.assertEqual(out["recommended_count"], 2)
        self.assertEqual(calls["auction"], ["A", "B"])
        self.assertEqual(saved, ["A", "B"])
        # 空 dragons_v2 不得阻止 prep 池（M5）
        self.assertNotIn("无 v2 龙头", err)

    def test_all_ignored_falls_back_and_does_not_refilter(self):
        snapshot = {"candidates": [
            {"code": "A", "name": "甲", "composite_score": 80,
             "is_true_dragon": True, "verdict": "ignore",
             "ignore_reason": "资金离场"},
        ]}
        dragons = [{"code": "A", "name": "甲"}]
        out, err, code, calls, saved = self._run(
            snapshot=snapshot, dragons=dragons)
        self.assertIsNone(code)
        self.assertEqual(out["pool_source"], "legacy")
        self.assertEqual(out["fallback_reason"], "all_ignored")
        self.assertEqual(calls["auction"], ["A"])   # 回退池未被 ignore 再滤
        self.assertEqual(saved, ["A"])

    def test_all_ignored_and_legacy_empty_ends_cleanly(self):
        snapshot = {"candidates": [
            {"code": "A", "composite_score": 80, "is_true_dragon": True,
             "verdict": "ignore", "ignore_reason": "资金离场"},
        ]}
        out, err, code, calls, saved = self._run(snapshot=snapshot, dragons=[])
        self.assertIsNone(code)
        self.assertTrue(out["pool_empty"])
        self.assertEqual(out["candidate_codes"], 0)
        self.assertEqual(calls["auction"], [])
        self.assertEqual(calls["quotes"], 0)
        self.assertEqual(saved, [])

    def test_no_snapshot_falls_back(self):
        out, err, code, calls, saved = self._run(
            snapshot=None, dragons=[{"code": "L", "name": "龙"}])
        self.assertIsNone(code)
        self.assertEqual(out["pool_source"], "legacy")
        self.assertEqual(out["fallback_reason"], "no_snapshot")
        self.assertEqual(calls["auction"], ["L"])

    def test_snapshot_read_error_falls_back(self):
        out, _, code, calls, _ = self._run(
            snapshot_error=RuntimeError("db down"),
            dragons=[{"code": "L", "name": "龙"}])
        self.assertIsNone(code)
        self.assertEqual(out["fallback_reason"], "snapshot_read_error")
        self.assertEqual(calls["auction"], ["L"])

    def test_calendar_failure_falls_back(self):
        out, err, code, calls, _ = self._run(
            calendar_error=RuntimeError("tdx down"),
            dragons=[{"code": "L", "name": "龙"}])
        self.assertIsNone(code)
        self.assertEqual(out["fallback_reason"], "calendar")
        self.assertEqual(calls["auction"], ["L"])
        self.assertIn("交易日历不可用", err)

    def test_explicit_legacy_skips_prep_snapshot(self):
        snapshot = {"candidates": [
            {"code": "A", "composite_score": 80, "is_true_dragon": True,
             "verdict": "strong"}]}
        out, _, code, calls, _ = self._run(
            pool="legacy", snapshot=snapshot,
            dragons=[{"code": "L", "name": "龙"}])
        self.assertIsNone(code)
        self.assertEqual(out["pool_source"], "legacy")
        self.assertEqual(calls["snapshot_reads"], 0)
        self.assertEqual(calls["auction"], ["L"])

    def test_legacy_without_dragons_exits_nonzero(self):
        _, err, code, calls, _ = self._run(pool="legacy", dragons=[])
        self.assertEqual(code, 1)
        self.assertEqual(calls["auction"], [])

    def test_days_ignored_when_prep_pool_succeeds(self):
        snapshot = {"candidates": [
            {"code": "A", "composite_score": 80, "is_true_dragon": True,
             "verdict": "strong"}]}
        out, err, code, _, _ = self._run(snapshot=snapshot, days=5)
        self.assertIsNone(code)
        self.assertTrue(out["days_ignored"])
        self.assertIn("--days 仅在 legacy", err)

    def test_days_applies_on_fallback(self):
        snapshot = {"candidates": [
            {"code": "A", "composite_score": 80, "is_true_dragon": True,
             "verdict": "ignore"}]}
        dragons = [{"code": "L", "name": "龙"}]
        out, err, code, calls, _ = self._run(
            snapshot=snapshot, dragons=dragons, days=1,
            legacy_dates=[DAY, PREV])
        self.assertIsNone(code)
        self.assertEqual(out["fallback_reason"], "all_ignored")
        self.assertFalse(out["days_ignored"])
        self.assertEqual(out["lookback_dates"], [PREV])

    def test_prep_snapshot_date_matches_collect_candidates_ex(self):
        """N1：auction 与 buy 的池解析必须命中同一 T-1 快照。"""
        from dragon_quant.review_account.market import collect_candidates_ex
        from dragon_quant.review_account.models import StrategyConfig

        snapshot = {"candidates": [
            {"code": "A", "composite_score": 80, "is_true_dragon": True,
             "verdict": "strong"}]}
        out, _, _, _, _ = self._run(snapshot=snapshot)
        with patch("dragon_quant.storage.assets_db.get_prep_snapshot",
                   return_value=snapshot):
            _, ctx = collect_candidates_ex(
                [PREV, DAY], DAY, StrategyConfig(), lambda *a, **k: [])
        self.assertEqual(out["prep_snapshot_date"], PREV)
        self.assertEqual(ctx["prep_snapshot_date"], out["prep_snapshot_date"])


if __name__ == "__main__":
    unittest.main()
