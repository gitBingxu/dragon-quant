"""Reviewed synthetic data for the factor E2E baseline (M1E1).

Only stdlib; this module imports no application code.  It is *fixture data*:

* :func:`dragon_db_seed` builds the ``dragon.db`` spec (``scans_v2`` /
  ``scan_stocks_v2`` / ``vpa_analysis``) that ``factor build`` migrates;
* :func:`scenario_payload` builds ``scenario_factor.json`` — the fake
  provider responses for the xueqiu daily-K path used by ``factor eval`` and
  the TDX finance/calendar path used by ``factor fin *`` / fin-factor build.

The generated JSON file (``scenario_factor.json``) is the source of truth for
the child processes; regenerate it only when the reviewed numbers below
change::

    .venv/bin/python dragon_quant/tests/e2e/fixtures/factor_builders.py
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:  # package import under pytest; script mode when run directly
    from . import seed_builders as sb
except ImportError:  # pragma: no cover - direct script execution
    import seed_builders as sb  # type: ignore

CHINA = timezone(timedelta(hours=8))
FIXTURES = Path(__file__).resolve().parent

#: Four scan days.  2026-09-11 is the frozen "today" used by most cases.
SCAN_DATES = ("2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11")
TODAY = "2026-09-11"
NOW = "2026-09-11T15:30:00+08:00"

CODES = ("600001", "600002", "600003", "600004", "600005", "600006")
NAMES = ("龙头A", "跟风B", "补涨C", "银行D", "龙头E", "一字F")

# ─── per-day five-dimension values (index order == CODES) ──────────────────
# ``None`` = source column NULL -> the factor row must be absent (缺失=无行).
COMPOSITE = (10.0, 20.0, 30.0, 40.0, 50.0, 60.0)
DRIVE = (90.0, 80.0, 70.0, 60.0, 50.0, 40.0)
LEADERSHIP = (10.0, 20.0, 30.0, 40.0, 50.0, 60.0)
ANTI_DROP = (75.0, 75.0, 75.0, 75.0, 75.0, 75.0)   # MAD == 0 (winsorize skip)
LIQUIDITY = (55.0, None, 55.0, 55.0, 55.0, 55.0)   # 600002 missing
ABSORPTION = (49.0, 49.0, 49.0, 49.0, None, 49.0)  # 600005 missing
TRUE_DRAGON = (1, 1, 0, None, 1, 0)                # 600004 NULL (missing != 0)

SECTOR_CODE = "881101"
SECTOR_NAME = "行业板块"

#: ``created_at`` pinned for every scan row -> ``available_at`` is exact.
SCAN_CREATED_AT = f"{TODAY} 15:30:00"

# ─── eval forward-return construction ──────────────────────────────────────
# open(t) recursion: base 10.0 on 2026-09-09; r0 produces the 2026-09-08
# labels (rank 1..6, Spearman +1); r1 produces the 2026-09-09 labels with
# rank vector [1, 2, 6, 3, 5, 4] -> Spearman 1 - 6*14/(6*35) = 0.6.
EVAL_DATES = ("2026-09-09", "2026-09-10", "2026-09-11")
R0 = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06)
R1 = (0.01, 0.02, 0.06, 0.03, 0.05, 0.04)

# ─── vpa_analysis rows (2026-09-11 only) ──────────────────────────────────
VPA_FACTORS = {
    "600001": [("vol_amount", 70.0), ("trend_verify", 65.0),
               ("breakout", 50.0), ("divergence", 40.0)],
    "600002": [("vol_amount", 60.0), ("trend_verify", 55.0),
               ("divergence", 30.0)],                       # breakout missing
    "600003": [("vol_amount", 50.0), ("trend_verify", 45.0),
               ("breakout", 60.0), ("divergence", 20.0)],
    "600004": [("vol_amount", 40.0), ("trend_verify", 35.0),
               ("breakout", 70.0), ("divergence", 10.0)],
}
VPA_HEALTH = {"600001": 80.0, "600002": 70.0, "600003": 60.0, "600004": 50.0}
#: Cross-day backfill row (created_at != trade_date) -> skipped entirely.
VPA_BACKFILLED = {"code": "600005", "health_score": 30.0,
                  "created_at": "2026-09-10 15:30:00"}

# ─── finance fixture (verified calibration + Q2 ledger + Q1/Q2 observed) ──
FIN_LAYOUT = "gpcw_10"
FIN_PERIOD = "2026-06-30"
FIN_PRIOR_PERIOD = "2026-03-31"       # 600005 only -> cohort mismatch
FIN_MD5 = "md5-q2-v1"
FIN_FILENAME = "tdxfin/gpcw20260630.zip"

#: field-array order for the 10-column gpcw layout (index -> semantic field).
FIN_FIELD_ORDER = (
    "zong_guben", "meigujing_zichan", "jing_zichan", "zhuying_shouru",
    "jing_lirun", "yingye_lirun", "zhuying_lirun", "shuihou_lirun",
)

SHARES = 100_000.0          # 万股 -> identity: bps * shares * 1e4 == assets


def _fin_values(index: int) -> dict:
    bps = 5.5 + 0.5 * index
    return {
        "zong_guben": SHARES,
        "meigujing_zichan": bps,
        "jing_zichan": bps * SHARES * 1e4,
        "zhuying_shouru": 3.0e9 + index * 1e8,
        "jing_lirun": 3.0e8 + index * 1e7,
        "yingye_lirun": 3.5e8 + index * 1e7,
        "zhuying_lirun": 4.0e8 + index * 1e7,
        "shuihou_lirun": 2.9e8 + index * 1e7,
    }


#: Registered factor names (reviewed from ``factor list --json``; sorted).
EXPECTED_FACTOR_NAMES = (
    "auction_dim_direction", "auction_dim_liquidity", "auction_dim_supply",
    "auction_dim_tail_grab", "auction_dim_vol_price", "auction_direction",
    "auction_final_slippage_pct", "auction_is_weak", "auction_open_gap_pct",
    "auction_rank", "auction_strength", "auction_strong_to_weak",
    "auction_tail_matched_acceleration", "auction_turnover_pct",
    "auction_unmatched_matched_ratio", "auction_volume_ratio_5d",
    "auction_weak_to_strong", "fin_bps", "fin_net_assets",
    "fin_net_margin", "fin_net_profit_yoy", "fin_revenue_yoy", "fin_roe",
    "five_dim_absorption", "five_dim_anti_drop", "five_dim_composite",
    "five_dim_drive", "five_dim_is_true_dragon", "five_dim_leadership",
    "five_dim_liquidity", "prep_chip_concentration",
    "prep_dim_d1", "prep_dim_d2", "prep_dim_d3", "prep_dim_d4", "prep_dim_d5",
    "prep_dim_d6", "prep_distance_high60", "prep_feedback_state",
    "prep_high_days", "prep_in_pool_today", "prep_limit_up_suc_rate",
    "prep_main_net_ratio", "prep_mainline_rank", "prep_market_state",
    "prep_position_pct", "prep_review_grade", "prep_review_score",
    "prep_role", "prep_seal_ratio", "prep_t_pct", "prep_t_turnover",
    "prep_turnover_health", "vpa_breakout", "vpa_divergence",
    "vpa_health_score", "vpa_trend_verify", "vpa_vol_amount",
)

#: ``default_enabled=True`` factors (``factor build`` without ``--only``).
DEFAULT_ENABLED_NAMES = (
    "five_dim_absorption", "five_dim_anti_drop", "five_dim_composite",
    "five_dim_drive", "five_dim_is_true_dragon", "five_dim_leadership",
    "five_dim_liquidity", "vpa_breakout", "vpa_divergence",
    "vpa_health_score", "vpa_trend_verify", "vpa_vol_amount",
)

#: ``five_dim_composite`` row count / value expectations for one scan day.
FIVE_DIM_ROWS = {
    "five_dim_composite": 6,
    "five_dim_drive": 6,
    "five_dim_leadership": 6,
    "five_dim_anti_drop": 6,
    "five_dim_liquidity": 5,
    "five_dim_absorption": 5,
    "five_dim_is_true_dragon": 5,
}
VPA_ROWS = {
    "vpa_health_score": 4,
    "vpa_vol_amount": 4,
    "vpa_trend_verify": 4,
    "vpa_breakout": 3,
    "vpa_divergence": 4,
}
SINGLE_DAY_FACTOR_ROWS = 58   # sum(FIVE_DIM_ROWS) + sum(VPA_ROWS)

FIN_LEDGER = {code: _fin_values(index)
              for index, code in enumerate(("600001", "600002", "600003",
                                            "600004"))}
_FIN_PERIOD_INT = int(FIN_PERIOD.replace("-", ""))
_FIN_PRIOR_INT = int(FIN_PRIOR_PERIOD.replace("-", ""))
FIN_OBSERVED = {
    "600001": dict(FIN_LEDGER["600001"], updated_date=_FIN_PERIOD_INT),
    "600002": dict(FIN_LEDGER["600002"], updated_date=_FIN_PERIOD_INT),
    "600003": dict(FIN_LEDGER["600003"], updated_date=_FIN_PERIOD_INT),
    "600004": dict(FIN_LEDGER["600004"], updated_date=_FIN_PERIOD_INT),
    # Not in the ledger: Q1 snapshot -> excluded by the modal-period cohort.
    "600005": {
        "zong_guben": 80_000.0, "meigujing_zichan": 4.0,
        "jing_zichan": 3.2e9, "zhuying_shouru": 2.0e9,
        "jing_lirun": 2.0e8, "yingye_lirun": 2.4e8,
        "zhuying_lirun": 2.8e8, "shuihou_lirun": 1.9e8,
        "updated_date": _FIN_PRIOR_INT,
    },
    # Not in the ledger: Q2 snapshot -> joins the cohort via observed fallback.
    "600006": {
        "zong_guben": 120_000.0, "meigujing_zichan": 8.0,
        "jing_zichan": 9.6e9, "zhuying_shouru": 4.0e9,
        "jing_lirun": 4.0e8, "yingye_lirun": 4.5e8,
        "zhuying_lirun": 5.0e8, "shuihou_lirun": 3.9e8,
        "updated_date": _FIN_PERIOD_INT,
    },
}


def _fields(values: dict) -> list[float]:
    return [float(values[field]) for field in FIN_FIELD_ORDER] + [0.0, -1.0]


def _epoch_ms(day: str, hour: int = 15, minute: int = 0) -> int:
    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, hour, minute,
                        tzinfo=CHINA).timestamp() * 1000)


def _bar(day: str, open_px: float) -> dict:
    close = round(open_px, 4)
    return {
        "timestamp": _epoch_ms(day),
        "volume": 1_000_000.0,
        "open": close,
        "high": round(close * 1.002, 4),
        "low": round(close * 0.998, 4),
        "close": close,
        "chg": 0.0,
        "pct": 0.0,
        "turnover": 3.0,
        "amount": round(1_000_000.0 * close, 4),
    }


def eval_bars() -> dict[str, list[dict]]:
    """Daily K for the six codes across the three eval dates."""

    opens = {}
    for index, code in enumerate(CODES):
        first = 10.0
        second = round(first * (1.0 + R0[index]), 4)
        third = round(second * (1.0 + R1[index]), 4)
        opens[code] = (first, second, third)
    return {
        code: [_bar(day, open_px)
               for day, open_px in zip(EVAL_DATES, opens[code])]
        for code in CODES
    }


def scenario_payload() -> dict:
    """Reviewed fake-provider responses for ``scenario_factor.json``."""

    calendar = sb.calendar_bars()
    return {
        "providers": {
            "xueqiu": {
                "get_kline": {
                    "by_code": {
                        **eval_bars(),
                        "SH000001": calendar,
                    }
                }
            },
            "tdx": {
                "get_kline": {"by_code": {"SH000001": calendar}},
                "get_financial_files": [{
                    "filename": FIN_FILENAME,
                    "md5": FIN_MD5,
                    "size": 12345,
                }],
                "get_financial_records": [
                    {"code": code, "fields": _fields(values)}
                    for code, values in FIN_LEDGER.items()
                ],
                "get_finance_info": {
                    "by_code": {
                        code: dict(values)
                        for code, values in FIN_OBSERVED.items()
                    }
                },
            },
            "ths": {},
            "tencent": {},
        }
    }


# ─── dragon.db seed ────────────────────────────────────────────────────────


def _scan_rows() -> list[dict]:
    rows = []
    for scan_date in SCAN_DATES:
        for index, code in enumerate(CODES):
            rows.append({
                "scan_id": f"v2_{scan_date.replace('-', '')}_5",
                "code": code,
                "name": NAMES[index],
                "rank": index + 1,
                "composite_score": COMPOSITE[index],
                "board_count": 1 if index % 2 else 3,
                "concepts_json": json.dumps([SECTOR_NAME], ensure_ascii=False),
                "dim_drive": DRIVE[index],
                "dim_anti_drop": ANTI_DROP[index],
                "dim_leadership": LEADERSHIP[index],
                "dim_absorption": ABSORPTION[index],
                "dim_liquidity": LIQUIDITY[index],
                "is_true_dragon": TRUE_DRAGON[index],
                "reject_reason": None,
                "report_text": f"{NAMES[index]}({code})",
                "source": "v2",
            })
    return rows


def _raw_output(scan_date: str) -> str:
    ranking = [
        {
            "code": code,
            "primary_sector": SECTOR_CODE,
            "primary_sector_name": SECTOR_NAME,
            "primary_sector_category": "hy",
            "primary_sector_source": "tdx",
        }
        for code in CODES
    ]
    return json.dumps({"ranking": ranking}, ensure_ascii=False)


def _vpa_rows() -> list[dict]:
    rows = []
    for index, code in enumerate(sorted(VPA_FACTORS)):
        factors = [{"name": name, "score": score}
                   for name, score in VPA_FACTORS[code]]
        rows.append({
            "id": index + 1,
            "trade_date": TODAY,
            "code": code,
            "name": NAMES[CODES.index(code)],
            "source": "xueqiu",
            "health_score": VPA_HEALTH[code],
            "signal": "healthy",
            "summary": f"{code} summary",
            "factors_json": json.dumps(factors, ensure_ascii=False),
            "version": "0.7.2",
            "created_at": SCAN_CREATED_AT,
        })
    rows.append({
        "id": len(rows) + 1,
        "trade_date": TODAY,
        "code": VPA_BACKFILLED["code"],
        "name": NAMES[CODES.index(VPA_BACKFILLED["code"])],
        "source": "xueqiu",
        "health_score": VPA_BACKFILLED["health_score"],
        "signal": "healthy",
        "summary": "cross-day backfill",
        "factors_json": json.dumps([{"name": "vol_amount", "score": 30.0}]),
        "version": "0.7.2",
        "created_at": VPA_BACKFILLED["created_at"],
    })
    return rows


def dragon_db_seed() -> dict:
    return {
        "tables": {
            "scans_v2": {
                "columns": {
                    "id": "TEXT NOT NULL",
                    "scan_date": "TEXT NOT NULL",
                    "elapsed_s": "REAL",
                    "top_n": "INTEGER",
                    "candidates_n": "INTEGER",
                    "workers": "INTEGER",
                    "raw_output": "TEXT",
                    "source": "TEXT",
                    "created_at": "TEXT",
                },
                "primary_key": ["id"],
                "rows": [
                    {
                        "id": f"v2_{scan_date.replace('-', '')}_5",
                        "scan_date": scan_date,
                        "elapsed_s": 12.5,
                        "top_n": 5,
                        "candidates_n": len(CODES),
                        "workers": 1,
                        "raw_output": _raw_output(scan_date),
                        "source": "v2",
                        "created_at": f"{scan_date} 15:30:00",
                    }
                    for scan_date in SCAN_DATES
                ],
            },
            "scan_stocks_v2": {
                "columns": {
                    "scan_id": "TEXT NOT NULL",
                    "code": "TEXT NOT NULL",
                    "name": "TEXT",
                    "rank": "INTEGER",
                    "composite_score": "REAL",
                    "board_count": "INTEGER",
                    "concepts_json": "TEXT",
                    "dim_drive": "REAL",
                    "dim_anti_drop": "REAL",
                    "dim_leadership": "REAL",
                    "dim_absorption": "REAL",
                    "dim_liquidity": "REAL",
                    "is_true_dragon": "INTEGER",
                    "reject_reason": "TEXT",
                    "report_text": "TEXT",
                    "source": "TEXT",
                },
                "primary_key": ["scan_id", "code"],
                "rows": _scan_rows(),
            },
            "vpa_analysis": {
                "columns": {
                    "id": "INTEGER NOT NULL",
                    "trade_date": "TEXT NOT NULL",
                    "code": "TEXT NOT NULL",
                    "name": "TEXT",
                    "source": "TEXT",
                    "health_score": "REAL",
                    "signal": "TEXT",
                    "summary": "TEXT",
                    "factors_json": "TEXT",
                    "version": "TEXT",
                    "created_at": "TEXT",
                },
                "primary_key": ["id"],
                "rows": _vpa_rows(),
            },
        }
    }


def write_scenario(path: str | Path | None = None) -> Path:
    target = Path(path) if path else FIXTURES / "scenario_factor.json"
    target.write_text(
        json.dumps(scenario_payload(), ensure_ascii=False, sort_keys=True,
                   indent=2) + "\n",
        encoding="utf-8",
    )
    return target


if __name__ == "__main__":  # pragma: no cover - fixture generation
    print(write_scenario())
