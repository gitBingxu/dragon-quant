"""
tests for dragon_quant.review — 龙头回测验证
"""
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime

from dragon_quant.models.types import KBar
from dragon_quant.review import review_dragon, run_review


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

        with patch("dragon_quant.review.build_trade_calendar",
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

        with patch("dragon_quant.review.build_trade_calendar",
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

        with patch("dragon_quant.review.build_trade_calendar",
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

        with patch("dragon_quant.review.build_trade_calendar",
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

        with patch("dragon_quant.review.build_trade_calendar",
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

        with patch("dragon_quant.review.build_trade_calendar",
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

        with patch("dragon_quant.review.build_trade_calendar",
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

        with patch("dragon_quant.review.build_trade_calendar",
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

        with patch("dragon_quant.review.build_trade_calendar",
                   return_value={"2026-05-20"}):
            review_dragon("600172", "2026-05-19", provider=mock_provider)

        mock_provider.get_kline.assert_called_once_with("600172", days=20, fq_type="normal")


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
        with patch("dragon_quant.review.db.get_pending_dragons",
                   return_value=[]):
            results = run_review(verbose=False)
        self.assertEqual(results, [])

    def test_run_review_single_dragon(self):
        """单条 dragon 记录正常回测"""
        with patch("dragon_quant.review.db.get_pending_dragons") as mock_pending, \
             patch("dragon_quant.review.db.update_dragon_review") as mock_update, \
             patch("dragon_quant.review.XueqiuProvider") as mock_provider_cls, \
             patch("dragon_quant.review.build_trade_calendar",
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
        with patch("dragon_quant.review.db.get_pending_dragons") as mock_pending, \
             patch("dragon_quant.review.db.update_dragon_review"), \
             patch("dragon_quant.review.XueqiuProvider") as mock_provider_cls, \
             patch("dragon_quant.review.build_trade_calendar",
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
        with patch("dragon_quant.review.db.get_pending_dragons",
                   return_value=[]) as mock_pending, \
             patch("dragon_quant.review.XueqiuProvider") as mock_provider_cls, \
             patch("dragon_quant.review.build_trade_calendar",
                   return_value=self._calendar):
            mock_provider = MagicMock()
            mock_provider_cls.return_value = mock_provider
            mock_provider.get_kline.return_value = []
            run_review(force=True, verbose=False)
        mock_pending.assert_called_once_with(
            trade_date=None, top_n=None, review_status=None, source="v1",
        )

    def test_run_review_source_passes_through(self):
        """source 参数透传到 pending 查询和回测结果更新。"""
        with patch("dragon_quant.review.db.get_pending_dragons") as mock_pending, \
             patch("dragon_quant.review.db.update_dragon_review") as mock_update, \
             patch("dragon_quant.review.XueqiuProvider") as mock_provider_cls:
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
        with patch("dragon_quant.review.db.get_pending_dragons") as mock_pending, \
             patch("dragon_quant.review.db.update_dragon_review"), \
             patch("dragon_quant.review.XueqiuProvider") as mock_provider_cls, \
             patch("dragon_quant.review.build_trade_calendar",
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


if __name__ == "__main__":
    unittest.main()
