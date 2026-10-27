"""Canonical synthetic data shared by the prep/auction scenario builder and tests.

Only stdlib, no application import.  This module is *fixture data*: the numbers
here are the reviewed inputs for ``scenario_prep.json`` / ``scenario_auction.json``
and for the seeded ``assets.db`` / ``dragon.db`` rows.  Tests assert computed
outputs against goldens; they never re-derive these inputs at runtime.

Timestamps are epoch milliseconds (bar) or epoch seconds (THS first-limit-up
time), always Asia/Shanghai.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

CHINA = timezone(timedelta(hours=8))

TRADE_DATE = "2026-09-11"
WINDOW_DATES = ["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", TRADE_DATE]
CALENDAR_START = "2026-08-03"

PREP_NOW = "2026-09-11T16:45:00+08:00"
AUCTION_NOW = "2026-09-11T09:30:00+08:00"


def weekday_dates(start: str, end: str) -> list[date]:
    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    out = []
    while current <= last:
        if current.weekday() < 5:
            out.append(current)
        current += timedelta(days=1)
    return out


def epoch_ms(day: str, hour: int = 15, minute: int = 0) -> int:
    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, hour, minute,
                        tzinfo=CHINA).timestamp() * 1000)


def epoch_s(day: str, hour: int, minute: int, second: int = 0) -> int:
    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, hour, minute, second,
                        tzinfo=CHINA).timestamp())


def calendar_bars() -> list[dict]:
    """SH000001 daily bars: every weekday from 2026-08-03..2026-09-11."""

    bars = []
    for day in weekday_dates(CALENDAR_START, TRADE_DATE):
        ts = epoch_ms(day.isoformat())
        bars.append({
            "timestamp": ts, "volume": 0.0, "open": 3000.0, "high": 3010.0,
            "low": 2990.0, "close": 3000.0, "chg": 0.0, "pct": 0.0,
            "turnover": 0.0, "amount": 0.0,
        })
    return bars


# ─── THS limit-up pool / block top (T = 2026-09-11) ─────────────────────────

POOL_TOTAL = 62

FIRST_LIMIT_1005 = epoch_s(TRADE_DATE, 10, 5)
FIRST_LIMIT_0930 = epoch_s(TRADE_DATE, 9, 30)

POOL_ROWS = [
    {"code": "600005", "reason_type": "机器人", "limit_up_type": "换手板",
     "open_num": 0, "is_again_limit": 1, "high_days": "3天3板",
     "change_rate": 10.0, "turnover_rate": 12.0, "limit_up_suc_rate": 1.0,
     "first_limit_up_time": FIRST_LIMIT_1005},
    {"code": "600004", "reason_type": "银行科技", "limit_up_type": "T字板",
     "open_num": 5, "is_again_limit": 0, "high_days": "2天2板",
     "change_rate": 10.0, "turnover_rate": 4.0, "limit_up_suc_rate": 0.8,
     "first_limit_up_time": FIRST_LIMIT_0930},
]

BLOCK_ROWS = [
    {"name": "机器人", "limit_up_num": 23, "high": "4天2板", "high_num": 4,
     "stock_list": [{"code": "600005"}, {"code": "600003"}]},
    {"name": "AI算力", "limit_up_num": 12, "high": "2天2板", "high_num": 2,
     "stock_list": [{"code": "600001"}]},
    {"name": "银行科技", "limit_up_num": 5, "high": "1天1板", "high_num": 1,
     "stock_list": [{"code": "600004"}]},
]

FG_BOARDS = {
    "昨日涨停": {"rows": [
        {"code": "600001", "close": 11.0, "pre_close": 10.0},
        {"code": "600002", "close": 10.5, "pre_close": 10.0},
        {"code": "600003", "close": 10.2, "pre_close": 10.0},
    ], "status": "ok"},
    "昨日跌停": {"rows": [
        {"code": "600010", "close": 9.0, "pre_close": 10.0},
        {"code": "600011", "close": 9.0, "pre_close": 10.0},
    ], "status": "ok"},
    "昨日连板": {"rows": [{"code": "600001"}], "status": "ok"},
    "昨日首板": {"rows": [{"code": "600002"}], "status": "ok"},
    "昨日断板": {"rows": [], "status": "ok"},
    "昨日上榜": {"rows": [{"code": "600001"}, {"code": "600003"}], "status": "ok"},
}

# ─── market breadth rows (3105 active rows; 2100/950/50 + 5 specials) ──────

BREADTH_UP = 2100
BREADTH_DOWN = 950
BREADTH_FLAT = 50
BREADTH_TOTAL = BREADTH_UP + BREADTH_DOWN + BREADTH_FLAT + 5
BREADTH_TOTAL_AMOUNT = (
    (BREADTH_UP + BREADTH_DOWN + BREADTH_FLAT) * 10000.0
    + 11000.0 + 10500.0 + 3 * 9000.0
)


def breadth_rows() -> list[dict]:
    rows = []
    for index in range(BREADTH_UP):
        rows.append({"code": f"{index:06d}", "close": 10.2, "pre_close": 10.0,
                     "high": 10.3, "vol": 1000, "amount": 10000})
    for index in range(BREADTH_DOWN):
        rows.append({"code": f"{BREADTH_UP + index:06d}", "close": 9.8,
                     "pre_close": 10.0, "high": 10.1, "vol": 1000,
                     "amount": 10000})
    for index in range(BREADTH_FLAT):
        rows.append({"code": f"{BREADTH_UP + BREADTH_DOWN + index:06d}",
                     "close": 10.0, "pre_close": 10.0, "high": 10.0,
                     "vol": 1000, "amount": 10000})
    rows.append({"code": "900001", "close": 11.0, "pre_close": 10.0,
                 "high": 11.0, "vol": 1000, "amount": 11000,
                 "buy_price_limit": 11.0})
    rows.append({"code": "900002", "close": 10.5, "pre_close": 10.0,
                 "high": 11.0, "vol": 1000, "amount": 10500,
                 "buy_price_limit": 11.0})
    for code in ("900003", "900004", "900005"):
        rows.append({"code": code, "close": 9.0, "pre_close": 10.0,
                     "high": 9.1, "low": 9.0, "vol": 1000, "amount": 9000,
                     "sell_price_limit": 9.0})
    return rows


# ─── quotes (prep fund-flow + auction scoring inputs) ──────────────────────


def _quote(code: str, name: str, price: float, prev_close: float, pct: float,
           turnover: float, amount: float, main_net: float,
           *, limit_up: float = 11.0, limit_down: float = 9.0,
           float_cap: float = 800_000_000.0) -> dict:
    return {
        "code": code, "name": name, "price": price, "prev_close": prev_close,
        "open_px": prev_close, "high": max(price, prev_close),
        "low": min(price, prev_close), "pct": pct, "chg": round(price - prev_close, 4),
        "turnover_rate": turnover, "amplitude": 5.0, "volume": 1_000_000.0,
        "amount": amount, "market_cap": float_cap * 1.25,
        "float_market_cap": float_cap, "volume_ratio": 1.5, "pe": 20.0,
        "limit_up": limit_up, "limit_down": limit_down, "avg_price": price,
        "bid1_price": 0.0, "bid1_volume": 0.0, "ask1_volume": 0.0,
        "timestamp": epoch_ms(TRADE_DATE), "main_net_amount": main_net,
    }


QUOTES = [
    _quote("600001", "龙头A", 10.5, 10.0, 5.0, 8.0, 100_000_000.0, 12_345_600.0),
    _quote("600002", "跟风B", 9.78, 10.0, -2.2, 5.0, 80_000_000.0, -5_000_000.0),
    _quote("600003", "补涨C", 9.0, 10.0, -10.0, 6.0, 50_000_000.0, 2_000_000.0),
    _quote("600005", "龙头E", 11.0, 10.0, 10.0, 12.0, 500_000_000.0, 30_000_000.0),
    _quote("600004", "银行D", 9.9, 10.0, -1.0, 4.0, 20_000_000.0, -1_000_000.0),
]

QUOTE_BY_CODE = {row["code"]: row for row in QUOTES}

# ─── cninfo announcements (one page per code: the old item stops paging) ───

OLD_ANNOUNCEMENT = {"title": "较早公告", "type": "例行", "date": "2026-08-01",
                    "url": "u"}
SOFT_ANNOUNCEMENT = {"title": "关于股东减持计划的公告", "type": "减持",
                     "date": "2026-09-10", "url": "u"}
HARD_ANNOUNCEMENT = {"title": "立案调查公告", "type": "立案",
                     "date": "2026-09-10", "url": "u"}

ANNOUNCEMENTS = {
    "600001": [OLD_ANNOUNCEMENT],
    "600002": [SOFT_ANNOUNCEMENT, OLD_ANNOUNCEMENT],
    "600003": [OLD_ANNOUNCEMENT],
    "600004": [HARD_ANNOUNCEMENT, OLD_ANNOUNCEMENT],
    "600005": [OLD_ANNOUNCEMENT],
}

# ─── macro instruments (all four MACRO_SPECS keys, tdx only) ───────────────


def _macro_record(key: str, label: str, kind: str, price_unit: str,
                  chg_unit: str, price: float, prev_close: float | None,
                  chg: float | None, pct: float | None, *, chg_bp=None,
                  chg_bp_approx=None, precision_note=None, code: str,
                  market: str, session_date: str) -> dict:
    return {
        "key": key, "label": label, "kind": kind, "price_unit": price_unit,
        "chg_unit": chg_unit, "price": price, "prev_close": prev_close,
        "chg": chg, "pct": pct, "chg_bp": chg_bp,
        "chg_bp_approx": chg_bp_approx, "precision_note": precision_note,
        "source": "tdx", "code": code, "market": market,
        "session_date": session_date,
        "raw_date": session_date.replace("-", ""), "raw_time": "15:00:00",
    }


MACRO_INSTRUMENTS = {
    "jp_nikkei": _macro_record(
        "jp_nikkei", "日经225期指", "index", "points", "pct",
        64750.0, 64360.0, 390.0, 0.61, code="NK0Y", market="INTL_INDEX",
        session_date=TRADE_DATE),
    "cn_a50": _macro_record(
        "cn_a50", "富时A50期指", "index", "points", "pct",
        13164.0, 13279.0, -115.0, -0.87, code="CNY0", market="INTL_INDEX",
        session_date=TRADE_DATE),
    "ust10y": _macro_record(
        "ust10y", "美债10Y", "yield", "pct", "bp",
        5.15, 5.09, 0.06, None, chg_bp=6.0, chg_bp_approx=True,
        precision_note="TDX 两位小数日频快照，bp 为近似值", code="8_ATY",
        market="MACRO_INDICATOR", session_date="2026-09-10"),
    "gold": _macro_record(
        "gold", "COMEX金", "commodity", "usd_per_oz", "pct",
        4160.3, 4186.7, -26.4, -0.63, code="GC00W", market="COMEX_FUTURES",
        session_date="2026-09-10"),
}

MACRO_KEYS = ("jp_nikkei", "cn_a50", "ust10y", "gold")

# ─── dragon rows (mirror + source) ─────────────────────────────────────────

DRAGON_COLUMNS = {
    "trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL", "name": "TEXT",
    "scan_id": "TEXT", "rank": "INTEGER", "composite_score": "REAL",
    "board_count": "INTEGER", "open_px": "REAL", "close_px": "REAL",
    "high_px": "REAL", "low_px": "REAL", "pct": "REAL",
    "turnover_rate": "REAL", "amount": "REAL", "market_cap": "REAL",
    "concepts_json": "TEXT", "report_text": "TEXT", "is_true_dragon": "INTEGER",
    "version": "TEXT", "created_at": "TEXT", "buy_date": "TEXT",
    "buy_price": "REAL", "max_return_5d": "REAL", "max_drawdown_5d": "REAL",
    "max_return_hold_days": "INTEGER", "review_status": "TEXT", "source": "TEXT",
}


def dragon_row(trade_date: str, code: str, name: str, rank: int,
               score: float, concepts: list[str]) -> dict:
    return {
        "trade_date": trade_date, "code": code, "name": name,
        "scan_id": f"v2_{trade_date.replace('-', '')}_5", "rank": rank,
        "composite_score": score, "board_count": 2, "open_px": 10.0,
        "close_px": 11.0, "high_px": 11.0, "low_px": 10.0, "pct": 10.0,
        "turnover_rate": 8.0, "amount": 100_000_000.0,
        "market_cap": 1_000_000_000.0,
        "concepts_json": json.dumps(concepts, ensure_ascii=False),
        "report_text": f"{name} report", "is_true_dragon": 1,
        "version": "0.7.2", "created_at": f"{trade_date} 15:30:00",
        "buy_date": None, "buy_price": None, "max_return_5d": None,
        "max_drawdown_5d": None, "max_return_hold_days": None,
        "review_status": "pending", "source": "v2",
    }


def window_dragon_rows() -> dict[str, list[dict]]:
    return {
        "2026-09-07": [dragon_row("2026-09-07", "600001", "龙头A", 1, 88.0,
                                  ["AI算力"])],
        "2026-09-08": [
            dragon_row("2026-09-08", "600001", "龙头A", 1, 86.0, ["AI算力"]),
            dragon_row("2026-09-08", "600002", "跟风B", 2, 62.0, ["机器人"]),
        ],
        "2026-09-09": [dragon_row("2026-09-09", "600003", "补涨C", 1, 90.0,
                                  ["机器人"])],
        "2026-09-10": [dragon_row("2026-09-10", "600002", "跟风B", 2, 62.0,
                                  ["机器人"])],
        "2026-09-11": [
            dragon_row("2026-09-11", "600005", "龙头E", 1, 82.0, ["机器人"]),
            dragon_row("2026-09-11", "600004", "银行D", 2, 40.0, ["银行科技"]),
        ],
    }


def flatten_window_rows(rows: dict[str, list[dict]] | None = None):
    rows = rows or window_dragon_rows()
    return [row for day in sorted(rows) for row in rows[day]]


def dragon_db_seed(source_rows: list[dict] | None = None) -> dict:
    """``dragon.db`` spec: ``dragons_v2`` rows (source-of-truth for reconciliation)."""

    return {"tables": {"dragons_v2": {
        "columns": DRAGON_COLUMNS,
        "primary_key": ["trade_date", "code"],
        "rows": list(source_rows or []),
    }}}


def mirror_db_seed(mirror_rows: list[dict] | None = None) -> dict:
    """``assets.db`` spec with only the ``dragons`` mirror table."""

    return {"tables": {"dragons": {
        "columns": DRAGON_COLUMNS,
        "primary_key": ["trade_date", "code"],
        "rows": list(mirror_rows or []),
    }}}


def dragon_payload(trade_date: str, window_dates: list[str],
                   rows: dict[str, list[dict]] | None = None) -> dict:
    """Raw ``prep_assets.dragons`` payload equivalent to ``build_dragons_payload``."""

    rows = rows or window_dragon_rows()
    payload_rows = []
    days = []
    for day in window_dates:
        day_rows = []
        for row in rows.get(day, []):
            item = {
                "code": row["code"], "name": row["name"],
                "scan_id": row["scan_id"], "rank": row["rank"],
                "composite_score": row["composite_score"],
                "board_count": row["board_count"], "open_px": row["open_px"],
                "close_px": row["close_px"], "high_px": row["high_px"],
                "low_px": row["low_px"], "pct": row["pct"],
                "turnover_rate": row["turnover_rate"], "amount": row["amount"],
                "market_cap": row["market_cap"],
                "concepts": json.loads(row["concepts_json"]),
                "report_text": row["report_text"],
                "is_true_dragon": True, "version": row["version"],
                "source": "v2", "trade_date": day,
            }
            day_rows.append(item)
            payload_rows.append(item)
        scan_ids = sorted({item["scan_id"] for item in day_rows if item.get("scan_id")})
        versions = sorted({item["version"] for item in day_rows if item.get("version")})
        days.append({
            "trade_date": day,
            "scan_id": scan_ids[0] if len(scan_ids) == 1 else None,
            "version": versions[0] if len(versions) == 1 else None,
            "count": len(day_rows),
            "status": "ok" if day_rows else "empty",
        })
    return {
        "trade_date": trade_date,
        "scan_id": None,
        "version": None,
        "count": len(payload_rows),
        "rows": payload_rows,
        "window_days": 5,
        "window_trade_dates": list(window_dates),
        "window_source": "calendar",
        "days": days,
    }


# ─── prep asset payloads used for historical / recompute seeds ────────────


def pool_payload(trade_date: str = TRADE_DATE) -> dict:
    return {"trade_date": trade_date, "api_date": trade_date.replace("-", ""),
            "total": POOL_TOTAL, "rows": [dict(row) for row in POOL_ROWS],
            "truncated": False}


def block_payload(trade_date: str = TRADE_DATE) -> dict:
    return {"trade_date": trade_date, "api_date": trade_date.replace("-", ""),
            "rows": [dict(row) for row in BLOCK_ROWS]}


def cninfo_payload(as_of: str, codes: list[str],
                   trade_date: str = TRADE_DATE) -> dict:
    return {
        "as_of": as_of, "window_days": 7, "window_start": "2026-09-04",
        "items": {
            code: {"announcements": [], "max_announcement_date": "2026-08-01",
                   "truncated": False}
            for code in codes
        },
        "truncated_codes": [],
    }


def macro_payload(trade_date: str = TRADE_DATE) -> dict:
    return {
        "trade_date": trade_date, "as_of": trade_date,
        "instruments": {key: dict(record)
                        for key, record in MACRO_INSTRUMENTS.items()},
        "degraded": [],
        "sources": {key: "tdx" for key in MACRO_KEYS},
        "source_errors": {},
    }


def report_ok(types: list[str]) -> dict:
    return {asset_type: {"status": "ok"} for asset_type in types}


def prep_assets_tables(assets: dict[str, dict]) -> dict:
    """Seed spec for ``assets.db.prep_assets`` ({asset_type: payload})."""

    return {"tables": {"prep_assets": {
        "columns": {
            "trade_date": "TEXT NOT NULL",
            "asset_type": "TEXT NOT NULL",
            "payload_json": "TEXT NOT NULL",
            "created_at": "TEXT",
        },
        "primary_key": ["trade_date", "asset_type"],
        "rows": [
            {"trade_date": TRADE_DATE, "asset_type": asset_type,
             "payload_json": json.dumps(payload, ensure_ascii=False,
                                        sort_keys=True),
             "created_at": f"{TRADE_DATE} 16:45:00"}
            for asset_type, payload in assets.items()
        ],
    }}}


# ─── auction raw items (TDX get_auction shape) ─────────────────────────────


def _item(clock: str, price: float, matched: float, unmatched: float) -> dict:
    return {"time": clock, "price": price, "matched": matched,
            "unmatched": unmatched}


AUCTION_ITEMS_600001 = [
    _item("09:15:00", 10.40, 0, 100),
    _item("09:18:00", 10.45, 5000, 200),
    _item("09:20:00", 10.50, 12000, 150),
    _item("09:21:00", 10.52, 16000, 300),
    _item("09:22:00", 10.55, 21000, 250),
    _item("09:23:00", 10.58, 26000, 200),
    _item("09:24:00", 10.62, 32000, 300),
    _item("09:24:30", 10.66, 38000, 250),
    _item("09:24:45", 10.68, 43000, 200),
    _item("09:25:00", 10.70, 50000, 150),
]

AUCTION_ITEMS_600002 = [
    _item("09:15:00", 10.00, 0, -100),
    _item("09:18:00", 9.95, 2000, -200),
    _item("09:20:00", 9.90, 5000, -150),
    _item("09:21:00", 9.88, 7000, -300),
    _item("09:22:00", 9.86, 9000, -250),
    _item("09:23:00", 9.84, 11000, -200),
    _item("09:24:00", 9.82, 13000, -300),
    _item("09:24:30", 9.80, 15000, -250),
    _item("09:24:45", 9.79, 16500, -200),
    _item("09:25:00", 9.78, 18000, -150),
]

AUCTION_ITEMS_600003 = [
    _item("09:15:00", 10.00, 0, -100),
    _item("09:18:00", 9.80, 500, -200),
    _item("09:20:00", 9.50, 1000, -300),
    _item("09:21:00", 9.20, 1500, -400),
    _item("09:22:00", 9.10, 2000, -500),
    _item("09:23:00", 9.05, 2500, -600),
    _item("09:24:00", 9.02, 3000, -700),
    _item("09:24:30", 9.01, 3500, -800),
    _item("09:24:45", 9.00, 4000, -900),
    _item("09:25:00", 9.00, 4500, -1000),
]

AUCTION_ITEMS = {
    "600001": AUCTION_ITEMS_600001,
    "600002": AUCTION_ITEMS_600002,
    "600003": AUCTION_ITEMS_600003,
}

AUCTION_CANDIDATES = [
    {"code": "600001", "name": "龙头A", "composite_score": 80.0,
     "is_true_dragon": True, "verdict": "strong", "verdict_label": "强烈建议"},
    {"code": "600002", "name": "跟风B", "composite_score": 70.0,
     "is_true_dragon": True, "verdict": "light", "verdict_label": "轻仓博弈"},
    {"code": "600003", "name": "补涨C", "composite_score": 90.0,
     "is_true_dragon": True, "verdict": "strong", "verdict_label": "强烈建议"},
    {"code": "600004", "name": "银行D", "composite_score": 95.0,
     "is_true_dragon": True, "verdict": "ignore", "verdict_label": "忽略",
     "ignore_reason": "资金离场"},
]

AUCTION_PREV_DATE = "2026-09-10"


def prep_snapshot_seed(candidates: list[dict] | None = None,
                       snapshot_date: str = AUCTION_PREV_DATE) -> dict:
    payload = {
        "trade_date": snapshot_date,
        "version": "0.6",
        "sentiment": {"position_pct": 0.5, "position_advice": "正常",
                      "degraded": []},
        "main_boards": [],
        "macro": {"available": False},
        "candidates": list(candidates or AUCTION_CANDIDATES),
        "dragon_source": {"window_days": 5, "dates": []},
    }
    return {"tables": {"prep_snapshots": {
        "columns": {
            "trade_date": "TEXT NOT NULL", "source": "TEXT NOT NULL",
            "result_json": "TEXT NOT NULL", "created_at": "TEXT",
        },
        "primary_key": ["trade_date", "source"],
        "rows": [{
            "trade_date": snapshot_date, "source": "v2",
            "result_json": json.dumps(payload, ensure_ascii=False,
                                      sort_keys=True),
            "created_at": f"{snapshot_date} 16:45:00",
        }],
    }}}


def auction_assets_seed(rows: list[dict]) -> dict:
    """Explicit ``auction_assets`` rows (used for corruption tests)."""

    return {"tables": {"auction_assets": {
        "columns": {
            "trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
            "name": "TEXT", "items_json": "TEXT NOT NULL",
            "quote_json": "TEXT", "created_at": "TEXT",
        },
        "primary_key": ["trade_date", "code"],
        "rows": rows,
    }}}


def auction_snapshots_seed(rows: list[dict]) -> dict:
    return {"tables": {"auction_snapshots": {
        "columns": {
            "trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
            "source": "TEXT NOT NULL", "analysis_json": "TEXT NOT NULL",
            "created_at": "TEXT",
        },
        "primary_key": ["trade_date", "code", "source"],
        "rows": rows,
    }}}
