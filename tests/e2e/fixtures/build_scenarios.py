"""Build the reviewed M1B synthetic provider scenarios.

This is fixture *data* tooling, not part of the runtime harness: it writes
``scenario_scan.json`` / ``scenario_review.json`` next to this file.  Run it
only when the reviewed fixture values change::

    .venv/bin/python tests/e2e/fixtures/build_scenarios.py

Every bar is derived from a hand-authored cumulative-pct table so the fixture
is reviewable at the table level instead of as thousands of opaque JSON lines.
The generated JSON files are the source of truth for the E2E tests; goldens
are still pinned separately and never regenerated from a CLI run.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:  # package import when run from pytest; script mode when invoked directly
    from . import seed_builders as sb
except ImportError:  # pragma: no cover - direct ``python build_scenarios.py``
    import seed_builders as sb

CHINA = timezone(timedelta(hours=8))
FIXTURES = Path(__file__).resolve().parent
NOW = "2026-09-11T15:30:00+08:00"
TRADE_DATE = "2026-09-11"


# ─── bar helpers ────────────────────────────────────────────────────────────


def stamp(day: int, hour: int, minute: int) -> int:
    moment = datetime(2026, 9, day, hour, minute, tzinfo=CHINA)
    return int(moment.timestamp() * 1000)


def _bar(ts: int, open_px: float, close: float, pct: float, *,
         high: float | None = None, low: float | None = None,
         volume: float = 1000.0, turnover: float = 0.0) -> dict:
    high = max(open_px, close) if high is None else high
    low = min(open_px, close) if low is None else low
    return {
        "timestamp": ts,
        "volume": volume,
        "open": round(open_px, 4),
        "high": round(high, 4),
        "low": round(low, 4),
        "close": round(close, 4),
        "chg": round(close - open_px, 4),
        "pct": pct,
        "turnover": turnover,
        "amount": round(volume * close, 4),
    }


def minute_series(points: list[tuple[int, int, float]], base: float, *,
                  day: int = 11, one_line: bool = False,
                  volume: float = 1000.0,
                  turnover: float = 0.0) -> list[dict]:
    """Session minutes as cumulative day pct values (first open == ``base``)."""

    bars: list[dict] = []
    previous = base
    for hour, minute, pct in points:
        close = round(base * (1 + pct / 100.0), 4)
        if one_line:
            bars.append(_bar(stamp(day, hour, minute), close, close, pct,
                             high=close, low=close, volume=volume,
                             turnover=turnover))
        else:
            bars.append(_bar(stamp(day, hour, minute), previous, close, pct,
                             volume=volume, turnover=turnover))
            previous = close
    return bars


def weekday_dates(start: str, end: str) -> list[date]:
    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    out = []
    while current <= last:
        if current.weekday() < 5:
            out.append(current)
        current += timedelta(days=1)
    return out


def daily_series(closes: list[float], pcts: list[float], dates: list[date],
                 *, one_line_last: bool = False) -> list[dict]:
    bars: list[dict] = []
    previous = closes[0]
    for index, (close, pct, day) in enumerate(zip(closes, pcts, dates)):
        ts = int(datetime(day.year, day.month, day.day, 15, 0,
                          tzinfo=CHINA).timestamp() * 1000)
        last = index == len(closes) - 1
        if one_line_last and last:
            bars.append(_bar(ts, close, close, pct, high=close, low=close,
                             volume=500000.0, turnover=0.5))
        else:
            high = max(previous, close) * 1.005
            low = min(previous, close) * 0.995
            bars.append(_bar(ts, previous, close, pct, high=high, low=low,
                             volume=1_000_000.0, turnover=3.0))
        previous = close
    return bars


def five_min_series(bars: list[tuple[float, float]], *, day: int = 11) -> list[dict]:
    """Six 5-minute bars starting 09:35; each tuple is (open, close)."""

    start = datetime(2026, 9, day, 9, 35, tzinfo=CHINA)
    out = []
    for index, (open_px, close) in enumerate(bars):
        moment = start + timedelta(minutes=5 * index)
        out.append(_bar(int(moment.timestamp() * 1000), open_px, close, 0.0,
                        volume=10_000.0))
    return out


def quote(code: str, name: str, price: float, limit_up: float, pct: float, *,
          turnover: float, volume: float = 100_000.0,
          bid1_volume: float = 0.0, limit_down: float = 0.0) -> dict:
    bid1_price = limit_up if bid1_volume > 0 else 0.0
    return {
        "code": code, "name": name, "price": price, "prev_close": round(limit_up / 1.1, 4),
        "open_px": round(limit_up / 1.1, 4), "high": price, "low": price,
        "pct": pct, "chg": round(price - limit_up / 1.1, 4),
        "turnover_rate": turnover, "amplitude": 5.0,
        "volume": volume, "amount": round(price * volume, 4),
        "market_cap": price * 10_000_000, "float_market_cap": price * 8_000_000,
        "volume_ratio": 1.5, "pe": 20.0, "limit_up": limit_up,
        "limit_down": limit_down or round(limit_up / 1.1 * 0.9, 4),
        "avg_price": price, "bid1_price": bid1_price,
        "bid1_volume": bid1_volume, "ask1_volume": 0.0,
        "timestamp": stamp(11, 15, 0), "main_net_amount": None,
    }


def rank_sector(code: str, name: str, pct: float, category: str) -> dict:
    return {"code": code, "name": name, "pct": pct, "amplitude": 3.0,
            "turnover_rate": 1.0, "category": category, "source": "tdx"}


def component(code: str, name: str, sector_code: str, sector_name: str,
              pct: float, price: float) -> dict:
    return {"code": code, "name": name, "exchange": "SH",
            "sector_code": sector_code, "sector_name": sector_name,
            "pct": pct, "price": price, "five_day_return": 0.0}


# ─── scan scenario ──────────────────────────────────────────────────────────


def _fill(start_hour: int, start_minute: int, count: int,
          value: float) -> dict[tuple[int, int], float]:
    """Contiguous minute keys across an hour boundary (same session)."""

    out: dict[tuple[int, int], float] = {}
    hour, minute = start_hour, start_minute
    for _ in range(count):
        out[(hour, minute)] = value
        minute += 1
        if minute == 60:
            hour += 1
            minute = 0
    return out


def build_scan() -> dict:
    morning = [(9, 30 + index) for index in range(30)] + [
        (10, index) for index in range(11)]                   # 09:30–10:10
    afternoon = [(13, 25 + index) for index in range(21)]     # 13:25–13:45

    def flat(points, value):
        return {(hour, minute): value for hour, minute in points}

    # 600001: seals at 09:39 and holds; the 09:32 pulse leads its sector.
    p600001 = {(9, 30): 0.0}
    p600001.update({(9, 31): 1.5, (9, 32): 3.0, (9, 33): 5.0,
                    (9, 34): 7.0, (9, 35): 8.0, (9, 36): 8.5,
                    (9, 37): 9.0, (9, 38): 9.5, (9, 39): 10.0})
    p600001.update(_fill(9, 40, 31, 10.0))
    p600001.update(flat(afternoon, 10.0))

    # 600002: fades below the limit (10.97 < 11.00 outside the 0.1% band), so
    # it never holds a stable seal; its pulse starts with the sector pulse.
    p600002 = {(9, 30): 0.0, (9, 31): 0.0, (9, 32): 0.0,
               (9, 33): 1.5, (9, 34): 3.0, (9, 35): 4.5,
               (9, 36): 6.0, (9, 37): 7.0, (9, 38): 8.0,
               (9, 39): 8.5, (9, 40): 8.8, (9, 41): 9.0,
               (9, 42): 9.2, (9, 43): 9.4, (9, 44): 9.5,
               (9, 45): 9.6, (9, 46): 9.65, (9, 47): 9.7}
    p600002.update(_fill(9, 48, 23, 9.7))
    p600002.update(flat(afternoon, 9.7))
    p600002.update({(13, 31): 9.5, (13, 32): 9.2, (13, 33): 9.1,
                    (13, 34): 9.3, (13, 35): 9.5})

    # 600003: seals at 10:05 (second in its sector); 09:58 pulse leads.
    p600003 = {}
    for index in range(27):                                    # 09:30–09:56
        p600003[(9, 30 + index)] = round(index * 4.0 / 26.0, 2)
    p600003.update({(9, 57): 4.0, (9, 58): 5.0, (9, 59): 6.5,
                    (10, 0): 8.0, (10, 1): 8.5, (10, 2): 9.0,
                    (10, 3): 9.5, (10, 4): 9.8})
    p600003.update(_fill(10, 5, 6, 10.0))
    p600003.update(flat(afternoon, 10.0))

    p600006 = flat(morning, 10.0)
    p600006.update(flat(afternoon, 10.0))

    # Sector 880001: one 0.4% follow pulse at 09:33–09:34, then flat; the
    # afternoon mirrors the market dip so hold/rebound are exercised.
    p880001 = {(9, 30): 0.0, (9, 31): 0.0, (9, 32): 0.0,
               (9, 33): 0.2, (9, 34): 0.4}
    p880001.update(_fill(9, 35, 36, 0.4))
    p880001.update(flat(afternoon, 1.0))
    p880001.update({(13, 31): 0.8, (13, 32): 0.5, (13, 33): 0.4,
                    (13, 34): 0.6, (13, 35): 0.9})
    p880001.update(_fill(13, 36, 10, 1.0))

    # Sector 880002: follow pulse at 10:00–10:01 after 600003's 09:58 pulse.
    p880002 = flat(morning, 0.0)
    p880002[(10, 0)] = 0.2
    p880002.update(_fill(10, 1, 6, 0.4))
    p880002.update(flat(afternoon, 1.0))
    p880002.update({(13, 31): 0.8, (13, 32): 0.5, (13, 33): 0.4,
                    (13, 34): 0.6, (13, 35): 0.9})
    p880002.update(_fill(13, 36, 10, 1.0))

    market = flat(morning, 0.2)
    market.update(flat(afternoon, 0.2))
    market.update({(13, 31): 0.0, (13, 32): -0.3, (13, 33): -0.4,
                   (13, 34): -0.2, (13, 35): 0.1, (13, 36): 0.3})
    market.update({(13, 37 + index): 0.4 for index in range(9)})
    quiet = flat(morning, 0.1)
    quiet.update(flat(afternoon, 0.1))

    def ordered(points):
        return sorted(points.items())

    def series(points, base, **kwargs):
        return minute_series([(h, m, p) for (h, m), p in ordered(points)],
                             base, **kwargs)

    minute_payload = {
        "600001": series(p600001, 10.0, volume=1_000_000.0, turnover=12.0),
        "600002": series(p600002, 10.0, volume=900_000.0, turnover=8.0),
        "600003": series(p600003, 10.0, volume=800_000.0, turnover=6.0),
        "600006": minute_series(
            [(hour, minute, 10.0) for hour, minute in sorted(p600006)],
            10.0, one_line=True, volume=100_000.0, turnover=0.5),
        "SH000001": series(market, 3000.0, volume=0.0),
    }
    sector_minute = {
        "880001": series(p880001, 100.0, volume=0.0),
        "880002": series(p880002, 100.0, volume=0.0),
    }
    for code in ("880003", "880004", "880005", "880006", "880007", "880008"):
        sector_minute[code] = series(quiet, 100.0, volume=0.0)

    # 5-minute history: primary sectors rally, industry "flee" sectors dive.
    rally = five_min_series([
        (100.00, 100.25), (100.25, 100.50), (100.50, 100.75),
        (100.75, 101.00), (101.00, 101.25), (101.25, 101.50)])
    dive = five_min_series([
        (100.00, 99.86), (99.86, 99.72), (99.72, 99.58),
        (99.58, 99.44), (99.44, 99.30), (99.30, 99.16)])
    five_min = {
        code: rally for code in
        ("880001", "880002", "880003", "880004", "880005", "880006",
         "880007", "880008")
    }
    five_min.update({code: dive for code in ("881001", "881002", "881003")})

    ranking = {
        "hy": [rank_sector("881002", "银行", -2.0, "hy"),
               rank_sector("881003", "医药", 1.0, "hy"),
               rank_sector("881001", "半导体", 5.0, "hy")],
        "gn": [rank_sector("880001", "AI算力", 13.0, "gn"),
               rank_sector("880002", "机器人", 12.0, "gn"),
               rank_sector("880003", "算力租赁", 11.0, "gn"),
               rank_sector("880004", "半导体设备", 10.0, "gn"),
               rank_sector("880005", "数据要素", 9.0, "gn"),
               rank_sector("880006", "银行科技", 8.0, "gn"),
               rank_sector("880007", "医药商业", 7.0, "gn"),
               rank_sector("880008", "消费电子", 6.0, "gn")],
        "dq": [rank_sector("880201", "江苏", 2.5, "dq")],
    }

    components = {
        "880001": [component("600001", "龙头A", "880001", "AI算力", 10.0, 11.0),
                   component("600002", "跟风B", "880001", "AI算力", 9.95, 10.995),
                   component("600011", "小弟1", "880001", "AI算力", 5.0, 5.5),
                   component("600012", "小弟2", "880001", "AI算力", 1.0, 5.3)],
        "880002": [component("600003", "补涨C", "880002", "机器人", 9.9, 11.0),
                   component("600006", "一字F", "880002", "机器人", 10.0, 11.0),
                   component("600013", "小弟3", "880002", "机器人", 4.0, 5.2),
                   component("600014", "小弟4", "880002", "机器人", 1.0, 5.05)],
        "880003": [component("300001", "创业E", "880003", "算力租赁", 20.0, 12.0)],
        "880004": [component("600005", "*ST股", "880004", "半导体设备", 5.0, 5.25)],
        "880005": [component("830001", "北交所", "880005", "数据要素", 30.0, 13.0)],
        "880006": [component("600004", "银行D", "880006", "银行科技", 1.0, 5.05)],
        "880007": [component("600015", "医药", "880007", "医药商业", 2.0, 5.1)],
        "880008": [component("600016", "消费", "880008", "消费电子", 1.5, 5.075)],
    }

    quotes = [
        quote("600001", "龙头A", 11.0, 11.0, 10.0, turnover=12.0,
              volume=1_000_000.0, bid1_volume=600_000.0),
        quote("600002", "跟风B", 10.97, 11.0, 9.7, turnover=8.0,
              volume=900_000.0),
        quote("600003", "补涨C", 11.0, 11.0, 10.0, turnover=6.0,
              volume=800_000.0, bid1_volume=320_000.0),
        quote("600006", "一字F", 11.0, 11.0, 10.0, turnover=0.5,
              volume=100_000.0, bid1_volume=50_000.0),
        quote("600011", "小弟1", 5.5, 5.5, 5.0, turnover=5.0),
        quote("600012", "小弟2", 5.3, 5.5, 1.0, turnover=3.0),
        quote("600013", "小弟3", 5.2, 5.5, 4.0, turnover=4.0),
        quote("600014", "小弟4", 5.05, 5.5, 1.0, turnover=1.0),
        quote("300001", "创业E", 12.0, 12.0, 20.0, turnover=15.0),
        quote("600005", "*ST股", 5.25, 5.5, 5.0, turnover=2.0),
        quote("830001", "北交所", 13.0, 13.0, 30.0, turnover=10.0),
        quote("600004", "银行D", 5.05, 5.5, 1.0, turnover=2.0),
        quote("600015", "医药", 5.1, 5.5, 2.0, turnover=2.0),
        quote("600016", "消费", 5.075, 5.5, 1.5, turnover=2.0),
    ]

    scan_dates = weekday_dates("2026-09-01", "2026-09-11")
    daily = {
        "600001": daily_series(
            [8.0, 8.2, 8.5, 8.9, 9.3, 9.7, 10.0, 10.5, 11.0],
            [3.0, 2.5, 3.5, 4.0, 4.5, 4.3, 10.0, 10.0, 10.0], scan_dates),
        "600002": daily_series(
            [7.0, 7.2, 7.5, 7.8, 8.0, 8.3, 8.6, 9.0, 9.5],
            [2.0, 2.8, 3.2, 3.0, 2.5, 3.5, 3.6, 4.0, 10.0], scan_dates),
        "600003": daily_series(
            [6.0, 6.2, 6.5, 6.8, 7.0, 7.3, 7.6, 8.0, 8.4],
            [2.0, 3.0, 4.0, 3.5, 3.0, 4.0, 4.5, 5.0, 10.0], scan_dates),
        "600006": daily_series(
            [5.0, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8],
            [1.0, 2.0, 1.5, 2.5, 2.0, 1.5, 2.0, 1.8, 10.0],
            scan_dates, one_line_last=True),
    }

    calendar_dates = weekday_dates("2026-07-01", "2026-09-11")
    calendar = daily_series(
        [12.0] * len(calendar_dates), [0.0] * len(calendar_dates),
        calendar_dates)

    tdx = {
        "get_sector_ranking": {"by_category": ranking},
        "get_sector_components": {"by_code": components},
        "get_sector_1min_kline": {"by_code": sector_minute},
        "get_sector_5min_kline_history": {"by_code": five_min},
        "get_kline": {"by_code": daily},
        "get_minute_kline": {"by_code": minute_payload},
        "batch_get_quotes": quotes,
    }
    ths = {
        # Only reached if a TDX category returns empty (S10 degraded case).
        "get_sector_ranking": {"by_category": {"hy": [], "gn": [], "dq": []}},
    }
    xueqiu = {"get_kline": {"by_code": {"000001": calendar}}}
    return {
        "case": "scan",
        "now": NOW,
        "trade_date": TRADE_DATE,
        "note": "M1B scan scenario; reviewed bar tables live in build_scenarios.py.",
        "providers": {
            "tdx": tdx,
            "ths": ths,
            "xueqiu": xueqiu,
            "tencent": {"batch_get_quotes": quotes},
        },
    }


# ─── review scenario ────────────────────────────────────────────────────────


def build_review() -> dict:
    calendar_dates = weekday_dates("2026-07-01", "2026-09-11")
    calendar = daily_series(
        [12.0] * len(calendar_dates), [0.0] * len(calendar_dates),
        calendar_dates)

    dates = weekday_dates("2026-07-01", "2026-09-11")
    # 600001: selected 08-17; 08-18 is the first tradable day (low 10.00);
    # peak 10.80 on 08-21 with a 9.60 trough on the way (drawdown -4%).
    special = {
        "2026-08-18": (10.10, 10.50, 10.00, 10.30, 3.0),
        "2026-08-19": (10.30, 10.40, 9.80, 10.20, -1.0),
        "2026-08-20": (10.20, 10.60, 9.60, 10.50, 3.0),
        "2026-08-21": (10.50, 10.80, 9.90, 10.70, 2.0),
        "2026-08-24": (10.70, 10.50, 10.00, 10.30, -1.0),
        "2026-08-25": (10.30, 10.40, 10.10, 10.20, -1.0),
    }
    main = []
    for day in dates:
        key = day.isoformat()
        if key in special:
            open_px, high, low, close, pct = special[key]
            ts = int(datetime(day.year, day.month, day.day, 15, 0,
                              tzinfo=CHINA).timestamp() * 1000)
            main.append(_bar(ts, open_px, close, pct, high=high, low=low,
                             volume=1_000_000.0, turnover=3.0))
        else:
            ts = int(datetime(day.year, day.month, day.day, 15, 0,
                              tzinfo=CHINA).timestamp() * 1000)
            main.append(_bar(ts, 10.0, 10.0, 0.0, high=10.05, low=9.95,
                             volume=1_000_000.0, turnover=2.0))

    # 600002: selected 08-21; every later bar is one-line, so no entry.
    wall = []
    for day in dates:
        ts = int(datetime(day.year, day.month, day.day, 15, 0,
                          tzinfo=CHINA).timestamp() * 1000)
        if day > date(2026, 8, 21):
            wall.append(_bar(ts, 11.0, 11.0, 10.0, high=11.0, low=11.0,
                             volume=50_000.0, turnover=0.4))
        else:
            wall.append(_bar(ts, 10.0, 10.0, 0.0, high=10.05, low=9.95,
                             volume=1_000_000.0, turnover=2.0))

    return {
        "case": "review",
        "now": NOW,
        "trade_date": TRADE_DATE,
        "note": "M1B review scenario; one successful, one no-entry, one error.",
        "providers": {
            "xueqiu": {
                "get_kline": {
                    "by_code": {
                        "000001": calendar,
                        "600001": main,
                        "600002": wall,
                        "600003": {"error": "RuntimeError: 模拟K线故障"},
                    }
                }
            }
        },
    }


# ─── prep scenario ─────────────────────────────────────────────────────────


def build_prep() -> dict:
    """M1D1 prep scenario: one healthy trading day with all five dims populated.

    The macro response covers **all four** ``MACRO_SPECS`` keys from the tdx
    source, which is the C18 guarantee: a healthy prep must never reach the
    Yahoo/urllib fallback.  ``seed_builders`` owns the reviewed numbers.
    """
    return {
        "case": "prep",
        "now": sb.PREP_NOW,
        "trade_date": sb.TRADE_DATE,
        "note": ("M1D1 prep scenario; reviewed rows live in "
                 "seed_builders.py. Macro covers every MACRO_SPECS key via tdx."),
        "providers": {
            "tdx": {
                "get_kline": {"by_code": {"SH000001": sb.calendar_bars()}},
                "batch_get_quotes": sb.QUOTES,
                "get_market_breadth_rows": sb.breadth_rows(),
                "get_fg_boards": sb.FG_BOARDS,
                "get_announcements": {
                    "by_code": {code: list(items)
                                for code, items in sb.ANNOUNCEMENTS.items()},
                },
                "get_macro_quotes": sb.MACRO_INSTRUMENTS,
            },
            "ths": {
                "get_limit_up_pool": {"by_date": {
                    sb.TRADE_DATE.replace("-", ""): {
                        "total": sb.POOL_TOTAL,
                        "rows": [dict(row) for row in sb.POOL_ROWS],
                        "truncated": False,
                    },
                }},
                "get_block_top": {"by_date": {
                    sb.TRADE_DATE.replace("-", ""): {
                        "rows": [dict(row) for row in sb.BLOCK_ROWS],
                    },
                }},
            },
        },
    }


# ─── auction scenario ──────────────────────────────────────────────────────


def build_auction() -> dict:
    """M1D1 auction scenario: three pooled codes (bull / bear / vetoed).

    600001 is a same-day high-open bull, 600002 a fading bear, and 600003
    queues at the limit-down price in the tail window (reject gate 0.4).
    The tencent quote batch is identical so the fallback path (A11) can serve
    the same reviewed values.
    """
    quotes = [sb.QUOTE_BY_CODE[code] for code in ("600001", "600002", "600003")]
    return {
        "case": "auction",
        "now": sb.AUCTION_NOW,
        "trade_date": sb.TRADE_DATE,
        "note": "M1D1 auction scenario; reviewed items live in seed_builders.py.",
        "providers": {
            "tdx": {
                "get_kline": {"by_code": {"SH000001": sb.calendar_bars()}},
                "batch_get_quotes": quotes,
                "get_auction": {
                    "by_code": {code: [dict(row) for row in items]
                                for code, items in sb.AUCTION_ITEMS.items()},
                },
            },
            "tencent": {"batch_get_quotes": quotes},
        },
    }


def write(name: str, payload: dict) -> None:
    target = FIXTURES / name
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"wrote {target}")


# ─── account / live_trade scenario (M1E2) ─────────────────────────────────


def _account_daily(day: date, *, close: float = 10.0) -> dict:
    """Daily bar for the account fixture (amount matches the unit-test FakeData)."""

    ts = int(datetime(day.year, day.month, day.day, 15, 0,
                      tzinfo=CHINA).timestamp() * 1000)
    return {
        "timestamp": ts, "volume": 1_000_000.0, "open": close,
        "high": round(close + 0.2, 4), "low": round(close - 0.2, 4),
        "close": close, "chg": 0.0, "pct": 0.0, "turnover": 12.0,
        "amount": 1_000_000_000.0,
    }


def _account_5min(day: date, *, open_px: float, high: float, low: float,
                  close: float) -> list[dict]:
    """48 real 5-minute bars (09:35–11:30 / 13:05–15:00)."""

    bars: list[dict] = []
    for hour, minute, count in ((9, 35, 24), (13, 5, 24)):
        start = datetime(day.year, day.month, day.day, hour, minute,
                         tzinfo=CHINA)
        for index in range(count):
            ts = int((start + timedelta(minutes=5 * index)).timestamp() * 1000)
            bars.append({
                "timestamp": ts, "volume": 10_000.0, "open": open_px,
                "high": high, "low": low, "close": close, "chg": 0.0,
                "pct": 0.0, "turnover": 1.0, "amount": 100_000.0,
            })
    return bars


def build_account() -> dict:
    """M1E2 account/buy/sell scenario mirroring ``tests.test_review_account``.

    Reviewed inputs:

    * SH000001 has a weekday calendar 2026-07-01..2026-09-11 (warmup + post-exit).
    * 600001 daily bars are flat 10 with high 10.2 / low 9.8 and amount 1e9,
      exactly the unit-test ``history()`` values the golden was derived from.
    * 2026-09-07 and 2026-09-08 5-minute bars are flat 10 (±0.05).  On
      2026-09-08/09 every bar dips to 9.5 so the first-day stop and the
      T+2 hard stop are both reachable while the fill price stays 10.00.
    """

    calendar_dates = weekday_dates("2026-07-01", "2026-09-11")
    calendar = [
        {**_account_daily(day, close=3000.0), "high": 3010.0,
         "low": 2990.0, "amount": 0.0, "turnover": 0.0, "volume": 0.0}
        for day in calendar_dates
    ]
    daily = [_account_daily(day) for day in calendar_dates]

    five_min: list[dict] = []
    dip_days = {"2026-09-08", "2026-09-09"}
    for day in weekday_dates("2026-09-04", "2026-09-11"):
        if day.isoformat() in dip_days:
            five_min.extend(_account_5min(day, open_px=10.0, high=10.0,
                                          low=9.5, close=10.0))
        else:
            five_min.extend(_account_5min(day, open_px=10.0, high=10.05,
                                          low=9.95, close=10.0))

    return {
        "case": "account",
        "now": NOW,
        "trade_date": TRADE_DATE,
        "note": ("M1E2 review-account/buy/sell scenario; equivalent to the "
                 "unit-test FakeData used by the 99495.199 golden."),
        "providers": {
            "xueqiu": {
                "get_kline": {"by_code": {"SH000001": calendar,
                                          "600001": daily}},
                "get_5min_kline_for": {"by_code": {"600001": five_min}},
            }
        },
    }


def main() -> None:
    write("scenario_scan.json", build_scan())
    write("scenario_review.json", build_review())
    write("scenario_prep.json", build_prep())
    write("scenario_auction.json", build_auction())
    write("scenario_account.json", build_account())


if __name__ == "__main__":
    main()
