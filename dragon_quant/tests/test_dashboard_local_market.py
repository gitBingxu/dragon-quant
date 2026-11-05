"""竞价成效本地行情/窗口单测（方案 §5.4 / §9.1，TDD 先 RED）。

覆盖：
- ``LocalStockDailyMarket`` 只返回预加载 bars、无 ``calendar``（→ bars_union）；
- 无快照 / 无本地日 K → ``insufficient_data`` + 正确 reason；
- 正常路径：1/3/5 日标签、选中日逐股、coverage、caveats；
- 窗口 codes 超过 300 上限显式 ``truncated`` + 告警。
"""

from __future__ import annotations

from web_ui.backend.presenters.auction_effect import (
    MAX_WINDOW_CODES,
    LocalStockDailyMarket,
    run_auction_effect,
)

TRADE_DATE = "2026-09-11"
BAR_DATES = [
    "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16",
    "2026-09-17", "2026-09-18",
]


def _row(code: str, **kwargs) -> dict:
    row = {
        "code": code, "version": "2", "is_weak": False, "strength": 80.0,
        "level": "强", "direction": "bull", "rank": 1,
        "validation_status": "ok", "data_quality": {"status": "ok"},
        "risk_events": [], "config_version": "auction_v2",
        "unverified": False, "created_at": "2026-09-11 09:26:00",
    }
    row.update(kwargs)
    return row


class FakeStore:
    def __init__(self, days: dict[str, list[dict]]):
        self.days = days

    def snapshot_dates(self, date_from: str, date_to: str) -> list[str]:
        return sorted(day for day in self.days if date_from <= day <= date_to)

    def load(self, day: str):
        return list(self.days.get(day, []))


def _bars(open_prices: list[float]) -> dict:
    rows = {}
    for code in ("600001",):
        rows[code] = [
            {"date": day, "open": price, "high": price, "low": price,
             "close": price, "volume": 1000.0, "amount": 500_000_000.0}
            for day, price in zip(BAR_DATES, open_prices)
        ]
    return {
        "rows": rows,
        "truncated": False,
        "bar_dates": list(BAR_DATES),
        "codes": sorted(rows),
    }


def _loader(bars: dict):
    calls: list[tuple] = []

    def load(codes, date_from, date_to):
        calls.append((list(codes), date_from, date_to))
        return bars

    load.calls = calls
    return load


# ─── local market ──────────────────────────────────────────────────────────


def test_local_market_only_returns_preloaded_bars():
    market = LocalStockDailyMarket({"600001": [
        {"date": "2026-09-11", "open": 10.0}]})
    assert market.daily("600001", "2026-01-01") == [
        {"date": "2026-09-11", "open": 10.0}]
    assert market.daily("600999") == []
    # 没有 calendar()：decision_review 必须退化为 bars_union。
    assert not hasattr(market, "calendar")


# ─── 无数据边界 ────────────────────────────────────────────────────────────


def test_no_snapshot_returns_insufficient_data():
    result = run_auction_effect(
        trade_date=TRADE_DATE, store=FakeStore({}),
        load_bars=_loader(_bars([10.0] * len(BAR_DATES))))
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "no_snapshot"
    assert result["data_source"] == "local_stock_daily"
    assert result["local_coverage"]["snapshot_dates"] == 0
    assert [item["horizon"] for item in result["horizons"]] == [1, 3, 5]


def test_no_local_bars_returns_local_bars_missing():
    store = FakeStore({TRADE_DATE: [_row("600001")]})
    empty = {"rows": {}, "truncated": False, "bar_dates": [], "codes": []}
    result = run_auction_effect(
        trade_date=TRADE_DATE, store=store, load_bars=_loader(empty))
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "local_bars_missing"
    assert result["local_coverage"]["selected_codes"] == 1
    assert result["local_coverage"]["loaded_codes"] == 0
    assert any("涨停标记" in caveat for caveat in result["caveats"])


# ─── 正常路径 ──────────────────────────────────────────────────────────────


def test_effect_ok_path_with_1_3_5_labels():
    store = FakeStore({TRADE_DATE: [_row("600001")]})
    prices = [10.0, 10.5, 10.6, 10.8, 10.9, 11.0]
    loader = _loader(_bars(prices))
    result = run_auction_effect(
        trade_date=TRADE_DATE, store=store, load_bars=loader,
        min_days=1, min_group_samples=1)

    assert result["status"] == "ok"
    assert result["reason"] is None
    assert result["calendar_basis"] == "bars_union"
    assert result["cost_bps"] == 13.0

    horizons = {item["horizon"]: item for item in result["horizons"]}
    assert set(horizons) == {1, 3, 5}
    strong = next(group for group in horizons[1]["groups"]
                  if group["group"] == "strong")
    assert strong["observations"] == 1
    assert strong["valid"] == 1
    assert strong["quality"] == "ok"
    assert strong["direction_hit_rate"] == 1.0
    # 1 日：10.5 / 10.0 - 1 = 5%，扣 13bp → 4.87%
    assert strong["mean_net_return_pct"] == __import__("pytest").approx(4.87, abs=1e-6)

    selected = result["selected_day"]
    assert {item["horizon"] for item in selected} == {1, 3, 5}
    assert {item["code"] for item in selected} == {"600001"}
    assert all(item["entry_date"] == "2026-09-11" for item in selected)
    assert selected[0]["exit_date"] == "2026-09-14"

    coverage = result["local_coverage"]
    assert coverage["window_codes"] == 1
    assert coverage["loaded_codes"] == 1
    assert coverage["codes_with_bars"] == 1
    assert coverage["bar_dates"] == len(BAR_DATES)
    assert coverage["truncated"] is False

    assert any("calendar_fallback" in warning for warning in result["warnings"])
    assert any("collect" not in caveat and "一字板" in caveat
               for caveat in result["caveats"])
    # 行情窗口传给 loader：选中日 -120 ~ +30。
    assert loader.calls[0][1] == "2026-05-14"
    assert loader.calls[0][2] == "2026-10-11"


def test_default_thresholds_report_insufficient_samples():
    store = FakeStore({TRADE_DATE: [_row("600001")]})
    result = run_auction_effect(
        trade_date=TRADE_DATE, store=store,
        load_bars=_loader(_bars([10.0, 10.5, 10.6, 10.8, 10.9, 11.0])))
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "insufficient_samples"
    strong = next(group for item in result["horizons"]
                  for group in item["groups"] if group["group"] == "strong")
    assert strong["quality"] == "low_confidence"
    # 低置信仍保留样本事实，不隐藏 valid / coverage。
    assert strong["valid"] == 1
    assert strong["coverage"] == 1.0


def test_window_truncates_codes_over_limit():
    codes = [f"{index:06d}" for index in range(MAX_WINDOW_CODES + 5)]
    rows = [_row(code) for code in codes]
    store = FakeStore({TRADE_DATE: rows})
    bars = {
        "rows": {code: [{"date": TRADE_DATE, "open": 10.0,
                         "volume": 1000.0, "amount": 500_000_000.0}]
                 for code in codes[:MAX_WINDOW_CODES]},
        "truncated": False,
        "bar_dates": [TRADE_DATE],
        "codes": codes[:MAX_WINDOW_CODES],
    }
    result = run_auction_effect(
        trade_date=TRADE_DATE, store=store, load_bars=_loader(bars),
        min_days=1, min_group_samples=1)
    coverage = result["local_coverage"]
    assert coverage["window_codes"] == MAX_WINDOW_CODES + 5
    assert coverage["truncated"] is True
    assert any("codes_truncated" in warning for warning in result["warnings"])
