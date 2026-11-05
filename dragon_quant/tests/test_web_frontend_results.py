"""dashboard 结果呈现精修的前端契约测试（纯源码 / dist 文本扫描，零依赖）。

守护：
1. 总览文案与筛选右对齐；
2. 近几日榜单 items 本地展开；
3. 抽屉移除双口径卡片、新增独立绩效请求与默认展开报告；
4. 复盘页结果优先 adapter + 折叠；五区块顺序固定；
5. 竞价页四区块顺序固定；
6. dist 与源码同批构建，旧文案不回流。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "web_ui" / "frontend" / "src"
DIST_ASSETS = REPO_ROOT / "web_ui" / "dist" / "assets"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dist_js() -> str:
    files = sorted(DIST_ASSETS.glob("*.js"))
    assert files, "dist/assets 缺少 JS 产物；请先 npm --prefix web_ui/frontend run build"
    return "".join(_read(path) for path in files)


def test_dragons_page_drops_header_description() -> None:
    text = _read(SRC / "pages" / "dragons" / "DragonsPage.tsx")
    assert "description=" not in text
    assert "龙头总览" in text


def test_summary_cards_copy_and_accessible_hints() -> None:
    text = _read(SRC / "pages" / "dragons" / "SummaryCards.tsx")
    for token in ("已回测", "待回测口径", "HintIcon", "宽松胜率", "严格胜率"):
        assert token in text, f"SummaryCards 缺少 {token!r}"

    hint = _read(SRC / "components" / "HintIcon.tsx")
    for token in ("aria-describedby", 'role="tooltip"', "onMouseEnter",
                  "onMouseLeave", "onFocus", "onBlur", "Escape"):
        assert token in hint, f"HintIcon 缺少 {token!r}"


def test_recent_dragons_expand_uses_items_locally() -> None:
    text = _read(SRC / "pages" / "dragons" / "RecentDragons.tsx")
    for token in ("entry.items ?? entry.preview", "支", "展开全部", "收起",
                  "COLLAPSED_COUNT", "IconChevronDown", "IconChevronUp"):
        assert token in text, f"RecentDragons 缺少 {token!r}"


def test_drawer_drops_review_cards_and_adds_performance() -> None:
    text = _read(SRC / "pages" / "dragons" / "DragonDetailDrawer.tsx")
    assert "ReviewCard" not in text
    assert "严格 / 宽松口径对照" not in text
    for token in ("入选后表现", "fetchDragonPerformance", "宽松口径",
                  "至最新日 K", "defaultOpen", "perfError", "重试"):
        assert token in text, f"DragonDetailDrawer 缺少 {token!r}"


def test_filter_bar_actions_are_right_aligned() -> None:
    text = _read(SRC / "pages" / "dragons" / "FilterBar.tsx")
    assert "justify-end" in text


def test_prep_adapter_has_explicit_fallback_chain() -> None:
    text = _read(SRC / "pages" / "prep" / "prepModel.ts")
    for token in ("review_candidates", "main_boards", "sentiment",
                  "review?.market", "top_drivers", "top_drags",
                  "expectations", "reason_code", "ignore_reason",
                  "数据不足", "不建议新建仓"):
        assert token in text, f"prepModel 缺少 {token!r}"


def test_prep_page_drops_raw_json_and_uses_new_sections() -> None:
    """原始 JSON 块已删除（调试走 API/CLI）；五区块顺序固定。"""
    page = _read(SRC / "pages" / "prep" / "PrepPage.tsx")
    for token in ("AssetCalendar", "PrepCoreResult", "PrepDayEffectSection",
                  "AssetDayTable", "AccuracyBoard"):
        assert token in page, f"PrepPage 缺少 {token!r}"
    assert "SnapshotRawDetails" not in page
    assert "JSON.stringify" not in page

    order = ["<AssetCalendar", "<PrepCoreResult", "<PrepDayEffectSection",
             "<AssetDayTable", "<AccuracyBoard"]
    positions = [page.index(token) for token in order]
    assert positions == sorted(positions)


def test_market_strip_drops_duplicated_evidence_copy() -> None:
    """指标卡已覆盖原文案；仅「分数环比」补成指标卡，evidence 列表删除。"""

    text = _read(SRC / "pages" / "prep" / "MarketStrip.tsx")
    assert "market.evidence" not in text
    assert "分数环比" in text and "market.scoreChange" in text
    model = _read(SRC / "pages" / "prep" / "prepModel.ts")
    assert "scoreChange" in model and "parseScoreChange" in model


def test_hint_icon_clamps_to_card_edges() -> None:
    """边缘卡片必须用 start/end 对齐，不能居中溢出视口。"""

    hint = _read(SRC / "components" / "HintIcon.tsx")
    for token in ("align", "left-0", "right-0", "-translate-x-1/2"):
        assert token in hint, f"HintIcon 缺少 {token!r}"
    summary = _read(SRC / "pages" / "dragons" / "SummaryCards.tsx")
    assert 'align="start"' in summary
    assert 'align="end"' in summary


def test_prep_page_no_longer_hosts_auction() -> None:
    """`/prep` 只保留盘后复盘；竞价拆到独立 `/auction` 页。"""

    text = _read(SRC / "pages" / "prep" / "PrepPage.tsx")
    assert "集合竞价" not in text
    assert "AuctionSection" not in text
    auction = _read(SRC / "pages" / "auction" / "AuctionPage.tsx")
    for token in ("AssetCalendar", "ResultsSection", "AssetCollectionSection",
                  "AccuracyBoard"):
        assert token in auction, f"AuctionPage 缺少 {token!r}"
    route = _read(SRC / "router.tsx")
    assert "<AuctionPage />" in route
    shell = _read(SRC / "AppShell.tsx")
    assert 'to: "/auction"' in shell and "竞价" in shell


def test_dist_contains_new_copy_and_drops_old_review_cards() -> None:
    js = _dist_js()
    for token in ("已回测", "待回测口径", "入选后表现",
                  "展开全部", "数据不足", "不建议新建仓", "tooltip"):
        assert token in js, f"dist JS 缺少新文案/键: {token!r}"
    assert "严格 / 宽松口径对照" not in js
    assert "5日收益" not in js


def test_dist_assets_are_built_together_with_source() -> None:
    html = _read(REPO_ROOT / "web_ui" / "dist" / "index.html")
    assert "/assets/" in html
    assert any(path.is_file() for path in DIST_ASSETS.glob("*.js"))
