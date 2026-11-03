"""C：``_collect_stock_daily`` 计划候选扩池单测（方案 §10.1，零网络）。

覆盖：

1. ``requested_codes`` / ``coverage`` 仍只描述详细池（不被扩池稀释）；
2. ``rows`` 含池外计划候选；
3. 扩池 code 取数失败只进 ``plan_window.missing``，``missing_codes`` / ``review_health`` 不变；
4. 空池仍抛错；
5. ``review_only`` 不扩池取数，且 cap 不裁掉存量行；
6. 池 code 存 120 根、扩池独有 code 存 20 根；
7. 无近 N 日快照时不写 ``plan_window``；清单读取异常写 ``plan_window.error``。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dragon_quant.infra.models import KBar
from dragon_quant.review_account.prep import collectors
from dragon_quant.review_account.prep.review import derive_pool_codes

DAY = "2026-10-19"
TODAY = "2026-10-19"


def _bars(count: int, *, seed: int = 0) -> list[KBar]:
    start = datetime(2026, 4, 1, 15, 0)
    bars = []
    for index in range(count):
        close = 10.0 + seed + index * 0.01
        bars.append(KBar(
            timestamp=int((start + timedelta(days=index)).timestamp() * 1000),
            volume=10_000.0, open=close - 0.1, high=close + 0.1,
            low=close - 0.2, close=close, chg=0.0, pct=0.1,
            turnover=1.5, amount=1e7,
        ))
    return bars


class FakeTdx:
    def __init__(self, *, bars=None, missing=(), fail=False):
        self.bars = bars or {}
        self.missing = set(missing)
        self.fail = fail
        self.calls: list[tuple] = []

    def batch_get_daily_klines(self, codes, days=120, end_date=None):
        self.calls.append((list(codes), days, end_date))
        if self.fail:
            raise RuntimeError("daily transport down")
        return {code: self.bars.get(code, _bars(5))
                for code in codes if code not in self.missing}


def _assets(*, block_extra=(), holdings=(), existing_rows=None,
            existing_requested=None):
    block_members = [{"code": "600001"}] + [{"code": c} for c in block_extra]
    assets = {
        "dragons": {
            "trade_date": DAY,
            "rows": [{"code": "600001", "name": "龙头A", "concepts": ["机器人"]}],
        },
        "ths_limit_up_pool": {
            "trade_date": DAY, "total": 1,
            "rows": [{"code": "600001", "name": "龙头A", "reason_type": "机器人"}],
        },
        "ths_block_top": {
            "trade_date": DAY,
            "rows": [{"name": "机器人", "stock_list": block_members}],
        },
        "market_breadth": {"as_of": DAY, "up_cnt": 1, "down_cnt": 1,
                           "flat_cnt": 0, "limit_up": [], "broken": [],
                           "limit_down": [], "total_amount": 1e12,
                           "sample_n": 5000},
        "fg_boards": {"as_of": DAY, "boards": {}},
        "holdings": {"as_of": DAY, "codes": list(holdings), "degraded": []},
        "stock_daily": {
            "as_of": DAY, "trade_date": DAY,
            "requested_codes": list(existing_requested or []),
            "rows": dict(existing_rows or {}),
            "review_codes": [],
            "review_health": {"status": "ok", "note": None, "missing": []},
        } if existing_rows is not None else None,
    }
    return assets


def _ctx(assets, tdx, *, review_only=False, review_codes=()):
    collected = {k: v for k, v in assets.items() if v is not None}
    return collectors.PrepContext(
        trade_date=DAY, today=TODAY, tdx=tdx, ths=MagicMock(),
        dragons_rows=[{"code": "600001"}], collected=collected,
        review_only=review_only, review_codes=list(review_codes),
    )


def _window(codes, *, lookback=10, dates=("2026-10-16",)):
    return {"codes": list(codes), "trade_dates": list(dates),
            "corrupt_dates": [], "lookback_days": lookback}


def _collect(assets, tdx, *, recent_codes=(), recent_error=None,
             review_only=False, review_codes=()):
    target = ("dragon_quant.infra.assets.db."
              "list_recent_plan_candidate_codes")
    if recent_error is not None:
        patcher = patch(target, side_effect=recent_error)
    else:
        patcher = patch(target, return_value=_window(recent_codes))
    with patcher:
        return collectors._collect_stock_daily(
            _ctx(assets, tdx, review_only=review_only,
                 review_codes=review_codes))


def test_requested_codes_and_coverage_stay_pool_scoped():
    pool = derive_pool_codes(_assets())["codes"]
    assert pool == ["600001"]
    tdx = FakeTdx(bars={"600001": _bars(130), "600777": _bars(25)})
    payload = _collect(_assets(), tdx, recent_codes=["600777"])

    assert payload["requested_codes"] == pool
    assert payload["requested_codes"] == ["600001"]
    assert payload["coverage"]["requested"] == 1
    assert payload["coverage"]["ratio"] == 1.0
    assert payload["returned_codes"] == ["600001"]
    assert payload["missing_codes"] == []
    # 扩池只体现在 rows 与 plan_window。
    assert "600777" in payload["rows"]
    assert payload["plan_window"] == {
        "lookback_days": 10,
        "trade_dates": ["2026-10-16"],
        "pool": 1,
        "recent": 1,
        "union": 2,
        "fetched": 2,
        "missing": [],
    }


def test_plan_only_failure_does_not_dilute_pool_health():
    tdx = FakeTdx(bars={"600001": _bars(130)}, missing={"600777"})
    payload = _collect(_assets(), tdx, recent_codes=["600777"])

    assert payload["requested_codes"] == ["600001"]
    assert payload["coverage"]["ratio"] == 1.0
    assert payload["missing_codes"] == []
    assert payload["review_health"] == {"status": "ok", "note": None,
                                        "missing": []}
    assert payload["plan_window"]["missing"] == ["600777"]
    assert payload["plan_window"]["fetched"] == 1


def test_bar_limits_pool_120_and_plan_only_20():
    tdx = FakeTdx(bars={"600001": _bars(130), "600777": _bars(25)})
    payload = _collect(_assets(), tdx, recent_codes=["600777"])

    assert len(payload["rows"]["600001"]) == 120
    assert len(payload["rows"]["600777"]) == 20
    # 保留的是最近 20 根（升序末段）。
    dates = [row["date"] for row in payload["rows"]["600777"]]
    assert dates == sorted(dates)
    assert dates[-1] > dates[0]


def test_empty_pool_still_raises():
    assets = _assets()
    assets["ths_limit_up_pool"] = {"trade_date": DAY, "total": 0, "rows": []}
    with pytest.raises(RuntimeError):
        _collect(assets, FakeTdx(), recent_codes=[])


def test_review_only_does_not_expand_fetch_and_keeps_existing_rows():
    existing_rows = {f"6000{index:02d}": [{"date": "2026-10-16"}]
                     for index in range(10)}
    assets = _assets(existing_rows=existing_rows,
                     existing_requested=["600001", "600002"])
    tdx = FakeTdx(bars={"600001": _bars(5)})
    payload = _collect(assets, tdx, recent_codes=["600777"],
                       review_only=True, review_codes=["600001"])

    # 只请求 review_codes，不拉扩池/池全集。
    assert tdx.calls == [(["600001"], 120, DAY)]
    # cap 下限 = 存量行数，扩池行不被裁掉。
    assert set(payload["rows"]) == set(existing_rows)
    # requested_codes 并入存量，但不新增扩池 code。
    assert payload["requested_codes"] == ["600001", "600002"]
    assert "600777" not in payload["requested_codes"]


def test_no_recent_window_keeps_legacy_payload_shape():
    tdx = FakeTdx(bars={"600001": _bars(130)})
    payload = _collect(_assets(), tdx, recent_codes=[])
    assert "plan_window" not in payload


def test_window_read_error_is_observable_and_does_not_break_collection():
    tdx = FakeTdx(bars={"600001": _bars(130)})
    payload = _collect(_assets(), tdx, recent_error=RuntimeError("db locked"))

    assert payload["requested_codes"] == ["600001"]
    assert payload["plan_window"].keys() == {"error"}
    assert "db locked" in payload["plan_window"]["error"]
    assert tdx.calls == [(["600001"], 120, DAY)]


def test_recent_window_priority_pool_first():
    assets = _assets(block_extra=["600002"])
    tdx = FakeTdx(bars={"600001": _bars(3), "600002": _bars(3),
                        "600777": _bars(3)})
    payload = _collect(assets, tdx, recent_codes=["600002", "600777"])
    # 池序优先、去重，扩池 code 只追加在池之后。
    assert tdx.calls[0][0] == ["600001", "600002", "600777"]
    assert payload["requested_codes"] == ["600001", "600002"]
    assert payload["plan_window"]["union"] == 3
