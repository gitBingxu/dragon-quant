"""M3 集成：详细池资产 / 双健康作用域 / 独立修复 / 快照 0.7。

全部用例使用临时 SQLite + 假 provider/collector（零网络、零真实数据目录），
重点断言：

* 旧 ``candidates`` / ``collection_report`` / legacy 采集选择逐字段不变；
* 资金/公告 ``legacy_codes`` 健康只看 dragons 子集，详细池补充代码不进旧状态；
* ``review_collect`` 与 ``plan.collect`` 完全分离，失败可重试、换血更新日 K；
* ``review`` 计算异常不中断旧链路；``--recompute`` 两次结果一致。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


from dragon_quant.review_account.prep.review import compute_review, derive_pool_codes

DAY = "2026-10-19"
NOW_DAY = "2026-10-19"
_CST = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _daily_bars(days: int = 25, code_seed: int = 0) -> list:
    """确定性日 K：缓慢上行 + 一次涨停，覆盖 D5/D6 所需 20+ 根。"""

    from dragon_quant.infra.models import KBar

    bars = []
    start = datetime.fromisoformat("2026-08-20").replace(hour=15, tzinfo=_CST)
    for index in range(days):
        close = 10.0 + code_seed + index * 0.02
        pct = 10.0 if index == days - 3 else 0.2
        bars.append(KBar(
            timestamp=int((start + timedelta(days=index)).timestamp() * 1000),
            volume=10_000.0, open=close - 0.1, high=close + 0.1, low=close - 0.2,
            close=close, chg=0.0, pct=pct, turnover=1.5, amount=1e7,
        ))
    return bars


def _quote(code: str, *, main_net=1e7, name=None):
    return SimpleNamespace(
        code=code, name=name or f"股票{code}", price=10.5, prev_close=10.0,
        pct=5.0, turnover_rate=8.0, amount=1e8, float_market_cap=8e9,
        main_net_amount=main_net,
    )


class FakeTdx:
    """只实现详细池所需方法；调用全部记录，未实现方法视为失败。"""

    def __init__(self, *, fail_quotes: bool = False, fail_daily: bool = False,
                 fail_auction: bool = False):
        self.fail_quotes = fail_quotes
        self.fail_daily = fail_daily
        self.fail_auction = fail_auction
        self.auction: dict[str, dict] = {}
        self.intraday: dict[str, list] = {}
        self.calls: list[tuple] = []

    def batch_get_quotes(self, codes):
        self.calls.append(("quotes", list(codes)))
        if self.fail_quotes:
            raise RuntimeError("quotes transport down")
        return [_quote(code) for code in codes]

    def get_announcements(self, code, count=30, page=1):
        self.calls.append(("announcements", code, page))
        return []

    def batch_get_daily_klines(self, codes, days=120, end_date=None):
        self.calls.append(("daily", list(codes), end_date))
        if self.fail_daily:
            raise RuntimeError("daily transport down")
        return {code: _daily_bars(code_seed=index) for index, code in enumerate(codes)}

    def get_auction_snapshot(self, code):
        self.calls.append(("auction_snapshot", code))
        if self.fail_auction:
            raise RuntimeError("auction transport down")
        return self.auction.get(code)

    def get_5min_kline_for_date(self, code, date):
        self.calls.append(("intraday", code, date))
        return self.intraday.get(code, [])


def _assets(day: str = DAY, *, holdings=(), block_extra=()):
    block_members = [{"code": "600001"}] + [{"code": code} for code in block_extra]
    return {
        "dragons": {
            "trade_date": day, "scan_id": f"v2_{day.replace('-', '')}_top25",
            "version": "1.0", "count": 1,
            "rows": [{"code": "600001", "name": "龙头A", "composite_score": 80,
                      "is_true_dragon": True, "concepts": ["机器人"]}],
        },
        "ths_limit_up_pool": {
            "trade_date": day, "api_date": day.replace("-", ""), "total": 1,
            "rows": [{"code": "600001", "name": "龙头A", "reason_type": "机器人",
                      "limit_up_type": "换手板", "open_num": 0,
                      "high_days": "2天2板", "change_rate": 9.99,
                      "turnover_rate": 8.5, "limit_up_suc_rate": 1.0,
                      "first_limit_up_time": 1792114286, "order_volume": 20000}],
            "truncated": False,
        },
        "ths_block_top": {
            "trade_date": day, "api_date": day.replace("-", ""),
            "rows": [{"name": "机器人", "limit_up_num": 1, "high": "2天2板",
                      "high_num": 2, "stock_list": block_members}],
        },
        "market_breadth": {
            "as_of": day, "up_cnt": 2100, "down_cnt": 900, "flat_cnt": 100,
            "limit_up": ["600001"], "broken": [], "limit_down": [],
            "total_amount": 1.5e12, "sample_n": 5100, "raw_n": 5200,
        },
        "fg_boards": {
            "as_of": day,
            "boards": {"昨日涨停": {"rows": [
                {"code": "600001", "close": 11.0, "pre_close": 10.0}], "status": "ok"}},
        },
        "holdings": {"as_of": day, "codes": list(holdings),
                     "sources": ["buy_sell_signals"], "degraded": []},
        "fund_flow": {
            "as_of": day,
            "quotes": {"600001": {"main_net_amount": 1e7, "pct": 10.0,
                                  "turnover_rate": 8.5, "amount": 1e8,
                                  "price": 10.5, "float_market_cap": 8e9,
                                  "name": "龙头A"}},
            "legacy_codes": ["600001"], "review_codes": [],
            "review_health": {"status": "ok", "note": None, "missing": []},
        },
        "cninfo_announcements": {
            "as_of": day, "window_days": 7, "window_start": "2026-10-12",
            "items": {"600001": {"announcements": [], "max_announcement_date": None,
                                 "truncated": False}},
            "truncated_codes": [], "legacy_codes": ["600001"], "review_codes": [],
            "review_health": {"status": "ok", "note": None, "missing": []},
        },
        "macro_global": {
            "as_of": day, "instruments": {}, "degraded": [], "sources": {},
        },
        "stock_daily": {
            "as_of": day, "trade_date": day, "requested_codes": ["600001"],
            "returned_codes": ["600001"], "missing_codes": [],
            "rows": {"600001": [
                {"date": f"2026-08-{index + 20:02d}", "open": 10.0, "high": 10.2,
                 "low": 9.9, "close": 10.0 + index * 0.02, "volume": 10000,
                 "amount": 1e7, "turnover": 1.5}
                for index in range(25)
            ]},
            "coverage": {"requested": 1, "returned": 1, "missing": [], "ratio": 1.0},
            "truncated": False, "review_codes": [],
            "review_health": {"status": "ok", "note": None, "missing": []},
        },
        # M5：无 T-1 计划时的合法空资产（首日无计划）。
        "prev_plan": {
            "as_of": day, "source_trade_date": None, "context_version": None,
            "context_hash": None, "positions": [], "feedback_plans": [],
            "market_state": None, "degraded": [],
        },
        "auction_feedback": {
            "as_of": day, "rows_by_code": {}, "sources": [],
            "backfill": {"attempted": 0, "ok": 0, "failed": []},
            "unverified_count": 0, "degraded": [],
        },
        "t1_intraday": {
            "as_of": day, "trade_date": day, "rows": {}, "missing_codes": [],
            "checkpoint": ["09:25", "10:00"], "degraded": [],
        },
    }


_ASSET_NAMES = ("dragons", "ths_limit_up_pool", "ths_block_top", "market_breadth",
                "fg_boards", "holdings", "fund_flow", "cninfo_announcements",
                "macro_global", "stock_daily", "prev_plan", "auction_feedback",
                "t1_intraday")
_EMPTY_ASSETS = ("prev_plan", "auction_feedback", "t1_intraday")


def _ok_report() -> dict:
    report = {name: {"status": "ok"} for name in _ASSET_NAMES}
    for name in _EMPTY_ASSETS:
        report[name] = {"status": "empty"}
    return report


def _patched_assets_db(tmp: str):
    db_path = str(Path(tmp) / "assets.db")
    return patch("dragon_quant.infra.assets.db._connect",
                 side_effect=lambda *a, **k: sqlite3.connect(db_path))


def _services(tdx):
    return SimpleNamespace(providers=SimpleNamespace(
        tdx=tdx, ths=None, tencent=None, xueqiu=None))


def _recording_registry(calls: list[tuple]):
    from dragon_quant.review_account import prep

    out = {}
    for name, spec in dict(prep.ASSET_COLLECTORS).items():
        def _collect(ctx, _name=name, _spec=spec):
            calls.append((_name, ctx.review_only, list(ctx.review_codes)))
            return _spec.collect(ctx)
        out[name] = prep.CollectorSpec(_collect, spec.same_day_only,
                                       spec.suspicious_check)
    return out


def _run(day: str, calls: list[tuple], tdx: FakeTdx, *, force: bool = False,
         now: str = NOW_DAY):
    from dragon_quant.review_account import prep
    from dragon_quant.review_account.prep import SHANGHAI

    moment = datetime.fromisoformat(now).replace(hour=16, minute=45, tzinfo=SHANGHAI)
    with patch.object(prep, "ASSET_COLLECTORS", _recording_registry(calls)), \
            patch.object(prep, "_validate_trade_date", return_value=["2026-10-16", day]), \
            patch.object(prep, "TdxProvider"), patch.object(prep, "THSProvider"):
        return prep.run_prep(day, force=force, now=moment,
                             services=_services(tdx))


def _legacy_candidates(assets: dict, day: str = DAY) -> list[dict]:
    from dragon_quant.review_account.prep import (
        compute_candidates, compute_main_boards,
    )
    from dragon_quant.review_account.verdicts import evaluate_candidates

    boards = compute_main_boards(assets)
    return evaluate_candidates(compute_candidates(assets, day), boards, day)


# ---------------------------------------------------------------------------
# 独立修复：新增代码只触发 review_collect
# ---------------------------------------------------------------------------


def test_new_detail_code_only_triggers_review_collect_and_keeps_legacy_untouched():
    from dragon_quant.infra.assets import db as assets_db
    from dragon_quant.review_account import prep

    import tempfile

    with tempfile.TemporaryDirectory() as tmp, _patched_assets_db(tmp):
        stored = _assets(holdings=["600777"])
        report = _ok_report()
        assets_db.save_prep_assets(DAY, {k: v for k, v in stored.items()}, report)
        before = assets_db.get_prep_assets(DAY)
        expected_candidates = _legacy_candidates(before, DAY)

        plan = prep.plan_prep(before, DAY, NOW_DAY)
        assert plan.collect == []                                  # legacy 零采集
        assert set(plan.review_collect) == {
            "fund_flow", "cninfo_announcements", "stock_daily"}

        calls: list[tuple] = []
        tdx = FakeTdx()
        out = _run(DAY, calls, tdx)

        assert calls and all(review_only for _, review_only, _ in calls)
        assert all(codes == ["600777"] for _, _, codes in calls)
        after = assets_db.get_prep_assets(DAY)
        assert after["collection_report"] == before["collection_report"]
        assert after["fund_flow"]["legacy_codes"] == ["600001"]
        assert after["cninfo_announcements"]["legacy_codes"] == ["600001"]
        assert "600777" in after["fund_flow"]["quotes"]
        assert "600777" in after["cninfo_announcements"]["items"]
        assert "600777" in after["stock_daily"]["rows"]
        assert after["stock_daily"]["requested_codes"] == \
            derive_pool_codes(after)["codes"]

        assert out["candidates"] == expected_candidates
        review_codes = [item["code"] for item in out["review_candidates"]]
        assert "600777" in review_codes
        assert "600777" not in {item["code"] for item in out["candidates"]}
        assert out["version"] == "0.7"


# ---------------------------------------------------------------------------
# 独立修复：失败可重试（不写「已修复」标记）
# ---------------------------------------------------------------------------


def test_review_repair_failure_is_isolated_and_retried_next_round():
    from dragon_quant.infra.assets import db as assets_db

    import tempfile

    with tempfile.TemporaryDirectory() as tmp, _patched_assets_db(tmp):
        stored = _assets(holdings=["600777"])
        assets_db.save_prep_assets(DAY, stored, _ok_report())
        before = assets_db.get_prep_assets(DAY)
        expected_candidates = _legacy_candidates(before, DAY)

        calls: list[tuple] = []
        out = _run(DAY, calls, FakeTdx(fail_quotes=True))

        after = assets_db.get_prep_assets(DAY)
        assert after["collection_report"] == before["collection_report"]
        assert out["candidates"] == expected_candidates
        assert any("fund_flow" in mark for mark in out["review"]["degraded"])
        assert after["fund_flow"]["review_health"]["status"] == "error"

        # 下一轮仍会重新尝试同一缺口（无「已修复」标记阻挡）
        from dragon_quant.review_account.prep.collectors import plan_review_repairs
        retry = plan_review_repairs(after, DAY, NOW_DAY)
        assert "fund_flow" in retry.collect
        assert "600777" in retry.missing["fund_flow"]


# ---------------------------------------------------------------------------
# 独立修复：主线换血 → stock_daily.requested_codes 更新
# ---------------------------------------------------------------------------


def test_pool_rotation_updates_stock_daily_requested_codes():
    from dragon_quant.infra.assets import db as assets_db

    import tempfile

    with tempfile.TemporaryDirectory() as tmp, _patched_assets_db(tmp):
        stored = _assets(block_extra=["600003"])
        assets_db.save_prep_assets(DAY, stored, _ok_report())
        before = assets_db.get_prep_assets(DAY)
        expected_candidates = _legacy_candidates(before, DAY)

        calls: list[tuple] = []
        out = _run(DAY, calls, FakeTdx())

        after = assets_db.get_prep_assets(DAY)
        pool_codes = derive_pool_codes(after)["codes"]
        assert "600003" in pool_codes
        assert after["stock_daily"]["requested_codes"] == pool_codes
        assert "600003" in after["stock_daily"]["rows"]
        assert "600001" in after["stock_daily"]["rows"]      # 旧行保留
        assert after["fund_flow"]["legacy_codes"] == ["600001"]
        assert out["candidates"] == expected_candidates
        assert "600003" not in {item["code"] for item in out["candidates"]}
        assert "600003" in {item["code"] for item in out["review_candidates"]}


# ---------------------------------------------------------------------------
# 双健康作用域
# ---------------------------------------------------------------------------


def test_legacy_all_none_keeps_empty_suspicious_even_if_review_codes_healthy():
    from dragon_quant.review_account.prep import _compute_all
    from dragon_quant.review_account.prep.collectors import (
        ASSET_COLLECTORS, _classify_payload,
    )

    assets = _assets(holdings=["600777"])
    assets["fund_flow"]["quotes"] = {
        "600001": {"main_net_amount": None, "pct": 0.0, "turnover_rate": 0.0,
                   "amount": 0.0},
        "600777": {"main_net_amount": 2e7, "pct": 3.0, "turnover_rate": 5.0,
                   "amount": 8e7},
    }
    assets["fund_flow"]["review_codes"] = ["600777"]
    assets["fund_flow"]["review_health"] = {"status": "ok", "note": None,
                                            "missing": []}
    entry = _classify_payload("fund_flow", assets["fund_flow"],
                              ASSET_COLLECTORS["fund_flow"])
    assert entry["status"] == "empty_suspicious"      # 只看 legacy 子集

    report = _ok_report()
    report["fund_flow"] = {"status": "empty_suspicious"}
    assets["collection_report"] = report
    out = _compute_all(DAY, assets)
    assert out["candidates"]
    assert out["candidates"][0]["facts"]["flow_available"] is False

    # 旧语义对照：同一 legacy 子集在升级前（无 review 代码）也是 Flow 不可用
    baseline = _assets()
    baseline["fund_flow"]["quotes"] = {
        "600001": {"main_net_amount": None, "pct": 0.0, "turnover_rate": 0.0,
                   "amount": 0.0}}
    baseline["collection_report"] = {**_ok_report(),
                                     "fund_flow": {"status": "empty_suspicious"}}
    base_out = _compute_all(DAY, baseline)
    assert out["candidates"] == base_out["candidates"]


def test_legacy_healthy_review_missing_keeps_legacy_ok_and_marks_review_degraded():
    from dragon_quant.review_account.prep import _compute_all
    from dragon_quant.review_account.prep.collectors import (
        ASSET_COLLECTORS, _classify_payload,
    )

    assets = _assets(holdings=["600777"])
    assets["fund_flow"]["review_codes"] = ["600777"]
    assets["fund_flow"]["review_health"] = {
        "status": "degraded", "note": "missing_codes", "missing": ["600777"]}
    assets["cninfo_announcements"]["review_codes"] = ["600777"]
    assets["cninfo_announcements"]["review_health"] = {
        "status": "degraded", "note": "missing_codes", "missing": ["600777"]}
    assets["collection_report"] = _ok_report()

    entry = _classify_payload("fund_flow", assets["fund_flow"],
                              ASSET_COLLECTORS["fund_flow"])
    assert entry["status"] == "ok"

    out = _compute_all(DAY, assets)
    assert out["candidates"] == _legacy_candidates(
        {**assets, "collection_report": {}}, DAY)
    assert "fund_flow_review_degraded" in out["review"]["degraded"]
    assert "cninfo_announcements_review_degraded" in out["review"]["degraded"]


# ---------------------------------------------------------------------------
# 快照 0.7 / 异常隔离 / 确定性
# ---------------------------------------------------------------------------


def test_review_failure_does_not_break_legacy_snapshot():
    from dragon_quant.review_account.prep import _compute_all

    assets = _assets()
    assets["collection_report"] = _ok_report()
    with patch("dragon_quant.review_account.prep.service.compute_review",
               side_effect=RuntimeError("boom")):
        out = _compute_all(DAY, assets)

    assert out["candidates"]
    assert out["sentiment"]["position_pct"] is not None
    assert out["review"]["enabled"] is True
    assert "boom" in out["review"]["compute_error"]
    assert out["review_candidates"] == []


def test_mainline_empty_maps_to_unavailable_not_real_absence():
    from dragon_quant.review_account.prep import _compute_all

    assets = _assets()
    assets.pop("ths_block_top")
    report = _ok_report()
    report.pop("ths_block_top")
    assets["collection_report"] = report
    out = _compute_all(DAY, assets)
    assert out["review"]["mainlines"] == []
    assert "mainline_unavailable" in out["review"]["degraded"]


def test_recompute_twice_is_byte_identical_and_keeps_old_keys():
    from dragon_quant.infra.assets import db as assets_db
    from dragon_quant.review_account import prep

    import tempfile

    with tempfile.TemporaryDirectory() as tmp, _patched_assets_db(tmp):
        assets_db.save_prep_assets(DAY, _assets(), _ok_report())
        with patch.object(prep, "rebuild_dragons_for_recompute", return_value=None), \
                patch.object(prep, "TdxProvider"), patch.object(prep, "THSProvider"):
            first = prep.run_prep(DAY, recompute=True)
            second = prep.run_prep(DAY, recompute=True)

        assert first == second
        assert first["version"] == "0.7"
        for key in ("trade_date", "version", "sentiment", "main_boards", "macro",
                    "candidates", "dragon_source", "review", "review_candidates",
                    "strategy_context"):
            assert key in first
        # frozen_at 是确定性决策阶段时点，不是 wall clock：两次 recompute 完全一致。
        assert first["strategy_context"]["frozen_at"] == \
            f"{DAY}T16:40:00+08:00"
        assert first["strategy_context"] == second["strategy_context"]
        assert assets_db.get_prep_snapshot(DAY)["review"] == first["review"]


def test_review_candidates_cover_sources_outside_legacy_candidates():
    assets = _assets(holdings=["600777"], block_extra=["600003"])
    assets["collection_report"] = _ok_report()
    review = compute_review(assets, DAY)
    codes = {item["code"] for item in review["review_candidates"]}
    assert {"600001", "600003", "600777"} <= codes
    assert "mainline_unavailable" not in review["degraded"]


# ---------------------------------------------------------------------------
# D6 20 日口径：至多 21 根 → 至多 20 个「当日→次日」收益
# ---------------------------------------------------------------------------


def _bars_from_closes(closes: list[float]) -> list[dict]:
    return [
        {"date": f"2026-08-{index + 1:02d}", "close": close,
         "high": close + 0.2, "low": close - 0.2, "volume": 1000}
        for index, close in enumerate(closes)
    ]


def test_daily_metrics_uses_exactly_20_next_day_returns_with_21_bars():
    from dragon_quant.review_account.prep.review.orchestrator import daily_metrics

    metrics = daily_metrics(_bars_from_closes([10.0 + index * 0.1 for index in range(21)]))
    assert metrics["next_day_up_sample"] == 20
    assert metrics["next_day_up_rate_20d"] == 1.0


def test_daily_metrics_keeps_actual_sample_when_fewer_than_20_pairs():
    from dragon_quant.review_account.prep.review.orchestrator import daily_metrics

    metrics = daily_metrics(_bars_from_closes([10.0 + index * 0.1 for index in range(20)]))
    assert metrics["next_day_up_sample"] == 19      # 实际样本数，不伪造成 20
    assert metrics["next_day_up_rate_20d"] == 1.0


def test_daily_metrics_small_sample_null_s2_but_keeps_sample():
    from dragon_quant.review_account.prep.review.orchestrator import daily_metrics
    from dragon_quant.review_account.prep.review.scoring import score_candidate

    metrics = daily_metrics(_bars_from_closes([10.0, 10.5]))
    assert metrics["next_day_up_sample"] == 1
    assert metrics["next_day_up_rate_20d"] == 1.0

    scored = score_candidate(
        {"limit_up_cnt_20d": 0, **metrics}, {"role": None})
    assert scored["dimensions"]["D6"]["sub"]["S2"] is None   # 样本 < 3 → null



# ---------------------------------------------------------------------------
# M5：prev_plan / auction_feedback / t1_intraday 与 feedback / plan_review
# ---------------------------------------------------------------------------

PREV_DAY = "2026-10-16"


def _plan_snapshot(day: str = PREV_DAY) -> dict:
    return {
        "trade_date": day,
        "version": "0.7",
        "strategy_context": {
            "context_version": "sc_v1",
            "trade_date": day,
            "frozen_at": f"{day}T16:40:00+08:00",
            "market": {"state": "strong", "score": 71.0, "coeff": 1.0},
            "positions": [
                {"code": "600001", "role": "leader", "review_score": 86.6,
                 "review_grade": "S", "position_pct": 12.0,
                 "eligibility": "eligible", "confidence": 0.92},
                {"code": "600777", "role": "follower", "review_score": 60.0,
                 "review_grade": "B", "position_pct": 4.0,
                 "eligibility": "eligible", "confidence": 0.7},
            ],
            "feedback_plans": [
                {"code": "600001", "role": "leader", "position_pct": 12.0,
                 "expected": {"gap_low": 3.0, "gap_high": 7.0,
                              "ratio_low": 1.2, "seal_by": "10:00"}},
                {"code": "600777", "role": "follower", "position_pct": 4.0,
                 "expected": {"gap_low": -2.0, "gap_high": 2.0,
                              "ratio_low": 0.7, "seal_by": None}},
            ],
            "portfolio": {"sum_pct": 16.0, "warning": None},
            "data_quality": {"degraded": [], "missing": []},
            "enums": {},
            "disclaimer": "x",
        },
    }


def _feedback_assets() -> dict:
    """M5 首采资产：不带 prev_plan/auction_feedback/t1_intraday。"""

    assets = _assets()
    for name in ("prev_plan", "auction_feedback", "t1_intraday"):
        assets.pop(name)
    assets["stock_daily"]["rows"] = {
        "600001": _two_day_rows(10.0, 10.9),
        "600777": _two_day_rows(10.0, 10.9),
    }
    return assets


def _two_day_rows(prev_close: float, t1_close: float) -> list[dict]:
    return [
        {"date": PREV_DAY, "open": 9.9, "high": prev_close, "low": 9.8,
         "close": prev_close, "volume": 10000, "amount": 1e7, "turnover": 1.5},
        {"date": DAY, "open": 10.2, "high": t1_close, "low": 10.0,
         "close": t1_close, "volume": 12000, "amount": 1.2e7, "turnover": 1.8},
    ]


def _m5_report() -> dict:
    report = _ok_report()
    for name in ("prev_plan", "auction_feedback", "t1_intraday"):
        report.pop(name)
    return report


def _seed_m5(tmp: str, *, seed_auction: bool = True):
    from dragon_quant.infra.assets import db as assets_db

    assets_db.save_prep_assets(DAY, _feedback_assets(), _m5_report())
    assets_db.save_prep_snapshot(PREV_DAY, _plan_snapshot())
    if seed_auction:
        assets_db.save_auction_asset(
            PREV_DAY, "600001", "龙头A",
            [{"time": "09:25:00", "price": 10.0, "matched": 1_000_000,
              "unmatched": 0}])
        assets_db.save_auction_asset(
            PREV_DAY, "600777", "跟风B",
            [{"time": "09:25:00", "price": 10.0, "matched": 1_000_000,
              "unmatched": 0}])
        assets_db.save_auction_asset(
            DAY, "600001", "龙头A",
            [{"time": "09:25:00", "price": 10.45, "matched": 1_300_000,
              "unmatched": 0}])


def test_feedback_uses_prev_plan_codes_and_backfills_same_day_auction():
    from dragon_quant.infra.assets import db as assets_db

    import tempfile

    with tempfile.TemporaryDirectory() as tmp, _patched_assets_db(tmp):
        _seed_m5(tmp)
        before_snapshot = assets_db.get_prep_snapshot(PREV_DAY)
        before_assets = assets_db.get_prep_assets(DAY)
        expected_candidates = _legacy_candidates(before_assets, DAY)

        calls: list[tuple] = []
        tdx = FakeTdx()
        tdx.auction["600777"] = {"price": 10.45, "amount": 1.3e7,
                                 "matched": 1_300_000, "unmatched": 0}
        out = _run(DAY, calls, tdx)

        # 本地 auction_assets 命中 600001；缺口 600777 走同日 TDX 补采。
        assert ("auction_snapshot", "600001") not in tdx.calls
        assert ("auction_snapshot", "600777") in tdx.calls
        assets = assets_db.get_prep_assets(DAY)
        rows = assets["auction_feedback"]["rows_by_code"]
        assert rows["600001"]["source"] == "auction_assets"
        assert rows["600777"]["source"] == "tdx_same_day"
        assert assets["auction_feedback"]["backfill"] == {
            "attempted": 1, "ok": 1, "failed": []}

        # prev_plan 只来自 T-1 快照的 positions/feedback_plans。
        assert assets["prev_plan"]["source_trade_date"] == PREV_DAY
        assert [p["code"] for p in assets["prev_plan"]["positions"]] == [
            "600001", "600777"]

        assert out["feedback"]["source_plan_date"] == PREV_DAY
        states = {item["code"]: item["state"] for item in out["feedback"]["items"]}
        assert states == {"600001": "confirmed", "600777": "confirmed"}
        assert out["plan_review"]["plan_date"] == PREV_DAY
        assert out["candidates"] == expected_candidates

        # T 快照（T-1 日）不可变：T+1 只写自己的快照。
        assert assets_db.get_prep_snapshot(PREV_DAY) == before_snapshot
        assert assets_db.get_prep_snapshot(DAY)["feedback"] == out["feedback"]


def test_auction_backfill_failure_is_unknown_not_confirmed():
    from dragon_quant.infra.assets import db as assets_db

    import tempfile

    with tempfile.TemporaryDirectory() as tmp, _patched_assets_db(tmp):
        _seed_m5(tmp)
        calls: list[tuple] = []
        out = _run(DAY, calls, FakeTdx(fail_auction=True))

        assets = assets_db.get_prep_assets(DAY)
        row = assets["auction_feedback"]["rows_by_code"]["600777"]
        assert row["verified"] is False
        assert row["unknown_reason"] == "auction_backfill_failed"
        assert assets["auction_feedback"]["backfill"]["failed"][0]["code"] == "600777"

        items = {item["code"]: item for item in out["feedback"]["items"]}
        assert items["600777"]["state"] == "unknown"
        assert items["600777"]["unknown_reason"] == "auction_backfill_failed"
        assert items["600777"]["allowed_actions"] == ["observe_only"]
        # 本地命中的 600001 不受补采失败影响。
        assert items["600001"]["state"] == "confirmed"


def test_historical_day_never_backfills_same_day_auction():
    from dragon_quant.review_account import prep

    assets = _feedback_assets()
    assets["collection_report"] = _m5_report()
    plan = prep.plan_prep(assets, DAY, "2026-10-20")

    assert "prev_plan" in plan.collect           # 快照复制，可历史重建
    assert "auction_feedback" not in plan.collect
    assert "t1_intraday" not in plan.collect


def test_without_prev_plan_feedback_is_absent_not_error():
    from dragon_quant.infra.assets import db as assets_db

    import tempfile

    with tempfile.TemporaryDirectory() as tmp, _patched_assets_db(tmp):
        assets_db.save_prep_assets(DAY, _feedback_assets(), _m5_report())
        out = _run(DAY, [], FakeTdx())

        assets = assets_db.get_prep_assets(DAY)
        assert assets["prev_plan"]["positions"] == []
        assert "feedback" not in out
        assert "plan_review" not in out
        assert out["candidates"]
