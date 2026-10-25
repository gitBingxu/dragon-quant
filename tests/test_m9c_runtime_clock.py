"""M9C 聚焦测试：commands 剩余取点 + assets_sync 审计补漏。

每个用例把 ``runtime.clock`` 钉在一个时刻，证明这些当前时间取点来自 clock
而不是真实 ``datetime.now``；时区/输出语义保持原样（Asia/Shanghai 日期、
naive ``%Y-%m-%d``、16:40 提示条件）。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from dragon_quant.assets_sync.cli_service import parse_asset_date
from dragon_quant.assets_sync.state import now_iso
from dragon_quant.commands._shared import resolve_trade_date
from dragon_quant.commands.prep import _prep_plan_hint
from dragon_quant.commands.vpa import handle as vpa_handle
from dragon_quant.runtime import clock


def shanghai(day: str, hour: int, minute: int, second: int = 0) -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(day).replace(
        hour=hour, minute=minute, second=second,
        tzinfo=ZoneInfo("Asia/Shanghai"))


@contextmanager
def fixed(moment: datetime):
    previous = clock.set_clock(clock.FixedClock(moment))
    try:
        yield
    finally:
        clock.set_clock(previous)


# ─── commands/_shared.resolve_trade_date ───────────────────────────────────


def test_resolve_trade_date_defaults_to_the_runtime_clock_in_shanghai():
    with fixed(shanghai("2026-09-11", 15, 30)):
        assert resolve_trade_date(None) == "2026-09-11"
    with fixed(shanghai("2026-09-11", 23, 59)):
        assert resolve_trade_date(None) == "2026-09-11"
    # UTC 05:00 仍是上海同一天 13:00；跨时区语义由 clock 的 tz 参数承担。
    with fixed(datetime.fromisoformat("2026-09-11T05:00:00+00:00")):
        assert resolve_trade_date(None) == "2026-09-11"


def test_resolve_trade_date_keeps_explicit_arguments_and_normalisation():
    with fixed(shanghai("2026-09-11", 15, 30)):
        assert resolve_trade_date("20260801") == "2026-08-01"
        assert resolve_trade_date("2026/08/01") == "2026-08-01"
        assert resolve_trade_date("2026-08-01") == "2026-08-01"


# ─── commands/vpa.py ───────────────────────────────────────────────────────


def _vpa_report() -> SimpleNamespace:
    return SimpleNamespace(
        code="600001", source="tdx", fallback=False, health_score=72.0,
        signal="healthy", summary="ok",
        factors=[SimpleNamespace(name="f1", title="t1", signal="s1",
                                 score=1.0, note="n", evidence={}, details={})])


def test_vpa_command_saves_the_runtime_clock_date(capsys):
    args = SimpleNamespace(code="600001", source="tdx", days=30, no_save=False)
    with fixed(shanghai("2026-09-11", 15, 30)), \
            patch("dragon_quant.vpa.analyze", return_value=_vpa_report()), \
            patch("dragon_quant.vpa.report.render", return_value="report"), \
            patch("dragon_quant.storage.db.upsert_vpa") as upsert:
        vpa_handle(args, services=None)

    assert upsert.call_count == 1
    assert upsert.call_args.kwargs["trade_date"] == "2026-09-11"
    assert capsys.readouterr().out.strip() == "report"


def test_vpa_command_no_save_never_writes():
    args = SimpleNamespace(code="600001", source="tdx", days=30, no_save=True)
    with fixed(shanghai("2026-09-11", 15, 30)), \
            patch("dragon_quant.vpa.analyze", return_value=_vpa_report()), \
            patch("dragon_quant.vpa.report.render", return_value="report"), \
            patch("dragon_quant.storage.db.upsert_vpa") as upsert:
        vpa_handle(args, services=None)
    assert upsert.call_count == 0


# ─── commands/prep.py ──────────────────────────────────────────────────────


def _plan(collect=(), repairs=()) -> SimpleNamespace:
    return SimpleNamespace(collect=list(collect), repairs=dict.fromkeys(repairs, ""))


def test_prep_plan_hint_compares_against_the_runtime_clock(capsys):
    with fixed(shanghai("2026-09-11", 15, 30)):
        _prep_plan_hint(_plan(), trade_date="2026-09-10", auto_date=True)
    assert "先运行 scan" in capsys.readouterr().err


def test_prep_plan_hint_is_quiet_when_the_anchor_is_today(capsys):
    with fixed(shanghai("2026-09-11", 15, 30)):
        _prep_plan_hint(_plan(), trade_date="2026-09-11", auto_date=True)
    assert "先运行 scan" not in capsys.readouterr().err


def test_prep_plan_hint_ignores_weekend_auto_anchor(capsys):
    with fixed(shanghai("2026-09-12", 12, 0)):  # Saturday
        _prep_plan_hint(_plan(), trade_date="2026-09-10", auto_date=True)
    assert "先运行 scan" not in capsys.readouterr().err


# ─── assets_sync/state.py ──────────────────────────────────────────────────


def test_state_now_iso_uses_the_runtime_clock():
    with fixed(shanghai("2026-10-21", 18, 30)):
        assert now_iso() == "2026-10-21T18:30:00+08:00"


def test_state_now_iso_honours_an_explicit_moment():
    explicit = shanghai("2026-01-02", 3, 4, 5)
    assert now_iso(explicit) == "2026-01-02T03:04:05+08:00"


# ─── assets_sync/cli_service.py ────────────────────────────────────────────


def test_parse_asset_date_future_gate_uses_the_runtime_clock():
    with fixed(shanghai("2026-10-21", 18, 30)):
        assert parse_asset_date("20261021", "--to") == "2026-10-21"
        try:
            parse_asset_date("20261022", "--to")
        except ValueError as exc:
            assert "不能是未来日期" in str(exc)
        else:  # pragma: no cover - regression guard
            raise AssertionError("未来日期必须被拒")
