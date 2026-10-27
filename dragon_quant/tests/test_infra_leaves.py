"""Task 3 infra leaf contracts: clock / models / market_calendar.

Canonical definitions live in :mod:`dragon_quant.infra`.  During migration the
old paths (``runtime.clock`` / ``models.types`` / ``utils.trading``) are alias
shims; this file only asserts the new home, leaving alias identity to
``dragon_quant/tests/test_legacy_shims.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

CLOCK_EXPORTS = (
    "Clock",
    "SystemClock",
    "FixedClock",
    "DEFAULT_STAMP_FORMAT",
    "get_clock",
    "set_clock",
    "reset_clock",
    "now",
    "today",
    "stamp",
    "monotonic",
)

MODEL_EXPORTS = (
    "KBar",
    "StockInfo",
    "Quote",
    "SectorPerformance",
    "Candidate",
    "ScoreResult",
)

CALENDAR_EXPORTS = (
    "build_trade_calendar",
    "tdx_trade_dates",
    "trade_days_between",
    "is_limit_up",
    "kbar_to_dict",
    "find_entry_day",
)


def _run_python(code: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "DQ_DATA_DIR": str(tmp_path / "data"),
        "HOME": str(tmp_path / "home"),
    }
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


# ─── clock ─────────────────────────────────────────────────────────────────


def test_clock_is_one_process_seam():
    from dragon_quant.infra import clock

    original = clock.get_clock()
    fixed = clock.FixedClock(
        datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc))
    assert clock.set_clock(fixed) is original
    try:
        assert clock.get_clock() is fixed
        assert clock.now(timezone.utc) == fixed.now(timezone.utc)
        assert clock.today(timezone.utc) == date(2026, 10, 25)
        assert clock.stamp() == "20261025_093000"
        assert clock.monotonic() == 0.0
    finally:
        assert clock.set_clock(original) is fixed
    assert clock.get_clock() is original


def test_clock_public_interface_is_unchanged():
    from dragon_quant.infra import clock

    missing = [name for name in CLOCK_EXPORTS if not hasattr(clock, name)]
    assert not missing, missing
    assert clock.DEFAULT_STAMP_FORMAT == "%Y%m%d_%H%M%S"
    assert isinstance(clock.get_clock(), clock.SystemClock)

    current = clock.get_clock()
    with pytest.raises(TypeError):
        clock.set_clock(object())
    assert clock.get_clock() is current, "failed install must not change the seam"


def test_build_services_binds_the_active_infra_clock_seam():
    """app.build_services must not fall back to a second SystemClock instance."""
    from dragon_quant.app import build_services
    from dragon_quant.infra import clock

    previous = clock.get_clock()
    installed = clock.FixedClock(datetime(2026, 10, 25, 15, 0))
    clock.set_clock(installed)
    try:
        services = build_services()
        assert services.clock is installed
        assert services.clock is clock.get_clock()
    finally:
        clock.set_clock(previous)
    assert build_services().clock is clock.get_clock()


# ─── models ────────────────────────────────────────────────────────────────


def test_models_are_defined_in_infra():
    from dragon_quant.infra import models

    for name in MODEL_EXPORTS:
        model = getattr(models, name)
        assert is_dataclass(model), name
        assert model.__module__ == "dragon_quant.infra.models", name


def test_model_fields_and_defaults_are_unchanged():
    from dragon_quant.infra import models

    assert [f.name for f in fields(models.KBar)] == [
        "timestamp", "volume", "open", "high", "low", "close", "chg", "pct",
        "turnover", "amount",
    ]
    assert [f.name for f in fields(models.StockInfo)] == [
        "code", "name", "exchange", "sector_code", "sector_name", "pct",
        "price", "five_day_return",
    ]

    quote_fields = fields(models.Quote)
    assert [f.name for f in quote_fields][:19] == [
        "code", "name", "price", "prev_close", "open_px", "high", "low",
        "pct", "chg", "turnover_rate", "amplitude", "volume", "amount",
        "market_cap", "float_market_cap", "volume_ratio", "pe", "limit_up",
        "limit_down",
    ]
    optional_quote_defaults = {
        f.name: f.default for f in quote_fields if f.default is not MISSING
    }
    assert optional_quote_defaults == {
        "bid1_price": 0.0,
        "bid1_volume": 0.0,
        "ask1_volume": 0.0,
        "timestamp": 0,
        "main_net_amount": None,
    }

    first = models.Candidate(code="600519", name="贵州茅台")
    second = models.Candidate(code="000001", name="平安银行")
    first.concepts.append("白酒")
    assert second.concepts == [], "concepts must keep a per-instance factory"

    result = models.ScoreResult(dim="drive", score=1.0, weight=0.3)
    assert result.details == {}
    result.details["x"] = 1
    assert models.ScoreResult("drive", 1.0, 0.3).details == {}


# ─── market calendar ───────────────────────────────────────────────────────


def test_market_calendar_importable():
    from dragon_quant.infra.market_calendar import is_limit_up, trade_days_between

    assert callable(is_limit_up) and callable(trade_days_between)


def test_market_calendar_public_interface_is_unchanged():
    from dragon_quant.infra import market_calendar

    missing = [name for name in CALENDAR_EXPORTS if not callable(getattr(market_calendar, name, None))]
    assert not missing, missing


def test_market_calendar_keeps_trading_day_and_limit_up_semantics():
    from dragon_quant.infra.market_calendar import (
        find_entry_day,
        is_limit_up,
        kbar_to_dict,
        trade_days_between,
    )
    from dragon_quant.infra.models import KBar

    calendar = {"2026-09-01", "2026-09-03", "2026-09-05"}
    assert trade_days_between("2026-09-01", "2026-09-05", calendar) == 2

    assert is_limit_up({"pct": 9.9, "close": 11.0, "high": 11.0}) is True
    assert is_limit_up({"pct": 9.9, "close": 10.9, "high": 11.0}) is False
    assert is_limit_up({"pct": 5.0, "close": 11.0, "high": 11.0}) is False

    stamp = int(datetime(2026, 9, 11, 15, 0).timestamp() * 1000)
    bar = KBar(stamp, 1000.0, 10.0, 10.5, 9.5, 10.2, 0.2, 2.0, 1.0, 1.0e6)
    assert kbar_to_dict(bar) == {
        "date": "2026-09-11",
        "open": 10.0,
        "close": 10.2,
        "high": 10.5,
        "low": 9.5,
        "volume": 1000.0,
        "pct": 2.0,
    }

    klines = [
        {"date": "2026-09-10", "high": 10.0, "low": 9.0},
        {"date": "2026-09-11", "high": 11.0, "low": 11.0},
        {"date": "2026-09-12", "high": 12.0, "low": 11.0},
    ]
    assert find_entry_day(klines, "2026-09-10") == klines[2]
    assert find_entry_day(klines[:2], "2026-09-10") is None


# ─── B1: infra is a zero-eager leaf package ────────────────────────────────


def test_importing_infra_does_not_eager_load_subpackages(tmp_path):
    code = (
        "import sys\n"
        "import dragon_quant.infra\n"
        "assert 'dragon_quant.infra.provider' not in sys.modules\n"
        "assert 'dragon_quant.infra.clock' not in sys.modules\n"
        "from dragon_quant.infra import clock, models, market_calendar\n"
        "assert 'dragon_quant.infra.provider' not in sys.modules\n"
        "print('ok')\n"
    )
    result = _run_python(code, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert not (tmp_path / "data").exists(), "importing infra leaves must not create DQ_DATA_DIR"
