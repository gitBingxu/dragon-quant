"""入选后表现纯计算服务单测（无 DB / 无 provider / 无时钟）。

口径：宽松入场（入选后首个 ``high != low`` 日按最低价买入），窗口从买入日
到 ``as_of`` 的最新有效日 K，与固定 5 日 ``_window_metrics`` 完全隔离。
"""

from __future__ import annotations

import math

import pytest

from dragon_quant.review.main import _is_valid_bar
from dragon_quant.review.performance import (
    HISTORY_SHORT_REASON,
    compute_performance,
    is_valid_daily_bar,
)

TRADE_DATE = "2026-08-17"
AS_OF = "2026-08-25"


def bar(day: str, open_px: float, high: float, low: float, close: float,
        volume: float = 1000.0) -> dict:
    return {
        "date": day,
        "open": open_px,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "pct": 0.0,
    }


def _baseline() -> list[dict]:
    return [
        bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        # 入选后首个交易日一字板，不可介入。
        bar("2026-08-18", 11.0, 11.0, 11.0, 11.0),
        # 首个可介入日：买入价 = low = 10.5。
        bar("2026-08-19", 10.8, 11.0, 10.5, 10.9),
        bar("2026-08-20", 11.0, 11.8, 10.4, 11.6),
        bar("2026-08-21", 11.6, 12.0, 11.2, 11.9),
        # 峰值之后的新低不进入回撤窗口。
        bar("2026-08-24", 11.0, 11.1, 9.0, 9.2),
        bar("2026-08-25", 9.2, 9.4, 9.1, 9.3),
    ]


def test_entry_is_first_tradable_day_at_low_price() -> None:
    result = compute_performance(_baseline(), TRADE_DATE, AS_OF)

    assert result["status"] == "ok"
    assert result["buy_date"] == "2026-08-19"
    assert result["buy_price"] == 10.5
    assert result["peak_date"] == "2026-08-21"
    assert result["latest_bar_date"] == "2026-08-25"


def test_max_return_uses_bars_after_entry_only() -> None:
    klines = [
        bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        # 买入日自身高点 12.0 不计入最大收益。
        bar("2026-08-18", 10.0, 12.0, 10.0, 11.0),
        bar("2026-08-19", 10.5, 11.0, 10.2, 10.8),
    ]

    result = compute_performance(klines, TRADE_DATE, "2026-08-19")

    assert result["buy_date"] == "2026-08-18"
    assert result["peak_date"] == "2026-08-19"
    assert result["max_return_since_entry"] == 10.0
    assert result["max_drawdown_since_entry"] == 0.0


def test_zero_drawdown_never_serializes_as_negative_zero() -> None:
    """微小负回撤 round 到 0 时必须是 +0.0，避免 UI 显示 -0.00%。"""

    klines = [
        bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        # 买入日 low = 10.0。
        bar("2026-08-18", 10.2, 10.5, 10.0, 10.4),
        # 峰值前低点比买入价低 1e-9：/10*100 后 round(,2) 会得到 -0.0。
        bar("2026-08-19", 10.4, 11.0, 9.999999999, 10.9),
    ]

    result = compute_performance(klines, TRADE_DATE, "2026-08-19")

    value = result["max_drawdown_since_entry"]
    assert value == 0.0
    assert math.copysign(1.0, value) == 1.0


def test_peak_tie_takes_earliest_date() -> None:
    klines = [
        bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        bar("2026-08-18", 10.0, 10.2, 9.8, 10.0),
        bar("2026-08-19", 10.0, 12.0, 9.9, 11.9),
        bar("2026-08-20", 12.0, 12.0, 11.8, 12.0),
    ]

    result = compute_performance(klines, TRADE_DATE, "2026-08-20")

    assert result["peak_date"] == "2026-08-19"


def test_drawdown_window_ends_at_peak_and_includes_buy_day() -> None:
    klines = [
        bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        # 买入日 low = 10.0；峰值前的新低 9.9 进入回撤窗口。
        bar("2026-08-18", 10.2, 10.6, 10.0, 10.5),
        bar("2026-08-19", 10.4, 12.0, 9.9, 11.8),
        # 峰值之后的新低不进入回撤窗口。
        bar("2026-08-20", 11.0, 11.2, 5.0, 5.2),
    ]

    result = compute_performance(klines, TRADE_DATE, "2026-08-20")

    assert result["max_return_since_entry"] == 20.0
    assert result["max_drawdown_since_entry"] == -1.0


def test_post_peak_low_is_ignored_in_drawdown() -> None:
    klines = [
        bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        # 买入日 low = 10.0。
        bar("2026-08-18", 10.2, 10.6, 10.0, 10.5),
        # 峰值前的新低 9.5 进入回撤窗口。
        bar("2026-08-19", 10.4, 10.9, 9.5, 10.6),
        # 峰值日 high = 12.0。
        bar("2026-08-20", 10.8, 12.0, 10.4, 11.8),
        # 峰值后的新低 5.0 不计入。
        bar("2026-08-21", 11.0, 11.2, 5.0, 5.2),
    ]

    result = compute_performance(klines, TRADE_DATE, "2026-08-21")

    # 回撤窗口 = 买入日 → 峰值日；峰值后的 5.0 不计入。
    assert result["peak_date"] == "2026-08-20"
    assert result["max_drawdown_since_entry"] == -5.0


def test_no_entry_skips_continuous_one_word_limits() -> None:
    klines = [
        bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        bar("2026-08-18", 11.0, 11.0, 11.0, 11.0),
        bar("2026-08-19", 12.1, 12.1, 12.1, 12.1),
    ]

    result = compute_performance(klines, TRADE_DATE, AS_OF)

    assert result["status"] == "no_entry"
    assert result["buy_date"] is None and result["buy_price"] is None
    assert result["peak_date"] is None
    assert result["max_return_since_entry"] is None
    assert result["max_drawdown_since_entry"] is None
    assert result["latest_bar_date"] == "2026-08-19"
    assert result["bars_in_window"] == 0
    assert result["bars_after_entry"] == 0


def test_empty_window_when_as_of_equals_buy_date() -> None:
    klines = [
        bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        bar("2026-08-18", 10.5, 11.0, 10.2, 10.8),
    ]

    result = compute_performance(klines, TRADE_DATE, "2026-08-18")

    assert result["status"] == "empty_window"
    assert result["buy_date"] == "2026-08-18"
    assert result["buy_price"] == 10.2
    assert result["latest_bar_date"] == "2026-08-18"
    assert result["peak_date"] is None
    assert result["max_return_since_entry"] is None
    assert result["max_drawdown_since_entry"] is None
    assert result["bars_in_window"] == 1
    assert result["bars_after_entry"] == 0


def test_empty_klines_is_error() -> None:
    result = compute_performance([], TRADE_DATE, AS_OF)

    assert result["status"] == "error"
    assert result["error"] == "无K线数据"
    assert result["buy_date"] is None


def test_history_not_covering_trade_date_is_explicit_error() -> None:
    klines = [
        bar("2026-08-20", 10.0, 10.2, 9.8, 10.0),
        bar("2026-08-21", 10.0, 10.5, 9.9, 10.4),
    ]

    result = compute_performance(klines, TRADE_DATE, AS_OF)

    assert result["status"] == "error"
    assert result["error"] == HISTORY_SHORT_REASON


def test_invalid_bars_are_filtered_before_entry_search() -> None:
    klines = [
        bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        # 负价、high < low、OHLC 越界、零成交平盘占位均无效。
        bar("2026-08-18", -1.0, 11.0, 10.0, 10.5),
        bar("2026-08-19", 10.0, 9.0, 9.5, 9.8),
        bar("2026-08-20", 10.5, 11.0, 10.0, 12.0),
        bar("2026-08-21", 10.0, 10.0, 10.0, 10.0, volume=0.0),
        bar("2026-08-24", 10.5, 11.0, 10.2, 10.8),
    ]

    result = compute_performance(klines, TRADE_DATE, AS_OF)

    assert result["buy_date"] == "2026-08-24"
    assert result["buy_price"] == 10.2
    assert result["latest_bar_date"] == "2026-08-24"


def test_future_bars_beyond_as_of_are_dropped() -> None:
    klines = [
        bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        bar("2026-08-18", 10.5, 11.0, 10.2, 10.8),
        bar("2026-08-19", 10.8, 11.0, 10.6, 10.9),
        bar("2026-08-25", 11.0, 50.0, 10.9, 49.0),
    ]

    result = compute_performance(klines, TRADE_DATE, "2026-08-20")

    assert result["status"] == "ok"
    assert result["latest_bar_date"] == "2026-08-19"
    assert result["max_return_since_entry"] == 7.84  # 未来 bar 不参与
    assert result["bars_after_entry"] == 1


def test_as_of_before_trade_date_raises() -> None:
    with pytest.raises(ValueError):
        compute_performance(_baseline(), TRADE_DATE, "2026-08-16")


def test_valid_bar_parity_with_review_main() -> None:
    samples = [
        bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        bar("2026-08-18", -1.0, 11.0, 10.0, 10.5),
        bar("2026-08-19", 10.0, 9.0, 9.5, 9.8),
        bar("2026-08-20", 10.5, 11.0, 10.0, 12.0),
        bar("2026-08-21", 10.0, 10.0, 10.0, 10.0, volume=0.0),
        bar("2026-08-24", 10.5, 11.0, 10.2, 10.8),
        {"date": "2026-08-25"},
        "not-a-bar",
    ]

    for sample in samples:
        assert is_valid_daily_bar(sample) == _is_valid_bar(sample)


def test_contract_has_no_fixed_five_day_keys() -> None:
    result = compute_performance(_baseline(), TRADE_DATE, AS_OF)

    assert "max_return_5d" not in result
    assert "max_drawdown_5d" not in result
    assert set(result) == {
        "caliber",
        "status",
        "as_of",
        "buy_date",
        "buy_price",
        "max_return_since_entry",
        "max_drawdown_since_entry",
        "peak_date",
        "latest_bar_date",
        "bars_in_window",
        "bars_after_entry",
        "error",
    }
    assert result["caliber"] == "loose"
    assert result["as_of"] == AS_OF
