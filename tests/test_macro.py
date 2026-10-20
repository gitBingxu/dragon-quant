"""宏观扰动采集单测：门面回退 / 日 K canonical / quote 过滤 / 降级 / JSON 原生性。"""

import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from dragon_quant.providers.macro import (
    MACRO_SPECS, collect_macro, build_macro_record,
)
from dragon_quant.review_account.prep import _compute_all, compute_macro


def _rec(key="us_dji", price=51966.03, prev=52002.41, source="tdx_ex",
         session_date="2026-10-19", **extra):
    spec = next(s for s in MACRO_SPECS if s.key == key)
    rec = build_macro_record(
        spec, price=price, prev_close=prev, source=source,
        code="X", market="m", session_date=session_date,
        chg_bp_approx=True if spec.kind == "yield" else None,
    )
    if rec is None:
        return None
    rec.update(extra)
    return rec


class _Source:
    def __init__(self, result=None, error=None):
        self.result = result or {}
        self.error = error
        self.calls = []

    def get_macro_quotes(self, specs, trade_date):
        self.calls.append([s.key for s in specs])
        if self.error:
            raise self.error
        return {s.key: self.result[s.key] for s in specs if s.key in self.result}


class TestCollectMacro(unittest.TestCase):
    def test_all_from_first_source_and_no_extra_calls(self):
        tdx = _Source({s.key: _rec(s.key) for s in MACRO_SPECS})
        tencent = _Source({})
        out = collect_macro({"tdx": tdx, "tencent": tencent}, "2026-10-20")
        self.assertTrue(out["instruments"])
        self.assertEqual(out["degraded"], [])
        self.assertTrue(all(v == "tdx" for v in out["sources"].values()))
        self.assertEqual(tencent.calls, [])      # 已满足则不再调用后续源
        self.assertEqual(out["as_of"], "2026-10-20")

    def test_fallback_only_missing_specs(self):
        tdx = _Source({"us_dji": _rec("us_dji")})
        yahoo = _Source({"ust10y": _rec("ust10y", price=5.15, prev=5.09,
                                        source="yahoo", chg_bp_approx=False),
                         "gold": _rec("gold", price=4160.3, prev=4186.7,
                                      source="yahoo")})
        out = collect_macro({"tdx": tdx, "yahoo": yahoo}, "2026-10-20")
        self.assertIn("ust10y", out["instruments"])
        self.assertEqual(out["sources"]["ust10y"], "yahoo")
        self.assertEqual(yahoo.calls[0], ["us_ixic", "us_spx", "ust10y", "gold"])

    def test_partial_degraded(self):
        tdx = _Source({"us_dji": _rec("us_dji")})
        out = collect_macro({"tdx": tdx}, "2026-10-20")
        self.assertTrue(out["instruments"])
        self.assertIn("ust10y", out["degraded"])
        self.assertIn("gold", out["degraded"])

    def test_all_fail_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            collect_macro({"tdx": _Source(error=RuntimeError("boom"))},
                          "2026-10-20")
        self.assertIn("tdx", str(cm.exception))

    def test_source_error_recorded_but_others_continue(self):
        tdx = _Source(error=RuntimeError("down"))
        tencent = _Source({"us_dji": _rec("us_dji", source="tencent")})
        out = collect_macro({"tdx": tdx, "tencent": tencent}, "2026-10-20")
        self.assertIn("tdx", out["source_errors"])
        self.assertEqual(out["sources"]["us_dji"], "tencent")

    def test_json_native_no_nan(self):
        rec = _rec("us_dji", price=float("nan"))
        # build_macro_record 对 NaN 返回 None；collect 不会把 NaN 落库
        self.assertIsNone(rec)
        out = collect_macro({"tdx": _Source({"us_dji": _rec("us_dji")})},
                            "2026-10-20")
        json.dumps(out, allow_nan=False)


class _FakeDF:
    def __init__(self, records):
        self._records = records
        self.empty = not records

    def to_dict(self, orient):
        assert orient == "records"
        return self._records


class _FakeKlineClient:
    def __init__(self, by_code):
        self.by_code = by_code

    def goods_kline(self, market, code, period, start, count):
        return _FakeDF(self.by_code.get(code, []))


def _bar(day, close):
    return {"datetime": datetime.fromisoformat(day), "close": close,
            "open": close, "high": close, "low": close, "vol": 0}


class TestTdxMacroDailyK(unittest.TestCase):
    def _provider(self):
        from dragon_quant.providers.tdx import TdxProvider
        records = {
            "A_DJI": [_bar("2026-10-15", 52177.0), _bar("2026-10-16", 52002.41),
                      _bar("2026-10-19", 51966.03), _bar("2026-10-20", 52432.0)],
            "A_IXIC": [_bar("2026-10-16", 26140.03), _bar("2026-10-19", 26430.89)],
            "A_SPX": [_bar("2026-10-16", 7659.44), _bar("2026-10-19", 7690.73)],
            "8_ATY": [_bar("2026-10-16", 5.09), _bar("2026-10-19", 5.15)],
            "GC00W": [_bar("2026-10-19", 0.0)],
            "Au99.99": [_bar("2026-10-16", 900.89), _bar("2026-10-19", 889.78)],
        }
        p = TdxProvider()
        p._macro_client = _FakeKlineClient(records)
        ex = SimpleNamespace(INTL_INDEX=12, COMEX_FUTURES=16,
                             SH_GOLD=46, MACRO_INDICATOR=38)
        period = SimpleNamespace(DAILY=4)
        return p, patch("dragon_quant.providers.tdx._ex_enums",
                        return_value=(ex, period))

    def test_canonical_last_completed_bar_and_yield_approx(self):
        p, patcher = self._provider()
        with patcher:
            out = p.get_macro_quotes(list(MACRO_SPECS), "2026-10-20")
        # 10-20 盘中 bar 被 < trade_date 排除
        self.assertEqual(out["us_dji"]["session_date"], "2026-10-19")
        self.assertAlmostEqual(out["us_dji"]["pct"], -0.07, places=2)
        self.assertEqual(out["ust10y"]["chg_bp"], 6.0)
        self.assertTrue(out["ust10y"]["chg_bp_approx"])
        self.assertIn("近似", out["ust10y"]["precision_note"])

    def test_gold_falls_back_to_sh_gold(self):
        p, patcher = self._provider()
        with patcher:
            out = p.get_macro_quotes(list(MACRO_SPECS), "2026-10-20")
        gold = out["gold"]
        self.assertEqual(gold["market"], "SH_GOLD")
        self.assertEqual(gold["label"], "沪金")
        self.assertEqual(gold["price_unit"], "cny_per_g")

    def test_unavailable_client_raises(self):
        from dragon_quant.providers.tdx import TdxProvider
        p = TdxProvider()
        with patch.object(p, "_get_macro_client", return_value=None):
            with self.assertRaises(RuntimeError):
                p.get_macro_quotes(list(MACRO_SPECS), "2026-10-20")


class TestTencentMacro(unittest.TestCase):
    def _line(self, name, code, price, prev, pct, stamp):
        fields = [""] * 40
        fields[1], fields[2], fields[3], fields[4] = name, code, price, prev
        fields[30], fields[31], fields[32] = stamp, "0", pct
        return f'v_{code}="' + "~".join(fields) + '";'

    def test_completed_session_only(self):
        from dragon_quant.providers.tencent import TencentProvider
        raw = "\n".join([
            self._line("道琼斯", "usDJI", "51966.03", "52002.41", "-0.07",
                       "2026-10-19 16:00:00"),
            self._line("纳斯达克", "usIXIC", "26430.89", "26140.03", "1.11",
                       "2026-10-20 12:00:00"),
        ])
        with patch("dragon_quant.providers.tencent._fetch_macro_raw",
                   return_value=raw):
            out = TencentProvider().get_macro_quotes(
                list(MACRO_SPECS), "2026-10-20")
        self.assertIn("us_dji", out)
        self.assertNotIn("us_ixic", out)       # 当日盘中 session 被拒绝
        self.assertEqual(out["us_dji"]["source"], "tencent")

    def test_http_failure_raises(self):
        from dragon_quant.providers.tencent import TencentProvider
        with patch("dragon_quant.providers.tencent._fetch_macro_raw",
                   side_effect=RuntimeError("429")):
            with self.assertRaises(RuntimeError):
                TencentProvider().get_macro_quotes(
                    list(MACRO_SPECS), "2026-10-20")


class TestXueqiuMacro(unittest.TestCase):
    def _data(self):
        from zoneinfo import ZoneInfo
        ts = int(datetime.fromisoformat("2026-10-19T16:00:00")
                 .replace(tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
        return {"data": {"quote": {"current": 51966.03, "last_close": 52002.41,
                                   "percent": -0.07, "chg": -36.38,
                                   "timestamp": ts}}}

    def test_no_cookie_returns_empty(self):
        from dragon_quant.providers.xueqiu import XueqiuProvider
        with patch("dragon_quant.providers.xueqiu.get_xq", return_value=None):
            out = XueqiuProvider().get_macro_quotes(
                [s for s in MACRO_SPECS if s.key == "us_dji"], "2026-10-20")
        self.assertEqual(out, {})

    def test_success(self):
        from dragon_quant.providers.xueqiu import XueqiuProvider
        with patch("dragon_quant.providers.xueqiu.get_xq", return_value="ck"), \
             patch("dragon_quant.providers.xueqiu._fetch_macro_quote_json",
                   return_value=self._data()):
            out = XueqiuProvider().get_macro_quotes(
                [s for s in MACRO_SPECS if s.key == "us_dji"], "2026-10-20")
        self.assertEqual(out["us_dji"]["session_date"], "2026-10-19")
        self.assertEqual(out["us_dji"]["source"], "xueqiu")

    def test_http_failure_raises(self):
        from dragon_quant.providers.xueqiu import XueqiuProvider
        with patch("dragon_quant.providers.xueqiu.get_xq", return_value="ck"), \
             patch("dragon_quant.providers.xueqiu._fetch_macro_quote_json",
                   side_effect=RuntimeError("403")):
            with self.assertRaises(RuntimeError):
                XueqiuProvider().get_macro_quotes(
                    [s for s in MACRO_SPECS if s.key == "us_dji"], "2026-10-20")


class TestYahooMacro(unittest.TestCase):
    def _payload(self):
        from zoneinfo import ZoneInfo
        def ts(day):
            return int(datetime.fromisoformat(day + "T13:30:00")
                       .replace(tzinfo=ZoneInfo("America/New_York")).timestamp())
        return {"chart": {"result": [{
            "timestamp": [ts("2026-10-16"), ts("2026-10-19"), ts("2026-10-20")],
            "indicators": {"quote": [{"close": [5.09, 5.15, 5.16]}]},
        }]}}

    def test_uses_completed_bars(self):
        from dragon_quant.providers.macro import YahooMacroSource
        with patch("dragon_quant.providers.macro._fetch_yahoo_chart",
                   return_value=self._payload()):
            out = YahooMacroSource().get_macro_quotes(
                [s for s in MACRO_SPECS if s.key == "ust10y"], "2026-10-20")
        rec = out["ust10y"]
        self.assertEqual(rec["session_date"], "2026-10-19")
        self.assertEqual(rec["price"], 5.15)
        self.assertEqual(rec["chg_bp"], 6.0)
        self.assertFalse(rec["chg_bp_approx"])

    def test_failure_raises(self):
        from dragon_quant.providers.macro import YahooMacroSource
        with patch("dragon_quant.providers.macro._fetch_yahoo_chart",
                   side_effect=RuntimeError("429")):
            with self.assertRaises(RuntimeError):
                YahooMacroSource().get_macro_quotes(
                    [s for s in MACRO_SPECS if s.key == "ust10y"], "2026-10-20")


class TestComputeMacro(unittest.TestCase):
    def _assets(self):
        from tests.test_prep import _assets
        return _assets()

    def test_available_session_and_stale(self):
        out = compute_macro(self._assets(), "2026-10-19")
        self.assertTrue(out["available"])
        self.assertEqual(out["session"], "overnight")
        self.assertFalse(out["instruments"]["us_dji"]["stale"])

    def test_missing_asset_degrades(self):
        assets = self._assets()
        assets.pop("macro_global")
        out = compute_macro(assets, "2026-10-19")
        self.assertFalse(out["available"])
        self.assertEqual(out["degraded"], ["macro_global"])

    def test_partial_degraded_and_macro_partial_mark(self):
        assets = self._assets()
        assets["macro_global"]["instruments"].pop("ust10y")
        out = _compute_all("2026-10-19", assets)
        self.assertTrue(out["macro"]["available"])
        self.assertIn("ust10y", out["macro"]["degraded"])
        self.assertIn("macro_partial", out["sentiment"]["degraded"])

    def test_collection_error_adds_macro_global(self):
        assets = self._assets()
        assets.pop("macro_global")
        assets["collection_report"] = {"macro_global": {"status": "error"}}
        out = _compute_all("2026-10-19", assets)
        self.assertFalse(out["macro"]["available"])
        self.assertIn("macro_global", out["sentiment"]["degraded"])


if __name__ == "__main__":
    unittest.main()
