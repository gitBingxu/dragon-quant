"""tests for dragon_quant.providers.tdx — TdxProvider 数据转换与 fallback 路由。

覆盖（均无需安装 tmdx）：
  - 纯函数转换 _to_kbars/_to_quote/_to_sector/_to_stockinfo
  - 市场/代码解析 _market_of/_resolve（含 SH000001→999999 指数别名）
  - orchestrator._try_primary 主链路→老链路回退
  - easy_tdx 未安装时 TdxProvider 优雅降级（恒返回空，触发回退）
"""
import io
import unittest
from collections import namedtuple
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from unittest.mock import patch

from dragon_quant.models.types import SectorPerformance, StockInfo
from dragon_quant.orchestrator import _try_primary
from dragon_quant.providers.tdx import (
    TdxProvider,
    _easy_tdx_installed,
    _market_of,
    _resolve,
    _to_kbars,
    _to_quote,
    _to_sector,
    _to_stockinfo,
    warn_if_unavailable,
)


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")  # noqa: DTZ007 — 测试用本地 naive 时间


class TestMarketResolve(unittest.TestCase):
    def test_market_of(self):
        self.assertEqual(_market_of("600000"), 1)   # SH
        self.assertEqual(_market_of("000001"), 0)   # SZ 平安银行
        self.assertEqual(_market_of("300750"), 0)   # SZ 创业板
        self.assertEqual(_market_of("881376"), 1)   # SH 行业板块指数
        self.assertEqual(_market_of("999999"), 1)   # SH 上证指数
        self.assertEqual(_market_of("830799"), 2)   # BJ

    def test_resolve_index_alias(self):
        # SH000001（上证指数）→ 999999；000001 仍是平安银行（SZ）
        self.assertEqual(_resolve("SH000001"), (1, "999999"))
        self.assertEqual(_resolve("600000"), (1, "600000"))
        self.assertEqual(_resolve("000001"), (0, "000001"))


class TestToKbars(unittest.TestCase):
    def test_pct_chained(self):
        records = [
            {"datetime": _dt("2026-06-19 09:30"), "open": 10.0, "high": 10.5,
             "low": 9.9, "close": 10.4, "vol": 100, "amount": 1e6},
            {"datetime": _dt("2026-06-19 09:31"), "open": 10.4, "high": 10.6,
             "low": 10.3, "close": 10.6, "vol": 200, "amount": 2e6},
        ]
        bars = _to_kbars(records)
        self.assertEqual(len(bars), 2)
        self.assertAlmostEqual(bars[0].pct, 0.0)  # 首根无前收
        self.assertAlmostEqual(bars[1].pct, (10.6 - 10.4) / 10.4 * 100, places=3)
        self.assertAlmostEqual(bars[1].volume, 200)
        self.assertGreater(bars[0].timestamp, 0)

    def test_nan_volume_coerced(self):
        records = [{"datetime": _dt("2026-06-19 09:30"), "open": 10.0, "high": 10.0,
                    "low": 10.0, "close": 10.0, "vol": float("nan"),
                    "amount": float("nan")}]
        bars = _to_kbars(records)
        self.assertEqual(bars[0].volume, 0.0)   # 板块指数分钟线 vol=NaN → 0
        self.assertEqual(bars[0].amount, 0.0)


class TestToQuote(unittest.TestCase):
    def test_full_quote(self):
        r = {
            "code": "600519", "name": "贵州茅台",
            "close": 1500.0, "pre_close": 1500.0,
            "open": 1490.0, "high": 1510.0, "low": 1480.0,
            "vol": 10000, "amount": 1.5e9,
            "turnover": 2.5, "vol_ratio": 1.2,
            "total_shares": 125619.78, "float_shares": 125619.78,  # 万股
            "pe_dynamic": 30.0, "pe_ttm": 0.0,
            "buy_price_limit": 1650.0, "sell_price_limit": 1350.0,
            "bid_price": 1500.0, "bid_volume": 500, "ask_volume": 300,
        }
        q = _to_quote(r)
        self.assertIsNotNone(q)
        self.assertEqual(q.code, "600519")
        self.assertEqual(q.bid1_price, 1500.0)   # 封单买一价
        self.assertEqual(q.bid1_volume, 500)     # 封单量
        self.assertEqual(q.ask1_volume, 300)
        self.assertEqual(q.turnover_rate, 2.5)
        self.assertEqual(q.limit_up, 1650.0)
        self.assertAlmostEqual(q.market_cap, 125619.78 * 1e4 * 1500.0)  # 万股→股→元

    def test_invalid_close_returns_none(self):
        self.assertIsNone(_to_quote({"close": 0.0}))
        self.assertIsNone(_to_quote({"close": float("nan")}))


class TestSectorAndStock(unittest.TestCase):
    def test_to_sector(self):
        s = _to_sector({"code": "881376", "name": "数字媒体",
                        "price": 907.26, "pre_close": 837.14})
        self.assertIsInstance(s, SectorPerformance)
        self.assertAlmostEqual(s.pct, (907.26 / 837.14 - 1) * 100, places=2)

    def test_to_stockinfo(self):
        st = _to_stockinfo({"code": "600000", "name": "浦发银行",
                            "close": 10.5, "pre_close": 10.0}, "881101")
        self.assertIsInstance(st, StockInfo)
        self.assertAlmostEqual(st.pct, 5.0, places=6)
        self.assertEqual(st.sector_code, "881101")


class TestTryPrimary(unittest.TestCase):
    def test_primary_ok_no_fallback(self):
        called = []
        r = _try_primary(lambda: [1, 2], lambda: called.append(1) or [3],
                         "kline", "雪球")
        self.assertEqual(r, [1, 2])
        self.assertEqual(called, [])

    def test_primary_empty_falls_back(self):
        r = _try_primary(list, lambda: [3, 4], "kline", "雪球")
        self.assertEqual(r, [3, 4])

    def test_primary_raises_falls_back(self):
        def boom():
            raise RuntimeError("boom")
        r = _try_primary(boom, lambda: [5], "kline", "雪球")
        self.assertEqual(r, [5])


class TestTdxProviderDegrade(unittest.TestCase):
    """easy_tdx 未安装时所有方法优雅降级返回空/None，触发 orchestrator 回退。"""

    def test_all_empty_without_easytdx(self):
        with patch("dragon_quant.providers.tdx._import_easytdx", return_value=None):
            p = TdxProvider()
            self.assertEqual(p.get_sector_ranking(), [])
            self.assertEqual(p.get_sector_components("881101"), [])
            self.assertEqual(p.get_kline("600000"), [])
            self.assertEqual(p.get_5min_kline("600000"), [])
            self.assertEqual(p.get_minute_kline("600000"), [])
            self.assertEqual(p.get_sector_5min_kline("881101"), [])
            self.assertEqual(p.get_sector_1min_kline("881101"), [])
            self.assertEqual(p.get_sector_5min_kline_history("881101"), [])
            self.assertEqual(p.batch_get_quotes(["600000", "000001"]), [])
            self.assertIsNone(p.get_quote("600000"))


_VersionInfo = namedtuple("VersionInfo", "major minor micro releaselevel serial")


class TestEasyTdxInstalled(unittest.TestCase):
    """_easy_tdx_installed：find_spec 命中→True、未找到→None→False。"""

    def test_true_when_spec_found(self):
        with patch("importlib.util.find_spec", return_value=object()):
            self.assertTrue(_easy_tdx_installed())

    def test_false_when_not_found(self):
        with patch("importlib.util.find_spec", return_value=None):
            self.assertFalse(_easy_tdx_installed())


class TestWarnIfUnavailable(unittest.TestCase):
    """warn_if_unavailable：已安装不打印；缺失时按 Python 版本打印不同建议到 stderr。"""

    def test_no_warning_when_installed(self):
        with patch("dragon_quant.providers.tdx._easy_tdx_installed", return_value=True):
            buf = io.StringIO()
            with redirect_stderr(buf):
                warn_if_unavailable()
            self.assertEqual(buf.getvalue(), "")

    def test_warn_python_below_310(self):
        with patch("dragon_quant.providers.tdx._easy_tdx_installed", return_value=False), \
             patch("dragon_quant.providers.tdx.sys.version_info",
                   _VersionInfo(3, 9, 0, "final", 0)):
            buf = io.StringIO()
            with redirect_stderr(buf):
                warn_if_unavailable()
            text = buf.getvalue()
            self.assertIn("未安装 tmdx", text)
            self.assertIn("升级 Python", text)
            self.assertIn("review-account", text)

    def test_warn_python_ge_310(self):
        with patch("dragon_quant.providers.tdx._easy_tdx_installed", return_value=False), \
             patch("dragon_quant.providers.tdx.sys.version_info",
                   _VersionInfo(3, 11, 0, "final", 0)):
            buf = io.StringIO()
            with redirect_stderr(buf):
                warn_if_unavailable()
            text = buf.getvalue()
            self.assertIn("pip install tmdx", text)
            self.assertNotIn("升级 Python", text)


class TestCliWarnIntegration(unittest.TestCase):
    """cli.main() 对除 -h/--help/-v/--version 外的所有命令调用 warn_if_unavailable。"""

    def test_warn_called_for_command(self):
        import dragon_quant.cli as cli
        with patch.object(cli, "warn_if_unavailable") as mock_warn, \
             patch.object(cli, "_cmd_blacklist", return_value=None), \
             patch("sys.argv", ["dragon-quant", "blacklist", "list"]):
            cli.main()
        mock_warn.assert_called_once()

    def test_warn_not_called_for_help(self):
        import dragon_quant.cli as cli
        with patch.object(cli, "warn_if_unavailable") as mock_warn, \
             patch("sys.argv", ["dragon-quant", "--help"]), \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main()
        mock_warn.assert_not_called()

    def test_warn_not_called_for_version(self):
        import dragon_quant.cli as cli
        with patch.object(cli, "warn_if_unavailable") as mock_warn, \
             patch("sys.argv", ["dragon-quant", "--version"]), \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main()
        mock_warn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
