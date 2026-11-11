"""策略页前端契约：真实路由 + 导航 + API + 账户回测表单策略下拉 + dist 构建。"""

from __future__ import annotations

from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "web_ui" / "frontend"
DIST = FRONTEND.parent / "dist"


def _read(*parts: str) -> str:
    return (FRONTEND.joinpath(*parts)).read_text(encoding="utf-8")


def test_router_has_reachable_strategies_page():
    router = _read("src", "router.tsx")
    assert "StrategiesPage" in router
    assert "/^\\/strategies$/" in router


def test_nav_exposes_strategies_entry():
    shell = _read("src", "AppShell.tsx")
    assert 'to: "/strategies"' in shell
    assert 'label: "策略"' in shell


def test_api_calls_backend_endpoints():
    api = _read("src", "api", "strategies.ts")
    for path in ("/api/strategies", "/api/strategies/current",
                 "/api/strategies/selection"):
        assert path in api
    page = _read("src", "pages", "strategies", "StrategiesPage.tsx")
    assert "保存" in page
    assert "expected_revision" in api or "expectedRevision" in api


def test_account_run_form_sends_strategy_selection():
    modal = _read("src", "pages", "account", "CreateRunModal.tsx")
    assert "fetchStrategies" in modal
    assert "strategy_id" in modal


def test_dist_bundle_contains_strategies_page():
    index = DIST / "index.html"
    assert index.is_file()
    assets = sorted((DIST / "assets").glob("*.js"))
    assert assets
    text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in assets)
    assert "策略" in text
