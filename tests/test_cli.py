"""tests for dragon_quant.cli — 命令行入口。"""
import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from dragon_quant._version import __version__
from dragon_quant import cli


class TestCliHelp(unittest.TestCase):

    def test_top_level_short_help(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "-h"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()

        output = buf.getvalue()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("Usage:", output)
        self.assertIn("Commands:", output)
        self.assertIn("Examples:", output)
        self.assertIn("scan", output)
        self.assertNotIn("scan_v2", output)
        self.assertIn("Use \"dragon-quant <command> -h\"", output)

    def test_buy_help_mentions_force(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "buy", "-h"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("--force", buf.getvalue())
        self.assertIn("buy --force", buf.getvalue())

    def test_sell_help_mentions_force(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "sell", "-h"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("--force", buf.getvalue())
        self.assertIn("sell --force", buf.getvalue())

    def test_scan_help_does_not_run_scan(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "scan", "-h"]), \
             patch("dragon_quant.cli.orchestrate_scan") as mock_scan:
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()

        output = buf.getvalue()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("Usage: dragon-quant scan [options]", output)
        self.assertIn("--top TOP", output)
        self.assertIn("真龙数量", output)
        self.assertIn("--force", output)
        self.assertIn("--no-cache", output)
        mock_scan.assert_not_called()

    def test_review_help_includes_source_and_ui_options(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "review", "-h"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()

        output = buf.getvalue()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("Usage: dragon-quant review [options]", output)
        self.assertIn("--source {v1,v2}", output)
        self.assertIn("--ui-only", output)

    def test_review_account_help(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "review-account", "-h"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()

        output = buf.getvalue()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("Usage: dragon-quant review-account [options]", output)
        self.assertIn("--from DATE_FROM", output)
        self.assertIn("--capital CAPITAL", output)

    def test_data_kline_help_includes_required_options(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "data", "kline", "-h"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()

        output = buf.getvalue()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("Usage: dragon-quant data kline --code CODE [options]", output)
        self.assertIn("--code CODE", output)
        self.assertIn("--source {xueqiu,tencent}", output)
        self.assertIn("--days DAYS", output)


class TestCliVersion(unittest.TestCase):

    def test_short_version_option(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "-v"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()

        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), f"dragon-quant {__version__}")


class TestCliSourceArgs(unittest.TestCase):

    def test_review_source_passes_to_run_review(self):
        with patch("sys.argv", ["dragon-quant", "review", "--source", "v2", "--date", "20260519"]), \
             patch("dragon_quant.review.run_review") as mock_run:
            cli.main()

        mock_run.assert_called_once_with(
            trade_date="2026-05-19", top_n=None, force=False, verbose=True, source="v2"
        )

    def test_review_ui_only_source_passes_to_server(self):
        with patch("sys.argv", ["dragon-quant", "review", "--ui-only", "--source", "v2", "--no-browser"]), \
             patch("web_ui.server.start_server") as mock_start:
            cli.main()

        mock_start.assert_called_once_with(port=8765, open_browser=False, default_source="v2")

    def test_review_account_passes_to_service(self):
        with patch("sys.argv", [
            "dragon-quant", "review-account", "--from", "20260501", "--to", "20260601",
            "--capital", "200000", "--source", "v2",
        ]), patch("dragon_quant.review_account.run_review_account") as mock_run:
            cli.main()

        mock_run.assert_called_once_with(
            date_from="2026-05-01",
            date_to="2026-06-01",
            initial_cash=200000.0,
            source="v2",
            strategy_name="dragon_pullback_daily",
            verbose=True,
        )

    def test_review_account_ui_only_uses_account_page(self):
        with patch("sys.argv", [
            "dragon-quant", "review-account", "--ui-only", "--source", "v2", "--no-browser",
        ]), patch("web_ui.server.start_server") as mock_start:
            cli.main()

        mock_start.assert_called_once_with(
            port=8765, open_browser=False, default_source="v2", default_page="account"
        )

    def test_buy_passes_replay_time(self):
        with patch("sys.argv", ["dragon-quant", "buy", "--date", "20260907", "--at", "10:00"]), \
             patch("dragon_quant.live_trade.run_buy") as run:
            cli.main()
        self.assertEqual(run.call_args.kwargs["as_of"], "10:00")
        self.assertNotIn("account_name", run.call_args.kwargs)
        self.assertNotIn("capital", run.call_args.kwargs)

    def test_buy_passes_force_kwarg(self):
        with patch("sys.argv", ["dragon-quant", "buy", "--force"]), \
             patch("dragon_quant.live_trade.run_buy") as run:
            cli.main()
        self.assertTrue(run.call_args.kwargs["force"])

    def test_buy_without_force_passes_false(self):
        with patch("sys.argv", ["dragon-quant", "buy"]), \
             patch("dragon_quant.live_trade.run_buy") as run:
            cli.main()
        self.assertFalse(run.call_args.kwargs["force"])

    def test_sell_passes_force_kwarg(self):
        with patch("sys.argv", ["dragon-quant", "sell", "--force"]), \
             patch("dragon_quant.live_trade.run_sell") as run:
            cli.main()
        self.assertTrue(run.call_args.kwargs["force"])

    def test_review_account_passes_shared_config(self):
        with patch("sys.argv", ["dragon-quant", "review-account", "--from", "20260907", "--to", "20260911", "--config", "params.json"]), \
             patch("dragon_quant.cli._strategy_params", return_value={"max_position_pct": 25}), \
             patch("dragon_quant.review_account.run_review_account") as run:
            cli.main()
        self.assertEqual(run.call_args.kwargs["strategy_params"], {"max_position_pct": 25})

    def test_buy_session_error_prints_message_and_exits(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "buy"]), \
             patch.object(cli, "warn_if_unavailable"), \
             patch("dragon_quant.live_trade.run_buy", side_effect=ValueError("当前午间休市（11:30–13:00），请 13:00 后执行")), \
             redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                cli.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("午间休市", buf.getvalue())
        self.assertNotIn("Traceback", buf.getvalue())

    def test_sell_session_error_prints_message_and_exits(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "sell"]), \
             patch.object(cli, "warn_if_unavailable"), \
             patch("dragon_quant.live_trade.run_sell", side_effect=ValueError("当前已收盘")), \
             redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                cli.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("已收盘", buf.getvalue())

    def test_scan_history_uses_v2_source(self):
        scan = {
            "id": "v2_20260519_5",
            "raw_output": '{"ranking": [], "source": "v2"}',
        }
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "scan", "--date", "20260519", "--top", "5"]), \
             patch("dragon_quant.storage.db.get_latest_scan_by_date", return_value=scan) as mock_get, \
             redirect_stdout(buf):
            cli.main()

        mock_get.assert_called_once_with("2026-05-19", 5, source="v2")
        self.assertEqual(json.loads(buf.getvalue()), {"ranking": [], "source": "v2"})

    def test_scan_history_empty_raw_output_returns_error_without_rebuild(self):
        scan = {
            "id": "v2_20260519_5",
            "scan_date": "2026-05-19",
            "top_n": 5,
            "raw_output": "",
        }
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "scan", "--date", "20260519", "--top", "5"]), \
             patch("dragon_quant.storage.db.get_latest_scan_by_date", return_value=scan), \
             patch("dragon_quant.storage.db.get_scan_stocks") as mock_get_stocks, \
             redirect_stdout(buf):
            cli.main()

        self.assertEqual(json.loads(buf.getvalue()), {
            "error": "scan raw_output is empty",
            "scan_id": "v2_20260519_5",
            "scan_date": "2026-05-19",
            "top_n": 5,
            "source": "v2",
        })
        mock_get_stocks.assert_not_called()

    def test_scan_v2_alias_still_uses_v2_history(self):
        scan = {
            "id": "v2_20260519_5",
            "raw_output": '{"ranking": [], "source": "v2"}',
        }
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "scan_v2", "--date", "20260519", "--top", "5"]), \
             patch("dragon_quant.storage.db.get_latest_scan_by_date", return_value=scan) as mock_get, \
             redirect_stdout(buf):
            cli.main()

        mock_get.assert_called_once_with("2026-05-19", 5, source="v2")
        self.assertEqual(json.loads(buf.getvalue()), {"ranking": [], "source": "v2"})

    def test_long_version_option(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "--version"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()

        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), f"dragon-quant {__version__}")


class TestCliDragons(unittest.TestCase):

    def test_dragons_help(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "dragons", "-h"]):
            with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
                cli.main()

        output = buf.getvalue()
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("Usage: dragon-quant dragons [--date YYYYMMDD] [--true-only]", output)
        self.assertIn("--true-only", output)
        self.assertNotIn("--source", output)

    def test_dragons_default_uses_latest_date(self):
        dragon = {
            "code": "600172", "name": "黄河旋风", "rank": 1,
            "composite_score": 92.5, "is_true_dragon": True,
            "concepts": ["超硬材料"], "report_text": "x",
        }
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "dragons"]), \
             patch("dragon_quant.storage.db.list_dragon_trade_dates",
                   return_value=["2026-05-18", "2026-05-19"]) as mock_dates, \
             patch("dragon_quant.storage.db.get_dragons", return_value=[dragon]) as mock_get, \
             redirect_stdout(buf):
            cli.main()

        mock_dates.assert_called_once_with(source="v2")
        mock_get.assert_called_once_with("2026-05-19", source="v2")
        out = json.loads(buf.getvalue())
        self.assertEqual(out["trade_date"], "2026-05-19")
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["dragons"][0]["code"], "600172")

    def test_dragons_true_only_filters(self):
        true_d = {"code": "600172", "name": "A", "is_true_dragon": True, "report_text": ""}
        false_d = {"code": "000001", "name": "B", "is_true_dragon": False, "report_text": ""}
        none_d = {"code": "600519", "name": "C", "is_true_dragon": None, "report_text": ""}
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "dragons", "--date", "20260519", "--true-only"]), \
             patch("dragon_quant.storage.db.get_dragons",
                   return_value=[true_d, false_d, none_d]) as mock_get, \
             redirect_stdout(buf):
            cli.main()

        mock_get.assert_called_once_with("2026-05-19", source="v2")
        out = json.loads(buf.getvalue())
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["dragons"][0]["code"], "600172")

    def test_dragons_date_supports_slash_format(self):
        dragon = {"code": "600172", "name": "A", "is_true_dragon": True, "report_text": ""}
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "dragons", "--date", "2026/05/19"]), \
             patch("dragon_quant.storage.db.get_dragons", return_value=[dragon]) as mock_get, \
             redirect_stdout(buf):
            cli.main()

        mock_get.assert_called_once_with("2026-05-19", source="v2")

    def test_dragons_no_data_exits_nonzero(self):
        buf = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "dragons"]), \
             patch("dragon_quant.storage.db.list_dragon_trade_dates", return_value=[]), \
             redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                cli.main()

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("error", json.loads(buf.getvalue()))


def _auction_quote(code, prev_close=10.0):
    from dragon_quant.models.types import Quote
    return Quote(code=code, name="x", price=10.0, prev_close=prev_close,
                 open_px=10.0, high=10.0, low=10.0, pct=0.0, chg=0.0,
                 turnover_rate=0.0, amplitude=0.0, volume=0.0, amount=0.0,
                 market_cap=100e8, float_market_cap=50e8,
                 volume_ratio=0.0, pe=0.0, limit_up=11.0, limit_down=9.0,
                 avg_price=10.0)


def _quote_payload(code):
    from dragon_quant.auction.serialize import quote_to_payload
    return quote_to_payload(_auction_quote(code))


class TestCmdAuctionStorage(unittest.TestCase):
    """auction 采集：原始资产 + 行情 + 快照，且不得回落 dragon.db。"""

    def _run(self, dragons, save_side_effect=None, quotes=None,
             batch_side_effect=None, replace_side_effect=None,
             evaluate_side_effect=None, seed_assets=None,
             auction_items=None, list_side_effect=None,
             quote_serialize_side_effect=None, as_json=True):
        from contextlib import ExitStack
        from types import SimpleNamespace
        from dragon_quant import cli_commands
        from dragon_quant.storage import db, assets_db

        if auction_items is None:
            auction_items = [{"time": "09:25:00", "price": 10.0,
                              "matched": 100, "unmatched": 0}]
        if quotes is None:
            quotes = [_auction_quote(d["code"]) for d in dragons]
        if replace_side_effect is None:
            replace_side_effect = lambda trade_date, payloads, source="v2": len(payloads)

        batch_kwargs = ({"side_effect": batch_side_effect}
                        if batch_side_effect is not None else {"return_value": quotes})
        buf, err = io.StringIO(), io.StringIO()
        exit_code = None

        # 模拟 auction_assets 持久化：采集后读回该日全部已存资产再评分。
        store = {a["code"]: a for a in (seed_assets or [])}

        def _persist(trade_date, code, name, items, quote=None):
            store[code] = {"trade_date": trade_date, "code": code, "name": name,
                           "items": items, "quote": quote}

        if isinstance(save_side_effect, list):
            effects = list(save_side_effect)

            def _save(trade_date, code, name, items, quote=None):
                effect = effects.pop(0) if effects else None
                if isinstance(effect, Exception):
                    raise effect
                _persist(trade_date, code, name, items, quote=quote)
        else:
            _save = _persist

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(db, "list_dragon_trade_dates", return_value=["2026-10-20"]))
            stack.enter_context(patch.object(db, "get_dragons", return_value=dragons))
            stack.enter_context(
                patch.object(cli_commands, "_resolve_trade_date",
                             return_value="2026-10-20"))
            stack.enter_context(
                patch("dragon_quant.data.get_auction", return_value=auction_items))
            get_quote = stack.enter_context(patch("dragon_quant.data.get_quote"))
            batch = stack.enter_context(
                patch("dragon_quant.data.batch_get_quotes", **batch_kwargs))
            if evaluate_side_effect is not None:
                stack.enter_context(
                    patch("dragon_quant.auction.evaluate",
                          side_effect=evaluate_side_effect))
            if quote_serialize_side_effect is not None:
                stack.enter_context(
                    patch("dragon_quant.auction.serialize.quote_to_payload",
                          side_effect=quote_serialize_side_effect))
            save = stack.enter_context(
                patch.object(assets_db, "save_auction_asset", side_effect=_save))
            if list_side_effect is not None:
                stack.enter_context(
                    patch.object(assets_db, "list_auction_assets",
                                 side_effect=list_side_effect))
            else:
                stack.enter_context(
                    patch.object(assets_db, "list_auction_assets",
                                 side_effect=lambda date: [store[c] for c in sorted(store)]))
            replace = stack.enter_context(
                patch.object(assets_db, "replace_auction_snapshots",
                             side_effect=replace_side_effect))
            stack.enter_context(redirect_stdout(buf))
            stack.enter_context(redirect_stderr(err))
            try:
                # 固定 legacy 池：用例只验证采集/落库契约，不依赖本地 assets.db 的
                # prep 快照状态（prep 池行为由 tests/test_auction_pool.py 覆盖）。
                cli_commands._cmd_auction(
                    SimpleNamespace(days=3, pool="legacy", json=as_json))
            except SystemExit as exc:
                exit_code = exc.code
        raw = buf.getvalue()
        out = json.loads(raw) if as_json and raw.strip() else raw
        return (save, replace, batch, get_quote, out, err.getvalue(), exit_code)

    def test_collect_writes_assets_and_contract_snapshot(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        save, replace, batch, get_quote, out, _, _code = self._run([dragon])

        save.assert_called_once()
        self.assertEqual(save.call_args.args[1], "600172")
        self.assertEqual(save.call_args.kwargs["quote"]["prev_close"], 10.0)
        replace.assert_called_once()
        payloads = replace.call_args.args[1]
        payload = payloads["600172"]
        self.assertEqual(set(payload), {
            "version", "code", "is_weak", "strength", "direction", "level",
            "dims", "reject_reason", "unverified", "rank",
        })
        self.assertEqual(payload["version"], "1")
        self.assertIsNone(payload["rank"])
        self.assertTrue(payload["unverified"])

        self.assertEqual(out["saved_count"], 1)
        self.assertEqual(out["scored_count"], 1)
        self.assertEqual(out["snapshot_saved_count"], 1)
        self.assertTrue(out["snapshot_ok"])
        self.assertEqual(out["failed"], [])
        self.assertEqual(out["quote"]["source"], "tdx")
        self.assertFalse(out["quote"]["degraded"])
        batch.assert_called_once()
        self.assertEqual(batch.call_args.kwargs["source"], "tdx")
        get_quote.assert_not_called()   # 禁止逐股请求

        from dragon_quant.storage import db
        for name in ("save_auction_snapshot", "get_auction_snapshot",
                     "list_auction_snapshots"):
            self.assertFalse(hasattr(db, name), f"db.{name} 应已移除")

    def test_default_human_output(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        _, _, _, _, out, err, code = self._run([dragon], as_json=False)
        self.assertIsNone(code)
        self.assertIn("集合竞价强度", out)
        self.assertIn("池来源 legacy", out)
        self.assertIn("600172 黄河旋风", out)
        self.assertIn("汇总：", out)
        with self.assertRaises(ValueError):
            json.loads(out)

    def test_collect_json_top_level_key_order(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        _, _, _, _, out, _, code = self._run([dragon])
        self.assertIsNone(code)
        self.assertEqual(list(out), [
            "ok", "mode", "trade_date", "source", "pool_source",
            "fallback_reason", "prep_snapshot_date", "prep_unavailable_reason",
            "recommended_count", "days_ignored", "lookback_dates",
            "candidate_codes", "saved_count", "saved", "empty", "failed",
            "quote", "quote_missing", "asset_read_error", "scored_count",
            "snapshot_saved_count", "snapshot_ok", "snapshot_skipped",
            "snapshot_error", "snapshots",
        ])

    def test_human_names_from_previously_persisted_assets(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        old_asset = {
            "trade_date": "2026-10-20", "code": "600009", "name": "早前批次",
            "items": [{"time": "09:25:00", "price": 10.0,
                       "matched": 100, "unmatched": 0}],
            "quote": _quote_payload("600009"),
        }
        _, _, _, _, out, _, code = self._run(
            [dragon], seed_assets=[old_asset], as_json=False)
        self.assertIsNone(code)
        self.assertIn("600172 黄河旋风", out)
        self.assertIn("600009 早前批次", out)

    def test_human_all_empty_auction_data(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        _, _, _, _, out, err, code = self._run(
            [dragon], auction_items=[], as_json=False)
        self.assertIsNone(code)
        self.assertIn("候选 1 只均无竞价盘口", out)
        self.assertNotIn("失败 0 条", out)
        self.assertNotIn("❌", err)

    def test_human_collect_layer_all_failed_exits_nonzero(self):
        _, _, _, _, out, err, code = self._run(
            [{"code": "600001", "name": "A"}],
            save_side_effect=[RuntimeError("disk full")], as_json=False)
        self.assertEqual(code, 1)
        self.assertIn("候选 1 只均未产出竞价强度判定", out)
        self.assertNotIn("无参与", out)
        self.assertIn("❌ 集合竞价失败", err)

    def test_human_scoring_all_failed_exits_zero(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        _, _, _, _, out, err, code = self._run(
            [dragon], evaluate_side_effect=RuntimeError("model bug"),
            as_json=False)
        self.assertIsNone(code)
        self.assertIn("原始资产已写入", out)
        self.assertNotIn("❌", err)

    def test_tdx_empty_falls_back_to_tencent_batch(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        _, _, batch, _, out, _, _code = self._run(
            [dragon], batch_side_effect=[[], [_auction_quote("600172")]])
        self.assertEqual(batch.call_count, 2)
        self.assertEqual(batch.call_args_list[0].kwargs["source"], "tdx")
        self.assertEqual(batch.call_args_list[1].kwargs["source"], "tencent")
        self.assertTrue(out["quote"]["fallback_used"])
        self.assertEqual(out["quote"]["source"], "tencent")
        self.assertEqual(out["quote"]["matched"], 1)
        self.assertFalse(out["quote"]["degraded"])

    def test_quotes_unavailable_degrades_but_still_scores(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        save, replace, _, _, out, _, _code = self._run(
            [dragon], batch_side_effect=[[], []])
        save.assert_called_once()
        self.assertIsNone(save.call_args.kwargs["quote"])
        replace.assert_called_once()
        self.assertTrue(out["ok"])       # 资产与快照都成功；行情降级不算失败
        self.assertTrue(out["quote"]["degraded"])
        self.assertEqual(out["quote"]["reason"], "empty")
        self.assertEqual(out["quote"]["source"], "none")
        self.assertEqual(out["saved_count"], 1)
        self.assertEqual(out["snapshot_saved_count"], 1)

    def test_scores_previously_persisted_same_day_assets(self):
        """同日早前批次的资产也参与评分，保证两表 code 集合一致（C4）。"""
        dragon = {"code": "600172", "name": "黄河旋风"}
        old_asset = {
            "trade_date": "2026-10-20", "code": "600009", "name": "old",
            "items": [{"time": "09:25:00", "price": 10.0,
                       "matched": 100, "unmatched": 0}],
            "quote": _quote_payload("600009"),
        }
        _, replace, _, _, out, _, code = self._run(
            [dragon], seed_assets=[old_asset])
        self.assertIsNone(code)
        self.assertEqual(set(replace.call_args.args[1]), {"600172", "600009"})
        self.assertEqual(out["scored_count"], 2)

    def test_partial_quotes_degraded(self):
        dragons = [{"code": "600001", "name": "A"},
                   {"code": "600002", "name": "B"}]
        _, replace, batch, _, out, _, code = self._run(
            dragons, quotes=[_auction_quote("600001")])
        self.assertIsNone(code)
        self.assertEqual(out["quote"]["matched"], 1)
        self.assertEqual(out["quote"]["missing"], ["600002"])
        self.assertTrue(out["quote"]["degraded"])
        self.assertEqual(out["quote"]["reason"], "partial")
        self.assertFalse(out["quote"]["fallback_used"])
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(out["quote_missing"], ["600002"])
        self.assertEqual(len(replace.call_args.args[1]), 2)

    def test_all_empty_assets_is_clean_no_op(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        _, replace, _, _, out, _, code = self._run(
            [dragon], auction_items=[])
        self.assertIsNone(code)
        self.assertEqual(out["saved_count"], 0)
        self.assertEqual(out["empty"], ["600172"])
        self.assertEqual(out["failed"], [])
        self.assertEqual(out["snapshot_skipped"], "no_payloads")
        self.assertTrue(out["snapshot_ok"])
        self.assertTrue(out["ok"])
        replace.assert_not_called()

    def test_quote_serialize_failure_isolated(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        _, replace, _, _, out, _, code = self._run(
            [dragon], quote_serialize_side_effect=RuntimeError("bad quote"))
        self.assertIsNone(code)
        self.assertEqual(out["saved_count"], 1)
        self.assertEqual(out["failed"][0]["stage"], "quote")
        self.assertIn("bad quote", out["failed"][0]["error"])
        self.assertEqual(out["quote_missing"], ["600172"])
        self.assertEqual(out["snapshot_saved_count"], 1)
        self.assertFalse(out["ok"])
        replace.assert_called_once()

    def test_asset_read_error_falls_back_to_run_assets(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        _, replace, _, _, out, _, code = self._run(
            [dragon], list_side_effect=RuntimeError("read boom"))
        self.assertIsNone(code)
        self.assertIn("read boom", out["asset_read_error"])
        self.assertEqual(out["failed"][0]["stage"], "asset_read")
        self.assertEqual(out["snapshot_saved_count"], 1)
        self.assertEqual(set(replace.call_args.args[1]), {"600172"})
        self.assertFalse(out["ok"])

    def test_quote_code_normalized_with_prefix(self):
        dragon = {"code": "600001", "name": "A"}
        _, _, _, _, out, _, _code = self._run(
            [dragon], quotes=[_auction_quote("SH600001")])
        self.assertEqual(out["quote"]["matched"], 1)
        self.assertEqual(out["quote"]["missing"], [])

    def test_single_code_failure_isolated(self):
        dragons = [{"code": "600001", "name": "A"},
                   {"code": "600002", "name": "B"}]
        save, replace, _, _, out, _, _code = self._run(
            dragons, save_side_effect=[RuntimeError("boom"), None])
        self.assertEqual(save.call_count, 2)
        self.assertEqual(out["saved_count"], 1)
        self.assertFalse(out["ok"])
        self.assertEqual(len(out["failed"]), 1)
        self.assertEqual(out["failed"][0]["code"], "600001")
        self.assertEqual(out["failed"][0]["stage"], "asset")
        self.assertIn("boom", out["failed"][0]["error"])
        # 失败 code 不参与评分，快照集合只含成功资产
        self.assertEqual(set(replace.call_args.args[1]), {"600002"})

    def test_all_failed_exits_nonzero_and_skips_replace(self):
        """全部写入失败通常是系统性故障，必须让定时任务感知。"""
        _, replace, _, _, out, err, code = self._run(
            [{"code": "600001", "name": "A"}],
            save_side_effect=[RuntimeError("disk full")])
        self.assertEqual(code, 1)
        replace.assert_not_called()
        self.assertEqual(out["snapshot_skipped"], "no_payloads")
        self.assertFalse(out["snapshot_ok"])
        self.assertNotIn("❌", err)  # JSON 模式只输出 JSON，不额外打人读错误行

    def test_snapshot_write_failure_reported_without_exit(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        _, _, _, _, out, err, code = self._run(
            [dragon], replace_side_effect=RuntimeError("locked"))
        self.assertIsNone(code)
        self.assertFalse(out["ok"])
        self.assertIn("locked", out["snapshot_error"])
        self.assertFalse(out["snapshot_ok"])
        self.assertIn("快照未产出", err)

    def test_all_scoring_failed_skips_replace_and_warns(self):
        dragon = {"code": "600172", "name": "黄河旋风"}
        save, replace, _, _, out, err, code = self._run(
            [dragon], evaluate_side_effect=RuntimeError("model bug"))
        self.assertIsNone(code)
        save.assert_called_once()
        replace.assert_not_called()
        self.assertEqual(out["saved_count"], 1)
        self.assertEqual(out["scored_count"], 0)
        self.assertEqual(out["snapshot_skipped"], "no_payloads")
        self.assertFalse(out["snapshot_ok"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["failed"][0]["stage"], "score")
        self.assertIn("快照未产出", err)


class TestCmdAuctionRecompute(unittest.TestCase):
    """--recompute：不触网、用已存原始资产重算并覆盖快照。"""

    def _asset(self, code, items=True, quote="default"):
        if quote == "default":
            quote = _quote_payload(code)
        return {
            "trade_date": "2026-10-20",
            "code": code,
            "name": code,
            "items": ([{"time": "09:25:00", "price": 10.0,
                        "matched": 100, "unmatched": 0}] if items else []),
            "quote": quote,
        }

    def _run(self, raw_date, assets, replace_side_effect=None,
             today="2026-10-20", as_json=True, list_side_effect=None):
        from types import SimpleNamespace
        from dragon_quant import cli_commands
        from dragon_quant.storage import assets_db

        if replace_side_effect is None:
            replace_side_effect = lambda trade_date, payloads, source="v2": len(payloads)
        buf, err = io.StringIO(), io.StringIO()
        exit_code = None
        list_kwargs = ({"side_effect": list_side_effect}
                       if list_side_effect is not None else {"return_value": assets})
        with patch.object(assets_db, "list_auction_assets", **list_kwargs), \
             patch.object(assets_db, "replace_auction_snapshots",
                          side_effect=replace_side_effect) as replace, \
             patch.object(cli_commands, "_resolve_trade_date", return_value=today), \
             patch("dragon_quant.data.get_auction") as get_auction, \
             patch("dragon_quant.data.batch_get_quotes") as batch, \
             redirect_stdout(buf), redirect_stderr(err):
            try:
                cli_commands._cmd_auction(
                    SimpleNamespace(days=3, recompute=raw_date, json=as_json))
            except SystemExit as exc:
                exit_code = exc.code
        raw = buf.getvalue()
        out = json.loads(raw) if as_json and raw.strip() else raw
        return (replace, get_auction, batch, out, err.getvalue(), exit_code)

    def test_recompute_default_human_output(self):
        assets = [self._asset("600001"), self._asset("600002")]
        _, _, _, out, err, code = self._run(
            "20261020", assets, as_json=False)
        self.assertIsNone(code)
        self.assertIn("集合竞价强度（重算", out)
        self.assertIn("600001", out)
        self.assertIn("汇总：", out)
        self.assertNotIn("❌", err)
        with self.assertRaises(ValueError):
            json.loads(out)

    def test_recompute_json_top_level_key_order(self):
        _, _, _, out, _, code = self._run("20261020", [self._asset("600001")])
        self.assertIsNone(code)
        self.assertEqual(list(out), [
            "ok", "mode", "trade_date", "source", "asset_count",
            "usable_count", "skipped_empty", "quote_missing", "scored_count",
            "snapshot_saved_count", "snapshot_ok", "snapshot_skipped",
            "snapshot_error", "failed", "snapshots",
        ])

    def test_invalid_date_human_error(self):
        _, _, _, out, err, code = self._run(
            "2026-13-40", [self._asset("600001")], as_json=False)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("❌ 集合竞价失败：无效日期", err)

    def test_read_assets_failure_human_error(self):
        _, _, _, out, err, code = self._run(
            "20261020", [], list_side_effect=RuntimeError("db down"),
            as_json=False)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("❌ 集合竞价失败：读取竞价原始资产失败", err)
        self.assertIn("db down", err)

    def test_invalid_date_json_top_level_key_order(self):
        _, _, _, out, _, code = self._run("2026-13-40", [self._asset("600001")])
        self.assertEqual(code, 1)
        self.assertEqual(list(out), ["ok", "mode", "trade_date", "error"])

    def test_human_format_failure_keeps_exit_zero(self):
        with patch("dragon_quant.auction.report.format_auction_report",
                   side_effect=RuntimeError("format boom")):
            _, _, _, out, err, code = self._run(
                "20261020", [self._asset("600001")], as_json=False)
        self.assertIsNone(code)
        self.assertEqual(out, "")
        self.assertIn("人读摘要生成失败", err)
        self.assertIn("format boom", err)

    def test_recompute_writes_snapshots_without_network(self):
        assets = [self._asset("600001"), self._asset("600002")]
        replace, get_auction, batch, out, _, code = self._run("20261020", assets)
        self.assertIsNone(code)
        replace.assert_called_once()
        payloads = replace.call_args.args[1]
        self.assertEqual(set(payloads), {"600001", "600002"})
        self.assertEqual(out["mode"], "recompute")
        self.assertEqual(out["trade_date"], "2026-10-20")
        self.assertEqual(out["snapshot_saved_count"], 2)
        self.assertEqual(out["quote_missing"], [])
        get_auction.assert_not_called()
        batch.assert_not_called()

    def test_recompute_accepts_slash_date(self):
        _, _, _, out, _, code = self._run(
            "2026/10/20", [self._asset("600001")])
        self.assertIsNone(code)
        self.assertEqual(out["trade_date"], "2026-10-20")

    def test_invalid_date_exits_nonzero(self):
        _, _, _, out, _, code = self._run("2026-13-40", [self._asset("600001")])
        self.assertEqual(code, 1)
        self.assertIn("无效日期", out["error"])

    def test_future_date_rejected(self):
        _, _, _, out, _, code = self._run(
            "20990101", [self._asset("600001")], today="2026-10-20")
        self.assertEqual(code, 1)
        self.assertIn("未来日期", out["error"])

    def test_no_assets_exits_nonzero(self):
        replace, _, _, out, _, code = self._run("20261020", [])
        self.assertEqual(code, 1)
        self.assertIn("无竞价原始资产", out["error"])
        replace.assert_not_called()

    def test_all_empty_items_exits_nonzero(self):
        replace, _, _, out, _, code = self._run(
            "20261020", [self._asset("600001", items=False)])
        self.assertEqual(code, 1)
        self.assertEqual(out["skipped_empty"], ["600001"])
        replace.assert_not_called()

    def test_corrupt_quote_isolated_and_not_reported_missing(self):
        assets = [self._asset("600001", quote={"code": "600001"}),
                  self._asset("600002")]
        replace, _, _, out, _, code = self._run("20261020", assets)
        self.assertIsNone(code)
        self.assertEqual(out["quote_missing"], [])
        self.assertEqual(len(out["failed"]), 1)
        self.assertEqual(out["failed"][0]["code"], "600001")
        self.assertEqual(out["failed"][0]["stage"], "quote")
        self.assertEqual(set(replace.call_args.args[1]), {"600002"})

    def test_null_quote_reported_missing_but_still_scored(self):
        assets = [self._asset("600001", quote=None)]
        replace, _, _, out, _, code = self._run("20261020", assets)
        self.assertIsNone(code)
        self.assertEqual(out["quote_missing"], ["600001"])
        replace.assert_called_once()
        self.assertTrue(out["snapshots"][0]["is_weak"])

    def test_corrupt_quote_error_isolated(self):
        corrupt = self._asset("600001", quote=None)
        corrupt["quote_error"] = "JSONDecodeError: bad"
        assets = [corrupt, self._asset("600002")]
        replace, _, _, out, _, code = self._run("20261020", assets)
        self.assertIsNone(code)
        self.assertEqual(out["failed"][0]["code"], "600001")
        self.assertEqual(out["failed"][0]["stage"], "quote")
        self.assertEqual(set(replace.call_args.args[1]), {"600002"})

    def test_report_includes_snapshot_error(self):
        _, _, _, out, _, code = self._run(
            "20261020", [self._asset("600001")])
        self.assertIsNone(code)
        self.assertIsNone(out["snapshot_error"])

    def test_snapshot_write_failure_exits_nonzero(self):
        _, _, _, out, _, code = self._run(
            "20261020", [self._asset("600001")],
            replace_side_effect=RuntimeError("disk full"))
        self.assertEqual(code, 1)
        self.assertIn("快照写入失败", out["error"])


if __name__ == "__main__":
    unittest.main()
