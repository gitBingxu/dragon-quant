"""``historical_events(daily_start=...)`` 与默认行为逐字等价 + 缓存键稳定。"""

from __future__ import annotations

from dragon_quant.review_account.data import historical_events
from dragon_quant.tests.test_bs_eval_evaluator import (
    FakeData,
    TRADE_DATE,
    weekdays,
)


class RecordingData(FakeData):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, str]] = []

    def daily(self, code, start, refresh=False):
        self.calls.append((code, start))
        return super().daily(code, start, refresh=refresh)


def _shape(events):
    return [
        (event.timestamp, event.phase, tuple(sorted(event.rows)), event.unavailable)
        for event in events
    ]


def test_daily_start_reuses_one_cache_key_and_keeps_events_identical():
    data = RecordingData()
    days = [day for day in weekdays("2026-08-03", "2026-09-11")
            if day > TRADE_DATE][:3]
    with_start = []
    for day in days:
        with_start.extend(_shape(historical_events(
            day, [], {"600001"}, data, daily_start=TRADE_DATE)))
    # 多日回放只用一个固定起点（MarketData.daily 的 days 缓存键稳定）。
    assert {(code, start) for code, start in data.calls} == {("600001", TRADE_DATE)}

    plain = RecordingData()
    without_start = []
    for day in days:
        without_start.extend(_shape(historical_events(
            day, [], {"600001"}, plain)))
    assert with_start == without_start
    assert [start for _, start in plain.calls] == days


def test_default_daily_start_is_none_keeps_per_day_lookup():
    data = RecordingData()
    list(historical_events(TRADE_DATE, [], {"600001"}, data))
    assert data.calls == [("600001", TRADE_DATE)]
