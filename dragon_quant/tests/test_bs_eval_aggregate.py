"""聚合口径：触发率分母、partial 排除、双口径胜率、卖飞/卖得对优先级。"""

from __future__ import annotations

from dragon_quant.bs_eval import aggregate, strategy_doc


def row(**overrides):
    base = {
        "trade_date": "2026-09-01", "code": "600001", "name": "样本",
        "eval_status": "closed", "is_terminal": 1,
        "buy_date": "2026-09-02", "buy_rule_code": "buy_open_ma5_pullback",
        "buy_loose_price": 9.8, "buy_strict_price": 10.0,
        "buy_loose_max_return_5d": 6.0, "buy_loose_max_drawdown_5d": -4.0,
        "buy_strict_max_return_5d": 5.0, "buy_strict_max_drawdown_5d": -4.0,
        "buy_window_partial": 0,
        "sell_date": "2026-09-04", "sell_rule_code": "hard_stop_loss",
        "sell_max_return_5d": 1.0, "sell_max_drawdown_5d": -1.0,
        "sell_window_partial": 0,
    }
    base.update(overrides)
    return base


def test_status_counts_include_every_status_even_when_zero():
    summary = aggregate.summarize([])
    assert summary["rows_total"] == 0
    assert summary["status_counts"] == {
        "pending": 0, "no_signal": 0, "open": 0, "closed": 0,
        "cannot_evaluate": 0,
    }
    assert summary["buy"]["trigger_rate"] is None


def test_trigger_rate_excludes_pending_and_cannot_evaluate():
    rows = [
        row(code="1", eval_status="no_signal", buy_date=None, sell_date=None),
        row(code="2"),
        row(code="3", eval_status="open", is_terminal=0),
        row(code="4", eval_status="pending", buy_date=None, sell_date=None),
        row(code="5", eval_status="cannot_evaluate", buy_date=None, sell_date=None),
    ]
    summary = aggregate.summarize(rows)
    assert summary["buy"]["triggered"] == 2
    # 分母 = no_signal + open + closed = 3。
    assert summary["buy"]["trigger_rate"] == round(2 / 3 * 100, 2)


def test_partial_rows_are_excluded_from_win_rate_but_kept_in_triggered():
    rows = [
        row(code="1", buy_window_partial=1),
        row(code="2", buy_window_partial=0),
    ]
    summary = aggregate.summarize(rows)
    assert summary["buy"]["triggered"] == 2
    assert summary["buy"]["strict"]["samples"] == 1
    assert summary["buy"]["strict"]["wins"] == 1


def test_dual_caliber_win_uses_return_and_drawdown_thresholds():
    rows = [
        row(code="1", buy_loose_max_return_5d=0.0,
            buy_loose_max_drawdown_5d=-4.0, buy_strict_max_return_5d=0.0),
        row(code="2", buy_loose_max_return_5d=0.0,
            buy_loose_max_drawdown_5d=-4.0, buy_strict_max_return_5d=3.0,
            buy_strict_max_drawdown_5d=-5.0),
        row(code="3", buy_loose_max_return_5d=0.0,
            buy_loose_max_drawdown_5d=-4.0, buy_strict_max_return_5d=3.0,
            buy_strict_max_drawdown_5d=-4.99),
    ]
    summary = aggregate.summarize(rows)
    assert summary["buy"]["loose"]["wins"] == 0  # return==0 不算胜
    assert summary["buy"]["strict"]["wins"] == 1  # -5.0 不算胜，-4.99 算胜
    assert summary["buy"]["strict"]["win_rate"] == round(1 / 3 * 100, 2)


def test_sell_verdict_flew_wins_over_good_and_is_mutually_exclusive():
    rows = [
        row(code="1", sell_max_return_5d=5.0, sell_max_drawdown_5d=-9.0),
        row(code="2", sell_max_return_5d=4.99, sell_max_drawdown_5d=-3.0),
        row(code="3", sell_max_return_5d=4.99, sell_max_drawdown_5d=-2.99),
    ]
    summary = aggregate.summarize(rows)
    assert summary["sell"]["samples"] == 3
    assert summary["sell"]["flew"] == 1
    assert summary["sell"]["good"] == 1
    assert summary["sell"]["neutral"] == 1
    assert summary["sell"]["flew_rate"] == round(1 / 3 * 100, 2)
    assert aggregate.sell_verdict(rows[0]) == "flew"


def test_sell_partial_is_excluded_and_not_counted_as_neutral():
    rows = [row(code="1", sell_window_partial=1)]
    summary = aggregate.summarize(rows)
    assert summary["sell"]["triggered"] == 1
    assert summary["sell"]["samples"] == 0
    assert summary["sell"]["neutral"] == 0
    assert aggregate.sell_verdict(rows[0]) == "partial"


def test_rule_breakdown_always_outputs_all_rules():
    summary = aggregate.summarize([])
    assert [item["rule"] for item in summary["buy"]["rule_breakdown"]] == [
        item["code"] for item in strategy_doc.BUY_SIGNALS]
    assert [item["rule"] for item in summary["sell"]["rule_breakdown"]] == [
        item["name"] for item in strategy_doc.SELL_RULES]
    assert all(item["count"] == 0 for item in summary["buy"]["rule_breakdown"])
    assert all(item["count"] == 0 for item in summary["sell"]["rule_breakdown"])


def test_means_round_to_two_decimals_and_ignore_null():
    rows = [
        row(code="1", buy_strict_max_return_5d=1.006,
            buy_strict_max_drawdown_5d=-3.334),
        row(code="2", buy_strict_max_return_5d=None,
            buy_strict_max_drawdown_5d=None, buy_window_partial=1),
    ]
    summary = aggregate.summarize(rows)
    assert summary["buy"]["strict"]["avg_return"] == 1.01
    assert summary["buy"]["strict"]["avg_drawdown"] == -3.33


def test_buy_summary_returns_buy_side_only():
    payload = aggregate.buy_summary([row()])
    assert set(payload) == {
        "triggered", "strict", "loose", "rule_breakdown",
        "samples", "wins", "win_rate", "avg_return", "avg_drawdown",
    }
    assert payload["samples"] == 1
    assert payload["wins"] == 1
