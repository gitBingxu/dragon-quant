"""盘后复盘（prep）单元测试：纯函数 / 降级 / 门禁 / 采集契约 / 快照消费。"""

import unittest
from unittest.mock import MagicMock, patch

from dragon_quant.review_account.models import StrategyConfig


def _assets(trade_date: str = "2026-10-19", **overrides):
    """构造最小资产集，便于逐项覆盖。"""
    base = {
        "dragons": {"trade_date": trade_date, "scan_id": "v2_20261019_top25",
                    "version": "1.0", "count": 1,
                    "rows": [{"code": "600001", "name": "样本", "composite_score": 80,
                              "is_true_dragon": True, "concepts": ["低空经济"]}]},
        "ths_limit_up_pool": {"trade_date": trade_date, "api_date": "20261019",
                              "total": 59,
                              "rows": [{"code": "600001", "reason_type": "低空经济",
                                        "limit_up_type": "换手板", "open_num": 3,
                                        "is_again_limit": 1, "high_days": "2天2板",
                                        "first_limit_up_time": 1792114286}]},
        "ths_block_top": {"trade_date": trade_date, "api_date": "20261019",
                          "rows": [{"name": "低空经济", "limit_up_num": 23,
                                    "high": "4天2板", "high_num": 4,
                                    "stock_list": [{"code": "600001"}]}]},
        "market_breadth": {"as_of": trade_date, "up_cnt": 2100, "down_cnt": 2900,
                           "flat_cnt": 100, "limit_up": ["600001"], "broken": ["600002"],
                           "limit_down": ["600003"], "total_amount": 1.5e12,
                           "sample_n": 5100, "raw_n": 5200},
        "fg_boards": {"as_of": trade_date,
                      "boards": {"昨日涨停": {"rows": [
                          {"code": "600001", "close": 11.0, "pre_close": 10.0}], "status": "ok"},
                          "昨日跌停": {"rows": [], "status": "ok"},
                          "昨日上榜": {"rows": [{"code": "600001"}], "status": "ok"}}},
        "fund_flow": {"as_of": trade_date,
                      "quotes": {"600001": {"main_net_amount": 12345678.0, "pct": 10.0,
                                            "turnover_rate": 5.0, "amount": 1e8}}},
        "cninfo_announcements": {"as_of": trade_date, "window_days": 7,
                                 "window_start": "2026-10-12",
                                 "items": {"600001": {"announcements": [
                                     {"title": "公告", "type": "t", "date": "2026-10-18",
                                      "url": "u"}], "max_announcement_date": "2026-10-18",
                                     "truncated": False}},
                                 "truncated_codes": []},
        "collection_report": {},
    }
    base.update(overrides)
    return base


class TestPositionAdvice(unittest.TestCase):
    def test_bands(self):
        from dragon_quant.review_account.prep import position_advice
        self.assertEqual(position_advice(90, 2), ("重仓", 0.8))
        self.assertEqual(position_advice(50, 40), ("空仓", 0.0))
        self.assertEqual(position_advice(30, 15), ("轻仓", 0.3))
        self.assertEqual(position_advice(50, 3), ("正常", 0.5))


class TestComputeSentiment(unittest.TestCase):
    def test_full_assets(self):
        from dragon_quant.review_account.prep import compute_sentiment
        s = compute_sentiment(_assets())
        self.assertEqual(s["limit_up_cnt"], 59)          # THS T 日权威
        self.assertEqual(s["limit_up_cnt_ths"], 59)
        self.assertEqual(s["limit_up_cnt_t1_fg"], 1)     # FG T-1 交叉
        self.assertEqual(s["down_cnt"], 2900)
        self.assertEqual(s["broken_cnt"], 1)             # breadth 优先
        self.assertEqual(s["limit_down_cnt"], 1)
        self.assertEqual(s["yesterday_premium_pct"], 10.0)
        self.assertEqual(s["max_board_height"], 4)
        self.assertEqual(s["lhb_cnt"], 1)
        self.assertEqual(s["position_input"],
                         {"limit_up_cnt": "ths_t", "limit_down_cnt": "breadth_t"})
        self.assertEqual(s["position_advice"], "正常")
        self.assertEqual(s["degraded"], [])

    def test_ths_missing_falls_back_to_fg_and_marks_degraded(self):
        from dragon_quant.review_account.prep import compute_sentiment
        assets = _assets()
        assets.pop("ths_limit_up_pool")
        s = compute_sentiment(assets)
        self.assertIsNone(s["limit_up_cnt"])             # 对外主口径不拿 T-1 冒充 T
        self.assertEqual(s["limit_up_cnt_t1_fg"], 1)
        self.assertEqual(s["position_input"]["limit_up_cnt"], "fg_t1")
        self.assertIn("ths_limit_up_pool", s["degraded"])
        self.assertIn("position_input_fg_t1", s["degraded"])

    def test_both_limit_sources_missing_yields_null_position(self):
        from dragon_quant.review_account.prep import compute_sentiment
        assets = _assets()
        assets.pop("ths_limit_up_pool")
        assets.pop("fg_boards")
        assets.pop("market_breadth")
        s = compute_sentiment(assets)
        self.assertIsNone(s["position_pct"])
        self.assertIsNone(s["position_advice"])
        self.assertIn("market_breadth", s["degraded"])

    def test_broken_cnt_falls_back_to_ths_open_num(self):
        from dragon_quant.review_account.prep import compute_sentiment
        assets = _assets()
        assets.pop("market_breadth")
        s = compute_sentiment(assets)
        self.assertEqual(s["broken_cnt"], 1)             # open_num=3 > 0
        self.assertEqual(s["factor_sources"]["broken_cnt"],
                         "ths_limit_up_pool(open_num>0)")


class TestComputeMainBoards(unittest.TestCase):
    def test_block_top_primary_path(self):
        from dragon_quant.review_account.prep import compute_main_boards
        boards = compute_main_boards(_assets())
        self.assertEqual(boards[0]["board_name"], "低空经济")
        self.assertEqual(boards[0]["limit_up_num"], 23)
        self.assertEqual(boards[0]["dragon_count"], 1)

    def test_fallback_to_concepts(self):
        from dragon_quant.review_account.prep import compute_main_boards
        assets = _assets()
        assets.pop("ths_block_top")
        boards = compute_main_boards(assets)
        self.assertEqual(boards, [{"board_name": "低空经济", "dragon_count": 1}])
        # 回退形状缺 limit_up_num/high，消费方必须 .get()
        self.assertIsNone(boards[0].get("limit_up_num"))


class TestComputeCandidates(unittest.TestCase):
    def test_enriches_with_pool_flow_and_announcements(self):
        from dragon_quant.review_account.prep import compute_candidates
        cands = compute_candidates(_assets(), "2026-10-19")
        c = cands[0]
        self.assertEqual(c["trade_date"], "2026-10-19")
        self.assertEqual(c["reason_type"], "低空经济")
        self.assertEqual(c["open_num"], 3)
        self.assertIs(c["is_again_limit"], True)
        self.assertEqual(c["main_net_amount"], 12345678.0)
        self.assertEqual(c["announcements"][0]["title"], "公告")

    def test_missing_enrichment_yields_none_not_error(self):
        from dragon_quant.review_account.prep import compute_candidates
        assets = _assets()
        assets.pop("ths_limit_up_pool")
        assets.pop("fund_flow")
        assets.pop("cninfo_announcements")
        c = compute_candidates(assets, "2026-10-19")[0]
        self.assertIsNone(c["reason_type"])
        self.assertIsNone(c["main_net_amount"])
        self.assertEqual(c["announcements"], [])


class TestComputeErrorIsolation(unittest.TestCase):
    def test_malformed_block_top_falls_back_to_concepts(self):
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets()
        assets["ths_block_top"] = {"rows": "not-a-list-of-dicts"}  # 畸形
        out = _compute_all("2026-10-19", assets)
        # 类型收敛后走回退路径（真龙 concepts），不影响其它因子
        self.assertEqual(out["main_boards"],
                         [{"board_name": "低空经济", "dragon_count": 1}])
        self.assertEqual(out["sentiment"]["limit_up_cnt"], 59)
        self.assertEqual(out["candidates"][0]["code"], "600001")

    def test_output_contract_keys(self):
        from dragon_quant.review_account.prep import _compute_all
        out = _compute_all("2026-10-19", _assets())
        self.assertEqual(out["version"], "0.2")
        self.assertEqual(out["dragon_source"]["scan_id"], "v2_20261019_top25")
        for k in ("sentiment", "main_boards", "candidates"):
            self.assertIn(k, out)


class TestMarketBreadthAggregation(unittest.TestCase):
    def _row(self, code, close, pre, vol=100, amount=1000.0, lu=None, ld=None, high=None):
        return {"code": code, "close": close, "pre_close": pre, "high": high or close,
                "vol": vol, "amount": amount, "buy_price_limit": lu, "sell_price_limit": ld}

    def test_counts_and_filters(self):
        from dragon_quant.review_account.prep import _collect_market_breadth, PrepContext
        tdx = MagicMock()
        tdx.get_market_breadth_rows.return_value = [
            self._row("A", 11.0, 10.0, lu=11.0),          # 涨停
            self._row("B", 10.5, 10.0, high=11.0, lu=11.0),  # 炸板
            self._row("C", 9.0, 10.0, ld=9.0),             # 跌停
            self._row("D", 10.2, 10.0),                    # 红盘
            self._row("SUSP", 10.0, 10.0, vol=0, amount=0.0),  # 停牌（有价无量）
            self._row("INVALID", 0.0, 10.0),               # 无效
        ]
        ctx = PrepContext(trade_date="2026-10-19", today="2026-10-19",
                          tdx=tdx, ths=MagicMock())
        p = _collect_market_breadth(ctx)
        self.assertEqual(p["sample_n"], 4)                 # 停牌/无效被剔除
        self.assertEqual(p["up_cnt"], 3)
        self.assertEqual(p["down_cnt"], 1)
        self.assertEqual(p["limit_up"], ["A"])
        self.assertEqual(p["broken"], ["B"])
        self.assertEqual(p["limit_down"], ["C"])

    def test_sample_suspicious_threshold(self):
        from dragon_quant.review_account.prep import _breadth_suspicious
        self.assertIsNotNone(_breadth_suspicious({"sample_n": 100}))
        self.assertIsNone(_breadth_suspicious({"sample_n": 5000}))


class TestFundFlowSuspicious(unittest.TestCase):
    def test_all_zero_or_none_is_suspicious(self):
        from dragon_quant.review_account.prep import _fund_flow_suspicious
        self.assertIsNotNone(_fund_flow_suspicious({"quotes": {"A": {"main_net_amount": 0},
                                                               "B": {"main_net_amount": None}}}))
        self.assertIsNotNone(_fund_flow_suspicious({"quotes": {"A": {"main_net_amount": None}}}))
        self.assertIsNone(_fund_flow_suspicious({"quotes": {"A": {"main_net_amount": 100}}}))
        self.assertIsNone(_fund_flow_suspicious({"quotes": {}}))


class TestCollectionReportDegradation(unittest.TestCase):
    """compute 必须消费 collection_report：坏/缺状态资产按缺失降级，绝不产出信号。"""

    def test_empty_suspicious_breadth_not_used(self):
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets()
        assets["collection_report"] = {
            "market_breadth": {"status": "empty_suspicious", "note": "sample_n=100"}}
        s = _compute_all("2026-10-19", assets)["sentiment"]
        # 残缺宽度不得产出跌停数 → 显式回退 FG T-1 并标记，不静默使用残缺值
        self.assertIsNone(s["limit_down_cnt"])
        self.assertEqual(s["position_input"]["limit_down_cnt"], "fg_t1")
        self.assertIn("market_breadth", s["degraded"])
        self.assertIn("position_input_fg_t1", s["degraded"])

    def test_error_asset_degrades(self):
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets()
        assets["collection_report"] = {
            "ths_limit_up_pool": {"status": "error", "error": "403"}}
        s = _compute_all("2026-10-19", assets)["sentiment"]
        self.assertIsNone(s["limit_up_cnt"])
        self.assertIn("ths_limit_up_pool", s["degraded"])

    def test_fund_flow_all_zero_marks_none_on_candidates(self):
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets()
        assets["fund_flow"]["quotes"]["600001"]["main_net_amount"] = 0.0
        assets["collection_report"] = {
            "fund_flow": {"status": "empty_suspicious", "note": "全为 0"}}
        out = _compute_all("2026-10-19", assets)
        self.assertIsNone(out["candidates"][0]["main_net_amount"])   # 不是 0
        self.assertIn("fund_flow", out["sentiment"]["degraded"])

    def test_ok_status_keeps_values(self):
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets()
        assets["collection_report"] = {"market_breadth": {"status": "ok"},
                                       "ths_limit_up_pool": {"status": "ok"}}
        s = _compute_all("2026-10-19", assets)["sentiment"]
        self.assertEqual(s["limit_up_cnt"], 59)
        self.assertEqual(s["degraded"], [])

    def test_as_of_mismatch_degrades(self):
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets()
        assets["fg_boards"]["as_of"] = "2026-10-17"        # 非 trade_date
        assets["collection_report"] = {"fg_boards": {"status": "ok"}}
        s = _compute_all("2026-10-19", assets)["sentiment"]
        self.assertIn("fg_boards", s["degraded"])
        self.assertIsNone(s["yesterday_premium_pct"])
        self.assertIsNone(s["lhb_cnt"])

    def test_cninfo_as_of_differing_is_not_degraded(self):
        """公告 as_of 是采集时间而非数据时点，历史补采不应因此降级。"""
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets("2026-10-16")
        assets["cninfo_announcements"]["as_of"] = "2026-10-18"   # 今天采集
        assets["collection_report"] = {"cninfo_announcements": {"status": "ok"}}
        out = _compute_all("2026-10-16", assets)
        self.assertNotIn("cninfo_announcements", out["sentiment"]["degraded"])
        self.assertEqual(out["candidates"][0]["announcements"][0]["title"], "公告")

    def test_fg_board_missing_is_unknown_not_zero(self):
        """THS 缺失 + FG 单板块缺失（双重故障）→ position 必须 null，不得按 0 家算。"""
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets()
        assets.pop("ths_limit_up_pool")
        assets["fg_boards"]["boards"]["昨日涨停"] = {"rows": [], "status": "missing"}
        assets["collection_report"] = {"fg_boards": {"status": "ok"},
                                       "ths_limit_up_pool": {"status": "error"}}
        s = _compute_all("2026-10-19", assets)["sentiment"]
        self.assertIsNone(s["limit_up_cnt_t1_fg"])       # missing ≠ 0
        self.assertIsNone(s["position_pct"])
        self.assertIn("fg_boards_partial", s["degraded"])

    def test_collector_total_failure_visible_in_degraded(self):
        """首次采集失败（无 stale payload）也必须出现在 degraded，不得静默。"""
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets()
        assets.pop("ths_block_top")                      # 采集失败且无历史残留
        assets["collection_report"] = {
            "ths_block_top": {"status": "error", "error": "403"}}
        out = _compute_all("2026-10-19", assets)
        self.assertIn("ths_block_top", out["sentiment"]["degraded"])

    def test_empty_status_on_must_nonempty_degrades(self):
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets()
        assets["collection_report"] = {"market_breadth": {"status": "empty"}}
        s = _compute_all("2026-10-19", assets)["sentiment"]
        self.assertIn("market_breadth", s["degraded"])
        self.assertIsNone(s["down_cnt"])


class _FakeDF:
    def __init__(self, records):
        self._records = records
        self.empty = not records

    def to_dict(self, orient):
        assert orient == "records"
        return self._records


class _FakeTdxClient:
    def __init__(self, boards, members):
        self._boards = boards
        self._members = members

    def get_board_list(self, **kwargs):
        return _FakeDF(self._boards)

    def get_board_members(self, board_symbol, **kwargs):
        return _FakeDF(self._members.get(board_symbol, []))


class TestTdxFgBoards(unittest.TestCase):
    """get_fg_boards 批量 + 逐板块 status；get_fg_board_members 旧契约字段保留。"""

    def _provider(self):
        from types import SimpleNamespace
        from unittest.mock import patch as _patch
        from dragon_quant.providers import tdx

        et = SimpleNamespace(
            BoardType=SimpleNamespace(FG="FG"),
            BoardSortColumn=SimpleNamespace(CHANGE_PCT="pct"),
            SortType=SimpleNamespace(CHANGE_PCT="pct"),
            SortOrder=SimpleNamespace(DESC="desc"),
        )
        client = _FakeTdxClient(
            boards=[{"name": "昨日涨停", "code": "880863"},
                    {"name": "昨日上榜", "code": "880871"}],
            members={"880863": [{"code": "600001", "name": "甲", "close": 11.0,
                                 "pre_close": 10.0, "total_market_cap_ab": 5e9,
                                 "main_net_amount": 100.0}],
                     "880871": []},
        )
        p = tdx.TdxProvider()
        p._client = client
        p._preset = SimpleNamespace(COMMON=1, QUOTE=2)
        return p, _patch.object(tdx, "_import_easytdx", return_value=et)

    def test_batch_status_and_missing(self):
        p, patcher = self._provider()
        with patcher:
            out = p.get_fg_boards(["昨日涨停", "昨日上榜", "不存在"])
        self.assertEqual(out["昨日涨停"]["status"], "ok")
        self.assertEqual(len(out["昨日涨停"]["rows"]), 1)
        self.assertEqual(out["昨日上榜"]["status"], "ok")
        self.assertEqual(out["不存在"], {"rows": [], "status": "missing"})

    def test_legacy_member_contract_keeps_full_fields(self):
        """旧方法字段集不缩水（含 market_cap），向后兼容。"""
        p, patcher = self._provider()
        with patcher:
            rows = p.get_fg_board_members("昨日涨停")
        self.assertEqual(set(rows[0]), {
            "code", "name", "close", "pre_close", "open", "high", "low", "turnover",
            "amount", "market_cap", "main_net_amount", "buy_price_limit",
            "sell_price_limit"})
        self.assertEqual(rows[0]["market_cap"], 5e9)


class TestRunPrepIntegration(unittest.TestCase):
    """采集→单事务落库→读回→计算 全链路（provider 用假实现，prep.db 用 tmp）。"""

    def test_full_pipeline_persists_assets_and_snapshot(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from dragon_quant.review_account import prep as prep_mod

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "prep.db")

            def fake_collect(ctx):
                return {"count": 1,
                        "rows": [{"code": "600001", "composite_score": 80,
                                  "is_true_dragon": True, "concepts": ["低空经济"]}],
                        "trade_date": ctx.trade_date}

            with patch.object(prep_mod, "ASSET_COLLECTORS", {
                    "dragons": prep_mod.CollectorSpec(fake_collect, False),
                    "ths_limit_up_pool": prep_mod.CollectorSpec(
                        lambda c: {"total": 10, "rows": [{"code": "600001"}]}, False),
                 }), \
                 patch("dragon_quant.review_account.prep._validate_trade_date"), \
                 patch("dragon_quant.review_account.prep.TdxProvider"), \
                 patch("dragon_quant.review_account.prep.THSProvider"), \
                 patch("dragon_quant.storage.prep_db._connect",
                       side_effect=lambda: sqlite3.connect(db_path)):
                out = prep_mod.run_prep("2026-10-16")

                from dragon_quant.storage import prep_db
                assets = prep_db.get_prep_assets("2026-10-16")
                snap = prep_db.get_prep_snapshot("2026-10-16")

        # 资产已落库并含 collection_report
        self.assertEqual(set(assets), {"dragons", "ths_limit_up_pool", "collection_report"})
        self.assertEqual(assets["collection_report"]["dragons"]["status"], "ok")
        # 快照已落库，且由资产计算而来
        self.assertEqual(snap["sentiment"]["limit_up_cnt"], 10)
        self.assertEqual(snap["candidates"][0]["code"], "600001")
        self.assertEqual(out["version"], "0.2")


class TestMalformedAssetsTypeCoercion(unittest.TestCase):
    """畸形 payload（list/str）不得击穿整个 sentiment。"""

    def test_malformed_market_breadth_keeps_other_factors(self):
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets()
        assets["market_breadth"] = ["unexpected"]
        s = _compute_all("2026-10-19", assets)["sentiment"]
        self.assertEqual(s["limit_up_cnt"], 59)
        self.assertIsNone(s["up_cnt"])
        self.assertIsNotNone(s.get("broken_cnt"))   # 回退 open_num 仍可用

    def test_malformed_pool_keeps_other_factors(self):
        from dragon_quant.review_account.prep import _compute_all
        assets = _assets()
        assets["ths_limit_up_pool"] = "garbage"
        s = _compute_all("2026-10-19", assets)["sentiment"]
        self.assertIsNone(s["limit_up_cnt"])
        self.assertEqual(s["down_cnt"], 2900)       # breadth 仍有效
        self.assertEqual(s["up_cnt"], 2100)


class TestCollectionGate(unittest.TestCase):
    """采集门禁：历史日跳过 same_day_only 资产；补跑仍采 THS/cninfo/dragons。"""

    def test_historical_day_skips_same_day_only(self):
        from dragon_quant.review_account import prep
        with patch.object(prep, "TdxProvider", return_value=MagicMock()), \
             patch.object(prep, "THSProvider", return_value=MagicMock()), \
             patch.dict(prep.ASSET_COLLECTORS, clear=True):
            # 只保留三类作代表：same_day_only 的 market_breadth + 非当日限定的 dragons/ths
            prep.ASSET_COLLECTORS.update({
                "dragons": prep.CollectorSpec(lambda c: {"count": 0}, False),
                "market_breadth": prep.CollectorSpec(lambda c: {"sample_n": 5000}, True),
                "ths_limit_up_pool": prep.CollectorSpec(lambda c: {"total": 0}, False),
            })
            payloads, report = prep._collect_all("2026-10-01")
        self.assertNotIn("market_breadth", payloads)
        self.assertEqual(report["market_breadth"]["status"], "unavailable")
        self.assertIn("dragons", payloads)
        self.assertIn("ths_limit_up_pool", payloads)

    def test_collector_error_does_not_break_others(self):
        from dragon_quant.review_account import prep

        def _boom(_):
            raise RuntimeError("403")

        with patch.object(prep, "TdxProvider", return_value=MagicMock()), \
             patch.object(prep, "THSProvider", return_value=MagicMock()), \
             patch.dict(prep.ASSET_COLLECTORS, clear=True):
            prep.ASSET_COLLECTORS.update({
                "bad": prep.CollectorSpec(_boom, False),
                "good": prep.CollectorSpec(lambda c: {"v": 1}, False),
            })
            payloads, report = prep._collect_all("2026-10-19")
        self.assertEqual(report["bad"]["status"], "error")
        self.assertIn("403", report["bad"]["error"])
        self.assertIn("good", payloads)


class TestCollectCninfoLookAhead(unittest.TestCase):
    def _ctx(self, tdx):
        from dragon_quant.review_account.prep import PrepContext
        return PrepContext(trade_date="2026-10-19", today="2026-10-25", tdx=tdx,
                           ths=MagicMock(),
                           dragons_rows=[{"code": "600001"}])

    def test_filters_future_announcements(self):
        from dragon_quant.review_account.prep import _collect_cninfo
        tdx = MagicMock()
        tdx.get_announcements.return_value = [
            {"title": "future", "date": "2026-10-22", "type": "", "url": ""},
            {"title": "in-window", "date": "2026-10-18", "type": "", "url": ""},
            {"title": "old", "date": "2026-10-10", "type": "", "url": ""},
        ]
        payload = _collect_cninfo(self._ctx(tdx))
        titles = [a["title"] for a in payload["items"]["600001"]["announcements"]]
        self.assertEqual(titles, ["in-window"])            # 未来与窗口外均被过滤
        self.assertEqual(payload["items"]["600001"]["max_announcement_date"], "2026-10-22")

    def test_truncated_flag_when_page_cap_reached(self):
        from dragon_quant.review_account import prep
        tdx = MagicMock()
        # 每页都返回晚于窗口起点的公告 → 翻满 MAX_PAGES 仍未到窗口
        tdx.get_announcements.return_value = [
            {"title": "x", "date": "2026-10-18", "type": "", "url": ""}]
        payload = prep._collect_cninfo(self._ctx(tdx))
        self.assertTrue(payload["items"]["600001"]["truncated"])
        self.assertEqual(payload["truncated_codes"], ["600001"])

    def test_stops_when_reaching_window_start(self):
        from dragon_quant.review_account.prep import _collect_cninfo
        tdx = MagicMock()
        tdx.get_announcements.return_value = [
            {"title": "x", "date": "2026-10-11", "type": "", "url": ""}]  # < window_start
        payload = _collect_cninfo(self._ctx(tdx))
        self.assertFalse(payload["items"]["600001"]["truncated"])
        tdx.get_announcements.assert_called_once()


class TestValidateTradeDate(unittest.TestCase):
    def _tdx(self, dates):
        from dragon_quant.review_account.prep import SHANGHAI
        from datetime import datetime
        tdx = MagicMock()
        tdx.get_kline.return_value = [
            MagicMock(timestamp=int(datetime.fromisoformat(d).replace(tzinfo=SHANGHAI)
                                    .timestamp() * 1000)) for d in dates]
        return tdx

    def test_future_date_rejected(self):
        from datetime import datetime
        from dragon_quant.review_account.prep import PrepError, SHANGHAI, _validate_trade_date
        now = datetime(2026, 10, 19, 16, 0, tzinfo=SHANGHAI)
        with self.assertRaises(PrepError):
            _validate_trade_date("2026-10-20", self._tdx(["2026-10-19"]), now)

    def test_intraday_rejected_without_calendar_check(self):
        from datetime import datetime
        from dragon_quant.review_account.prep import PrepError, SHANGHAI, _validate_trade_date
        now = datetime(2026, 10, 19, 10, 0, tzinfo=SHANGHAI)
        tdx = self._tdx([])
        with self.assertRaises(PrepError) as cm:
            _validate_trade_date("2026-10-19", tdx, now)
        self.assertIn("盘中", str(cm.exception))            # 不误报「非交易日」
        tdx.get_kline.assert_not_called()

    def test_calendar_unavailable_distinct_from_non_trading_day(self):
        from datetime import datetime
        from dragon_quant.review_account.prep import PrepError, SHANGHAI, _validate_trade_date
        now = datetime(2026, 10, 19, 16, 0, tzinfo=SHANGHAI)
        with self.assertRaises(PrepError) as cm:
            _validate_trade_date("2026-10-08", self._tdx([]), now)
        self.assertIn("数据源失败", str(cm.exception))
        with self.assertRaises(PrepError) as cm:
            _validate_trade_date("2026-10-18", self._tdx(["2026-10-16"]), now)
        self.assertIn("非交易日", str(cm.exception))

    def test_valid_historical_day_passes(self):
        from datetime import datetime
        from dragon_quant.review_account.prep import SHANGHAI, _validate_trade_date
        now = datetime(2026, 10, 19, 16, 0, tzinfo=SHANGHAI)
        _validate_trade_date("2026-10-16", self._tdx(["2026-10-15", "2026-10-16"]), now)


class TestRunPrepRecompute(unittest.TestCase):
    def test_recompute_without_assets_raises(self):
        from dragon_quant.review_account.prep import PrepError, run_prep
        with patch("dragon_quant.storage.prep_db.get_prep_assets", return_value={}):
            with self.assertRaises(PrepError):
                run_prep("2026-10-19", recompute=True)

    def test_recompute_uses_stored_assets_only(self):
        from dragon_quant.review_account.prep import run_prep
        day = "2026-10-16"   # 历史日（今日 2026-10-18，未来日期会被门禁拒绝）
        with patch("dragon_quant.storage.prep_db.get_prep_assets",
                   return_value=_assets(day)) as ga, \
             patch("dragon_quant.storage.prep_db.save_prep_snapshot") as save, \
             patch("dragon_quant.review_account.prep.TdxProvider") as tdx_cls, \
             patch("dragon_quant.review_account.prep.THSProvider") as ths_cls:
            out = run_prep(day, recompute=True)
        ga.assert_called_once_with(day)
        tdx_cls.assert_not_called()                        # 不触网
        ths_cls.assert_not_called()
        save.assert_called_once()
        self.assertEqual(out["sentiment"]["limit_up_cnt"], 59)

    def test_recompute_deterministic(self):
        from dragon_quant.review_account.prep import _compute_all
        a = _assets()
        self.assertEqual(_compute_all("2026-10-19", a), _compute_all("2026-10-19", a))


class TestQuotesMainNetAmountMapping(unittest.TestCase):
    def test_missing_field_stays_none(self):
        from dragon_quant.providers.tdx import _to_quote
        q = _to_quote({"code": "600001", "close": 10.0, "pre_close": 9.0})
        self.assertIsNone(q.main_net_amount)

    def test_present_field_mapped(self):
        from dragon_quant.providers.tdx import _to_quote
        q = _to_quote({"code": "600001", "close": 10.0, "pre_close": 9.0,
                       "main_net_amount": 1234.0})
        self.assertEqual(q.main_net_amount, 1234.0)


class TestNormalizeRows(unittest.TestCase):
    def test_nan_and_numpy_like_values(self):
        from dragon_quant.providers.tdx import _normalize_value
        self.assertIsNone(_normalize_value(float("nan")))
        self.assertIsNone(_normalize_value(float("inf")))
        self.assertEqual(_normalize_value(3), 3)

        class FakeNumpy:                      # 模拟 numpy 标量的 .item()
            def item(self):
                return 42.5

        self.assertEqual(_normalize_value(FakeNumpy()), 42.5)


class TestLimitUpPoolPagination(unittest.TestCase):
    """THS 涨停池分页：去重、页数上限、末页终止。"""

    def _provider(self):
        from dragon_quant.providers.ths import THSProvider
        return THSProvider()

    def _payload(self, rows, total):
        import json
        return json.dumps({"status_code": 0,
                           "data": {"page": {"total": total}, "info": rows}})

    def test_multi_page_collects_all(self):
        from dragon_quant.providers import ths
        page1 = [{"code": f"{i:06d}"} for i in range(200)]
        page2 = [{"code": f"{i:06d}"} for i in range(200, 400)]
        replies = iter([self._payload(page1, 400), self._payload(page2, 400)])
        with patch.object(ths, "_fetch_with_retry", side_effect=lambda *a, **k: next(replies)):
            out = self._provider().get_limit_up_pool("20261016")
        self.assertEqual(out["total"], 400)
        self.assertEqual(len(out["rows"]), 400)
        self.assertEqual(len({r["code"] for r in out["rows"]}), 400)

    def test_server_ignoring_page_does_not_duplicate(self):
        """服务端忽略 page 时仍返回同一页 → 去重后不重复累计。"""
        from dragon_quant.providers import ths
        page = [{"code": f"{i:06d}"} for i in range(200)]
        with patch.object(ths, "_fetch_with_retry",
                          side_effect=lambda *a, **k: self._payload(page, 800)):
            out = self._provider().get_limit_up_pool("20261016")
        self.assertEqual(len(out["rows"]), 200)      # 未放大
        self.assertLessEqual(len(out["rows"]), 800)

    def test_page_cap_limits_requests(self):
        from dragon_quant.providers import ths
        calls = {"n": 0}

        def fake(*a, **k):
            calls["n"] += 1
            rows = [{"code": f"{calls['n']:03d}{i:03d}"} for i in range(200)]
            return self._payload(rows, 10**6)        # total 畸形巨大

        with patch.object(ths, "_fetch_with_retry", side_effect=fake):
            self._provider().get_limit_up_pool("20261016")
        self.assertEqual(calls["n"], ths.DATAAPI_MAX_PAGES)

    def test_block_top_parsing(self):
        import json
        from dragon_quant.providers import ths
        payload = json.dumps({"status_code": 0,
                              "data": [{"name": "低空经济", "limit_up_num": 23}]})
        with patch.object(ths, "_fetch_with_retry", return_value=payload):
            out = self._provider().get_block_top("20261016")
        self.assertEqual(out["rows"][0]["name"], "低空经济")

    def test_error_status_raises(self):
        import json
        from dragon_quant.providers import ths
        payload = json.dumps({"status_code": -1, "status_msg": "limit must be <= 200"})
        with patch.object(ths, "_fetch_with_retry", return_value=payload):
            with self.assertRaises(RuntimeError) as cm:
                self._provider().get_limit_up_pool("20261016")
        self.assertIn("200", str(cm.exception))

    def test_truncated_flag_when_cap_reached(self):
        from dragon_quant.providers import ths
        counter = {"n": 0}

        def fake(*a, **k):
            counter["n"] += 1
            rows = [{"code": f"{counter['n']}{i:03d}"} for i in range(200)]
            return self._payload(rows, 10**6)      # total 远大于可翻页上限

        with patch.object(ths, "_fetch_with_retry", side_effect=fake):
            out = self._provider().get_limit_up_pool("20261016")
        self.assertTrue(out["truncated"])
        self.assertLess(len(out["rows"]), out["total"])


class TestThsRetry(unittest.TestCase):
    def test_ranking_returns_none_on_exhaust(self):
        from dragon_quant.providers import ths
        with patch.object(ths.urllib.request, "urlopen",
                          side_effect=OSError("403")), \
             patch.object(ths.time, "sleep", return_value=None):
            self.assertIsNone(ths._fetch_with_retry("http://x", attempts=3))

    def test_raise_on_exhaust_for_dataapi(self):
        from dragon_quant.providers import ths
        with patch.object(ths.urllib.request, "urlopen",
                          side_effect=OSError("403")), \
             patch.object(ths.time, "sleep", return_value=None):
            with self.assertRaises(RuntimeError):
                ths._fetch_with_retry("http://x", attempts=3, raise_on_exhaust=True)

    def test_succeeds_after_retry(self):
        from dragon_quant.providers import ths
        calls = {"n": 0}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok":1}'

        def fake(_req, timeout=0):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("403")
            return Resp()

        with patch.object(ths.urllib.request, "urlopen", side_effect=fake), \
             patch.object(ths.time, "sleep", return_value=None):
            self.assertEqual(ths._fetch_with_retry("http://x", attempts=3), '{"ok":1}')


class TestCollectCandidatesSnapshot(unittest.TestCase):
    """collect_candidates / load_market_sentiment 改读 prep.db 后行为不变。"""

    def test_uses_snapshot_when_present(self):
        from dragon_quant.review_account.market import collect_candidates
        snap = {"candidates": [
            {"code": "A", "composite_score": 90, "is_true_dragon": True},
            {"code": "B", "composite_score": 70, "is_true_dragon": True},
            {"code": "C", "composite_score": 40, "is_true_dragon": True},
            {"code": "D", "composite_score": 80, "is_true_dragon": False},
        ]}
        loader = MagicMock(return_value=[])
        with patch("dragon_quant.storage.prep_db.get_prep_snapshot", return_value=snap):
            got = collect_candidates(["2026-09-04"], "2026-09-07", StrategyConfig(), loader)
        self.assertEqual([c["code"] for c in got], ["A", "B"])
        loader.assert_not_called()

    def test_falls_back_when_no_snapshot(self):
        from dragon_quant.review_account.market import collect_candidates
        loader = MagicMock(return_value=[])
        with patch("dragon_quant.storage.prep_db.get_prep_snapshot", return_value=None):
            collect_candidates(
                ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07"],
                "2026-09-07", StrategyConfig(), loader)
        self.assertEqual([c.args[0] for c in loader.call_args_list],
                         ["2026-09-02", "2026-09-03", "2026-09-04"])

    def test_falls_back_on_snapshot_read_error(self):
        from dragon_quant.review_account.market import collect_candidates
        loader = MagicMock(return_value=[{"code": "X", "composite_score": 60,
                                          "is_true_dragon": True}])
        with patch("dragon_quant.storage.prep_db.get_prep_snapshot",
                   side_effect=RuntimeError("db down")):
            got = collect_candidates(["2026-09-04"], "2026-09-07", StrategyConfig(), loader)
        self.assertEqual([c["code"] for c in got], ["X"])

    def test_v1_source_skips_snapshot(self):
        from dragon_quant.review_account.market import collect_candidates
        loader = MagicMock(return_value=[])
        with patch("dragon_quant.storage.prep_db.get_prep_snapshot") as gp:
            collect_candidates(["2026-09-04"], "2026-09-07", StrategyConfig(source="v1"), loader)
        gp.assert_not_called()

    def test_snapshot_all_filtered_falls_back(self):
        from dragon_quant.review_account.market import collect_candidates
        snap = {"candidates": [{"code": "LOW", "composite_score": 10, "is_true_dragon": True}]}
        loader = MagicMock(return_value=[{"code": "X", "composite_score": 60,
                                          "is_true_dragon": True}])
        with patch("dragon_quant.storage.prep_db.get_prep_snapshot", return_value=snap):
            got = collect_candidates(["2026-09-04"], "2026-09-07", StrategyConfig(), loader)
        self.assertEqual([c["code"] for c in got], ["X"])


class TestPreSourceV1Removed(unittest.TestCase):
    def test_cli_has_no_source_flag(self):
        import io
        from dragon_quant import cli
        err = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "prep", "--source", "v1"]), \
             patch("sys.stderr", err), self.assertRaises(SystemExit):
            cli.main()
        self.assertIn("unrecognized", err.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
