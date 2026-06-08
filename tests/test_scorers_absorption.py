"""
tests for dragon_quant.scorers.absorption — 资金承接评分器
"""
import unittest
from dragon_quant.scorers.absorption import _window_return, _detect_events
from dragon_quant.models.types import KBar


class TestWindowReturn(unittest.TestCase):

    def test_positive_return(self):
        bars = [
            KBar(timestamp=i * 1000, volume=100, open=10.0, high=11, low=9,
                 close=10 + i * 0.1, chg=0, pct=0, turnover=0, amount=1000)
            for i in range(6)
        ]
        ret = _window_return(bars, 0, 5)
        self.assertGreater(ret, 0)

    def test_negative_return(self):
        bars = [
            KBar(timestamp=i * 1000, volume=100, open=10.0, high=11, low=9,
                 close=10 - i * 0.1, chg=0, pct=0, turnover=0, amount=1000)
            for i in range(6)
        ]
        ret = _window_return(bars, 0, 5)
        self.assertLess(ret, 0)

    def test_zero_open_guard(self):
        bars = [
            KBar(timestamp=0, volume=100, open=0.0, high=11, low=9,
                 close=10.0, chg=0, pct=0, turnover=0, amount=1000),
            KBar(timestamp=1000, volume=100, open=10.0, high=11, low=9,
                 close=12.0, chg=0, pct=0, turnover=0, amount=1000),
        ]
        ret = _window_return(bars, 0, 1)
        self.assertEqual(ret, 0.0)

    def test_flat(self):
        bars = [
            KBar(timestamp=i * 1000, volume=100, open=10.0, high=11, low=9,
                 close=10.0, chg=0, pct=0, turnover=0, amount=1000)
            for i in range(6)
        ]
        ret = _window_return(bars, 0, 5)
        self.assertAlmostEqual(ret, 0.0)


def _kb(ts: int, o: float, c: float) -> KBar:
    high = max(o, c)
    low = min(o, c)
    return KBar(
        timestamp=ts,
        volume=100,
        open=o,
        high=high,
        low=low,
        close=c,
        chg=0,
        pct=0,
        turnover=0,
        amount=1000,
    )


class TestDetectEvents(unittest.TestCase):

    def test_same_window_drop_creates_event(self):
        # 7 根 bar，确保有一个 start>=1 的窗口也能扫描到
        step = 300_000
        ts = [i * step for i in range(7)]

        # 目标板块：窗口 [0..5] 里第一根阴线，其余多数阳线，上涨>0.3%
        target = [
            _kb(ts[0], 100.0, 99.8),   # 阴线
            _kb(ts[1], 99.8, 100.2),   # 第一根阳线（rally_bar=1）
            _kb(ts[2], 100.2, 100.4),
            _kb(ts[3], 100.4, 100.6),
            _kb(ts[4], 100.6, 100.8),
            _kb(ts[5], 100.8, 101.0),
            _kb(ts[6], 101.0, 101.1),
        ]

        # 其他板块：在同窗口 [0..5] 内明显下跌（<-0.3%），且能找到 dive_bar=1
        s1 = [
            _kb(ts[0], 100.0, 100.0),
            _kb(ts[1], 100.0, 99.0),   # 阴线大跌，满足 dive
            _kb(ts[2], 99.0, 99.0),
            _kb(ts[3], 99.0, 99.0),
            _kb(ts[4], 99.0, 99.0),
            _kb(ts[5], 99.0, 99.0),
            _kb(ts[6], 99.0, 99.0),
        ]
        s2 = [
            _kb(ts[0], 200.0, 200.0),
            _kb(ts[1], 200.0, 198.0),  # 阴线大跌
            _kb(ts[2], 198.0, 198.0),
            _kb(ts[3], 198.0, 198.0),
            _kb(ts[4], 198.0, 198.0),
            _kb(ts[5], 198.0, 198.0),
            _kb(ts[6], 198.0, 198.0),
        ]

        events = _detect_events(
            target,
            {"S1": s1, "S2": s2},
            {"S1": "板块1", "S2": "板块2"},
        )
        self.assertGreaterEqual(len(events), 1)
        evt = events[0]
        self.assertGreaterEqual(evt.get("fleeing_count", 0), 2)
        self.assertIn("dive_time", evt)
        self.assertIn("rally_time", evt)
        self.assertLessEqual(evt.get("time_diff_min", 999), 10)

    def test_lead_window_drop_only_still_creates_event(self):
        step = 300_000
        ts = [i * step for i in range(7)]

        # 目标板块：窗口 [1..6] 触发（>=4阳线、上涨>0.3%），rally_bar=1
        target = [
            _kb(ts[0], 100.0, 99.9),
            _kb(ts[1], 99.9, 100.2),
            _kb(ts[2], 100.2, 100.4),
            _kb(ts[3], 100.4, 100.6),
            _kb(ts[4], 100.6, 100.8),
            _kb(ts[5], 100.8, 101.0),
            _kb(ts[6], 101.0, 101.2),
        ]

        # 其他板块：
        # - lead 窗口 [0..5] 下跌（<-0.3%）
        # - same 窗口 [1..6] 不下跌（避免 same_hit）
        # 并且让 dive_bar=1（发生在 bucket=1）以满足因果校验
        s1 = [
            _kb(ts[0], 100.0, 100.0),
            _kb(ts[1], 100.0, 99.0),   # dive
            _kb(ts[2], 99.0, 99.0),
            _kb(ts[3], 99.0, 99.0),
            _kb(ts[4], 99.0, 99.0),
            _kb(ts[5], 99.0, 99.0),
            _kb(ts[6], 99.0, 100.5),  # same窗口转为上涨
        ]
        s2 = [
            _kb(ts[0], 200.0, 200.0),
            _kb(ts[1], 200.0, 198.0),
            _kb(ts[2], 198.0, 198.0),
            _kb(ts[3], 198.0, 198.0),
            _kb(ts[4], 198.0, 198.0),
            _kb(ts[5], 198.0, 198.0),
            _kb(ts[6], 198.0, 201.0),
        ]

        events = _detect_events(
            target,
            {"S1": s1, "S2": s2},
            {"S1": "板块1", "S2": "板块2"},
        )
        self.assertGreaterEqual(len(events), 1)

    def test_bucket_alignment_tolerates_small_timestamp_offset(self):
        step = 300_000
        ts_target = [i * step for i in range(6)]
        ts_other = [i * step + 1_000 for i in range(6)]  # 同 bucket，偏移 1 秒

        target = [
            _kb(ts_target[0], 100.0, 99.8),
            _kb(ts_target[1], 99.8, 100.2),
            _kb(ts_target[2], 100.2, 100.4),
            _kb(ts_target[3], 100.4, 100.6),
            _kb(ts_target[4], 100.6, 100.8),
            _kb(ts_target[5], 100.8, 101.0),
        ]

        s1 = [
            _kb(ts_other[0], 100.0, 100.0),
            _kb(ts_other[1], 100.0, 99.0),
            _kb(ts_other[2], 99.0, 99.0),
            _kb(ts_other[3], 99.0, 99.0),
            _kb(ts_other[4], 99.0, 99.0),
            _kb(ts_other[5], 99.0, 99.0),
        ]
        s2 = [
            _kb(ts_other[0], 200.0, 200.0),
            _kb(ts_other[1], 200.0, 198.0),
            _kb(ts_other[2], 198.0, 198.0),
            _kb(ts_other[3], 198.0, 198.0),
            _kb(ts_other[4], 198.0, 198.0),
            _kb(ts_other[5], 198.0, 198.0),
        ]

        events = _detect_events(
            target,
            {"S1": s1, "S2": s2},
            {"S1": "板块1", "S2": "板块2"},
        )
        self.assertGreaterEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
