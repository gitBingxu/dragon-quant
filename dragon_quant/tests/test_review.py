"""
tests for dragon_quant.review — 龙头回测验证
"""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

from dragon_quant.infra.models import KBar
from dragon_quant.review import review_dragon, run_review

ROOT = Path(__file__).resolve().parents[2]


class TestReviewPackageFacade(unittest.TestCase):
    """Task 12: ``review.py`` is now the ``review`` package + facade proxy."""

    def test_review_package_replaces_module_and_keeps_patch_surface(self):
        import dragon_quant.review as review
        import dragon_quant.review.main as main

        assert not (ROOT / "dragon_quant" / "review.py").exists()
        assert review.run_review.__module__ == "dragon_quant.review.main"
        assert review.run_review is main.run_review
        assert review.review_dragon is main.review_dragon
        assert review.build_trade_calendar is main.build_trade_calendar
        assert review.XueqiuProvider is main.XueqiuProvider

    def test_review_package_proxy_forwards_patch_to_main(self):
        import dragon_quant.review as review
        import dragon_quant.review.main as main

        sentinel = object()
        with patch.object(review, "build_trade_calendar", sentinel):
            assert main.build_trade_calendar is sentinel
            assert review.build_trade_calendar is sentinel
        assert review.build_trade_calendar is main.build_trade_calendar

    def test_review_main_uses_explicit_store_names(self):
        """No ambiguous ``db`` alias: review/main names its owners explicitly."""
        import dragon_quant.review.main as main
        from dragon_quant.scan.storage import dragons as dragon_store
        from dragon_quant.vpa import storage as vpa_store

        assert main.dragon_store is dragon_store
        assert main.vpa_store is vpa_store
        assert not hasattr(main, "db")


# ── helpers ──────────────────────────────────────────────────────────

def _mk_kbar(date_str: str, open_px: float, close: float, high: float,
             low: float, pct: float, vol: float = 1e6) -> KBar:
    """工厂：给定日期字符串创建一根日K。"""
    ts = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
    return KBar(
        timestamp=ts, open=open_px, close=close,
        high=high, low=low, pct=pct, chg=0.0,
        volume=vol, turnover=3.0, amount=1000,
    )


# ── review_dragon ────────────────────────────────────────────────────

class TestReviewDragon(unittest.TestCase):
    """review_dragon() 核心回测逻辑"""

    def setUp(self):
        # 通用日历：05-19 到 06-02 全是交易日
        self._calendar = {
            "2026-05-19", "2026-05-20", "2026-05-21",
            "2026-05-22", "2026-05-25", "2026-05-26", "2026-05-27",
            "2026-05-28", "2026-05-29", "2026-06-01", "2026-06-02",
        }

    # ── 成功路径 ──

    def test_review_completed_calculation(self):
        """最大回撤只统计到最大收益出现日，不含峰值后的更低点"""
        trade_date = "2026-05-19"

        klines = [
            _mk_kbar("2026-05-18", 10.0, 11.0, 11.0, 10.0, 10.0),
            _mk_kbar("2026-05-19", 11.0, 12.1, 12.1, 10.8, 10.0),
            _mk_kbar("2026-05-20", 12.5, 13.0, 13.5, 11.5, 7.5),
            _mk_kbar("2026-05-21", 13.0, 14.0, 14.5, 12.5, 7.7),
            _mk_kbar("2026-05-22", 14.0, 13.0, 14.0, 11.8, -7.1),
            _mk_kbar("2026-05-25", 13.0, 15.0, 16.0, 12.8, 15.4),
            _mk_kbar("2026-05-26", 15.0, 14.0, 15.5, 10.5, -6.7),
            _mk_kbar("2026-05-27", 14.0, 14.5, 14.8, 13.8, 3.6),
        ]

        mock_provider = MagicMock()
        mock_provider.get_kline.return_value = klines

        with patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=self._calendar):
            result = review_dragon("000001", trade_date, provider=mock_provider)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["buy_date"], "2026-05-20")
        self.assertEqual(result["buy_price"], 11.5)
        # 峰值出现在 2026-05-25，峰值后 2026-05-26 的更低点 10.5 不应计入回撤
        expected_return = round((16.0 - 11.5) / 11.5 * 100, 2)
        expected_drawdown = 0.0
        self.assertAlmostEqual(result["max_return_5d"], expected_return, places=1)
        self.assertAlmostEqual(result["max_drawdown_5d"], expected_drawdown, places=1)
        self.assertEqual(result["max_return_hold_days"], 3)

    def test_review_buy_date_filter_5_days(self):
        """只用买入后 5 个交易日，不会多用"""
        trade_date = "2026-05-22"

        klines = [
            _mk_kbar("2026-05-21", 9.0, 9.9, 9.9, 8.9, 10.0),
            _mk_kbar("2026-05-22", 9.9, 10.89, 10.89, 9.9, 10.0),
            _mk_kbar("2026-05-25", 11.0, 10.0, 11.0, 9.5, -8.2),
            _mk_kbar("2026-05-26", 10.0, 11.0, 12.0, 10.0, 10.0),
            _mk_kbar("2026-05-27", 11.0, 10.5, 11.0, 10.5, -4.5),
            _mk_kbar("2026-05-28", 10.5, 9.5, 10.5, 9.0, -9.5),
            _mk_kbar("2026-05-29", 9.5, 10.0, 10.5, 9.5, 5.3),
            _mk_kbar("2026-06-01", 10.0, 10.5, 11.0, 9.8, 5.0),
            _mk_kbar("2026-06-02", 10.5, 12.0, 13.0, 10.5, 14.3),
        ]

        mock_provider = MagicMock()
        mock_provider.get_kline.return_value = klines

        with patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=self._calendar):
            result = review_dragon("000001", trade_date, provider=mock_provider)

        self.assertEqual(result["status"], "completed")
        # 收益窗口实际为买入后第 2~5 个交易日：05-27/05-28/05-29/06-01
        # 因此不会使用 06-02 的 high=13.0；峰值日为 05-27，回撤窗口只统计到 05-27
        expected_return = round((11.0 - 9.5) / 9.5 * 100, 2)
        expected_drawdown = 0.0
        self.assertAlmostEqual(result["max_return_5d"], expected_return, places=1)
        self.assertAlmostEqual(result["max_drawdown_5d"], expected_drawdown, places=1)
        self.assertEqual(result["max_return_hold_days"], 2)

    def test_review_drawdown_includes_t1_low_before_peak(self):
        """最大回撤应覆盖买入日至峰值日，包含被收益窗口跳过的 T+1 低点"""
        trade_date = "2026-05-19"

        klines = [
            _mk_kbar("2026-05-19", 9.0, 9.9, 9.9, 8.9, 10.0),
            _mk_kbar("2026-05-20", 10.0, 10.8, 11.5, 10.0, 9.1),   # 买入日 @ 10.0
            _mk_kbar("2026-05-21", 10.8, 9.0, 9.5, 8.5, -16.7),    # T+1，更低低点，应计入回撤
            _mk_kbar("2026-05-22", 9.0, 11.5, 12.0, 10.2, 27.8),
            _mk_kbar("2026-05-25", 11.5, 13.5, 14.0, 10.8, 17.4),  # 峰值日
            _mk_kbar("2026-05-26", 13.5, 8.5, 13.6, 7.5, -37.0),   # 峰值后新低，不应计入回撤
            _mk_kbar("2026-05-27", 8.5, 9.0, 9.5, 8.0, 5.9),
        ]

        mock_provider = MagicMock()
        mock_provider.get_kline.return_value = klines

        with patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=self._calendar):
            result = review_dragon("000001", trade_date, provider=mock_provider)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["buy_date"], "2026-05-20")
        self.assertEqual(result["buy_price"], 10.0)
        self.assertEqual(result["max_return_hold_days"], 3)
        expected_return = round((14.0 - 10.0) / 10.0 * 100, 2)
        expected_drawdown = round((8.5 - 10.0) / 10.0 * 100, 2)
        self.assertAlmostEqual(result["max_return_5d"], expected_return, places=1)
        self.assertAlmostEqual(result["max_drawdown_5d"], expected_drawdown, places=1)

    def test_review_multiple_limit_up_skipped(self):
        """连续一字板跳过，找到第一个可介入日（high!=low）"""
        trade_date = "2026-05-20"

        klines = [
            _mk_kbar("2026-05-19", 10.0, 11.0, 11.0, 10.0, 10.0),
            _mk_kbar("2026-05-20", 11.0, 12.1, 12.1, 11.0, 10.0),
            _mk_kbar("2026-05-21", 12.1, 13.31, 13.31, 13.31, 10.0),  # 一字板 high==low，跳过
            _mk_kbar("2026-05-22", 13.5, 14.0, 14.5, 12.0, 5.0),     # 可介入
            _mk_kbar("2026-05-25", 14.0, 15.0, 15.0, 13.0, 7.1),
        ]

        mock_provider = MagicMock()
        mock_provider.get_kline.return_value = klines

        with patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=self._calendar):
            result = review_dragon("000001", trade_date, provider=mock_provider)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["buy_date"], "2026-05-22")
        self.assertEqual(result["buy_price"], 12.0)

    # ── 边界与错误路径 ──

    def test_review_no_entry_day(self):
        """全是一字板（high==low） → no_entry"""
        trade_date = "2026-05-25"

        klines = [
            _mk_kbar("2026-05-25", 10.0, 11.0, 11.0, 11.0, 10.0),
            _mk_kbar("2026-05-26", 11.0, 12.1, 12.1, 12.1, 10.0),
            _mk_kbar("2026-05-27", 12.1, 13.31, 13.31, 13.31, 10.0),
        ]

        mock_provider = MagicMock()
        mock_provider.get_kline.return_value = klines

        with patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=self._calendar):
            result = review_dragon("000001", trade_date, provider=mock_provider)

        self.assertEqual(result["status"], "no_entry")
        self.assertIsNone(result["buy_date"])
        self.assertIsNone(result["buy_price"])

    def test_review_provider_raises_error(self):
        """Provider 抛出异常 → error"""
        mock_provider = MagicMock()
        mock_provider.get_kline.side_effect = RuntimeError("网络超时")

        result = review_dragon("000001", "2026-05-19", provider=mock_provider)

        self.assertEqual(result["status"], "error")
        self.assertIn("网络超时", result["error"])

    def test_review_empty_klines(self):
        """Provider 返回空列表 → error"""
        mock_provider = MagicMock()
        mock_provider.get_kline.return_value = []

        result = review_dragon("000001", "2026-05-19", provider=mock_provider)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "无K线数据")

    def test_review_no_future_dates(self):
        """买入后 0 个交易日 → 收益/回撤为 0"""
        trade_date = "2026-05-20"

        klines = [
            _mk_kbar("2026-05-19", 10.0, 11.0, 11.0, 10.0, 10.0),
            _mk_kbar("2026-05-20", 11.0, 12.1, 12.1, 11.0, 10.0),
            _mk_kbar("2026-05-21", 12.0, 13.0, 13.0, 11.0, 8.3),
        ]

        mock_provider = MagicMock()
        mock_provider.get_kline.return_value = klines
        empty_calendar = {"2026-05-20", "2026-05-21"}

        with patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=empty_calendar):
            result = review_dragon("000001", trade_date, provider=mock_provider)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["max_return_5d"], 0)
        self.assertEqual(result["max_drawdown_5d"], 0)

    def test_review_klines_no_future_match(self):
        """K 线数据中没有覆盖买入后的交易日 → 收益/回撤为 0"""
        trade_date = "2026-05-27"

        klines = [
            _mk_kbar("2026-05-26", 10.0, 11.0, 11.0, 10.0, 10.0),
            _mk_kbar("2026-05-27", 11.0, 12.1, 12.1, 11.0, 10.0),
            _mk_kbar("2026-05-28", 12.0, 13.0, 13.5, 11.5, 8.3),
        ]

        mock_provider = MagicMock()
        mock_provider.get_kline.return_value = klines

        with patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=self._calendar):
            result = review_dragon("000001", trade_date, provider=mock_provider)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["max_return_5d"], 0)
        self.assertEqual(result["max_drawdown_5d"], 0)

    def test_review_one_word_board_skipped(self):
        """一字板（high==low）被跳过，找到下一个可介入日"""
        trade_date = "2026-05-20"

        klines = [
            _mk_kbar("2026-05-19", 10.0, 11.0, 11.0, 10.0, 10.0),
            _mk_kbar("2026-05-20", 11.0, 11.0, 11.0, 11.0, 0.0),     # 一字板 trade_date
            _mk_kbar("2026-05-21", 11.0, 11.0, 11.0, 11.0, 0.0),     # 一字板，跳过
            _mk_kbar("2026-05-22", 11.0, 10.0, 11.5, 9.0, -9.1),     # 可介入 @ low=9.0
        ]

        mock_provider = MagicMock()
        mock_provider.get_kline.return_value = klines

        with patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=self._calendar):
            result = review_dragon("000001", trade_date, provider=mock_provider)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["buy_date"], "2026-05-22")
        self.assertEqual(result["buy_price"], 9.0)

    def test_review_symbol_used_in_kline_call(self):
        """验证 code 参数正确传递给 provider.get_kline"""
        mock_provider = MagicMock()
        mock_provider.get_kline.return_value = [
            _mk_kbar("2026-05-20", 10.0, 10.5, 10.5, 9.8, -5.0),
        ]

        with patch("dragon_quant.review.main.build_trade_calendar",
                   return_value={"2026-05-20"}):
            review_dragon("600172", "2026-05-19", provider=mock_provider)

        call = mock_provider.get_kline.call_args
        assert call.args == ("600172",)
        assert call.kwargs["fq_type"] == "normal"
        assert call.kwargs["days"] >= 20


# ── run_review ───────────────────────────────────────────────────────

class TestRunReview(unittest.TestCase):
    """run_review() 批量回测"""

    def setUp(self):
        self._calendar = {
            "2026-05-19", "2026-05-20", "2026-05-21",
            "2026-05-22", "2026-05-25", "2026-05-26", "2026-05-27",
        }

    def test_run_review_no_pending_empty_list(self):
        """无待回测记录 → 返回 []"""
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=[]):
            results = run_review(verbose=False)
        self.assertEqual(results, [])

    def test_run_review_single_dragon(self):
        """单条 dragon 记录正常回测"""
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons") as mock_pending, \
             patch("dragon_quant.review.main.dragon_store.save_dual_review") as mock_update, \
             patch("dragon_quant.review.main.XueqiuProvider") as mock_provider_cls, \
             patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=self._calendar):

            mock_pending.return_value = [{
                "code": "000001", "name": "平安银行", "trade_date": "2026-05-19",
            }]
            mock_provider = MagicMock()
            mock_provider_cls.return_value = mock_provider
            mock_provider.get_kline.return_value = [
                _mk_kbar("2026-05-18", 10.0, 11.0, 11.0, 10.0, 10.0),
                _mk_kbar("2026-05-19", 11.0, 12.1, 12.1, 11.0, 10.0),
                _mk_kbar("2026-05-20", 12.5, 13.0, 13.5, 11.5, 7.5),
                _mk_kbar("2026-05-21", 13.0, 14.0, 14.5, 12.5, 7.7),
                _mk_kbar("2026-05-22", 14.0, 13.0, 14.0, 11.8, -7.1),
                _mk_kbar("2026-05-25", 13.0, 15.0, 16.0, 12.8, 15.4),
                _mk_kbar("2026-05-26", 15.0, 14.0, 15.5, 10.5, -6.7),
                _mk_kbar("2026-05-27", 14.0, 14.5, 14.8, 13.8, 3.6),
            ]

            results = run_review(trade_date="2026-05-19", verbose=False)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(results[0]["buy_price"], 11.5)
        mock_update.assert_called_once()

    def test_run_review_filter_5_to_20_days(self):
        """自动筛选：跳过不在 5~20 交易日窗口的记录"""
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons") as mock_pending, \
             patch("dragon_quant.review.main.dragon_store.save_dual_review"), \
             patch("dragon_quant.review.main.XueqiuProvider") as mock_provider_cls, \
             patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=self._calendar):

            mock_pending.return_value = [
                {"code": "000001", "name": "平安银行", "trade_date": "2026-05-26"},  # 太新，跳过
                {"code": "600172", "name": "黄河旋风", "trade_date": "2026-05-19"},  # 在窗口内
            ]
            mock_provider = MagicMock()
            mock_provider_cls.return_value = mock_provider
            # 给足够 K 线让 review_dragon 能跑
            mock_provider.get_kline.return_value = [
                _mk_kbar("2026-05-18", 10.0, 11.0, 11.0, 10.0, 10.0),
                _mk_kbar("2026-05-19", 11.0, 12.1, 12.1, 10.8, 10.0),
                _mk_kbar("2026-05-20", 12.5, 13.0, 13.5, 11.5, 7.5),
                _mk_kbar("2026-05-21", 13.0, 14.0, 14.5, 12.5, 7.7),
                _mk_kbar("2026-05-22", 14.0, 13.0, 14.0, 11.8, -7.1),
                _mk_kbar("2026-05-25", 13.0, 15.0, 16.0, 12.8, 15.4),
                _mk_kbar("2026-05-26", 15.0, 14.0, 15.5, 10.5, -6.7),
                _mk_kbar("2026-05-27", 14.0, 14.5, 14.8, 13.8, 3.6),
            ]

            results = run_review(verbose=False)

        # 只保留 05-19（在 5~20 窗口），05-26 被筛掉
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["code"], "600172")

    def test_run_review_force_passes_through(self):
        """force=True 时传 review_status=None，不传 status 过滤"""
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=[]) as mock_pending, \
             patch("dragon_quant.review.main.XueqiuProvider") as mock_provider_cls, \
             patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=self._calendar):
            mock_provider = MagicMock()
            mock_provider_cls.return_value = mock_provider
            mock_provider.get_kline.return_value = []
            run_review(force=True, verbose=False)
        mock_pending.assert_called_once_with(
            trade_date=None, top_n=None, review_status=None, source="v2",
        )

    def test_run_review_source_passes_through(self):
        """source 参数透传到 pending 查询和回测结果更新。"""
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons") as mock_pending, \
             patch("dragon_quant.review.main.dragon_store.save_dual_review") as mock_update, \
             patch("dragon_quant.review.main.XueqiuProvider") as mock_provider_cls:
            mock_pending.return_value = [{
                "code": "000001", "name": "平安银行", "trade_date": "2026-05-19",
            }]
            mock_provider = MagicMock()
            mock_provider_cls.return_value = mock_provider
            mock_provider.get_kline.return_value = [
                _mk_kbar("2026-05-18", 10.0, 11.0, 11.0, 10.0, 10.0),
                _mk_kbar("2026-05-19", 11.0, 12.1, 12.1, 11.0, 10.0),
                _mk_kbar("2026-05-20", 12.5, 13.0, 13.5, 11.5, 7.5),
            ]

            run_review(trade_date="2026-05-19", source="v2", verbose=False)

        mock_pending.assert_called_once_with(trade_date="2026-05-19", top_n=None, source="v2")
        self.assertEqual(mock_update.call_args.kwargs["source"], "v2")

    def test_run_review_force_still_filters_window(self):
        """force=True 时仍过滤 5~20 交易日窗口"""
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons") as mock_pending, \
             patch("dragon_quant.review.main.dragon_store.save_dual_review"), \
             patch("dragon_quant.review.main.XueqiuProvider") as mock_provider_cls, \
             patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=self._calendar):

            mock_pending.return_value = [
                {"code": "000001", "name": "平安银行", "trade_date": "2026-05-26"},  # 太新
                {"code": "600172", "name": "黄河旋风", "trade_date": "2026-05-19"},  # 窗口内
            ]
            mock_provider = MagicMock()
            mock_provider_cls.return_value = mock_provider
            mock_provider.get_kline.return_value = [
                _mk_kbar("2026-05-18", 10.0, 11.0, 11.0, 10.0, 10.0),
                _mk_kbar("2026-05-19", 11.0, 12.1, 12.1, 10.8, 10.0),
                _mk_kbar("2026-05-20", 12.5, 13.0, 13.5, 11.5, 7.5),
                _mk_kbar("2026-05-21", 13.0, 14.0, 14.5, 12.5, 7.7),
                _mk_kbar("2026-05-22", 14.0, 13.0, 14.0, 11.8, -7.1),
                _mk_kbar("2026-05-25", 13.0, 15.0, 16.0, 12.8, 15.4),
                _mk_kbar("2026-05-26", 15.0, 14.0, 15.5, 10.5, -6.7),
                _mk_kbar("2026-05-27", 14.0, 14.5, 14.8, 13.8, 3.6),
            ]

            results = run_review(force=True, verbose=False)

        # force 仍会过滤掉 05-26，只保留 05-19
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["code"], "600172")

    def test_run_review_window_uses_the_runtime_clock(self):
        """M9A: the 5~20 trading-day window is anchored on ``runtime.clock``."""
        from datetime import timedelta, timezone

        from dragon_quant.infra import clock as runtime_clock

        self.addCleanup(runtime_clock.reset_clock)
        runtime_clock.set_clock(runtime_clock.FixedClock(
            datetime(2026, 5, 30, 15, 30,
                     tzinfo=timezone(timedelta(hours=8)))))

        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=[{"code": "600172", "name": "黄河旋风",
                                  "trade_date": "2026-05-19"}]), \
             patch("dragon_quant.review.main.dragon_store.save_dual_review"), \
             patch("dragon_quant.review.main.XueqiuProvider") as mock_provider_cls, \
             patch("dragon_quant.review.main.build_trade_calendar",
                   return_value=set()) as calendar:
            mock_provider_cls.return_value.get_kline.return_value = []
            run_review(verbose=False)

        call = calendar.call_args
        assert call.args == ("2026-04-15", "2026-05-30")
        assert call.kwargs["days"] >= 90



class TestRunReviewServicesInjection(unittest.TestCase):
    """M2A: run_review consumes a supplied provider bundle without constructing XueqiuProvider."""

    def setUp(self):
        self._calendar = {"2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"}

    def _klines(self):
        return [
            _mk_kbar("2026-05-18", 10.0, 11.0, 11.0, 10.0, 10.0),
            _mk_kbar("2026-05-19", 11.0, 12.1, 12.1, 11.0, 10.0),
            _mk_kbar("2026-05-20", 12.5, 13.0, 13.5, 11.5, 7.5),
            _mk_kbar("2026-05-21", 13.0, 14.0, 14.5, 12.5, 7.7),
        ]

    def _services(self, xueqiu):
        from unittest.mock import MagicMock

        from dragon_quant.app import build_services
        from dragon_quant.infra.provider.bundle import ProviderBundle

        bundle = ProviderBundle(
            tdx=MagicMock(name="tdx"), ths=MagicMock(name="ths"),
            xueqiu=xueqiu, tencent=MagicMock(name="tencent"),
        )
        return build_services(providers=bundle)

    def test_supplied_bundle_provider_is_used_for_kline_loading(self):
        xueqiu = MagicMock()
        xueqiu.get_kline.return_value = self._klines()

        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons") as mock_pending, \
                patch("dragon_quant.review.main.dragon_store.save_dual_review") as mock_update, \
                patch("dragon_quant.review.main.XueqiuProvider") as provider_cls, \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar):
            mock_pending.return_value = [{
                "code": "000001", "name": "平安银行", "trade_date": "2026-05-19",
            }]
            results = run_review(trade_date="2026-05-19", verbose=False,
                                 services=self._services(xueqiu))

        provider_cls.assert_not_called()
        call = xueqiu.get_kline.call_args
        assert call.args == ("000001",)
        assert call.kwargs["fq_type"] == "normal"
        assert call.kwargs["days"] >= 20
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "completed")
        mock_update.assert_called_once()

    def test_explicit_provider_wins_over_services(self):
        explicit = MagicMock()
        explicit.get_kline.return_value = self._klines()
        bundled = MagicMock()

        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons") as mock_pending, \
                patch("dragon_quant.review.main.dragon_store.save_dual_review"), \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar):
            mock_pending.return_value = [{
                "code": "000001", "name": "平安银行", "trade_date": "2026-05-19",
            }]
            run_review(trade_date="2026-05-19", verbose=False,
                       provider=explicit, services=self._services(bundled))

        explicit.get_kline.assert_called_once()
        bundled.get_kline.assert_not_called()

    def test_no_pending_records_do_not_touch_supplied_providers(self):
        xueqiu = MagicMock()
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=[]), \
                patch("dragon_quant.review.main.XueqiuProvider") as provider_cls:
            results = run_review(verbose=False, services=self._services(xueqiu))

        self.assertEqual(results, [])
        provider_cls.assert_not_called()
        xueqiu.get_kline.assert_not_called()


# ── 双口径（严格：入选次日开盘价）────────────────────────────────────

class TestStrictReviewDragon(unittest.TestCase):
    """严格口径：入选后首个交易日按开盘价买入，不顺延。"""

    def setUp(self):
        self._calendar = {
            "2026-05-19", "2026-05-20", "2026-05-21",
            "2026-05-22", "2026-05-25", "2026-05-26",
        }

    def _run(self, trade_date, klines, calendar=None):
        return review_dragon(
            "600001", trade_date, klines=klines,
            calendar=calendar or self._calendar, as_of="2026-05-26")

    def test_strict_buys_next_day_open_while_loose_buys_low(self):
        klines = [
            _mk_kbar("2026-05-19", 10.0, 11.0, 11.0, 10.0, 10.0),
            _mk_kbar("2026-05-20", 10.5, 10.8, 11.0, 9.8, 5.0),
            _mk_kbar("2026-05-21", 10.9, 11.2, 11.5, 10.5, 3.0),
            _mk_kbar("2026-05-22", 11.2, 11.0, 11.6, 10.4, -2.0),
            _mk_kbar("2026-05-25", 11.0, 11.8, 12.0, 10.9, 7.0),
        ]
        result = self._run("2026-05-19", klines)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["buy_date"], "2026-05-20")
        self.assertAlmostEqual(result["buy_price"], 9.8)
        strict = result["strict"]
        self.assertEqual(strict["review_status"], "completed")
        self.assertEqual(strict["buy_date"], "2026-05-20")
        self.assertAlmostEqual(strict["buy_price"], 10.5)
        # 共享窗口：买入后第 2~5 个交易日（05-21/22/25）
        self.assertAlmostEqual(strict["max_return_5d"],
                               round((12.0 - 10.5) / 10.5 * 100, 2))
        self.assertAlmostEqual(strict["max_drawdown_5d"],
                               round((9.8 - 10.5) / 10.5 * 100, 2))
        self.assertEqual(strict["max_return_hold_days"], 3)

    def test_one_word_limit_up_bought_at_open_without_deferral(self):
        klines = [
            _mk_kbar("2026-05-19", 10.0, 11.0, 11.0, 10.0, 10.0),
            _mk_kbar("2026-05-20", 11.0, 11.0, 11.0, 11.0, 10.0),  # 一字涨停
            _mk_kbar("2026-05-21", 11.0, 11.0, 11.0, 11.0, 10.0),
            _mk_kbar("2026-05-22", 11.5, 12.0, 12.5, 11.0, 5.0),
            _mk_kbar("2026-05-25", 12.0, 12.5, 13.0, 11.8, 4.0),
        ]
        result = self._run("2026-05-19", klines)

        strict = result["strict"]
        self.assertEqual(strict["review_status"], "completed")
        self.assertEqual(strict["buy_date"], "2026-05-20")
        self.assertAlmostEqual(strict["buy_price"], 11.0)
        # 宽松口径不受影响：仍跳过一字板，找 05-22 的最低价。
        self.assertEqual(result["buy_date"], "2026-05-22")
        self.assertAlmostEqual(result["buy_price"], 11.0)

    def test_missing_next_day_bar_is_cannot_evaluate(self):
        klines = [
            _mk_kbar("2026-05-19", 10.0, 11.0, 11.0, 10.0, 10.0),
            # 05-20 缺失（停牌或数据缺失无法区分）
            _mk_kbar("2026-05-21", 11.0, 11.5, 11.8, 10.8, 5.0),
            _mk_kbar("2026-05-22", 11.4, 11.9, 12.0, 11.0, 4.0),
        ]
        result = self._run("2026-05-19", klines)

        strict = result["strict"]
        self.assertEqual(strict["review_status"], "cannot_evaluate")
        self.assertEqual(strict["reason"], "停牌或数据缺失无法区分")
        self.assertIsNone(strict["buy_date"])
        # 宽松口径仍可找到后续可介入日。
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["buy_date"], "2026-05-21")

    def test_zero_volume_flat_placeholder_is_cannot_evaluate(self):
        klines = [
            _mk_kbar("2026-05-19", 10.0, 11.0, 11.0, 10.0, 10.0),
            _mk_kbar("2026-05-20", 11.0, 11.0, 11.0, 11.0, 0.0, vol=0.0),
            _mk_kbar("2026-05-21", 11.2, 11.6, 11.8, 11.0, 5.0),
        ]
        result = self._run("2026-05-19", klines)
        self.assertEqual(result["strict"]["review_status"], "cannot_evaluate")
        self.assertEqual(result["strict"]["reason"],
                         "停牌或数据缺失无法区分")

    def test_invalid_ohlc_bar_is_cannot_evaluate(self):
        bar = _mk_kbar("2026-05-20", 11.0, 11.0, 10.5, 11.5, 5.0)  # high < low
        klines = [
            _mk_kbar("2026-05-19", 10.0, 11.0, 11.0, 10.0, 10.0),
            bar,
            _mk_kbar("2026-05-21", 11.2, 11.6, 11.8, 11.0, 5.0),
        ]
        result = self._run("2026-05-19", klines)
        self.assertEqual(result["strict"]["review_status"], "cannot_evaluate")

    def test_transport_failure_returns_no_strict_result(self):
        provider = MagicMock()
        provider.get_kline.side_effect = RuntimeError("网络超时")
        result = review_dragon("600001", "2026-05-19", provider=provider)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["strict"])

    def test_empty_window_is_cannot_evaluate_but_loose_keeps_zero_zero(self):
        calendar = {"2026-05-19", "2026-05-20"}
        klines = [
            _mk_kbar("2026-05-19", 10.0, 11.0, 11.0, 10.0, 10.0),
            _mk_kbar("2026-05-20", 10.5, 10.8, 11.0, 9.8, 5.0),
        ]
        result = self._run("2026-05-19", klines, calendar=calendar)
        # 严格：窗口为空 → cannot_evaluate
        self.assertEqual(result["strict"]["review_status"], "cannot_evaluate")
        # 宽松：旧 0/0 completed 契约不变
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["max_return_5d"], 0)
        self.assertEqual(result["max_drawdown_5d"], 0)

    def test_partial_window_is_reported(self):
        calendar = {"2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"}
        klines = [
            _mk_kbar("2026-05-19", 10.0, 11.0, 11.0, 10.0, 10.0),
            _mk_kbar("2026-05-20", 10.5, 10.8, 11.0, 9.8, 5.0),
            _mk_kbar("2026-05-21", 10.9, 11.2, 11.5, 10.5, 3.0),
            _mk_kbar("2026-05-22", 11.2, 11.0, 11.6, 10.4, -2.0),
        ]
        result = self._run("2026-05-19", klines, calendar=calendar)
        strict = result["strict"]
        self.assertEqual(strict["review_status"], "completed")
        self.assertEqual(strict["reason"], "partial_window")


# ── 批量：成熟度 / 聚合 / 重算 ──────────────────────────────────────

class TestRunReviewDualCaliber(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timedelta, timezone

        from dragon_quant.infra import clock as runtime_clock

        self.addCleanup(runtime_clock.reset_clock)
        runtime_clock.set_clock(runtime_clock.FixedClock(
            datetime(2026, 9, 11, 15, 30,
                     tzinfo=timezone(timedelta(hours=8)))))
        self._calendar = {
            d.strftime("%Y-%m-%d")
            for d in (
                datetime(2026, 7, 1) + timedelta(days=i) for i in range(80)
            )
            if d.weekday() < 5 and d.strftime("%Y-%m-%d") <= "2026-09-11"
        }

    def _entry(self, code, td, source="v2"):
        return {"code": code, "name": f"名字{code}", "trade_date": td,
                "source": source}

    def _klines(self):
        from datetime import date, timedelta as td
        bars = []
        current = date(2026, 6, 20)
        price = 10.0
        while current <= date(2026, 8, 10):
            if current.weekday() < 5:
                day = current.strftime("%Y-%m-%d")
                bars.append(_mk_kbar(day, price, price + 0.2, price + 0.3,
                                     price - 0.1, 1.0))
                price += 0.02
            current += td(days=1)
        return bars

    def _late_klines(self):
        return [
            _mk_kbar("2026-08-03", 10.0, 10.2, 10.3, 9.9, 1.0),
            _mk_kbar("2026-08-04", 10.2, 10.4, 10.6, 10.0, 1.0),
            _mk_kbar("2026-08-05", 10.4, 10.6, 10.8, 10.2, 1.0),
            _mk_kbar("2026-08-06", 10.6, 10.8, 11.0, 10.4, 1.0),
        ]

    def test_default_has_no_upper_age_cap_and_skips_recent(self):
        entries = [self._entry("600001", "2026-07-01"),
                   self._entry("600002", "2026-09-10")]
        provider = MagicMock()
        provider.get_kline.return_value = self._klines()
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=entries), \
                patch("dragon_quant.review.main.dragon_store.save_dual_review"), \
                patch("dragon_quant.review.main._run_vpa"), \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar):
            results = run_review(verbose=False, provider=provider)

        self.assertEqual([r["code"] for r in results], ["600001"])

    def test_klines_and_calendar_are_aggregated_once_per_code(self):
        entries = [self._entry("600001", "2026-07-01"),
                   self._entry("600001", "2026-07-10")]
        provider = MagicMock()
        provider.get_kline.return_value = self._klines()
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=entries), \
                patch("dragon_quant.review.main.dragon_store.save_dual_review"), \
                patch("dragon_quant.review.main._run_vpa"), \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar) as calendar:
            run_review(verbose=False, provider=provider)

        provider.get_kline.assert_called_once()
        calendar.assert_called_once()
        # 深度必须覆盖最老记录，而不是固定 20。
        self.assertGreaterEqual(
            provider.get_kline.call_args.kwargs["days"], 60)

    def test_recompute_all_dry_run_does_not_fetch_or_write(self):
        entries = [self._entry("600001", "2026-07-01")]
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=entries), \
                patch("dragon_quant.review.main.strict_store.list_keys",
                      return_value=set()), \
                patch("dragon_quant.review.main.dragon_store.save_dual_review") as save, \
                patch("dragon_quant.review.main.XueqiuProvider") as provider_cls:
            results = run_review(recompute_all=True, dry_run=True,
                                 verbose=False)

        self.assertEqual(results, [])
        provider_cls.assert_not_called()
        save.assert_not_called()

    def test_recompute_all_limit_takes_oldest_first(self):
        entries = [self._entry("600002", "2026-07-10"),
                   self._entry("600001", "2026-07-01")]
        provider = MagicMock()
        provider.get_kline.return_value = self._klines()
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=entries), \
                patch("dragon_quant.review.main.strict_store.list_keys",
                      return_value=set()), \
                patch("dragon_quant.review.main.dragon_store.save_dual_review") as save, \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar):
            run_review(recompute_all=True, limit=1, verbose=False,
                       provider=provider)

        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.kwargs["code"], "600001")

    def test_recompute_all_skips_existing_strict_unless_force(self):
        entries = [self._entry("600001", "2026-07-01"),
                   self._entry("600002", "2026-07-10")]
        provider = MagicMock()
        provider.get_kline.return_value = self._klines()
        existing = {("2026-07-01", "600001")}

        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=entries), \
                patch("dragon_quant.review.main.strict_store.list_keys",
                      return_value=existing), \
                patch("dragon_quant.review.main.dragon_store.save_dual_review") as save, \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar):
            run_review(recompute_all=True, verbose=False, provider=provider)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.kwargs["code"], "600002")

        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=entries), \
                patch("dragon_quant.review.main.strict_store.list_keys",
                      return_value=existing), \
                patch("dragon_quant.review.main.dragon_store.save_dual_review") as save, \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar):
            run_review(recompute_all=True, force=True, verbose=False,
                       provider=provider)
        self.assertEqual(save.call_count, 2)

    def test_recompute_all_does_not_run_vpa(self):
        entries = [self._entry("600001", "2026-07-01")]
        provider = MagicMock()
        provider.get_kline.return_value = self._klines()
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=entries), \
                patch("dragon_quant.review.main.strict_store.list_keys",
                      return_value=set()), \
                patch("dragon_quant.review.main.dragon_store.save_dual_review"), \
                patch("dragon_quant.review.main._run_vpa") as vpa, \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar):
            run_review(recompute_all=True, verbose=False, provider=provider)
        vpa.assert_not_called()

    def test_recompute_all_summary_counts_mixed_success_and_failure(self):
        import io
        from contextlib import redirect_stdout

        entries = [self._entry("600001", "2026-07-01"),
                   self._entry("600002", "2026-07-01"),
                   self._entry("600003", "2026-07-01")]
        provider = MagicMock()

        def _kline(code, **kwargs):
            if code == "600003":
                raise RuntimeError("网络超时")
            return self._klines()

        provider.get_kline.side_effect = _kline
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=entries), \
                patch("dragon_quant.review.main.strict_store.list_keys",
                      return_value=set()), \
                patch("dragon_quant.review.main.dragon_store.save_dual_review"), \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                run_review(recompute_all=True, verbose=True,
                           provider=provider)

        out = buffer.getvalue()
        assert "📊 重算完成: processed=3" in out
        assert "   completed=2" in out
        assert "   cannot_evaluate=0" in out
        assert "   failed=1" in out
        assert "   skipped=0" in out
        assert ("汇总校验: processed == completed + cannot_evaluate + failed"
                " -> True") in out

    def test_invalid_flag_combinations_raise(self):
        with self.assertRaises(ValueError):
            run_review(dry_run=True, verbose=False)
        with self.assertRaises(ValueError):
            run_review(limit=5, verbose=False)
        with self.assertRaises(ValueError):
            run_review(recompute_all=True, source="v1", verbose=False)
        with self.assertRaises(ValueError):
            run_review(recompute_all=True, trade_date="2026-07-01",
                       verbose=False)

    def test_v1_never_writes_strict(self):
        entries = [self._entry("600001", "2026-08-03", source="v1")]
        provider = MagicMock()
        provider.get_kline.return_value = self._klines()
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=entries), \
                patch("dragon_quant.review.main.dragon_store.save_dual_review") as save, \
                patch("dragon_quant.review.main._run_vpa"), \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar):
            run_review(trade_date="2026-08-03", source="v1",
                       verbose=False, provider=provider)

        self.assertEqual(save.call_count, 1)
        self.assertIsNone(save.call_args.kwargs["strict"])

    def test_recompute_all_error_does_not_overwrite(self):
        entries = [self._entry("600001", "2026-07-01")]
        provider = MagicMock()
        provider.get_kline.side_effect = RuntimeError("网络超时")
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=entries), \
                patch("dragon_quant.review.main.strict_store.list_keys",
                      return_value=set()), \
                patch("dragon_quant.review.main.dragon_store.save_dual_review") as save, \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar):
            results = run_review(recompute_all=True, verbose=False,
                                 provider=provider)

        self.assertEqual(results[0]["status"], "error")
        save.assert_not_called()

    def test_historical_short_kline_is_reported_without_guessing(self):
        entries = [self._entry("600001", "2026-07-01")]
        provider = MagicMock()
        # K 线只覆盖入选日之后 → 历史不足，绝不拿最近窗口冒充。
        provider.get_kline.return_value = self._late_klines()
        with patch("dragon_quant.review.main.dragon_store.get_pending_dragons",
                   return_value=entries), \
                patch("dragon_quant.review.main.strict_store.list_keys",
                      return_value=set()), \
                patch("dragon_quant.review.main.dragon_store.save_dual_review") as save, \
                patch("dragon_quant.review.main.build_trade_calendar",
                      return_value=self._calendar):
            results = run_review(recompute_all=True, verbose=False,
                                 provider=provider)

        self.assertEqual(results[0]["status"], "error")
        self.assertIn("历史数据不足", results[0]["error"])
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
