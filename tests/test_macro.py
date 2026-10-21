"""宏观扰动采集单测：规格口径 / 同日与 T-1 边界 / 回退 / 降级 / JSON 原生性。"""

import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from dragon_quant.providers.macro import (
    MACRO_INDEX_KEYS, MACRO_KEYS, MACRO_SPECS, MacroSpec, build_macro_record,
    collect_macro,
)
from dragon_quant.review_account.prep import _compute_all, compute_macro

TRADE_DATE = "2026-10-21"


def _rec(key="jp_nikkei", price=64750.0, prev=64360.0, source="tdx_ex",
         session_date=TRADE_DATE, **extra):
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


class TestMacroSpecs(unittest.TestCase):
    def test_specs_are_jp_a50_ust_gold(self):
        self.assertEqual(MACRO_KEYS, ("jp_nikkei", "cn_a50", "ust10y", "gold"))
        self.assertFalse([k for k in MACRO_KEYS if k.startswith("us_")])
        self.assertEqual(MACRO_INDEX_KEYS, ("jp_nikkei", "cn_a50"))
        self.assertTrue(set(MACRO_INDEX_KEYS) <= set(MACRO_KEYS))

    def test_index_specs_same_day_and_tdx_only(self):
        by_key = {s.key: s for s in MACRO_SPECS}
        jp, cn = by_key["jp_nikkei"], by_key["cn_a50"]
        self.assertEqual(jp.label, "日经225期指")
        self.assertEqual(cn.label, "富时A50期指")
        self.assertEqual(jp.tdx, (("INTL_INDEX", "NK0Y"),))
        self.assertEqual(cn.tdx, (("INTL_INDEX", "CNY0"),))
        for spec in (jp, cn):
            self.assertTrue(spec.same_day_close)
            self.assertEqual(spec.priority, ("tdx",))
            self.assertIsNone(spec.tencent)
            self.assertIsNone(spec.xueqiu)
            self.assertIsNone(spec.yahoo)
        for key in ("ust10y", "gold"):
            self.assertFalse(by_key[key].same_day_close)


class TestCollectMacro(unittest.TestCase):
    def test_all_from_first_source_and_no_extra_calls(self):
        tdx = _Source({s.key: _rec(s.key, price=5.15 if s.kind == "yield" else 100.0,
                                   prev=5.09 if s.kind == "yield" else 99.0)
                       for s in MACRO_SPECS})
        tencent = _Source({})
        out = collect_macro({"tdx": tdx, "tencent": tencent}, TRADE_DATE)
        self.assertTrue(out["instruments"])
        self.assertEqual(out["degraded"], [])
        self.assertTrue(all(v == "tdx" for v in out["sources"].values()))
        self.assertEqual(tencent.calls, [])      # 已满足则不再调用后续源
        self.assertEqual(out["as_of"], TRADE_DATE)
        self.assertNotIn("caliber", out)         # 口径只在逐品种记录上

    def test_fallback_only_missing_specs(self):
        tdx = _Source({"jp_nikkei": _rec("jp_nikkei")})
        yahoo = _Source({"ust10y": _rec("ust10y", price=5.15, prev=5.09,
                                        source="yahoo", session_date="2026-10-20",
                                        chg_bp_approx=False),
                         "gold": _rec("gold", price=4160.3, prev=4186.7,
                                      source="yahoo", session_date="2026-10-20")})
        out = collect_macro({"tdx": tdx, "yahoo": yahoo}, TRADE_DATE)
        self.assertIn("ust10y", out["instruments"])
        self.assertEqual(out["sources"]["ust10y"], "yahoo")
        # cn_a50 priority 只有 tdx，不会送给 yahoo
        self.assertEqual(yahoo.calls[0], ["ust10y", "gold"])

    def test_partial_degraded(self):
        tdx = _Source({"jp_nikkei": _rec("jp_nikkei")})
        out = collect_macro({"tdx": tdx}, TRADE_DATE)
        self.assertTrue(out["instruments"])
        for key in ("cn_a50", "ust10y", "gold"):
            self.assertIn(key, out["degraded"])

    def test_all_fail_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            collect_macro({"tdx": _Source(error=RuntimeError("boom"))}, TRADE_DATE)
        self.assertIn("tdx", str(cm.exception))

    def test_source_error_recorded_but_others_continue(self):
        tdx = _Source(error=RuntimeError("down"))
        yahoo = _Source({"ust10y": _rec("ust10y", price=5.15, prev=5.09,
                                        source="yahoo", session_date="2026-10-20")})
        out = collect_macro({"tdx": tdx, "yahoo": yahoo}, TRADE_DATE)
        self.assertIn("tdx", out["source_errors"])
        self.assertEqual(out["sources"]["ust10y"], "yahoo")
        self.assertIn("jp_nikkei", out["degraded"])
        self.assertIn("cn_a50", out["degraded"])

    def test_json_native_no_nan(self):
        rec = _rec("jp_nikkei", price=float("nan"))
        # build_macro_record 对 NaN 返回 None；collect 不会把 NaN 落库
        self.assertIsNone(rec)
        out = collect_macro({"tdx": _Source({"jp_nikkei": _rec("jp_nikkei")})},
                            TRADE_DATE)
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


def _tdx_records(with_t_plus_one=True):
    nk = [_bar("2026-10-19", 64960.0), _bar("2026-10-20", 64360.0),
          _bar("2026-10-21", 64750.0)]
    cn = [_bar("2026-10-19", 13344.0), _bar("2026-10-20", 13279.0),
          _bar("2026-10-21", 13164.0)]
    if with_t_plus_one:                      # 日经夜盘已生成 T+1 bar
        nk.append(_bar("2026-10-22", 65050.0))
        cn.append(_bar("2026-10-22", 13198.0))
    return {
        "NK0Y": nk,
        "CNY0": cn,
        "8_ATY": [_bar("2026-10-19", 5.09), _bar("2026-10-20", 5.15),
                  _bar("2026-10-21", 5.20)],
        "GC00W": [_bar("2026-10-20", 0.0)],
        "Au99.99": [_bar("2026-10-16", 900.89), _bar("2026-10-19", 889.78),
                    _bar("2026-10-20", 885.0), _bar("2026-10-21", 880.0)],
    }


class TestTdxMacroDailyK(unittest.TestCase):
    def _provider(self, records=None):
        from dragon_quant.providers.tdx import TdxProvider
        p = TdxProvider()
        p._macro_client = _FakeKlineClient(records or _tdx_records())
        ex = SimpleNamespace(INTL_INDEX=12, COMEX_FUTURES=16,
                             SH_GOLD=46, MACRO_INDICATOR=38)
        period = SimpleNamespace(DAILY=4)
        return p, patch("dragon_quant.providers.tdx._ex_enums",
                        return_value=(ex, period))

    def test_same_day_indices_include_t_bar(self):
        p, patcher = self._provider()
        with patcher:
            out = p.get_macro_quotes(list(MACRO_SPECS), TRADE_DATE)
        for key in ("jp_nikkei", "cn_a50"):
            self.assertEqual(out[key]["session_date"], TRADE_DATE)
            self.assertEqual(out[key]["caliber"], "same_day_daily_bar")
        self.assertAlmostEqual(out["jp_nikkei"]["pct"],
                               (64750.0 / 64360.0 - 1.0) * 100.0, places=3)
        self.assertAlmostEqual(out["cn_a50"]["pct"],
                               (13164.0 / 13279.0 - 1.0) * 100.0, places=3)

    def test_t_plus_one_bar_excluded(self):
        p, patcher = self._provider()
        with patcher:
            out = p.get_macro_quotes([s for s in MACRO_SPECS
                                      if s.key == "jp_nikkei"], TRADE_DATE)
        self.assertEqual(out["jp_nikkei"]["price"], 64750.0)   # 不是 65050

    def test_deterministic_with_and_without_t_plus_one_bar(self):
        """同一 trade_date 在 T+1 bar 出现前后取值一致（模拟 16:41 / 20:00）。"""
        p1, patch1 = self._provider(_tdx_records(with_t_plus_one=False))
        p2, patch2 = self._provider(_tdx_records(with_t_plus_one=True))
        with patch1:
            early = p1.get_macro_quotes(list(MACRO_SPECS), TRADE_DATE)
        with patch2:
            late = p2.get_macro_quotes(list(MACRO_SPECS), TRADE_DATE)
        self.assertEqual(early, late)

    def test_missing_t_bar_falls_back_to_previous_session(self):
        records = _tdx_records(with_t_plus_one=False)
        records["NK0Y"] = records["NK0Y"][:-1]     # 只有 T-1 及更早
        records["CNY0"] = records["CNY0"][:-1]
        p, patcher = self._provider(records)
        with patcher:
            out = p.get_macro_quotes(list(MACRO_SPECS), TRADE_DATE)
        for key in ("jp_nikkei", "cn_a50"):
            self.assertEqual(out[key]["session_date"], "2026-10-20")
        self.assertAlmostEqual(out["jp_nikkei"]["pct"],
                               (64360.0 / 64960.0 - 1.0) * 100.0, places=3)

    def test_previous_session_for_yield_and_gold(self):
        p, patcher = self._provider()
        with patcher:
            out = p.get_macro_quotes(list(MACRO_SPECS), TRADE_DATE)
        self.assertEqual(out["ust10y"]["session_date"], "2026-10-20")
        self.assertEqual(out["ust10y"]["chg_bp"], 6.0)
        self.assertTrue(out["ust10y"]["chg_bp_approx"])
        self.assertIn("近似", out["ust10y"]["precision_note"])
        self.assertEqual(out["ust10y"]["caliber"], "previous_daily_bar")
        gold = out["gold"]
        self.assertEqual(gold["market"], "SH_GOLD")
        self.assertEqual(gold["label"], "沪金")
        self.assertEqual(gold["price_unit"], "cny_per_g")
        self.assertEqual(gold["session_date"], "2026-10-20")

    def test_gold_uses_comex_when_available(self):
        records = _tdx_records()
        records["GC00W"] = [_bar("2026-10-19", 4100.0),
                            _bar("2026-10-20", 4142.5)]
        p, patcher = self._provider(records)
        with patcher:
            out = p.get_macro_quotes([s for s in MACRO_SPECS
                                      if s.key == "gold"], TRADE_DATE)
        gold = out["gold"]
        self.assertEqual(gold["market"], "COMEX_FUTURES")
        self.assertEqual(gold["label"], "COMEX金")
        self.assertEqual(gold["price_unit"], "usd_per_oz")
        self.assertEqual(gold["session_date"], "2026-10-20")
        self.assertEqual(gold["price"], 4142.5)

    def test_unavailable_client_raises(self):
        from dragon_quant.providers.tdx import TdxProvider
        p = TdxProvider()
        with patch.object(p, "_get_macro_client", return_value=None):
            with self.assertRaises(RuntimeError):
                p.get_macro_quotes(list(MACRO_SPECS), TRADE_DATE)


# 历史 provider 契约：当前 MACRO_SPECS 不再路由到腾讯/雪球，方法保留用于兼容与测试
_LEGACY_INDEX_SPEC = MacroSpec(
    key="legacy_us_index", label="道指", kind="index", price_unit="points",
    chg_unit="pct", tdx=(), priority=("tencent", "xueqiu"),
    tencent="usDJI", xueqiu=".DJI",
)


class TestTencentMacro(unittest.TestCase):
    def _line(self, name, code, price, prev, pct, stamp):
        fields = [""] * 40
        fields[1], fields[2], fields[3], fields[4] = name, code, price, prev
        fields[30], fields[31], fields[32] = stamp, "0", pct
        return f'v_{code}="' + "~".join(fields) + '";'

    def test_no_route_returns_empty_without_fetch(self):
        from dragon_quant.providers.tencent import TencentProvider
        with patch("dragon_quant.providers.tencent._fetch_macro_raw") as fetch:
            out = TencentProvider().get_macro_quotes(list(MACRO_SPECS), TRADE_DATE)
        self.assertEqual(out, {})
        fetch.assert_not_called()

    def test_completed_session_only(self):
        from dragon_quant.providers.tencent import TencentProvider
        raw = "\n".join([
            self._line("道琼斯", "usDJI", "51966.03", "52002.41", "-0.07",
                       "2026-10-20 16:00:00"),
            self._line("纳斯达克", "usIXIC", "26430.89", "26140.03", "1.11",
                       "2026-10-21 12:00:00"),
        ])
        with patch("dragon_quant.providers.tencent._fetch_macro_raw",
                   return_value=raw):
            out = TencentProvider().get_macro_quotes(
                [_LEGACY_INDEX_SPEC], TRADE_DATE)
        self.assertIn("legacy_us_index", out)
        self.assertEqual(out["legacy_us_index"]["source"], "tencent")
        self.assertEqual(out["legacy_us_index"]["session_date"], "2026-10-20")

    def test_http_failure_raises(self):
        from dragon_quant.providers.tencent import TencentProvider
        with patch("dragon_quant.providers.tencent._fetch_macro_raw",
                   side_effect=RuntimeError("429")):
            with self.assertRaises(RuntimeError):
                TencentProvider().get_macro_quotes(
                    [_LEGACY_INDEX_SPEC], TRADE_DATE)


class TestXueqiuMacro(unittest.TestCase):
    def _data(self):
        from zoneinfo import ZoneInfo
        ts = int(datetime.fromisoformat("2026-10-20T16:00:00")
                 .replace(tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
        return {"data": {"quote": {"current": 51966.03, "last_close": 52002.41,
                                   "percent": -0.07, "chg": -36.38,
                                   "timestamp": ts}}}

    def test_no_route_returns_empty(self):
        from dragon_quant.providers.xueqiu import XueqiuProvider
        with patch("dragon_quant.providers.xueqiu.get_xq", return_value=None):
            out = XueqiuProvider().get_macro_quotes(list(MACRO_SPECS), TRADE_DATE)
        self.assertEqual(out, {})

    def test_no_cookie_returns_empty(self):
        from dragon_quant.providers.xueqiu import XueqiuProvider
        with patch("dragon_quant.providers.xueqiu.get_xq", return_value=None):
            out = XueqiuProvider().get_macro_quotes(
                [_LEGACY_INDEX_SPEC], TRADE_DATE)
        self.assertEqual(out, {})

    def test_success(self):
        from dragon_quant.providers.xueqiu import XueqiuProvider
        with patch("dragon_quant.providers.xueqiu.get_xq", return_value="ck"), \
             patch("dragon_quant.providers.xueqiu._fetch_macro_quote_json",
                   return_value=self._data()):
            out = XueqiuProvider().get_macro_quotes(
                [_LEGACY_INDEX_SPEC], TRADE_DATE)
        self.assertEqual(out["legacy_us_index"]["session_date"], "2026-10-20")
        self.assertEqual(out["legacy_us_index"]["source"], "xueqiu")

    def test_http_failure_raises(self):
        from dragon_quant.providers.xueqiu import XueqiuProvider
        with patch("dragon_quant.providers.xueqiu.get_xq", return_value="ck"), \
             patch("dragon_quant.providers.xueqiu._fetch_macro_quote_json",
                   side_effect=RuntimeError("403")):
            with self.assertRaises(RuntimeError):
                XueqiuProvider().get_macro_quotes(
                    [_LEGACY_INDEX_SPEC], TRADE_DATE)


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
                [s for s in MACRO_SPECS if s.key == "ust10y"], TRADE_DATE)
        rec = out["ust10y"]
        self.assertEqual(rec["session_date"], "2026-10-20")
        self.assertEqual(rec["price"], 5.16)
        self.assertEqual(rec["chg_bp"], 1.0)
        self.assertFalse(rec["chg_bp_approx"])

    def test_same_day_spec_not_supported(self):
        from dragon_quant.providers.macro import YahooMacroSource
        spec = MacroSpec(key="fake_same_day", label="同日", kind="index",
                         price_unit="points", chg_unit="pct", tdx=(),
                         priority=("yahoo",), yahoo="^N225",
                         same_day_close=True)
        with patch("dragon_quant.providers.macro._fetch_yahoo_chart",
                   return_value=self._payload()):
            out = YahooMacroSource().get_macro_quotes([spec], TRADE_DATE)
        self.assertEqual(out, {})        # 不支持同日口径 → 不返回记录

    def test_failure_raises(self):
        from dragon_quant.providers.macro import YahooMacroSource
        with patch("dragon_quant.providers.macro._fetch_yahoo_chart",
                   side_effect=RuntimeError("429")):
            with self.assertRaises(RuntimeError):
                YahooMacroSource().get_macro_quotes(
                    [s for s in MACRO_SPECS if s.key == "ust10y"], TRADE_DATE)


class TestComputeMacro(unittest.TestCase):
    def _assets(self):
        from tests.test_prep import _assets
        return _assets()

    def test_available_session_and_stale(self):
        out = compute_macro(self._assets(), "2026-10-19")
        self.assertTrue(out["available"])
        self.assertEqual(out["session"], "same_day")
        self.assertFalse(out["instruments"]["jp_nikkei"]["stale"])
        self.assertEqual(out["instruments"]["jp_nikkei"]["caliber"],
                         "same_day_daily_bar")
        self.assertNotIn("caliber", out)     # 无顶层聚合

    def test_missing_asset_degrades(self):
        assets = self._assets()
        assets.pop("macro_global")
        out = compute_macro(assets, "2026-10-19")
        self.assertFalse(out["available"])
        self.assertEqual(out["degraded"], ["macro_global"])
        self.assertNotIn("caliber", out)

    def test_partial_degraded_and_macro_partial_mark(self):
        assets = self._assets()
        assets["macro_global"]["instruments"].pop("ust10y")
        out = _compute_all("2026-10-19", assets)
        self.assertTrue(out["macro"]["available"])
        self.assertIn("ust10y", out["macro"]["degraded"])
        self.assertIn("macro_partial", out["sentiment"]["degraded"])

    def test_missing_same_day_bar_marks_degraded(self):
        assets = self._assets()
        assets["macro_global"]["instruments"]["cn_a50"]["session_date"] = "2026-10-16"
        out = _compute_all("2026-10-19", assets)
        self.assertEqual(out["macro"]["session"], "mixed")
        self.assertIn("macro_same_day_missing", out["sentiment"]["degraded"])

    def test_all_indices_previous_session(self):
        assets = self._assets()
        for key in ("jp_nikkei", "cn_a50"):
            assets["macro_global"]["instruments"][key]["session_date"] = "2026-10-16"
        out = _compute_all("2026-10-19", assets)
        self.assertEqual(out["macro"]["session"], "previous_session")
        self.assertIn("macro_same_day_missing", out["sentiment"]["degraded"])

    def test_legacy_us_keys_filtered(self):
        assets = self._assets()
        inst = assets["macro_global"]["instruments"]
        inst.pop("jp_nikkei")
        inst.pop("cn_a50")
        inst["us_dji"] = {"key": "us_dji", "label": "道指", "kind": "index",
                          "price_unit": "points", "chg_unit": "pct",
                          "price": 51966.03, "prev_close": 52002.41,
                          "pct": -0.07, "session_date": "2026-10-16"}
        assets["macro_global"]["degraded"] = ["us_dji"]
        assets["macro_global"]["sources"]["us_dji"] = "tdx_ex"
        out = _compute_all("2026-10-19", assets)
        self.assertTrue(out["macro"]["available"])
        self.assertIn("jp_nikkei", out["macro"]["degraded"])
        self.assertIn("cn_a50", out["macro"]["degraded"])
        self.assertNotIn("us_dji", out["macro"]["degraded"])
        self.assertNotIn("us_dji", out["macro"]["instruments"])
        self.assertNotIn("us_dji", out["macro"]["sources"])
        self.assertIn("macro_same_day_missing", out["sentiment"]["degraded"])

    def test_collection_error_adds_macro_global(self):
        assets = self._assets()
        assets.pop("macro_global")
        assets["collection_report"] = {"macro_global": {"status": "error"}}
        out = _compute_all("2026-10-19", assets)
        self.assertFalse(out["macro"]["available"])
        self.assertIn("macro_global", out["sentiment"]["degraded"])


if __name__ == "__main__":
    unittest.main()
