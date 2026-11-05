"""``/bs`` 页面源码 + dist 文本契约。

策略文案的唯一来源是 ``dragon_quant.bs_eval.strategy_doc``（API 下发），因此
四类买点/十条卖出规则的**标签**由本测试直接校验该数据模块；dist 扫描钉住
静态页面契约（标题、空态、阈值区、分页组件与导航），避免把同一份文案复制
到前端造成漂移。
"""

from __future__ import annotations

from pathlib import Path

from dragon_quant.bs_eval import strategy_doc

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "web_ui" / "frontend" / "src"
DIST = REPO / "web_ui" / "dist"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dist_js() -> str:
    assets = sorted((DIST / "assets").glob("*.js"))
    assert assets, "dist 未构建：web_ui/dist/assets 缺少 JS"
    return "\n".join(_read(path) for path in assets)


def test_page_source_has_title_empty_state_filters_and_pagination():
    page = _read(FRONTEND / "pages" / "bs" / "BsPage.tsx")
    for token in ("BS策略", "假想买卖点回放", "PaginatedDataTable",
                  "python -m dragon_quant bs-eval", "窗口不足",
                  "显示窗口不足样本", "半仓腿",
                  "重新计算买卖点评估", "Modal", "fetchBsJobStatus", "postBsRebuild",
                  "aria-live", "数据生成中"):
        assert token in page, token
    card = _read(FRONTEND / "pages" / "bs" / "StrategyCard.tsx")
    for token in ("四类买点", "十条卖出规则", "评估口径", "回放上限"):
        assert token in card, token


def test_api_router_and_nav_are_wired():
    api = _read(FRONTEND / "api" / "bs.ts")
    for path in ("/api/bs/overview", "/api/bs/buys", "/api/bs/sells",
                 "/api/bs/jobs/status", "/api/bs/rebuild"):
        assert path in api, path
    router = _read(FRONTEND / "router.tsx")
    assert "BsPage" in router and "/^\\/bs$/" in router
    shell = _read(FRONTEND / "AppShell.tsx")
    assert 'to: "/bs"' in shell and 'label: "买卖点"' in shell


def test_new_source_uses_typography_tokens_not_tailwind_scale():
    for path in sorted((FRONTEND / "pages" / "bs").glob("*.tsx")) + [
            FRONTEND / "api" / "bs.ts"]:
        text = _read(path)
        assert "text-xs" not in text, path
        assert "text-sm" not in text, path


def test_dist_is_built_and_contains_static_page_contract():
    assert (DIST / "index.html").is_file()
    js = _dist_js()
    for token in ("BS策略", "假想买卖点回放", "四类买点",
                  "十条卖出规则", "评估口径", "回放上限",
                  "python -m dragon_quant bs-eval", "窗口不足",
                  "重新计算买卖点评估", "/api/bs/jobs/status", "/api/bs/rebuild"):
        assert token in js, token


def test_strategy_disclosure_covers_four_buys_and_ten_sells():
    assert len(strategy_doc.BUY_SIGNALS) == 4
    assert len(strategy_doc.SELL_RULES) == 10
    for item in strategy_doc.BUY_SIGNALS:
        assert item["label"] and item["summary"] and item["window"]
    for item in strategy_doc.SELL_RULES:
        assert item["label"] and item["summary"] and item["trigger"]
        assert isinstance(item["default_enabled"], bool)
    assert set(strategy_doc.THRESHOLDS) == {
        "buy_window_days", "window_skip", "window_size",
        "buy_win_return_gt", "buy_win_drawdown_gt",
        "sell_good_drawdown_le", "sell_fly_return_ge",
        "max_replay_trading_days",
    }
