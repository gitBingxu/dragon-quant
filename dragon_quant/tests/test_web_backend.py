"""FastAPI backend 单元测试：mock 仓储 + TestClient（零真实 DB 写入）。

覆盖 app/meta/404/缓存头、冻结 dragons 契约、scans 排序分页、factors 合并、
prep/auction 投影与账户兼容端点。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from web_ui.backend import deps
from web_ui.backend.app import create_app
from web_ui.backend.settings import DashboardSettings

BASE = "http://127.0.0.1"


def make_client(**overrides) -> TestClient:
    app = create_app(DashboardSettings())
    for name, value in overrides.items():
        app.dependency_overrides[getattr(deps, name)] = lambda value=value: value
    return TestClient(app, base_url=BASE)


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


class FakeAssets:
    def get_prep_assets(self, trade_date):
        return dict(PREP_ASSETS)

    def has_prep_snapshot(self, trade_date, source="v2"):
        return True

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

    def list_auction_snapshots(self, trade_date, source="v2"):
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
