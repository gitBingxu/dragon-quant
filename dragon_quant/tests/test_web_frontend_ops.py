"""dashboard 运维与复盘成效重构的前端契约测试（方案 §9.4）。

纯源码 / dist 文本扫描，零依赖、零网络：
1. 排版：html 14px、所有可见文字最小 1rem、dist 含六个文本令牌；
2. 语义色：danger/warning/normal/positive 四态，旧 warn/critical/info 清零；
3. `/prep` 六段顺序、标题、删除原始 JSON；
4. `/auction` 五段顺序、路由/导航、成效只读本地；
5. 扫描页运行态/日志；
6. `high_num` 只消费服务端 derived；
7. dist 与源码同批构建、旧文案不回流。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "web_ui" / "frontend" / "src"
DIST_ASSETS = REPO_ROOT / "web_ui" / "dist" / "assets"
STYLES = SRC / "styles.css"

TEXT_TOKENS = ("caption", "body", "title", "section", "page", "metric")
MIN_REM_PX = 14


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tsx_files() -> list[Path]:
    return sorted(SRC.rglob("*.tsx")) + sorted(SRC.rglob("*.ts"))


def _dist_js() -> str:
    files = sorted(DIST_ASSETS.glob("*.js"))
    assert files, "dist/assets 缺少 JS 产物；请先 npm --prefix web_ui/frontend run build"
    return "".join(_read(path) for path in files)


def _dist_css() -> str:
    files = sorted(DIST_ASSETS.glob("*.css"))
    assert files, "dist/assets 缺少 CSS 产物；请先 npm --prefix web_ui/frontend run build"
    return "".join(_read(path) for path in files)


# ─── 1. 排版 ───────────────────────────────────────────────────────────────


def test_html_root_font_size_is_14px_and_body_uses_1rem() -> None:
    css = _read(STYLES)
    assert re.search(r"html\s*\{[^}]*font-size:\s*14px", css, re.S), "html 必须是 14px 基准"
    assert re.search(r"body\s*\{[^}]*font-size:\s*1rem", css, re.S), "body 必须是 1rem"


def test_source_has_no_text_below_one_rem() -> None:
    offenders: list[str] = []
    pattern = re.compile(r"text-\[(\d+(?:\.\d+)?)px\]")
    for path in _tsx_files():
        for match in pattern.finditer(_read(path)):
            if float(match.group(1)) < MIN_REM_PX:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0)}")
    assert offenders == [], f"发现 <14px 文本类: {offenders[:5]}"


def test_css_font_sizes_are_at_least_14px() -> None:
    css = _read(STYLES)
    values = [float(value) for value in
              re.findall(r"font-size:\s*([\d.]+)px", css)]
    assert values, "styles.css 应至少保留 html 的 14px 声明"
    assert all(value >= MIN_REM_PX for value in values), values


def test_svg_font_sizes_are_at_least_14() -> None:
    offenders: list[str] = []
    for path in _tsx_files():
        for match in re.finditer(r'fontSize="(\d+(?:\.\d+)?)"', _read(path)):
            if float(match.group(1)) < MIN_REM_PX:
                offenders.append(f"{path.name}: fontSize={match.group(1)}")
    assert offenders == []


def test_theme_declares_six_text_tokens() -> None:
    css = _read(STYLES)
    for token in TEXT_TOKENS:
        assert f"--text-{token}:" in css, f"styles.css 缺少 --text-{token}"
    built = _dist_css()
    for token in TEXT_TOKENS:
        assert f"--text-{token}" in built, f"dist CSS 缺少 --text-{token}"


def test_no_text_xs_or_sm_utilities() -> None:
    for path in _tsx_files():
        text = _read(path)
        assert "text-xs" not in text, f"{path.name} 仍使用 text-xs"
        assert "text-sm" not in text, f"{path.name} 仍使用 text-sm"


# ─── 2. 语义色 ─────────────────────────────────────────────────────────────


def test_semantic_color_tokens_are_four_state() -> None:
    css = _read(STYLES)
    for name in ("danger", "warning", "normal", "positive"):
        assert f"--color-{name}:" in css, f"缺少 --color-{name}"
        assert f"--color-{name}-soft:" in css
        assert f"--color-{name}-line:" in css
    for legacy in ("--color-warn:", "--color-critical:", "--color-info:",
                   "--color-warn-soft:", "--color-critical-soft:",
                   "--color-info-soft:", "--color-warn-line:",
                   "--color-critical-line:"):
        assert legacy not in css, f"旧 token 未清理: {legacy}"


def test_no_legacy_tone_classes_in_source() -> None:
    pattern = re.compile(
        r"(text|bg|border|badge|alert)-(warn|critical|info)(-|\b)")
    offenders = [f"{path.name}: {match.group(0)}"
                 for path in _tsx_files()
                 for match in pattern.finditer(_read(path))]
    assert offenders == [], offenders[:5]


def test_inline_css_vars_have_no_legacy_tokens() -> None:
    for path in _tsx_files():
        text = _read(path)
        for legacy in ("var(--color-warn)", "var(--color-info)",
                       "var(--color-critical"):
            assert legacy not in text, f"{path.name} 仍引用 {legacy}"


def test_pnl_class_stays_up_down_only() -> None:
    text = _read(SRC / "lib" / "format.ts")
    block = text[text.index("export function pnlClass"):]
    block = block[: block.index("}")]
    assert "text-up" in block and "text-down" in block
    for status in ("danger", "warning", "normal", "positive"):
        assert status not in block, f"pnlClass 不应使用状态色 {status}"


# ─── 3. /prep ──────────────────────────────────────────────────────────────


def test_prep_page_is_after_close_review_only() -> None:
    page = _read(SRC / "pages" / "prep" / "PrepPage.tsx")
    assert 'title="盘后复盘"' in page
    assert "集合竞价" not in page
    for token in ("运行概况", "上次计划复盘", "成效", "数据资产"):
        assert token in page, f"PrepPage 缺少 {token!r}"
    run_overview = _read(SRC / "pages" / "prep" / "RunOverview.tsx")
    assert "运行异常" in run_overview


def test_prep_page_has_fixed_section_order() -> None:
    page = _read(SRC / "pages" / "prep" / "PrepPage.tsx")
    order = ['title="运行概况"', 'title="上次计划复盘', 'title="市场"',
             'title="主线"', 'title="候选"', 'title="数据资产"']
    positions = [page.index(token) for token in order]
    assert positions == sorted(positions), positions


def test_prep_page_has_no_raw_json() -> None:
    page = _read(SRC / "pages" / "prep" / "PrepPage.tsx")
    assert "SnapshotRawDetails" not in page
    assert "JSON.stringify" not in page
    assert not (SRC / "pages" / "prep" / "SnapshotSection.tsx").exists()


def test_prep_calendar_is_shared_and_all_dates_clickable() -> None:
    calendar = _read(SRC / "components" / "AssetCalendar.tsx")
    for token in ("button", "aria-label", "onSelect", "inRange", "missing"):
        assert token in calendar, f"AssetCalendar 缺少 {token!r}"
    page = _read(SRC / "pages" / "prep" / "PrepPage.tsx")
    assert "AssetCalendar" in page and "AssetDayTable" in page
    api = _read(SRC / "api" / "assets.ts")
    for token in ("fetchPrepCalendar", "fetchReviewContext"):
        assert token in api


def test_calendar_uses_success_warning_error_icons() -> None:
    """main 必须修改 1：日期格与图例必须有可访问的成功/警告/错误图标。"""
    calendar = _read(SRC / "components" / "AssetCalendar.tsx")
    for token in ("StatusIcon", "<svg", "aria-hidden", "complete", "incomplete",
                  "positive-soft", "warning-soft", "danger-soft", "资产缺失"):
        assert token in calendar, f"AssetCalendar 缺少 {token!r}"
    assert calendar.count("<StatusIcon") >= 2, "图例与日期格都要渲染状态图标"


def test_market_strip_shows_real_inputs_not_sub_scores() -> None:
    """main 必须修改 2：市场必须是真实原始指标，不能用 0–100 子分冒充。"""
    strip = _read(SRC / "pages" / "prep" / "MarketStrip.tsx")
    for token in ("inputs", "limitUpCnt", "limitDownCnt", "premiumPct",
                  "promotionRate", "maxBoardHeight", "brokenRate",
                  "totalAmountYi", "涨停", "跌停", "最高板", "炸板率", "成交额"):
        assert token in strip, f"MarketStrip 缺少真实指标 {token!r}"
    assert "sub_scores" not in strip
    assert "meter" not in strip

    model = _read(SRC / "pages" / "prep" / "prepModel.ts")
    for token in ("rawInputs", "market?.inputs", "derived?.max_board_height"):
        assert token in model, f"prepModel 缺少 {token!r}"


def test_prep_copy_is_human_readable() -> None:
    """main 必须修改 3：主展示文案不得出现开发术语。"""
    page = _read(SRC / "pages" / "prep" / "PrepPage.tsx")
    assert "create=False" not in page
    assert "expected" not in page
    assert "查看复盘结果、计划成效与数据资产" in page

    run = _read(SRC / "pages" / "prep" / "RunOverview.tsx")
    assert "expected_ok / expected_total" not in run
    assert "error / empty / unavailable" not in run
    assert "当日应有资产" in run and "采集异常或不可用" in run

    plan = _read(SRC / "pages" / "prep" / "PlanReviewSection.tsx")
    for jargon in ("plan_review", "T+1 反馈", "检查点 T1"):
        assert jargon not in plan, f"PlanReviewSection 仍出现 {jargon!r}"
    assert "CHECKPOINT_LABELS" in plan and "次日反馈" in plan
    assert "计划日当日表现" in plan

    table = _read(SRC / "pages" / "prep" / "AssetDayTable.tsx")
    for token in ("assetTypeLabel", "数据日期", "无采集记录", "ASSET_STATUS_LABELS"):
        assert token in table, f"AssetDayTable 缺少 {token!r}"
    assert "无 report 条目" not in table


def test_auction_copy_is_human_readable() -> None:
    page = _read(SRC / "pages" / "auction" / "AuctionPage.tsx")
    for token in ("DIRECTION_LABELS", "reasonLabel", "CALENDAR_BASIS_LABELS",
                  "DATA_SOURCE_LABELS", "VALIDATION_LABELS", "技术信息",
                  "数据说明与覆盖范围"):
        assert token in page, f"AuctionPage 缺少 {token!r}"
    labels = _read(SRC / "lib" / "labels.ts")
    for token in ("看多", "看空", "次日开盘", "样本不足", "本地日 K",
                  "本地交易日", "其他原因"):
        assert token in labels, f"labels.ts 缺少中文映射 {token!r}"


def test_high_num_is_only_consumed_via_derived() -> None:
    model = _read(SRC / "pages" / "prep" / "prepModel.ts")
    assert "derived" in model and "board_height" in model
    assert "item.high_num" not in model
    assert "needs_recompute" in model


# ─── 4. /auction ───────────────────────────────────────────────────────────


def test_auction_page_has_five_sections_and_no_network_provider() -> None:
    page = _read(SRC / "pages" / "auction" / "AuctionPage.tsx")
    order = ["运行概况", "竞价结果", "成效", "资产日历", "当日逐股明细"]
    positions = [page.index(f'title="{token}"') for token in order]
    assert positions == sorted(positions), positions
    assert "fetchAuctionEffect" in page
    for forbidden in ("fetchDragonPerformance", "kline_provider",
                      "provider", "http://", "https://"):
        assert forbidden not in page, f"AuctionPage 不应出现 {forbidden!r}"


def test_auction_route_and_navigation_exist() -> None:
    route = _read(SRC / "router.tsx")
    assert "AuctionPage" in route and "/^\\/auction$/" in route
    shell = _read(SRC / "AppShell.tsx")
    assert 'to: "/auction"' in shell and "竞价" in shell


# ─── 5. 扫描页 ─────────────────────────────────────────────────────────────


def test_scans_pages_show_runtime_state_and_logs() -> None:
    api = _read(SRC / "api" / "scans.ts")
    for token in ("fetchScanLogs", "true_dragon_n", "log_entries",
                  "error_n", "status"):
        assert token in api, f"scans api 缺少 {token!r}"
    detail = _read(SRC / "pages" / "scans" / "ScanDetailPage.tsx")
    for token in ("错误摘要", "日志", "当日榜单", "fetchScanLogs", "上一页", "下一页"):
        assert token in detail, f"ScanDetailPage 缺少 {token!r}"
    listing = _read(SRC / "pages" / "scans" / "ScansPage.tsx")
    for token in ("SCAN_STATUS_LABELS", "真龙", "错误"):
        assert token in listing
    labels = _read(SRC / "lib" / "labels.ts")
    assert "已完成·无日志" in labels


# ─── 6. dist ───────────────────────────────────────────────────────────────


def test_dist_contains_new_copy_and_drops_old_blocks() -> None:
    js = _dist_js()
    for token in ("盘后复盘", "运行概况", "上次计划复盘", "运行异常",
                  "数据资产", "当日逐股明细", "待评估"):
        assert token in js, f"dist JS 缺少新文案: {token!r}"
    for legacy in ("次级信息", "原始 JSON", "严格 / 宽松口径对照"):
        assert legacy not in js, f"dist JS 不应回流出旧文案: {legacy!r}"


def test_dist_assets_referenced_by_index() -> None:
    html = _read(REPO_ROOT / "web_ui" / "dist" / "index.html")
    assert "/assets/" in html
    assert any(path.is_file() for path in DIST_ASSETS.glob("*.js"))
    assert any(path.is_file() for path in DIST_ASSETS.glob("*.css"))


# ─── 7. 核心问题直答（2026-11-01 增量） ───────────────────────────────────


CORE_QUESTION_FILES = (
    "components/MiniBar.tsx",
    "components/DataTable.tsx",
    "pages/scans/ScanHealthBand.tsx",
    "pages/scans/ScansPage.tsx",
    "pages/scans/ScanDetailPage.tsx",
    "pages/prep/PrepSummaryBand.tsx",
    "pages/prep/PrepPage.tsx",
    "pages/prep/PlanReviewSection.tsx",
    "pages/prep/RunOverview.tsx",
    "pages/prep/prepModel.ts",
    "pages/auction/AuctionPage.tsx",
    "lib/labels.ts",
    "api/assets.ts",
)


def test_prep_plan_followup_sections() -> None:
    page = _read(SRC / "pages" / "prep" / "PrepPage.tsx")
    for token in ("PrepSummaryBand", "fetchPlanFollowup", "结论"):
        assert token in page, f"PrepPage 缺少 {token!r}"

    plan = _read(SRC / "pages" / "prep" / "PlanReviewSection.tsx")
    for token in ("空仓验证", "否决股", "T+1", "T+3", "T+5", "verification"):
        assert token in plan, f"PlanReviewSection 缺少 {token!r}"

    model = _read(SRC / "pages" / "prep" / "prepModel.ts")
    for token in ("buildNoTradeOutcomes", "buildFollowupKpis", "buildAnomalies",
                  "recommendedN", "rejectedN"):
        assert token in model, f"prepModel 缺少 {token!r}"

    band = _read(SRC / "pages" / "prep" / "PrepSummaryBand.tsx")
    assert "recommendedN" in band and "rejectedN" in band, (
        "结论带的 n 必须是各组有效样本数，不是总和")

    api = _read(SRC / "api" / "assets.ts")
    assert "fetchPlanFollowup" in api and "PlanFollowup" in api
    assert "MarketVerification" in api and "VerificationConclusion" in api


def test_verification_labels_exist() -> None:
    labels = _read(SRC / "lib" / "labels.ts")
    for token in ("验证正确", "验证错误", "中性", "待评估"):
        assert token in labels, f"labels.ts 缺少验证标签 {token!r}"
    for token in ("VERIFICATION_CONCLUSION_LABELS", "VERIFICATION_REASON_LABELS",
                  "EXCLUSION_REASON_LABELS", "PLAN_ADVICE_LABELS"):
        assert token in labels, f"labels.ts 缺少映射 {token!r}"


def test_scan_health_band() -> None:
    band = _read(SRC / "pages" / "scans" / "ScanHealthBand.tsx")
    for token in ("created_at", "距今", "scan_date", "error_n", "true_dragon_n",
                  "candidates_n", "扫描健康"):
        assert token in band, f"ScanHealthBand 缺少 {token!r}"
    listing = _read(SRC / "pages" / "scans" / "ScansPage.tsx")
    assert "ScanHealthBand" in listing
    detail = _read(SRC / "pages" / "scans" / "ScanDetailPage.tsx")
    for token in ("MiniBar", "真龙", "rowClassName", "错误摘要"):
        assert token in detail, f"ScanDetailPage 缺少 {token!r}"


def test_auction_density() -> None:
    page = _read(SRC / "pages" / "auction" / "AuctionPage.tsx")
    assert "<details" not in page, "逐股折叠块应已删除（技术告警改用 CollapsibleSection）"
    for token in ("T+1", "T+3", "T+5", "强弱差", "outcomes.get(row.code)"):
        assert token in page, f"AuctionPage 缺少 {token!r}"
    assert page.count("OutcomeCell") >= 2, "T+1/3/5 三列应复用 OutcomeCell"


def test_new_components_have_no_slash_alpha() -> None:
    pattern = re.compile(
        r"(?:bg|text|border|ring|from|to)-"
        r"(?:white|black|brand|up|down|danger|warning|normal|positive|"
        r"hairline|raise|ink)/"
    )
    offenders: list[str] = []
    for name in CORE_QUESTION_FILES:
        text = _read(SRC / name)
        for match in pattern.finditer(text):
            offenders.append(f"{name}: {match.group(0)}")
    assert offenders == [], f"主题色禁止 Tailwind 斜杠透明度: {offenders[:5]}"

    mini = _read(SRC / "components" / "MiniBar.tsx")
    assert "aria-hidden" in mini and "title=" in mini
    assert "bg-brand" in mini and "bg-raise-strong" in mini


def test_dist_contains_core_question_copy() -> None:
    js = _dist_js()
    for token in ("空仓验证", "否决股", "扫描健康", "距今", "T+1", "强弱差"):
        assert token in js, f"dist JS 缺少核心问题文案: {token!r}"
