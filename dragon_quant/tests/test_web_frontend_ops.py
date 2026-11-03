"""dashboard 复盘 / 竞价改版前端契约测试（方案 §9、§13.4）。

纯源码 / dist 文本扫描，零依赖、零网络：
1. 排版：html 14px、所有可见文字最小 1rem、dist 含六个文本令牌；
2. 语义色：danger/warning/normal/positive 四态，旧 warn/critical/info 清零；
3. `/prep` 五区块顺序；删除旧「结论」「运行概况」「上次计划复盘」；
4. 共用 GitHub Contributions 日历：周列 × 7 行、月份/星期标签、Less-More、
   无格内图标、sticky、容器横滚、键盘与 aria；
5. `/auction` 四区块顺序；结果表只删风险事件/决策质量/关键证据三列；
6. AccuracyBoard 两页共用、固定端点、描述性统计文案；
7. 扫描页运行态/日志；
8. `high_num` 只消费服务端 derived；
9. dist 与源码同批构建、旧区块不回回流。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "web_ui" / "frontend" / "src"
DIST_ASSETS = REPO_ROOT / "web_ui" / "dist" / "assets"
STYLES = SRC / "styles.css"

PREP_PAGE = SRC / "pages" / "prep" / "PrepPage.tsx"
AUCTION_PAGE = SRC / "pages" / "auction" / "AuctionPage.tsx"
CALENDAR = SRC / "components" / "AssetCalendar.tsx"
ACCURACY_BOARD = SRC / "components" / "AccuracyBoard.tsx"
API = SRC / "api" / "assets.ts"
LABELS = SRC / "lib" / "labels.ts"

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


# ─── 3. /prep 五区块 ──────────────────────────────────────────────────────


def test_prep_page_is_after_close_review_only() -> None:
    page = _read(PREP_PAGE)
    assert 'title="盘后复盘"' in page
    assert "集合竞价" not in page
    for token in ("数据资产日历", "核心结果", "当前 T 日效果", "当日资产明细"):
        assert token in page, f"PrepPage 缺少区块 {token!r}"


def test_prep_page_has_five_sections_in_order() -> None:
    page = _read(PREP_PAGE)
    order = ["<AssetCalendar", "<PrepCoreResult", "<PrepDayEffectSection",
             "<AssetDayTable", "<AccuracyBoard"]
    positions = [page.index(token) for token in order]
    assert positions == sorted(positions), positions


def test_prep_page_drops_legacy_blocks_and_requests() -> None:
    page = _read(PREP_PAGE)
    for legacy in ("PrepSummaryBand", "RunOverview", "PlanReviewSection",
                   "CandidateList", "fetchPlanFollowup", "fetchReviewContext"):
        assert legacy not in page, f"PrepPage 仍引用旧区块 {legacy!r}"
    assert 'title="结论"' not in page
    assert 'title="运行概况"' not in page
    assert "上次计划复盘" not in page


def test_prep_page_uses_backend_default_date_and_new_endpoints() -> None:
    page = _read(PREP_PAGE)
    assert "default_date" in page, "PrepPage 必须使用后端 default_date"
    for token in ("fetchPrepEffect", "fetchPrepAccuracy"):
        assert token in page, f"PrepPage 缺少 {token!r}"

    api = _read(API)
    for token in ("fetchPrepEffect", "fetchPrepAccuracy", "fetchAuctionAccuracy",
                  "PrepEffect", "AccuracyResponse", "default_date_source"):
        assert token in api, f"assets api 缺少 {token!r}"


def test_prep_page_has_no_raw_json() -> None:
    page = _read(PREP_PAGE)
    assert "SnapshotRawDetails" not in page
    assert "JSON.stringify" not in page
    assert not (SRC / "pages" / "prep" / "SnapshotSection.tsx").exists()


# ─── 4. 共用日历（GitHub Contributions 风格） ──────────────────────────────


def test_asset_calendar_is_github_style_and_shared() -> None:
    calendar = _read(CALENDAR)
    for token in ("overflow-x-auto", "aria-current", "aria-label", "title=",
                  "onSelect", "button", "bg-panel-3",
                  "bg-warning-soft", "bg-positive-soft",
                  "border-warning-line", "border-positive-line", "ring-brand",
                  "完整", "缺失", "WEEKDAY_LABELS", "月",
                  "gridAutoFlow", "gridTemplateRows"):
        assert token in calendar, f"AssetCalendar 缺少 {token!r}"
    # 「资产缺失」红色态已并入黄色「缺失」：组件不再产出该标签，也不使用 danger 色阶。
    for dropped in ('"资产缺失"', "bg-danger-soft", "border-danger-line"):
        assert dropped not in calendar, f"AssetCalendar 不应再含 {dropped!r}"
    # 不保留格内图标：状态只由方块色阶表达。
    assert "StatusIcon" not in calendar, "日历不应再使用格内状态图标"
    assert "<svg" not in calendar, "日历不应再内联图标"

    for page in (PREP_PAGE, AUCTION_PAGE):
        assert "AssetCalendar" in _read(page), f"{page.name} 未复用共享日历"


def test_calendar_is_sticky_below_app_header() -> None:
    for page in (PREP_PAGE, AUCTION_PAGE):
        text = _read(page)
        assert "sticky top-14 z-20" in text, (
            f"{page.name} 日历 Section 必须 sticky 且避开 56px 顶栏")


def test_calendar_marks_missing_days_as_no_record() -> None:
    calendar = _read(CALENDAR)
    assert '"无"' in calendar
    assert '"完整"' in calendar
    assert '"缺失"' in calendar


# ─── 5. /auction 四区块 ────────────────────────────────────────────────────


def test_auction_page_has_four_sections_in_order() -> None:
    page = _read(AUCTION_PAGE)
    order = ["<AssetCalendar", "<ResultsSection", "<AssetCollectionSection",
             "<AccuracyBoard"]
    positions = [page.index(token) for token in order]
    assert positions == sorted(positions), positions
    for legacy in ("RunSection", "EffectSection", 'title="运行概况"',
                   'title="成效"'):
        assert legacy not in page, f"AuctionPage 仍展示旧区块 {legacy!r}"


def test_auction_results_table_keeps_required_columns() -> None:
    page = _read(AUCTION_PAGE)
    for removed in ('"关键证据"', '"风险事件"', '"决策质量"'):
        assert removed not in page, f"竞价结果表应删除 {removed}"
    for kept in ('"代码"', '"名称"', '"强度"', '"等级"', '"方向"',
                 '"排名"', '"采集状态"'):
        assert kept in page, f"竞价结果表缺少 {kept}"
    assert "OUTCOME_HORIZONS = [1, 3, 5]" in page
    assert "T+${horizon}" in page
    assert "OutcomeCell" in page
    assert "fetchAuctionEffect" in page, "T+1/3/5 仍取既有 selected_day 口径"


def test_auction_asset_collection_section_has_no_decision_quality() -> None:
    page = _read(AUCTION_PAGE)
    for token in ("当日资产采集", "采集状态", "未知原因",
                  "run.error_rows", "snapshot_only_codes", "asset_only_codes"):
        assert token in page, f"AuctionPage 资产采集区块缺少 {token!r}"
    assert "决策质量" not in page


# ─── 6. AccuracyBoard 两页共用 ────────────────────────────────────────────


def test_accuracy_board_is_shared_and_fixed() -> None:
    board = _read(ACCURACY_BOARD)
    for token in ("AccuracyResponse", "as_of", "最新可验证日", "hit_rate",
                  "excluded_by_reason", "excluded_snapshot_dates", "stocks",
                  "caveats", "描述性统计", "不构成策略有效性结论", "条数",
                  "day_kind_counts", "sample_unit"):
        assert token in board, f"AccuracyBoard 缺少 {token!r}"
    assert "胜率" not in board, "AccuracyBoard 不得使用「胜率」"
    assert "验证有效" not in board, "AccuracyBoard 不得使用「验证有效」"

    for page in (PREP_PAGE, AUCTION_PAGE):
        assert "AccuracyBoard" in _read(page), f"{page.name} 未复用 AccuracyBoard"

    api = _read(API)
    assert "/api/prep/accuracy" in api
    assert "/api/auction/accuracy" in api
    assert "fetchPrepAccuracy" in api and "fetchAuctionAccuracy" in api


def test_accuracy_board_does_not_receive_selected_date() -> None:
    """准确率固定全历史，不随日历选中日变化。"""
    board = _read(ACCURACY_BOARD)
    assert "trade_date" not in board
    assert "date=" not in board
    for page in (PREP_PAGE, AUCTION_PAGE):
        text = _read(page)
        # 页面里 AccuracyBoard 的调用不得传 date。
        match = re.search(r"<AccuracyBoard[^>]*>", text)
        assert match, f"{page.name} 未渲染 AccuracyBoard"
        assert "date" not in match.group(0), f"{page.name} 把选中日传给了 AccuracyBoard"


def test_new_labels_exist() -> None:
    labels = _read(LABELS)
    for token in ("DEFAULT_DATE_SOURCE_LABELS", "ACCURACY_REASON_LABELS",
                  "EFFECT_REASON_LABELS", "ACCURACY_GROUP_LABELS",
                  "本地前一交易日", "本地最近交易日", "记录日回退", "今日回退",
                  "窗口未成熟", "缺本地日 K", "推荐", "不推荐"):
        assert token in labels, f"labels.ts 缺少 {token!r}"


# ─── 7. 复盘核心结果 / 单日效果组件 ────────────────────────────────────────


def test_prep_core_result_combines_required_parts() -> None:
    core = _read(SRC / "pages" / "prep" / "PrepCoreResult.tsx")
    for token in ("MarketStrip", "MainlineTable", "是否建仓", "推荐建仓标的",
                  "positionPct", "positionAdvice", "expected_ok", "expected_total",
                  "buildAnomalies", "review_candidates", "eligibility"):
        assert token in core, f"PrepCoreResult 缺少 {token!r}"

    effect = _read(SRC / "pages" / "prep" / "PrepDayEffectSection.tsx")
    for token in ("max_return_5d", "max_drawdown_5d", "max_return_hold_days",
                  "recommended", "not_recommended", "data_insufficient",
                  "next_change_pct", "mean_max_return_5d",
                  "median_max_drawdown_5d", "EFFECT_REASON_LABELS"):
        assert token in effect, f"PrepDayEffectSection 缺少 {token!r}"


def test_market_strip_shows_real_inputs_not_sub_scores() -> None:
    """市场必须是真实原始指标，不能用 0–100 子分冒充。"""
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
    page = _read(PREP_PAGE)
    assert "create=False" not in page
    assert "expected" not in page

    table = _read(SRC / "pages" / "prep" / "AssetDayTable.tsx")
    for token in ("assetTypeLabel", "数据日期", "无采集记录", "ASSET_STATUS_LABELS"):
        assert token in table, f"AssetDayTable 缺少 {token!r}"
    assert "无 report 条目" not in table


def test_high_num_is_only_consumed_via_derived() -> None:
    model = _read(SRC / "pages" / "prep" / "prepModel.ts")
    assert "derived" in model and "board_height" in model
    assert "item.high_num" not in model
    assert "needs_recompute" in model


# ─── 8. 扫描页 ─────────────────────────────────────────────────────────────


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
    labels = _read(LABELS)
    assert "已完成·无日志" in labels


# ─── 9. dist ───────────────────────────────────────────────────────────────


def test_dist_contains_new_copy_and_drops_old_blocks() -> None:
    js = _dist_js()
    for token in ("盘后复盘", "核心结果", "当前 T 日效果", "当日资产明细",
                  "当日资产采集", "全历史逐股判断准确率", "描述性统计",
                  "完整", "缺失", "待评估"):
        assert token in js, f"dist JS 缺少新文案: {token!r}"
    for legacy in ("运行概况", "上次计划复盘", "原始 JSON"):
        assert legacy not in js, f"dist JS 不应回流出旧区块: {legacy!r}"


def test_dist_assets_referenced_by_index() -> None:
    html = _read(REPO_ROOT / "web_ui" / "dist" / "index.html")
    assert "/assets/" in html
    assert any(path.is_file() for path in DIST_ASSETS.glob("*.js"))
    assert any(path.is_file() for path in DIST_ASSETS.glob("*.css"))


# ─── 10. 核心问题直答（增量） ─────────────────────────────────────────────


CORE_QUESTION_FILES = (
    "components/MiniBar.tsx",
    "components/DataTable.tsx",
    "components/AssetCalendar.tsx",
    "components/AccuracyBoard.tsx",
    "pages/scans/ScanHealthBand.tsx",
    "pages/scans/ScansPage.tsx",
    "pages/scans/ScanDetailPage.tsx",
    "pages/prep/PrepPage.tsx",
    "pages/prep/PrepCoreResult.tsx",
    "pages/prep/PrepDayEffectSection.tsx",
    "pages/prep/prepModel.ts",
    "pages/auction/AuctionPage.tsx",
    "lib/labels.ts",
    "api/assets.ts",
)


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
    page = _read(AUCTION_PAGE)
    assert "<details" not in page, "逐股折叠块应已删除（技术告警改用 CollapsibleSection）"
    for token in ("T+1", "T+3", "T+5", "outcomes.get(row.code)"):
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
    for token in ("扫描健康", "距今", "T+1", "核心结果", "准确率"):
        assert token in js, f"dist JS 缺少核心问题文案: {token!r}"
