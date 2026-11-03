"""FastAPI backend 单元测试：mock 仓储 + TestClient（零真实 DB 写入）。

覆盖 app/meta/404/缓存头、冻结 dragons 契约、scans 排序分页、factors 合并、
prep/auction 投影、入选后表现端点与账户兼容端点。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from dragon_quant.infra.clock import FixedClock
from web_ui.backend import deps
from web_ui.backend.app import create_app
from web_ui.backend.settings import DashboardSettings

BASE = "http://127.0.0.1"


def _constant_override(value):
    """无参 override：FastAPI 会深拷贝带默认值的参数，状态化 fake 必须闭包返回。"""

    def override():
        return value

    return override


def make_client(**overrides) -> TestClient:
    app = create_app(DashboardSettings())
    if "scan_repository" in overrides and "scan_logs_repository" not in overrides:
        overrides["scan_logs_repository"] = FakeScanLogs()
    # 单元测试默认不读取真实 assets.db：本地交易日序列为空。
    overrides.setdefault("bar_calendar_loader", _empty_calendar_loader())
    for name, value in overrides.items():
        app.dependency_overrides[getattr(deps, name)] = _constant_override(value)
    return TestClient(app, base_url=BASE)


def _empty_calendar_loader():
    return lambda date_from=None, date_to=None: []


def _calendar_loader(dates):
    def load(date_from=None, date_to=None):
        return [day for day in dates
                if (date_from is None or day >= date_from)
                and (date_to is None or day <= date_to)]
    return load


# ─── app / meta / 404 / cache ───────────────────────────────────────────────


class _Paths:
    DATA_DIR = Path("/tmp/dq-test-data")
    DB_PATH = Path("/tmp/dq-test-data/dragon.db")
    ASSETS_DB_PATH = Path("/tmp/dq-test-data/assets.db")


def test_meta_and_api_404_and_host_guard():
    client = make_client(storage_paths=_Paths())
    meta = client.get("/api/meta")
    assert meta.status_code == 200
    body = meta.json()
    assert set(body) == {
        "version", "data_dir", "dragon_db", "assets_db", "dist_built",
        "sources", "default_source", "default_page", "routes",
    }
    assert body["sources"] == ["v1", "v2"]
    assert body["dragon_db"] is False
    missing = client.get("/api/does-not-exist")
    assert missing.status_code == 404
    assert missing.json() == {"error": "not found"}
    # 非法 Host 被 TrustedHostMiddleware 拒绝（TestClient 默认 testserver）。
    assert TestClient(create_app(DashboardSettings())).get("/api/meta").status_code == 400


def test_static_cache_headers():
    client = make_client(storage_paths=_Paths())
    root = client.get("/")
    assert root.status_code == 200
    assert root.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assets = Path(__file__).resolve().parents[2] / "web_ui" / "dist" / "assets"
    asset = next(iter(sorted(assets.glob("*.js"))), None)
    if asset is not None:
        response = client.get(f"/assets/{asset.name}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == (
            "public, max-age=31536000, immutable"
        )
    api = client.get("/api/does-not-exist")
    assert api.headers["cache-control"] == "no-cache"


def test_build_hint_when_dist_missing(tmp_path):
    settings = DashboardSettings(dist_dir=tmp_path / "missing")
    client = TestClient(create_app(settings), base_url=BASE)
    response = client.get("/some/page")
    assert response.status_code == 200
    assert "Web UI 尚未构建" in response.text


# ─── dragons ───────────────────────────────────────────────────────────────


ROW = {
    "trade_date": "2026-08-17", "code": "600001", "name": "龙头A",
    "scan_id": "v2_20260817_5", "rank": 1, "composite_score": 88.0,
    "board_count": 2, "open_px": 10.0, "close_px": 11.0, "high_px": 11.0,
    "low_px": 10.0, "pct": 10.0, "turnover_rate": 8.0, "amount": 1e8,
    "market_cap": 1e9, "concepts": ["AI算力"], "report_text": "r",
    "buy_date": "2026-08-18", "buy_price": 10.0, "max_return_5d": 8.0,
    "max_drawdown_5d": -4.0, "max_return_hold_days": 3,
    "review_status": "completed", "version": "0.7.2", "is_true_dragon": True,
    "source": "v2", "strict_buy_date": "2026-08-18", "strict_buy_price": 10.1,
    "strict_max_return_5d": 9.0, "strict_max_drawdown_5d": -4.0,
    "strict_max_return_hold_days": 3, "strict_review_status": "completed",
    "strict_review_reason": None, "strict_reviewed": True,
    "derived_status": "completed",
}
SUMMARY = {
    "total": 1, "completed": 1, "pending": 0, "avg_return": 8.0,
    "win_rate": 100.0, "best_stock_code": "600001", "best_stock_name": "龙头A",
    "best_return": 8.0, "source": "v2", "avg_return_strict": 9.0,
    "win_rate_loose": 100.0, "win_rate_strict": 100.0, "win_rate_loose_n": 1,
    "win_rate_strict_n": 1, "cannot_evaluate": 0, "pending_recompute": 0,
    "failed_recompute": 0, "best_stock_code_strict": "600001",
    "best_stock_name_strict": "龙头A", "best_return_strict": 9.0,
}


class FakeDragons:
    def __init__(self):
        self.get_dragons_calls: list[str] = []

    def resolve_caliber(self, value, source="v2"):
        if source == "v1":
            return "loose"
        return "loose" if (value or "").lower() == "loose" else "strict"

    def query_dragons(self, filters=None, sort_by="composite_score",
                      sort_dir="desc", source="v2", *, caliber=None):
        return [dict(ROW)]

    def summarize_review_rows(self, rows, source="v2", *,
                              include_derived_counts=False, caliber=None):
        summary = dict(SUMMARY)
        if include_derived_counts:
            summary.update({"derived_completed": 1, "derived_pending": 0})
        return summary

    def get_review_summary(self, source="v2"):
        return dict(SUMMARY)

    def list_dragon_trade_dates(self, source="v2"):
        return ["2026-08-17", "2026-08-21"]

    def get_dragons(self, trade_date, source="v2"):
        self.get_dragons_calls.append(trade_date)
        rows = [dict(ROW)]
        for row in rows:
            row["trade_date"] = trade_date
        return rows


class FakeScans:
    def list_scans(self, limit=50, source="v2"):
        return [{
            "id": "v2_20260817_5", "scan_date": "2026-08-17",
            "elapsed_s": 1.5, "top_n": 5, "candidates_n": 3,
            "workers": 2, "created_at": "2026-08-17 15:30:00", "source": "v2",
        }]

    def get_scan(self, scan_id, source="v2"):
        if scan_id == "nope":
            return None
        return {
            "id": scan_id, "scan_date": "2026-08-17", "elapsed_s": 1.5,
            "top_n": 5, "candidates_n": 3, "workers": 2,
            "created_at": "2026-08-17 15:30:00", "raw_output": '{"x": 1}',
            "source": "v2",
        }

    def list_scan_dates(self, date_from=None, date_to=None, source="v2"):
        return []

    def list_scan_stock_contributions_by_date(self, scan_date, source="v2"):
        return []

    def summarize_scan_stocks(self, scan_ids, source="v2"):
        if "v2_20260817_5" in set(scan_ids or []):
            return {"v2_20260817_5": {"stock_n": 2, "true_dragon_n": 1}}
        return {}

    def get_scan_stocks(self, scan_id, source="v2"):
        return [
            {"code": "600002", "name": "B", "rank": 2, "composite_score": 70.0,
             "board_count": 1, "concepts": [], "dim_drive": 1.0,
             "dim_anti_drop": 2.0, "dim_leadership": 3.0, "dim_absorption": 4.0,
             "dim_liquidity": 5.0, "is_true_dragon": False,
             "reject_reason": "x", "report_text": ""},
            {"code": "600001", "name": "A", "rank": 1, "composite_score": 88.0,
             "board_count": 2, "concepts": ["AI"], "dim_drive": 9.0,
             "dim_anti_drop": 8.0, "dim_leadership": 7.0, "dim_absorption": 6.0,
             "dim_liquidity": 5.0, "is_true_dragon": True,
             "reject_reason": None, "report_text": "r"},
        ]


def test_dragons_frozen_contract_and_new_endpoints():
    client = make_client(dragon_repository=FakeDragons(),
                         scan_repository=FakeScans())
    payload = client.get("/api/dragons").json()
    assert set(payload) == {"data", "count", "summary"}
    assert set(payload["data"][0]) == set(ROW) | {"source"}
    assert payload["summary"]["total"] == payload["count"] == len(payload["data"])
    legacy = client.get("/api/summary").json()
    assert set(legacy) == set(SUMMARY)
    dates = client.get("/api/dragons/trade-dates").json()
    assert dates["count"] == 2
    assert dates["data"][0]["preview"][0]["code"] == "600001"
    detail = client.get("/api/dragons/20260817/600001").json()
    assert detail["dims"]["drive"] == 9.0
    assert detail["dims_source"] == "scan_stocks"
    assert set(detail) >= {"quote", "review_loose", "review_strict",
                           "derived_status", "caliber"}
    assert client.get("/api/dragons/20260817/999999").status_code == 404


# ─── 近几日榜单 items / 入选后表现 ─────────────────────────────────────────


class FakeDragonsMany(FakeDragons):
    def get_dragons(self, trade_date, source="v2"):
        self.get_dragons_calls.append(trade_date)
        return [
            {"code": "600003", "name": "C", "rank": 3,
             "composite_score": 70.0, "is_true_dragon": False},
            {"code": "600001", "name": "A", "rank": 1,
             "composite_score": 90.0, "is_true_dragon": True},
            {"code": "600002", "name": "B", "rank": 2,
             "composite_score": 80.0, "is_true_dragon": True},
            {"code": "600004", "name": "D", "rank": None,
             "composite_score": 99.0, "is_true_dragon": False},
        ]


class FakeDragonsRows(FakeDragons):
    """可控日期 / 逐日 dragons 行 / ``query_dragons`` 的 dragons fake。"""

    def __init__(self, dates=(), rows_by_date=None, query_rows=None):
        super().__init__()
        self.dates = list(dates)
        self.rows_by_date = rows_by_date or {}
        self.query_rows = [dict(ROW)] if query_rows is None else query_rows

    def list_dragon_trade_dates(self, source="v2"):
        return list(self.dates)

    def get_dragons(self, trade_date, source="v2"):
        self.get_dragons_calls.append(trade_date)
        return [dict(row) for row in self.rows_by_date.get(trade_date, [])]

    def query_dragons(self, filters=None, sort_by="composite_score",
                      sort_dir="desc", source="v2", *, caliber=None):
        return [dict(row) for row in self.query_rows]


def _scan_contrib(code, name, rank, score, *, top_n=5, true_dragon=True,
                  created_at="2026-08-17 15:30:00",
                  scan_id="v2_20260817_5", board_count=2,
                  report_text="r", concepts=None) -> dict:
    """``scans.list_scan_stock_contributions_by_date`` 的单行形状。"""
    return {
        "scan_id": scan_id, "scan_top_n": top_n, "scan_created_at": created_at,
        "code": code, "name": name, "rank": rank, "composite_score": score,
        "board_count": board_count,
        "concepts": ["AI"] if concepts is None else concepts,
        "report_text": report_text, "open_px": 10.0, "close_px": 11.0,
        "high_px": 11.0, "low_px": 10.0, "pct": 10.0, "turnover_rate": 8.0,
        "amount": 1e8, "market_cap": 1e9, "is_true_dragon": true_dragon,
        "source": "v2",
    }


class FakeScansContribs(FakeScans):
    """带原始扫描真龙贡献的 scans fake（只读公共 API 子集）。"""

    def __init__(self, scan_dates=(), contribs=None, stocks=None):
        self.scan_dates = list(scan_dates)
        self.contribs = contribs or {}
        self.stocks = stocks or {}
        self.contribution_calls: list[str] = []

    def list_scan_dates(self, date_from=None, date_to=None, source="v2"):
        return list(self.scan_dates)

    def list_scan_stock_contributions_by_date(self, scan_date, source="v2"):
        self.contribution_calls.append(scan_date)
        return [dict(row) for row in self.contribs.get(scan_date, [])]

    def get_scan_stocks(self, scan_id, source="v2"):
        if scan_id in self.stocks:
            return [dict(row) for row in self.stocks[scan_id]]
        return super().get_scan_stocks(scan_id, source=source)


def _bar(day: str, open_px: float, high: float, low: float, close: float,
         volume: float = 1000.0) -> dict:
    return {
        "date": day, "open": open_px, "high": high, "low": low,
        "close": close, "volume": volume, "pct": 0.0,
    }


class FakeKlines:
    def __init__(self, bars=None, error: Exception | None = None) -> None:
        self.bars = bars or []
        self.error = error
        self.calls: list[tuple[str, int, str]] = []

    def get_kline(self, code: str, days: int = 20, fq_type: str = "after"):
        self.calls.append((code, days, fq_type))
        if self.error is not None:
            raise self.error
        return self.bars


CLOCK_NOW = FixedClock(datetime(2026, 8, 25, 15, 30,
                                tzinfo=ZoneInfo("Asia/Shanghai")))
PERF_BARS = [
    _bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
    _bar("2026-08-18", 11.0, 11.0, 11.0, 11.0),  # 一字板
    _bar("2026-08-19", 10.8, 11.0, 10.5, 10.9),  # 买入日
    _bar("2026-08-20", 11.0, 11.8, 10.4, 11.6),  # 峰值
    _bar("2026-08-21", 11.0, 11.2, 10.8, 11.0),  # 峰值后回落
    _bar("2026-08-25", 10.9, 11.0, 10.6, 10.8),
]
PERF_KEYS = {
    "trade_date", "code", "caliber", "status", "as_of", "buy_date",
    "buy_price", "max_return_since_entry", "max_drawdown_since_entry",
    "peak_date", "latest_bar_date", "bars_in_window", "bars_after_entry",
    "error",
}


def perf_client(klines=None):
    return make_client(
        dragon_repository=FakeDragons(),
        scan_repository=FakeScans(),
        kline_provider=klines or FakeKlines(PERF_BARS),
        clock_module=CLOCK_NOW,
    )


def test_trade_dates_items_full_and_preview_prefix():
    rows = [
        _scan_contrib("600003", "C", 3, 70.0),
        _scan_contrib("600001", "A", 1, 90.0),
        _scan_contrib("600002", "B", 2, 80.0),
        _scan_contrib("600004", "D", None, 99.0),  # rank=None 被过滤
    ]
    scans = FakeScansContribs(
        scan_dates=["2026-08-17", "2026-08-21"],
        contribs={"2026-08-17": rows, "2026-08-21": rows},
    )
    dragons = FakeDragonsRows(dates=["2026-08-17", "2026-08-21"])
    client = make_client(dragon_repository=dragons, scan_repository=scans)
    dates = client.get("/api/dragons/trade-dates").json()

    assert [entry["trade_date"] for entry in dates["data"]] == [
        "2026-08-21", "2026-08-17",
    ]
    for entry in dates["data"]:
        assert entry["count"] == 3
        assert [item["code"] for item in entry["items"]] == [
            "600001", "600002", "600003",
        ]
        assert entry["preview"] == entry["items"][:3]
        assert set(entry) == {"trade_date", "count", "preview", "items"}
    # 有扫描贡献时不得回退 dragons 视图。
    assert dragons.get_dragons_calls == []
    assert scans.contribution_calls == ["2026-08-21", "2026-08-17"]


def test_trade_dates_shows_deduped_true_dragons_from_raw_scan():
    """扫描 3 支真龙、dragons_v2 仅 2 行（模拟 5 日去重）。"""
    scans = FakeScansContribs(
        scan_dates=["2026-10-29"],
        contribs={"2026-10-29": [
            _scan_contrib("001206", "青岛食品", 1, 91.0,
                          created_at="2026-10-29 15:30:00",
                          scan_id="v2_20261029_5"),
            _scan_contrib("002531", "天顺风能", 2, 85.0,
                          created_at="2026-10-29 15:30:00",
                          scan_id="v2_20261029_5"),
            _scan_contrib("600118", "中国卫星", 3, 80.0,
                          created_at="2026-10-29 15:30:00",
                          scan_id="v2_20261029_5"),
        ]},
    )
    dragons = FakeDragonsRows(
        dates=["2026-10-29"],
        rows_by_date={"2026-10-29": [
            {"code": "002531", "name": "天顺风能", "rank": 2,
             "composite_score": 85.0, "is_true_dragon": True},
            {"code": "600118", "name": "中国卫星", "rank": 3,
             "composite_score": 80.0, "is_true_dragon": True},
        ]},
    )
    client = make_client(dragon_repository=dragons, scan_repository=scans)
    body = client.get("/api/dragons/trade-dates").json()

    assert body["count"] == 1
    entry = body["data"][0]
    assert entry["trade_date"] == "2026-10-29"
    assert entry["count"] == 3
    assert [item["code"] for item in entry["items"]] == [
        "001206", "002531", "600118",
    ]
    assert entry["items"][0]["rank"] == 1
    assert entry["preview"] == entry["items"][:3]
    assert dragons.get_dragons_calls == []


def test_trade_dates_include_dates_only_present_in_scans():
    scans = FakeScansContribs(
        scan_dates=["2026-10-30"],
        contribs={"2026-10-30": [
            _scan_contrib("600005", "E", 1, 80.0, scan_id="v2_20261030_5",
                          created_at="2026-10-30 15:30:00"),
        ]},
    )
    dragons = FakeDragonsRows(
        dates=["2026-10-29"],
        rows_by_date={"2026-10-29": [
            {"code": "600006", "name": "F", "rank": 1,
             "composite_score": 70.0, "is_true_dragon": True},
        ]},
    )
    client = make_client(dragon_repository=dragons, scan_repository=scans)
    data = client.get("/api/dragons/trade-dates").json()["data"]

    assert [entry["trade_date"] for entry in data] == [
        "2026-10-30", "2026-10-29",
    ]
    assert data[0]["items"][0]["code"] == "600005"
    assert data[1]["items"][0]["code"] == "600006"


def test_trade_dates_collects_limit_after_skipping_empty_dates():
    scans = FakeScansContribs(
        scan_dates=["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"],
        contribs={
            "2026-10-30": [_scan_contrib("600010", "X", 1, 50.0,
                                         true_dragon=False)],
            "2026-10-29": [],
            "2026-10-28": [_scan_contrib("600011", "Y", 1, 60.0)],
            "2026-10-27": [_scan_contrib("600012", "Z", 1, 61.0)],
        },
    )
    client = make_client(dragon_repository=FakeDragonsRows(),
                         scan_repository=scans)
    data = client.get("/api/dragons/trade-dates?limit=2").json()["data"]

    # 先截断日期再丢弃空卡片会少返回，必须逐日收集到 limit。
    assert [entry["trade_date"] for entry in data] == [
        "2026-10-28", "2026-10-27",
    ]


def test_trade_dates_filters_true_dragon_rank_and_top_n():
    scans = FakeScansContribs(
        scan_dates=["2026-08-17"],
        contribs={"2026-08-17": [
            _scan_contrib("600001", "A", 1, 90.0),
            _scan_contrib("600002", "B", 2, 80.0, true_dragon=False),
            _scan_contrib("600003", "C", None, 70.0),
            _scan_contrib("600004", "D", 6, 60.0, top_n=5),
            _scan_contrib("600005", "E", 3, 50.0, true_dragon=None),
        ]},
    )
    client = make_client(dragon_repository=FakeDragonsRows(),
                         scan_repository=scans)
    items = client.get("/api/dragons/trade-dates").json()["data"][0]["items"]

    assert [item["code"] for item in items] == ["600001", "600005"]
    assert items[1]["is_true_dragon"] is None


def test_trade_dates_best_contribution_ordering():
    def items_for(rows):
        scans = FakeScansContribs(scan_dates=["2026-08-17"],
                                  contribs={"2026-08-17": rows})
        client = make_client(dragon_repository=FakeDragonsRows(),
                             scan_repository=scans)
        return client.get("/api/dragons/trade-dates").json()["data"][0]["items"]

    # rank 最小优先
    items = items_for([
        _scan_contrib("600001", "A", 2, 90.0, scan_id="s2"),
        _scan_contrib("600001", "A", 1, 10.0, scan_id="s1",
                      created_at="2026-08-17 10:00:00"),
    ])
    assert len(items) == 1 and items[0]["rank"] == 1
    assert items[0]["composite_score"] == 10.0

    # rank 相同比 composite_score
    items = items_for([
        _scan_contrib("600001", "A", 2, 70.0, scan_id="s1"),
        _scan_contrib("600001", "A", 2, 80.0, scan_id="s2",
                      created_at="2026-08-17 10:00:00"),
    ])
    assert items[0]["composite_score"] == 80.0

    # rank 与分数都相同比 scan_created_at（后者优先）
    items = items_for([
        _scan_contrib("600001", "旧", 2, 80.0, scan_id="s1",
                      created_at="2026-08-17 10:00:00"),
        _scan_contrib("600001", "新", 2, 80.0, scan_id="s2",
                      created_at="2026-08-17 15:30:00"),
    ])
    assert items[0]["name"] == "新"


def test_trade_dates_sort_rank_score_code():
    scans = FakeScansContribs(
        scan_dates=["2026-08-17"],
        contribs={"2026-08-17": [
            _scan_contrib("600003", "C", 1, 80.0),
            _scan_contrib("600002", "B", 1, 90.0),
            _scan_contrib("600001", "A", 2, 95.0),
        ]},
    )
    client = make_client(dragon_repository=FakeDragonsRows(),
                         scan_repository=scans)
    entry = client.get("/api/dragons/trade-dates").json()["data"][0]

    assert [item["code"] for item in entry["items"]] == [
        "600002", "600003", "600001",
    ]
    assert entry["preview"] == entry["items"][:3]


def test_backtest_pool_and_summary_ignore_card_source():
    scans = FakeScansContribs(
        scan_dates=["2026-08-17"],
        contribs={"2026-08-17": [
            _scan_contrib("600001", "A", 1, 90.0),
            _scan_contrib("600009", "Z", 2, 80.0),  # 被 5 日去重，仅卡片可见
        ]},
    )
    dragons = FakeDragonsRows(
        dates=["2026-08-17"],
        rows_by_date={"2026-08-17": [dict(ROW)]},
    )
    client = make_client(dragon_repository=dragons, scan_repository=scans)

    assert client.get("/api/dragons/trade-dates").json()["data"][0]["count"] == 2
    pool = client.get("/api/dragons").json()
    assert pool["count"] == 1
    assert [row["code"] for row in pool["data"]] == ["600001"]
    assert client.get("/api/summary").json()["total"] == 1


def test_trade_dates_falls_back_to_dragons_when_scan_contributions_empty():
    dragons = FakeDragonsRows(
        dates=["2026-08-17"],
        rows_by_date={"2026-08-17": [dict(ROW)]},
    )
    scans = FakeScansContribs(scan_dates=["2026-08-17"])
    client = make_client(dragon_repository=dragons, scan_repository=scans)
    data = client.get("/api/dragons/trade-dates").json()["data"]

    assert len(data) == 1
    assert data[0]["count"] == 1
    assert data[0]["items"][0]["code"] == "600001"
    assert dragons.get_dragons_calls == ["2026-08-17"]


def test_performance_ok_shape_and_provider_args():
    klines = FakeKlines(PERF_BARS)
    client = perf_client(klines)

    response = client.get(
        "/api/dragons/20260817/600001/performance?as_of=20260825")
    body = response.json()

    assert response.status_code == 200
    assert set(body) == PERF_KEYS
    assert "max_return_5d" not in body and "max_drawdown_5d" not in body
    assert body["caliber"] == "loose"
    assert body["status"] == "ok"
    assert body["trade_date"] == "2026-08-17"
    assert body["code"] == "600001"
    assert body["as_of"] == "2026-08-25"
    assert body["buy_date"] == "2026-08-19"
    assert body["buy_price"] == 10.5
    assert body["peak_date"] == "2026-08-20"
    assert body["max_return_since_entry"] == 12.38
    assert body["max_drawdown_since_entry"] == -0.95
    assert body["latest_bar_date"] == "2026-08-25"
    assert body["bars_after_entry"] == 3
    assert klines.calls == [("600001", 90, "normal")]


def test_performance_defaults_as_of_from_injected_clock():
    client = perf_client()

    body = client.get("/api/dragons/20260817/600001/performance").json()

    assert body["status"] == "ok"
    assert body["as_of"] == "2026-08-25"


def test_performance_no_entry_and_empty_window():
    no_entry = perf_client(FakeKlines([
        _bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        _bar("2026-08-18", 11.0, 11.0, 11.0, 11.0),
        _bar("2026-08-19", 12.1, 12.1, 12.1, 12.1),
    ])).get("/api/dragons/20260817/600001/performance?as_of=20260819").json()
    assert no_entry["status"] == "no_entry"
    assert no_entry["buy_date"] is None
    assert no_entry["latest_bar_date"] == "2026-08-19"
    assert no_entry["bars_in_window"] == 0

    empty = perf_client(FakeKlines([
        _bar("2026-08-17", 10.0, 10.2, 9.8, 10.0),
        _bar("2026-08-18", 10.5, 11.0, 10.2, 10.8),
    ])).get("/api/dragons/20260817/600001/performance?as_of=20260818").json()
    assert empty["status"] == "empty_window"
    assert empty["buy_date"] == "2026-08-18"
    assert empty["latest_bar_date"] == "2026-08-18"
    assert empty["bars_in_window"] == 1
    assert empty["max_return_since_entry"] is None


def test_performance_provider_failure_is_200_status_error():
    client = perf_client(FakeKlines(error=RuntimeError("cookie expired")))

    response = client.get("/api/dragons/20260817/600001/performance")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert "cookie expired" in body["error"]
    assert body["buy_date"] is None


def test_performance_invalid_request_and_not_found():
    client = perf_client()

    bad_date = client.get("/api/dragons/2026-13-99/600001/performance")
    assert bad_date.status_code == 400
    assert bad_date.json()["error"] == "invalid_request"

    bad_as_of = client.get(
        "/api/dragons/20260817/600001/performance?as_of=20260816")
    assert bad_as_of.status_code == 400
    assert bad_as_of.json()["error"] == "invalid_request"

    missing = client.get("/api/dragons/20260817/999999/performance")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"


DEDUPED_SCAN_ID = "v2_20261029_5"
DEDUPED_DIMS_STOCK = {
    "code": "600001", "name": "龙头A", "rank": 1, "composite_score": 88.0,
    "board_count": 2, "concepts": ["AI算力"], "dim_drive": 9.0,
    "dim_anti_drop": 8.0, "dim_leadership": 7.0, "dim_absorption": 6.0,
    "dim_liquidity": 5.0, "is_true_dragon": True,
    "reject_reason": "未达门槛", "report_text": "r",
}


def _deduped_scan_client(dragons=None, klines=None):
    """dragons_v2 无行、当日原始扫描贡献存在（模拟被 5 日去重）。"""
    scans = FakeScansContribs(
        scan_dates=["2026-10-29"],
        contribs={"2026-10-29": [
            _scan_contrib("600001", "龙头A", 1, 88.0, scan_id=DEDUPED_SCAN_ID,
                          created_at="2026-10-29 15:30:00",
                          concepts=["AI算力"]),
        ]},
        stocks={DEDUPED_SCAN_ID: [DEDUPED_DIMS_STOCK]},
    )
    return make_client(
        dragon_repository=dragons or FakeDragonsRows(
            dates=["2026-10-29"], query_rows=[]),
        scan_repository=scans,
        kline_provider=klines or FakeKlines(PERF_BARS),
        clock_module=CLOCK_NOW,
    )


def test_dragon_detail_falls_back_to_scan_contribution():
    client = _deduped_scan_client()

    response = client.get("/api/dragons/20261029/600001")
    detail = response.json()

    assert response.status_code == 200
    assert detail["trade_date"] == "2026-10-29"
    assert detail["code"] == "600001"
    assert detail["name"] == "龙头A"
    assert detail["scan_id"] == DEDUPED_SCAN_ID
    assert detail["rank"] == 1
    assert detail["composite_score"] == 88.0
    assert detail["board_count"] == 2
    assert detail["concepts"] == ["AI算力"]
    assert detail["dims"]["drive"] == 9.0
    assert detail["reject_reason"] == "未达门槛"
    assert detail["dims_source"] == "scan_stocks"
    assert detail["quote"]["open"] == 10.0
    assert detail["review_loose"] == {
        "buy_date": None, "buy_price": None, "max_return_5d": None,
        "max_drawdown_5d": None, "max_return_hold_days": None,
        "review_status": None,
    }
    assert detail["review_strict"] is None
    assert detail["derived_status"] is None
    assert detail["caliber"] == "strict"


def test_dragon_detail_404_when_both_sources_missing():
    client = _deduped_scan_client()

    response = client.get("/api/dragons/20261029/600999")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_performance_falls_back_to_scan_contribution():
    client = _deduped_scan_client()

    response = client.get(
        "/api/dragons/20261029/600001/performance?as_of=20261030")
    body = response.json()

    assert response.status_code == 200
    # 记录存在性来自扫描贡献；该 fixture 的日 K 均早于入选日 → 无可介入日。
    assert set(body) == PERF_KEYS
    assert body["status"] == "no_entry"
    assert body["buy_date"] is None
    assert body["latest_bar_date"] == "2026-08-25"


def test_performance_scan_fallback_ok_with_covering_klines():
    klines = FakeKlines([
        _bar("2026-10-29", 10.0, 10.2, 9.8, 10.0),
        _bar("2026-10-30", 10.1, 10.6, 10.0, 10.5),
        _bar("2026-10-31", 10.6, 11.0, 10.4, 10.9),
    ])
    client = _deduped_scan_client(klines=klines)

    body = client.get(
        "/api/dragons/20261029/600001/performance?as_of=20261031").json()

    assert body["status"] == "ok"
    assert body["buy_date"] == "2026-10-30"
    assert body["buy_price"] == 10.0
    assert body["max_return_since_entry"] == 10.0


def test_performance_404_when_both_sources_missing():
    client = _deduped_scan_client()

    response = client.get(
        "/api/dragons/20261029/600999/performance?as_of=20261030")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_scans_sort_pagination_and_detail():
    client = make_client(scan_repository=FakeScans())
    listing = client.get("/api/scans").json()
    assert listing["count"] == 1
    detail = client.get("/api/scans/v2_20260817_5").json()
    assert detail["has_raw_output"] is True
    assert detail["raw_output_bytes"] == len('{"x": 1}')
    page = client.get("/api/scans/v2_20260817_5/stocks?limit=1").json()
    assert page["total"] == 2 and page["count"] == 1 and page["truncated"] is True
    assert page["data"][0]["code"] == "600001"  # rank 1 优先
    true_only = client.get(
        "/api/scans/v2_20260817_5/stocks?true_only=true").json()
    assert [row["code"] for row in true_only["data"]] == ["600001"]
    assert client.get("/api/scans/nope").status_code == 404


# ─── factors ───────────────────────────────────────────────────────────────


class FakeFactorService:
    def list_factors(self):
        return [{
            "name": "five_dim_composite", "label": "五维综合", "version": "1.0",
            "version_source": "const", "source": "scan", "frequency": "1d",
            "direction": "higher_better", "unit": "score",
            "available_phase": "post_close", "pit_rule": "after_close",
            "lookback": 0, "default_enabled": True, "depends_on": [],
            "valid_from": None, "deprecated_at": None, "description": "d",
        }]

    def query(self, *, date=None, date_from=None, date_to=None, name=None,
              code=None, version=None, db_path=None):
        if name == "unknown":
            raise ValueError("未知因子: 'unknown'")
        return {
            "trade_date": date, "rows": 1, "resolved_versions": {"f": "1.0"},
            "missing_versions": [],
            "values": {"600001": {"f": 88.0}},
            "available_at": {"600001": {"f": "2026-08-17 15:31:00"}},
            "source_scan_id": {"600001": {"f": "v2_20260817_5"}},
        }


class FakeFactorStorage:
    def list_registry(self, names=None, db_path=None):
        return [{
            "factor_name": "five_dim_composite", "factor_version": "1.0",
            "params": {"k": 1}, "spec_hash": "h", "created_at": "c",
        }]

    def list_build_runs(self, *, trade_date=None, limit=20, db_path=None):
        return [{
            "id": 7, "trade_date": trade_date or "2026-08-17",
            "started_at": "s", "finished_at": "f", "created_at": "c",
            "report": {"ok": True, "scan": {"scan_id": "x"}, "warnings": [],
                       "factors": {"f": {"status": "ok", "rows": 1,
                                         "coverage": 0.9, "warnings": []}}},
            "report_error": None,
        }]


def test_factors_registry_values_and_runs():
    client = make_client(factor_service=FakeFactorService(),
                         factor_storage=FakeFactorStorage())
    listing = client.get("/api/factors").json()
    assert listing["count"] == 1
    item = listing["data"][0]
    assert item["db_version"] == "1.0" and item["db_registered"] is True
    assert item["enabled"] is True
    values = client.get("/api/factors/values?trade_date=20260817").json()
    assert values["resolved_versions"] == {"f": "1.0"}
    assert values["rows"][0]["code"] == "600001"
    assert values["coverage"]["f"]["coverage"] == 0.9
    assert values["coverage_source"] == "build_run"
    assert client.get("/api/factors/values").status_code == 400
    bad = client.get("/api/factors/values?trade_date=20260817&name=unknown")
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_request"
    runs = client.get("/api/factors/runs?trade_date=20260817").json()
    assert runs["data"][0]["ok"] is True
    assert runs["data"][0]["report"] is None
    with_report = client.get(
        "/api/factors/runs?trade_date=20260817&include_report=true").json()
    assert with_report["data"][0]["report"]["ok"] is True


# ─── prep / auction ────────────────────────────────────────────────────────


PREP_ASSETS = {
    "dragons": {"rows": [{"code": "600001"}], "as_of": "2026-09-11"},
    "ths_limit_up_pool": {"rows": [{"code": "600001"}], "as_of": "2026-09-11"},
    "macro_global": {"as_of": "2026-09-11", "instruments": {}},
    "collection_report": {"dragons": {"status": "ok"},
                          "macro_global": {"status": "ok"}},
}

SNAPSHOT = {
    "trade_date": "2026-09-11", "version": "0.8",
    "sentiment": {"position_pct": 0.5}, "main_boards": [],
    "macro": {"available": True}, "dragon_source": {"window_days": 5},
    "candidates": [{"code": "600001"}],
    "review_candidates": [{"code": "600001", "legacy": "big"}],
    "raw_candidates": {"big": True},
    "review": {"status": "ok"}, "feedback": {}, "plan_review": {},
}


class FakeScanLogs:
    def summarize_scan_logs(self, scan_ids, source="v2"):
        if "v2_20260817_5" in set(scan_ids or []):
            return {"v2_20260817_5": {
                "log_entries": 2, "error_n": 1, "first_ts": 1.0, "last_ts": 2.0}}
        return {}

    def get_scan_logs_page(self, scan_id, level=None, offset=0, limit=200,
                           source="v2"):
        entries = [
            {"ts": 2.0, "category": "api:tdx", "level": "error",
             "message": "timeout", "code": ""},
            {"ts": 1.0, "category": "phase:A", "level": "info",
             "message": "A 完成", "code": ""},
        ]
        if level:
            entries = [entry for entry in entries if entry["level"] == level]
        window = entries[offset:offset + limit]
        return {"total": len(entries), "entries": window}

    def log_summary(self, scan_id, source="v2"):
        if scan_id == "v2_nologs":
            return {"error": "无日志"}
        return {
            "scan_id": scan_id, "total_entries": 2,
            "phases": {"A": "A 完成"},
            "api_stats": {"total": 1, "ok": 0, "error": 1, "total_ms": 5,
                          "by_provider": {"tdx": {"count": 1, "total_ms": 5}}},
            "error_count": 1, "scorer_count": 3, "source": "v2",
        }


class FakeAssets:
    def get_prep_assets(self, trade_date):
        return dict(PREP_ASSETS) if trade_date == "2026-09-11" else {}

    def has_prep_snapshot(self, trade_date, source="v2"):
        return trade_date == "2026-09-11"

    def get_prep_snapshot(self, trade_date, source="v2", with_meta=False):
        if trade_date != "2026-09-11":
            return (None, {"created_at": None}) if with_meta else None
        if with_meta:
            return dict(SNAPSHOT), {"created_at": "2026-09-11 17:00:00"}
        return dict(SNAPSHOT)

    def list_prep_asset_dates(self):
        return ["2026-09-10", "2026-09-11"]

    def list_prep_snapshot_dates(self):
        return ["2026-09-11"]

    def list_auction_snapshot_dates(self):
        return ["2026-09-11"]

    def list_auction_asset_dates(self):
        return ["2026-09-11"]

    def list_auction_snapshots(self, trade_date, source="v2",
                               with_meta=False):
        if trade_date != "2026-09-11":
            return []
        return [
            {"trade_date": trade_date, "code": "600001", "is_weak": False,
             "strength": 80.0, "level": "强", "direction": "bull", "rank": 1,
             "config_version": "auction_v2", "validation_status": "ok",
             "data_quality": {"status": "ok"}, "risk_events": []},
            {"trade_date": trade_date, "code": "600002", "is_weak": True,
             "strength": 0.0, "level": "弱", "data_quality": {"status": "ok"},
             "risk_events": []},
        ]

    def list_auction_assets(self, trade_date):
        return [{"code": "600001"}]

    def list_auction_asset_names(self, trade_date, **kwargs):
        return {"600001": "龙头A"}

    def get_previous_prep_snapshot(self, before_date, source="v2"):
        return None, {"trade_date": None, "created_at": None, "error": None}

    def get_prep_asset(self, trade_date, asset_type):
        return None

    def first_prep_snapshot_date(self, source="v2"):
        return "2026-09-11"

    def first_prep_record_date(self, source="v2"):
        return "2026-09-10"

    def prep_calendar_rows(self, date_from, date_to, **kwargs):
        if date_to < "2026-09-11" or date_from > "2026-09-11":
            return {}
        return {"2026-09-11": {
            "asset_types": ["dragons", "collection_report"],
            "report": {"dragons": {"status": "ok"}},
            "report_error": None,
            "has_snapshot": True,
        }}

    def auction_calendar_rows(self, date_from, date_to, **kwargs):
        if date_to < "2026-09-11" or date_from > "2026-09-11":
            return {}
        return {"2026-09-11": {"asset_codes": ["600001"],
                               "snapshot_codes": ["600001"]}}


def test_prep_watermark_and_snapshot_slim():
    client = make_client(assets_repository=FakeAssets())
    dates = client.get("/api/prep/dates").json()
    assert dates["count"] == 2
    watermark = client.get("/api/prep/20260911").json()
    assert watermark["available"] is True
    assert len(watermark["assets"]) == 13
    assert watermark["collection_report"][0]["asset_type"] == "dragons"
    assert "ths_block_top" in watermark["missing"]
    snapshot = client.get("/api/prep/20260911/snapshot").json()
    assert snapshot["version"] == "0.8"
    assert snapshot["blocks_present"]
    assert snapshot["raw_size_bytes"] > 0
    assert "raw_candidates" not in snapshot
    assert all("legacy" not in item for item in snapshot["review_candidates"])
    assert client.get("/api/prep/20200101/snapshot").status_code == 404


def test_auction_dates_and_day_summary():
    client = make_client(assets_repository=FakeAssets())
    dates = client.get("/api/auction/dates").json()
    assert dates["count"] == 1
    day = client.get("/api/auction/20260911?with_assets=true").json()
    assert day["count"] == 2 and day["available"] is True
    assert day["summary"]["strong"] == 1
    assert day["summary"]["vetoed"] == 1
    assert day["assets_count"] == 1
    assert day["snapshots"][0]["version"] == "auction_v2"


# ─── 错误形状（main 必须修改项） ───────────────────────────────────────────


def test_factor_runs_invalid_date_is_400():
    client = make_client(factor_storage=FakeFactorStorage())
    response = client.get("/api/factors/runs?trade_date=2026-13-99")
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_request"
    assert body["detail"]


def test_account_invalid_json_body_keeps_legacy_shape():
    client = make_client()
    malformed = client.post(
        "/api/account/runs",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert malformed.status_code == 400
    assert malformed.json() == {"error": "invalid json body"}

    not_object = client.post(
        "/api/account/runs",
        content=b"[]",
        headers={"Content-Type": "application/json"},
    )
    assert not_object.status_code == 400
    assert not_object.json() == {"error": "json body must be an object"}


# ─── 扫描运行态 / 日志端点（方案 §9.3） ────────────────────────────────────

SCAN_ROWS = [
    {"id": "v2_20260911_5", "scan_date": "2026-09-11", "elapsed_s": 1.5,
     "top_n": 5, "candidates_n": 3, "workers": 2,
     "created_at": "2026-09-11 15:30:00", "source": "v2"},
    {"id": "v2_20260910_5", "scan_date": "2026-09-10", "elapsed_s": 1.0,
     "top_n": 5, "candidates_n": 2, "workers": 2,
     "created_at": "2026-09-10 15:30:00", "source": "v2"},
    {"id": "v2_20260909_5", "scan_date": "2026-09-09", "elapsed_s": 0.9,
     "top_n": 5, "candidates_n": 1, "workers": 1,
     "created_at": "2026-09-09 15:30:00", "source": "v2"},
]


class FakeScansThreeStates(FakeScans):
    def list_scans(self, limit=50, source="v2"):
        return list(SCAN_ROWS)

    def summarize_scan_stocks(self, scan_ids, source="v2"):
        return {scan_id: {"stock_n": 3, "true_dragon_n": 2}
                for scan_id in scan_ids}

    def get_scan(self, scan_id, source="v2"):
        for row in SCAN_ROWS:
            if row["id"] == scan_id:
                return {**row, "raw_output": None}
        return None


class SpyScanLogs(FakeScanLogs):
    def __init__(self):
        self.summary_calls = []

    def summarize_scan_logs(self, scan_ids, source="v2"):
        stats = {
            "v2_20260911_5": {"log_entries": 3, "error_n": 1,
                              "first_ts": 1.0, "last_ts": 3.0},
            "v2_20260910_5": {"log_entries": 2, "error_n": 0,
                              "first_ts": 1.0, "last_ts": 2.0},
        }
        return {scan_id: stats[scan_id] for scan_id in scan_ids
                if scan_id in stats}

    def log_summary(self, scan_id, source="v2"):
        self.summary_calls.append(scan_id)
        return {"error": "无日志"}


def test_scans_list_three_states_without_per_row_log_summary():
    logs = SpyScanLogs()
    client = make_client(scan_repository=FakeScansThreeStates(),
                         scan_logs_repository=logs)
    payload = client.get("/api/scans").json()
    by_id = {row["id"]: row for row in payload["data"]}
    assert by_id["v2_20260911_5"]["status"] == "completed_with_errors"
    assert by_id["v2_20260910_5"]["status"] == "completed"
    assert by_id["v2_20260909_5"]["status"] == "logs_missing"
    assert by_id["v2_20260909_5"]["log_entries"] == 0
    assert by_id["v2_20260909_5"]["error_n"] is None
    assert by_id["v2_20260911_5"]["true_dragon_n"] == 2
    assert by_id["v2_20260911_5"]["stock_n"] == 3
    # 列表不逐行解析日志正文。
    assert logs.summary_calls == []


def test_scan_detail_includes_runtime_fields():
    client = make_client(scan_repository=FakeScansThreeStates(),
                         scan_logs_repository=SpyScanLogs())
    detail = client.get("/api/scans/v2_20260911_5").json()
    assert detail["status"] == "completed_with_errors"
    assert detail["error_n"] == 1
    assert detail["log_entries"] == 3
    assert client.get("/api/scans/v2_missing").status_code == 404


def test_scan_logs_page_and_summary():
    client = make_client(scan_repository=FakeScansThreeStates(),
                         scan_logs_repository=FakeScanLogs())
    page = client.get("/api/scans/v2_20260911_5/logs?limit=1").json()
    assert page["available"] is True
    assert page["total"] == 2
    assert len(page["entries"]) == 1
    entry = page["entries"][0]
    assert set(entry) == {"ts", "time", "category", "level", "message", "code"}
    assert entry["time"] == "08:00:02"
    assert page["summary"]["phases"][0]["key"] == "A"
    assert page["summary"]["api_error"] == 1
    assert "data" not in entry

    filtered = client.get("/api/scans/v2_20260911_5/logs?level=error").json()
    assert filtered["total"] == 1
    assert filtered["entries"][0]["message"] == "timeout"

    bad = client.get("/api/scans/v2_20260911_5/logs?level=debug")
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_request"

    missing = client.get("/api/scans/nope/logs")
    assert missing.status_code == 404


def test_scan_logs_missing_is_not_an_error():
    client = make_client(scan_repository=FakeScansThreeStates(),
                         scan_logs_repository=FakeScanLogs())
    # v2_nologs 不存在 → 404；用真实存在的 scan 但 log_summary 返回无日志。
    class NoLogs(FakeScanLogs):
        def get_scan_logs_page(self, scan_id, level=None, offset=0, limit=200,
                               source="v2"):
            return {"total": 0, "entries": []}

        def log_summary(self, scan_id, source="v2"):
            return {"error": "无日志"}

    client = make_client(scan_repository=FakeScansThreeStates(),
                         scan_logs_repository=NoLogs())
    page = client.get("/api/scans/v2_20260909_5/logs").json()
    assert page == {"scan_id": "v2_20260909_5", "available": False,
                    "total": 0, "entries": [], "summary": None}


# ─── prep 日历 / 缺日 / derived / review-context（方案 §9.3） ──────────────

FIXED_NOW = datetime(2026, 9, 11, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def _fixed_clock():
    return FixedClock(FIXED_NOW)


def test_prep_calendar_three_states_and_missing_day_clickable():
    client = make_client(assets_repository=FakeAssets(), clock_module=_fixed_clock())
    calendar = client.get("/api/prep/calendar?days=365").json()
    assert calendar["available"] is True
    assert calendar["end_date"] == "2026-09-11"
    assert [day["trade_date"] for day in calendar["days"]] == ["2026-09-11"]
    day = calendar["days"][0]
    assert day["status"] == "incomplete"
    assert day["has_snapshot"] is True
    assert "ths_block_top" in day["expected_missing"]
    assert day["expected_total"] >= 4

    bad = client.get("/api/prep/calendar?days=999")
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_request"
    bad_date = client.get("/api/prep/calendar?end=2026-13-01")
    assert bad_date.status_code == 400


def test_prep_missing_day_returns_200_with_all_expected_missing():
    client = make_client(assets_repository=FakeAssets(), clock_module=_fixed_clock())
    response = client.get("/api/prep/20200101")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["has_snapshot"] is False
    assert payload["expected_ok"] == 0
    assert payload["expected_total"] >= 4
    assert all(not row["present"] for row in payload["assets"])
    assert all(not row["asset_complete"] for row in payload["assets"])


def test_prep_watermark_exposes_category_and_expected():
    client = make_client(assets_repository=FakeAssets(), clock_module=_fixed_clock())
    payload = client.get("/api/prep/20260911").json()
    by_type = {row["asset_type"]: row for row in payload["assets"]}
    assert by_type["dragons"]["category"] == "core"
    assert by_type["dragons"]["expected"] is True
    assert by_type["dragons"]["asset_complete"] is True
    assert by_type["stock_daily"]["category"] == "review_conditional"
    assert payload["expected_total"] >= 4


def test_prep_snapshot_derived_decodes_packed_high_num():
    client = make_client(assets_repository=FakeAssets())
    payload = client.get("/api/prep/20260911/snapshot").json()
    assert "derived" in payload
    assert payload["derived"]["needs_recompute"] is False


def test_review_context_shape_and_missing_snapshot_reason():
    client = make_client(assets_repository=FakeAssets())
    context = client.get("/api/prep/20260911/review-context").json()
    assert context["available"] is True
    assert context["plan_effect"]["reason"] == "plan_review_missing"
    assert context["prev_trade_date"] is None

    missing = client.get("/api/prep/20200101/review-context").json()
    assert missing["available"] is False
    assert missing["reason"] == "snapshot_missing"
    assert client.get("/api/prep/not-a-date/review-context").status_code == 400


# ─── auction 日历 / 空日 / 成效（方案 §9.3） ───────────────────────────────


class FakeAuctionStore:
    def __init__(self, days):
        self.days = days

    def snapshot_dates(self, date_from, date_to):
        return sorted(day for day in self.days if date_from <= day <= date_to)

    def load(self, day):
        return list(self.days.get(day, []))


class FakeDecisionStoreModule:
    def __init__(self, days):
        self.days = days

    def build_store(self, source):
        assert source == "auction"
        return FakeAuctionStore(self.days)


def _bars_loader(bars):
    def load(codes, date_from, date_to):
        return bars
    return load


def test_auction_calendar_and_empty_day():
    client = make_client(assets_repository=FakeAssets(), clock_module=_fixed_clock())
    calendar = client.get("/api/auction/calendar?days=365").json()
    assert calendar["available"] is True
    assert calendar["latest_record_date"] == "2026-09-11"
    assert calendar["days"][0]["status"] == "complete"

    empty = client.get("/api/auction/20200101")
    assert empty.status_code == 200
    body = empty.json()
    assert body["available"] is False
    assert body["count"] == 0
    assert body["run"]["snapshot_count"] == 0
    assert body["run"]["evaluation_available"] is False


def test_auction_day_exposes_names_run_and_collection_status():
    client = make_client(assets_repository=FakeAssets())
    body = client.get("/api/auction/20260911?with_assets=true").json()
    assert body["names"] == {"600001": "龙头A"}
    assert body["asset_codes"] == ["600001"]
    by_code = {row["code"]: row for row in body["snapshots"]}
    assert by_code["600001"]["collection_status"] == "ok"
    assert by_code["600002"]["collection_status"] == "snapshot_only"
    assert body["run"]["snapshot_only_codes"] == ["600002"]
    assert body["run"]["quality_counts"]["vetoed"] == 1


def test_auction_effect_no_snapshot_and_no_bars_and_invalid_params():
    empty_store = FakeDecisionStoreModule({})
    no_bars = {"rows": {}, "truncated": False, "bar_dates": [], "codes": []}
    client = make_client(
        assets_repository=FakeAssets(),
        decision_review_store=empty_store,
        local_bars_loader=_bars_loader(no_bars),
    )
    payload = client.get("/api/auction/20260911/effect").json()
    assert payload["status"] == "insufficient_data"
    assert payload["reason"] == "no_snapshot"
    assert payload["data_source"] == "local_stock_daily"

    store = FakeDecisionStoreModule({"2026-09-11": [{
        "trade_date": "2026-09-11", "code": "600001", "version": "2",
        "is_weak": False, "strength": 80.0, "level": "强",
        "direction": "bull", "rank": 1, "validation_status": "ok",
        "data_quality": {"status": "ok"}, "unverified": False,
        "risk_events": [], "config_version": "auction_v2",
    }]})
    client = make_client(
        assets_repository=FakeAssets(),
        decision_review_store=store,
        local_bars_loader=_bars_loader(no_bars),
    )
    payload = client.get("/api/auction/20260911/effect").json()
    assert payload["status"] == "insufficient_data"
    assert payload["reason"] == "local_bars_missing"
    assert payload["local_coverage"]["selected_codes"] == 1

    assert client.get(
        "/api/auction/20260911/effect?horizons=2").status_code == 400
    assert client.get(
        "/api/auction/20260911/effect?horizons=").status_code == 400
    assert client.get(
        "/api/auction/20260911/effect?window=999").status_code == 400


def test_assets_router_never_references_providers():
    source = (Path(__file__).resolve().parents[2]
              / "web_ui" / "backend" / "routers" / "assets.py").read_text(
        encoding="utf-8")
    for token in ("kline_provider", "infra.provider", "XueqiuProvider",
                  "TdxProvider", "requests", "urllib"):
        assert token not in source, f"assets router 不应出现 {token!r}"


# ─── prep plan-followup / 空仓验证（方案 §9.2） ────────────────────────────

PLAN_FOLLOWUP_BAR_DATES = ["2026-09-11", "2026-09-14", "2026-09-15",
                           "2026-09-16", "2026-09-17", "2026-09-18"]


def _plan_snapshot(day: str) -> dict:
    return {
        "trade_date": day, "version": "0.8",
        "review": {"market": {
            "state": "strong" if day == "2026-09-11" else "neutral",
            "score": 80.0 if day == "2026-09-11" else 70.0,
            "score_change": 5.0,
            "inputs": {"limit_up_cnt": 88, "limit_down_cnt": 3,
                       "broken_rate": 0.183, "yesterday_premium_pct": 2.4},
        }},
        "sentiment": {"position_pct": 0.5},
        "review_candidates": [
            {"code": "600001", "name": "龙头A", "review_score": 88.0,
             "review_grade": "强", "position_pct": 20,
             "eligibility": "eligible"},
            {"code": "600002", "name": "跟风B", "review_score": 62.5,
             "review_grade": "一般", "position_pct": 0},
        ],
        "plan_review": {"plan_date": "2026-09-10", "eval_date": "2026-09-11",
                        "items": [], "no_trade": []},
        "feedback": {"checkpoint": "T1_open", "items": [], "degraded": []},
    }


class FakePrepAssets(FakeAssets):
    def get_prep_snapshot(self, trade_date, source="v2", with_meta=False):
        if trade_date not in ("2026-09-10", "2026-09-11"):
            return (None, {"created_at": None}) if with_meta else None
        payload = _plan_snapshot(trade_date)
        if with_meta:
            return dict(payload), {"created_at": f"{trade_date} 17:00:00"}
        return dict(payload)

    def get_previous_prep_snapshot(self, before_date, source="v2"):
        if before_date <= "2026-09-10":
            return None, {"trade_date": None, "created_at": None, "error": None}
        return _plan_snapshot("2026-09-10"), {
            "trade_date": "2026-09-10", "created_at": "2026-09-10 17:00:00",
            "error": None}


class FakePrepStore:
    def __init__(self, days):
        self.days = days

    def snapshot_dates(self, date_from, date_to):
        return sorted(day for day in self.days if date_from <= day <= date_to)

    def load(self, day):
        return dict(self.days[day]), {"created_at": f"{day} 16:45:00"}


class FakePrepDecisionStoreModule:
    def __init__(self, days):
        self.days = days
        self.sources: list[str] = []

    def build_store(self, source):
        self.sources.append(source)
        return FakePrepStore(self.days)


def _plan_bars() -> dict:
    rows = {
        "600001": [
            {"date": day, "open": 10.1 + index * 0.1, "high": 11.0,
             "low": 10.0, "close": 10.5, "volume": 1000.0,
             "amount": 500_000_000.0}
            for index, day in enumerate(PLAN_FOLLOWUP_BAR_DATES)
        ],
        "600002": [
            {"date": day, "open": 10.0, "high": 10.0, "low": 10.0,
             "close": 10.0, "volume": 1000.0, "amount": 500_000_000.0}
            for day in PLAN_FOLLOWUP_BAR_DATES
        ],
    }
    return {"rows": rows, "truncated": False,
            "bar_dates": list(PLAN_FOLLOWUP_BAR_DATES), "codes": sorted(rows)}


def test_prep_plan_followup_ok_path_and_store_source():
    store = FakePrepDecisionStoreModule(
        {"2026-09-10": _plan_snapshot("2026-09-10")})
    client = make_client(
        assets_repository=FakePrepAssets(),
        decision_review_store=store,
        local_bars_loader=_bars_loader(_plan_bars()),
    )
    response = client.get("/api/prep/20260911/plan-followup")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["reason"] is None
    assert body["trade_date"] == "2026-09-11"
    assert body["plan_date"] == "2026-09-10"
    assert body["plan_date_source"] == "plan_review"
    assert body["data_source"] == "local_stock_daily"
    assert body["horizons"] == [1, 3, 5]
    assert store.sources == ["prep"]

    rows = {row["code"]: row for row in body["rows"]}
    assert set(rows) == {"600001", "600002"}
    assert rows["600001"]["group"] == "recommended"
    assert rows["600002"]["group"] == "not_recommended"
    assert rows["600001"]["horizons"]["1"]["net_return_pct"] == pytest.approx(0.8601, abs=1e-3)
    assert rows["600001"]["horizons"]["1"]["outcome"] == "up"
    assert rows["600002"]["horizons"]["1"]["net_return_pct"] == pytest.approx(-0.13, abs=1e-6)
    groups = {group["group"]: group for group in body["summary"][0]["groups"]}
    assert groups["recommended"]["valid"] == 1
    assert groups["not_recommended"]["valid"] == 1
    assert body["summary"][0]["spread"] == pytest.approx(0.9901, abs=1e-3)
    assert body["local_coverage"]["plan_codes"] == 2
    assert any("13bp" in caveat for caveat in body["caveats"])


def test_prep_plan_followup_no_plan_and_missing_snapshot():
    client = make_client(assets_repository=FakeAssets())
    no_plan = client.get("/api/prep/20260911/plan-followup")
    assert no_plan.status_code == 200
    assert no_plan.json()["status"] == "insufficient_data"
    assert no_plan.json()["reason"] == "no_plan"
    assert no_plan.json()["rows"] == []

    missing = client.get("/api/prep/20200101/plan-followup")
    assert missing.status_code == 200
    assert missing.json()["reason"] == "snapshot_missing"


def test_prep_plan_followup_invalid_params():
    client = make_client(assets_repository=FakePrepAssets())
    assert client.get(
        "/api/prep/20260911/plan-followup?horizons=2").status_code == 400
    assert client.get(
        "/api/prep/20260911/plan-followup?horizons=").status_code == 400
    assert client.get(
        "/api/prep/not-a-date/plan-followup").status_code == 400


def test_review_context_market_verification_shape():
    client = make_client(assets_repository=FakePrepAssets())
    context = client.get("/api/prep/20260911/review-context").json()
    market = context["plan_effect"]["market"]
    verification = market["verification"]
    assert verification["conclusion"] == "correct"
    assert verification["advice"] == "hold"
    assert verification["prev_trade_date"] == "2026-09-10"
    assert verification["current_trade_date"] == "2026-09-11"
    evidence = verification["evidence"]
    assert evidence["prev_score"] == 70.0
    assert evidence["current_score"] == 80.0
    assert evidence["score_change"] == 10.0
    assert evidence["limit_up_cnt"] == 88
    assert evidence["limit_down_cnt"] == 3
    # 既有 score_change 语义不变（来自 review.market.score_change）。
    assert market["score_change"] == 5.0


# ─── 3A：默认日期字段 / prep effect / accuracy / 路由顺序 ────────────────────

ACC_CALENDAR = ["2026-09-01", "2026-09-02", "2026-09-03",
                "2026-09-04", "2026-09-05"]


def _acc_bar(day, *, open_px, close):
    return {"date": day, "open": open_px, "high": max(open_px, close) + 0.1,
            "low": min(open_px, close) - 0.1, "close": close,
            "volume": 1000.0}


def _acc_bars() -> dict:
    return {
        "600001": [
            _acc_bar("2026-09-01", open_px=10.0, close=10.5),
            _acc_bar("2026-09-02", open_px=10.0, close=11.0),
            _acc_bar("2026-09-03", open_px=11.0, close=9.0),
            _acc_bar("2026-09-04", open_px=10.5, close=12.0),
            _acc_bar("2026-09-05", open_px=12.0, close=12.5),
        ],
    }


def _acc_snapshot(day: str, candidates) -> dict:
    return {"trade_date": day, "version": "0.8",
            "sentiment": {"position_pct": 0.5},
            "review_candidates": candidates,
            "review": {"mainlines": []}}


class FakeAccuracyAssets(FakeAssets):
    def __init__(self):
        self.prep_days = {
            "2026-09-01": _acc_snapshot("2026-09-01", [
                {"code": "600001", "name": "龙头A",
                 "eligibility": "eligible", "position_pct": 20}]),
            "2026-09-02": _acc_snapshot("2026-09-02", [
                {"code": "600001", "name": "龙头A",
                 "eligibility": "eligible", "position_pct": 20}]),
        }

    def list_prep_snapshot_dates(self):
        return sorted(self.prep_days)

    def get_prep_snapshot(self, trade_date, source="v2", with_meta=False):
        payload = self.prep_days.get(trade_date)
        if with_meta:
            return payload, {"created_at": f"{trade_date} 17:00:00"}
        return payload

    def list_auction_snapshot_dates(self):
        return ["2026-09-01"]

    def list_auction_snapshots(self, trade_date, source="v2",
                               with_meta=False):
        if trade_date != "2026-09-01":
            return []
        return [{"trade_date": trade_date, "code": "600001", "version": "2",
                 "level": "强", "direction": "bear", "is_weak": False,
                 "data_quality": {"status": "ok"}, "unverified": False}]

    def list_auction_asset_names(self, trade_date, **kwargs):
        return {"600001": "龙头A"}


def _acc_bars_loader(*_args, **_kwargs):
    return {"rows": _acc_bars(), "truncated": False,
            "bar_dates": ACC_CALENDAR, "codes": ["600001"]}


def test_prep_calendar_default_date_fields():
    client = make_client(
        assets_repository=FakeAssets(),
        clock_module=_fixed_clock(),
        bar_calendar_loader=_calendar_loader(["2026-09-10", "2026-09-11"]),
    )
    calendar = client.get("/api/prep/calendar?days=365").json()
    assert calendar["default_date"] == "2026-09-10"
    assert calendar["default_date_source"] == "local_bar_previous"
    assert calendar["calendar_basis"] == "local_stock_daily_bars_union"
    assert calendar["calendar_latest_date"] == "2026-09-11"
    assert calendar["degraded"] is False
    assert calendar["degraded_reason"] is None


def test_auction_calendar_default_date_fields():
    client = make_client(
        assets_repository=FakeAssets(),
        clock_module=_fixed_clock(),
        bar_calendar_loader=_calendar_loader(["2026-09-10", "2026-09-11"]),
    )
    calendar = client.get("/api/auction/calendar?days=365").json()
    assert calendar["default_date"] == "2026-09-11"
    assert calendar["default_date_source"] == "local_bar_latest"
    assert calendar["calendar_latest_date"] == "2026-09-11"


def test_static_accuracy_routes_win_over_dynamic_date():
    client = make_client(
        assets_repository=FakeAccuracyAssets(),
        clock_module=_fixed_clock(),
        bar_calendar_loader=_calendar_loader(ACC_CALENDAR),
        local_bars_loader=_acc_bars_loader,
    )
    prep = client.get("/api/prep/accuracy")
    assert prep.status_code == 200
    assert prep.json()["summary"]["correct"] == 2
    assert prep.json()["as_of"] == "2026-09-02"

    auction = client.get("/api/auction/accuracy")
    assert auction.status_code == 200
    assert auction.json()["summary"]["correct"] == 1
    assert auction.json()["day_kind_counts"] == {}


class FakeEmptyAccuracyAssets(FakeAccuracyAssets):
    def list_prep_snapshot_dates(self):
        return []

    def list_auction_snapshot_dates(self):
        return []


def test_prep_accuracy_empty_db_returns_200_insufficient_data():
    client = make_client(
        assets_repository=FakeEmptyAccuracyAssets(), clock_module=_fixed_clock(),
        bar_calendar_loader=_calendar_loader([]),
        local_bars_loader=_acc_bars_loader,
    )
    body = client.get("/api/prep/accuracy").json()
    assert body["status"] == "insufficient_data"
    assert body["reason"] == "no_snapshots"
    auction = client.get("/api/auction/accuracy").json()
    assert auction["status"] == "insufficient_data"
    assert auction["reason"] == "no_snapshots"


EFFECT_CALENDAR = ["2026-09-10", "2026-09-11", "2026-09-14",
                   "2026-09-15", "2026-09-16", "2026-09-17"]


class FakeEffectAssets(FakeAssets):
    def __init__(self):
        self.snapshot = {
            "trade_date": "2026-09-11", "version": "0.8",
            "sentiment": {"position_pct": 0.5},
            "review": {"mainlines": [
                {"rank": 1, "board_name": "机器人", "category": "概念",
                 "is_mainline": True},
                {"rank": 2, "board_name": "AI算力", "category": "概念",
                 "is_mainline": False},
            ]},
            "review_candidates": [
                {"code": "600001", "name": "龙头A", "role": "leader",
                 "review_score": 88.0, "eligibility": "eligible",
                 "position_pct": 20},
                {"code": "600002", "name": "跟风B",
                 "eligibility": "not_qualified", "position_pct": 0},
            ],
        }
        self.blocks = {
            "2026-09-11": {"rows": [
                {"name": "机器人", "code": "885517", "change": 2.31},
                {"name": "AI算力", "code": "885100", "change": 1.0}]},
            "2026-09-14": {"rows": [
                {"name": "机器人", "code": "885517", "change": 3.14},
                {"name": "AI算力", "code": "885100", "change": 1.5}]},
        }

    def get_prep_snapshot(self, trade_date, source="v2", with_meta=False):
        payload = self.snapshot if trade_date == "2026-09-11" else None
        if with_meta:
            return payload, {"created_at": "2026-09-11 17:00:00"}
        return payload

    def get_prep_asset(self, trade_date, asset_type):
        if asset_type == "ths_block_top":
            return self.blocks.get(trade_date)
        return None


def _effect_bars_loader(*_args, **_kwargs):
    bars = {"600001": [
        _acc_bar("2026-09-14", open_px=10.0, close=10.5),
        _acc_bar("2026-09-15", open_px=10.5, close=11.0),
        _acc_bar("2026-09-16", open_px=11.0, close=11.8),
        _acc_bar("2026-09-17", open_px=11.8, close=10.4),
    ]}
    return {"rows": bars, "truncated": False,
            "bar_dates": EFFECT_CALENDAR, "codes": ["600001"]}


def test_prep_effect_route_shape_and_strict_fields():
    client = make_client(
        assets_repository=FakeEffectAssets(),
        bar_calendar_loader=_calendar_loader(EFFECT_CALENDAR),
        local_bars_loader=_effect_bars_loader,
    )
    body = client.get("/api/prep/20260911/effect").json()
    assert body["status"] == "ok"
    assert body["trade_date"] == "2026-09-11"
    assert body["next_trade_date"] == "2026-09-14"
    assert body["snapshot_version"] == "0.8"
    assert body["data_source"] == "local_prep_assets+local_stock_daily"
    boards = body["boards"]
    assert boards["source"] == "review_mainlines"
    assert len(boards["rows"]) == 1
    assert boards["rows"][0]["board_code"] == "885517"
    assert boards["rows"][0]["next_change_pct"] == 3.14
    rows = {row["code"]: row for row in body["stocks"]["rows"]}
    assert rows["600001"]["status"] in ("completed", "pending")
    assert rows["600001"]["group"] == "recommended"
    assert rows["600002"]["group"] == "not_recommended"
    assert body["coverage"]["boards_total"] == 1
    assert body["coverage"]["stocks_total"] == 2


def test_prep_effect_missing_snapshot_and_invalid_date():
    client = make_client(
        assets_repository=FakeEffectAssets(),
        bar_calendar_loader=_calendar_loader(EFFECT_CALENDAR),
        local_bars_loader=_effect_bars_loader,
    )
    missing = client.get("/api/prep/20200101/effect").json()
    assert missing["status"] == "insufficient_data"
    assert missing["reason"] == "snapshot_missing"
    assert client.get("/api/prep/not-a-date/effect").status_code == 400


def test_accuracy_router_registered_before_assets_router():
    from web_ui.backend.routers import ROUTERS
    from web_ui.backend.routers import accuracy as accuracy_router
    from web_ui.backend.routers import assets as assets_router

    order = [router for router in ROUTERS]
    assert order.index(accuracy_router.router) < order.index(assets_router.router)


def test_prep_effect_local_bars_error_is_visible_in_warnings():
    def boom(*_args, **_kwargs):
        raise RuntimeError("disk unavailable")

    client = make_client(
        assets_repository=FakeEffectAssets(),
        bar_calendar_loader=_calendar_loader(EFFECT_CALENDAR),
        local_bars_loader=boom,
    )
    response = client.get("/api/prep/20260911/effect")
    assert response.status_code == 200
    body = response.json()
    assert any("local_bars_error" in warning for warning in body["warnings"])
    assert body["stocks"]["rows"]
    assert {row["status"] for row in body["stocks"]["rows"]} <= {
        "cannot_evaluate", "pending"}


# ─── BS 策略只读端点 ───────────────────────────────────────────────────────


def _bs_row(**overrides):
    base = {
        "trade_date": "2026-09-01", "code": "600001", "name": "样本",
        "eval_status": "closed", "is_terminal": 1,
        "data_quality": "strict_5min", "replay_days": 5,
        "fallback_days": 0, "suspended_days": 0,
        "buy_date": "2026-09-02", "buy_rule_code": "buy_open_ma5_pullback",
        "buy_rule_text": "开盘贴近上日MA5", "buy_loose_price": 9.8,
        "buy_strict_price": 10.02, "buy_data_quality": "strict_5min",
        "buy_loose_max_return_5d": 6.0, "buy_loose_max_drawdown_5d": -4.0,
        "buy_strict_max_return_5d": 5.0, "buy_strict_max_drawdown_5d": -4.0,
        "buy_max_return_hold_days": 3, "buy_window_partial": 0,
        "sell_date": "2026-09-04", "sell_rule_code": "hard_stop_loss",
        "sell_rule_text": "触及止损", "sell_price": 9.5,
        "sell_hold_days": 2, "sell_data_quality": "strict_5min",
        "sell_half_date": None, "sell_half_price": None,
        "sell_half_qty": None, "sell_half_rule_code": None,
        "sell_max_return_5d": 1.0, "sell_max_drawdown_5d": -1.0,
        "sell_max_return_hold_days": 1, "sell_window_partial": 0,
    }
    base.update(overrides)
    return base


class FakeBsRepository:
    def __init__(self, *, run=None, rows=None, stale=0):
        self.run = run
        self.rows = list(rows or [])
        self.stale = stale
        self.calls: list[dict] = []

    def latest_run(self):
        return self.run

    def list_current(self, strategy_hash, evaluator_version):
        return [row for row in self.rows
                if row.get("strategy_hash", strategy_hash) == strategy_hash]

    def count_stale(self, strategy_hash, evaluator_version):
        return self.stale

    def query_signals(self, *, side, filters=None, sort_by="", sort_dir="desc",
                      strategy_hash=None, evaluator_version=None):
        self.calls.append({"side": side, "filters": dict(filters or {}),
                           "sort_by": sort_by, "sort_dir": sort_dir})
        rows = [row for row in self.rows
                if (row.get("buy_date") if side == "buy"
                    else row.get("sell_date")) is not None]
        rules = (filters or {}).get("rule_codes") or []
        if rules:
            key = "buy_rule_code" if side == "buy" else "sell_rule_code"
            rows = [row for row in rows if row.get(key) in rules]
        if (filters or {}).get("exclude_partial"):
            key = "buy_window_partial" if side == "buy" else "sell_window_partial"
            rows = [row for row in rows if not row.get(key)]
        return rows


BS_RUN = {
    "id": 7, "started_at": "2026-11-02 15:30:00",
    "finished_at": "2026-11-02 15:31:00", "range_from": None,
    "range_to": None, "limit_n": None, "force": False,
    "evaluator_version": "bs_eval/1", "strategy_name": "dragon_pullback_daily",
    "strategy_params": {}, "strategy_hash": "abcdef123456",
    "counters": {"rows_scanned": 1, "rows_skipped_terminal": 0,
                 "rows_evaluated": 1, "rows_no_signal": 0,
                 "rows_bought": 1, "rows_sold": 1, "rows_partial": 0,
                 "rows_error": 0},
    "note": None,
}


def _bs_client(repo):
    from dragon_quant.bs_eval import aggregate as bs_aggregate
    from dragon_quant.bs_eval import strategy_doc as bs_doc

    return make_client(bs_repository=repo, bs_strategy_doc=bs_doc,
                       bs_aggregate=bs_aggregate)


def test_bs_overview_empty_and_populated():
    empty = _bs_client(FakeBsRepository()).get("/api/bs/overview")
    assert empty.status_code == 200
    body = empty.json()
    assert body["run"] is None
    assert body["summary"]["rows_total"] == 0
    assert body["stale_rows"] == 0
    assert len(body["strategy"]["buy_signals"]) == 4
    assert len(body["strategy"]["sell_rules"]) == 10

    row = {**_bs_row(), "strategy_hash": "abcdef123456"}
    client = _bs_client(FakeBsRepository(run=BS_RUN, rows=[row], stale=2))
    body = client.get("/api/bs/overview").json()
    assert body["run"]["id"] == 7
    assert body["summary"]["rows_total"] == 1
    assert body["summary"]["buy"]["strict"]["samples"] == 1
    assert body["summary"]["sell"]["good"] == 0
    assert body["stale_rows"] == 2
    assert body["strategy"]["thresholds"]["max_replay_trading_days"] == 60


def test_bs_buys_shape_rule_filter_and_stable_summary_base():
    row = {**_bs_row(), "strategy_hash": "abcdef123456"}
    repo = FakeBsRepository(run=BS_RUN, rows=[row])
    body = _bs_client(repo).get(
        "/api/bs/buys?rule=buy_open_ma5_pullback&sort_by=evil&sort_dir=drop"
    ).json()
    assert body["count"] == 1
    item = body["data"][0]
    assert item["buy_rule_label"] == "开盘回踩MA5"
    assert item["strict_win"] is True
    assert item["loose_win"] is True
    assert item["buy_window_partial"] is False
    assert body["summary"]["samples"] == 1
    # data 查询带 rule，summary 基集不带 rule（切换买点时分母稳定）。
    assert repo.calls[0]["filters"]["rule_codes"] == ["buy_open_ma5_pullback"]
    assert repo.calls[1]["filters"].get("rule_codes", []) == []


def test_bs_sells_verdict_partial_and_half_exit():
    row = {**_bs_row(), "strategy_hash": "abcdef123456",
           "sell_rule_code": "next_day_limit_up_half",
           "sell_half_date": "2026-09-03", "sell_half_price": 10.4,
           "sell_half_qty": 500, "sell_half_rule_code": "next_day_limit_up_half"}
    item = _bs_client(FakeBsRepository(run=BS_RUN, rows=[row])).get(
        "/api/bs/sells").json()["data"][0]
    assert item["sell_rule_label"] == "次日涨停减半"
    assert item["verdict"] == "neutral"
    assert item["half_exit"]["qty"] == 500
    assert item["half_exit"]["rule_label"] == "次日涨停减半"

    partial_row = {**_bs_row(), "strategy_hash": "abcdef123456",
                   "sell_window_partial": 1,
                   "sell_max_return_5d": None, "sell_max_drawdown_5d": None}
    item = _bs_client(FakeBsRepository(run=BS_RUN, rows=[partial_row])).get(
        "/api/bs/sells").json()["data"][0]
    assert item["verdict"] == "partial"
    assert item["verdict_label"] == "窗口不足"


def test_bs_invalid_date_is_400_and_repository_error_is_500():
    client = _bs_client(FakeBsRepository(run=BS_RUN, rows=[]))
    bad = client.get("/api/bs/buys?date_from=not-a-date")
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_request"

    class Boom(FakeBsRepository):
        def query_signals(self, **_kwargs):
            raise RuntimeError("db gone")

    failed = _bs_client(Boom(run=BS_RUN, rows=[])).get("/api/bs/buys")
    assert failed.status_code == 500
    assert failed.json() == {"error": "db gone"}


def test_bs_router_route_list_includes_bs():
    from web_ui.backend.routers.meta import ROUTE_LIST

    assert "/bs" in ROUTE_LIST


# ─── BS 生成任务端点 ───────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, *, gate: threading.Event, output: str = "",
                 returncode: int = 0) -> None:
        self.gate = gate
        self.output = output
        self.final_code = returncode
        self.returncode: int | None = None
        self.terminated = 0

    def communicate(self):
        self.gate.wait(timeout=5)
        if self.returncode is None:
            self.returncode = self.final_code
        return self.output, None

    def wait(self, timeout: float | None = None):
        if self.returncode is None:
            self.gate.wait(timeout)
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1
        if self.returncode is None:
            self.returncode = -15
        self.gate.set()

    def kill(self) -> None:
        self.gate.set()


def _wait_job_state(runner, expected: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = runner.status()
        if status["state"] == expected:
            return status
        time.sleep(0.01)
    raise AssertionError(f"runner 未进入 {expected}: {runner.status()}")


def test_bs_job_status_idle_and_autogen_enabled_from_settings():
    from web_ui.backend.bs_runner import BsRunner

    client = make_client(bs_runner=BsRunner())
    body = client.get("/api/bs/jobs/status").json()
    assert body == {"state": "idle", "autogen_enabled": False, "last_run": None}

    enabled = make_client(
        bs_runner=BsRunner(),
        dashboard_settings=SimpleNamespace(bs_autogen=True),
    )
    assert enabled.get("/api/bs/jobs/status").json()["autogen_enabled"] is True


def test_bs_rebuild_202_then_409_and_origin_guard():
    from web_ui.backend.bs_runner import BsRunner

    gate = threading.Event()
    runner = BsRunner(popener=lambda argv, **kwargs: _FakeProc(gate=gate))
    client = make_client(bs_runner=runner)

    bad = client.post("/api/bs/rebuild",
                      headers={"Origin": "https://evil.example"})
    assert bad.status_code == 403
    assert bad.json() == {"error": "invalid_origin"}
    assert runner.status()["state"] == "idle"

    ok = client.post("/api/bs/rebuild",
                     headers={"Origin": "http://localhost:8765"})
    assert ok.status_code == 202
    body = ok.json()
    assert body["state"] == "running"
    assert body["autogen_enabled"] is False
    assert body["last_run"]["mode"] == "force"
    assert body["last_run"]["trigger"] == "manual"
    assert body["last_run"]["finished_at"] is None
    assert body["last_run"]["exit_code"] is None

    busy = client.post("/api/bs/rebuild")
    assert busy.status_code == 409
    busy_body = busy.json()
    assert busy_body["error"] == "job_running"
    assert busy_body["state"] == "running"
    assert busy_body["last_run"]["mode"] == "force"

    gate.set()
    assert _wait_job_state(runner, "succeeded")["last_run"]["exit_code"] == 0
    assert client.get("/api/bs/jobs/status").json()["state"] == "succeeded"


def test_bs_rebuild_spawn_failure_is_500_and_status_failed():
    from web_ui.backend.bs_runner import BsRunner

    def boom(argv, **kwargs):
        raise OSError("blocked by sandbox")

    client = make_client(bs_runner=BsRunner(popener=boom))
    response = client.post("/api/bs/rebuild")
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "spawn_failed"
    assert body["detail"] == "OSError: blocked by sandbox"

    status = client.get("/api/bs/jobs/status").json()
    assert status["state"] == "failed"
    assert status["last_run"]["output_tail"] == (
        "spawn_failed: OSError: blocked by sandbox"
    )
