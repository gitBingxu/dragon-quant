"""2026-11-05 dashboard 修复的前端契约（源码 + dist 同批）。

真实交互由 ``/private/tmp/dq-dashboard-followup-20261105/evidence/browser_check.py``
（Playwright + 隔离 fixture server）验证；本文件只锁住关键结构与单位口径，
防止后续重构悄悄退化。
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "web_ui" / "frontend" / "src"
DIST = REPO / "web_ui" / "dist"

DRAGONS_PAGE = SRC / "pages" / "dragons" / "DragonsPage.tsx"
SUMMARY_CARDS = SRC / "pages" / "dragons" / "SummaryCards.tsx"
JOB_CONTROL = SRC / "pages" / "dragons" / "ReviewJobControl.tsx"
REVIEW_API = SRC / "api" / "review.ts"
CALENDAR_PANEL = SRC / "components" / "CalendarPanel.tsx"
PREP_PAGE = SRC / "pages" / "prep" / "PrepPage.tsx"
AUCTION_PAGE = SRC / "pages" / "auction" / "AuctionPage.tsx"
ACCURACY_BOARD = SRC / "components" / "AccuracyBoard.tsx"
ACCOUNT_PAGE = SRC / "pages" / "account" / "AccountPage.tsx"
RESEARCH_PAGE = SRC / "pages" / "research" / "ResearchPage.tsx"
APP_SHELL = SRC / "AppShell.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dist_js() -> str:
    assets = sorted((DIST / "assets").glob("*.js"))
    assert assets, "dist 未构建：请运行 npm --prefix web_ui/frontend run build"
    return "\n".join(_read(path) for path in assets)


def test_review_entry_only_when_pending_positive_and_full_pool_copy():
    page = _read(DRAGONS_PAGE)
    assert "pendingCount != null && pendingCount > 0" in page
    assert "derived_pending ?? summary.pending ?? 0" in page
    assert "useIncrementalReview" in page
    # 完成后按最近一次提交的筛选刷新（筛选不被重置）。
    assert "appliedFilters.current" in page
    cards = _read(SUMMARY_CARDS)
    # 按钮进「待回测」标签行，状态文案留在卡片底部。
    assert "pendingHeader={review.action}" in page
    assert "headerAction={pendingHeader}" in cards
    assert "footer={pendingFooter}" in cards


def test_review_popover_persists_only_on_acknowledge():
    control = _read(JOB_CONTROL)
    assert "dq.dragons.review-intro.v1" in control
    assert "我知道了" in control
    assert "全量待处理池" in control
    assert "不覆盖已完成结果" in control
    assert "aria-expanded" in control and "aria-controls" in control
    assert "readIntroSeen" in control and "writeIntroSeen" in control
    # 只有 closeIntro(true) 才写已读。
    assert control.count("closeIntro(true)") == 1
    assert "localStorage.setItem" in control
    assert "try {" in control
    assert "disabled={busy}" in control
    assert "POLL_INTERVAL_MS" in control and "MAX_POLLS" in control


def test_review_control_restores_and_separates_business_outcome():
    control = _read(JOB_CONTROL)
    # 挂载只读恢复 + 作业身份守卫 + 串行轮询。
    assert "fetchReviewJobStatus()" in control
    assert "已有回测任务在运行" in control
    assert "started_at" in control
    assert "terminalStartRef" in control and "trackedStartRef" in control
    assert "window.setTimeout" in control
    assert "setInterval" not in control
    # 业务失败不得伪报全成功；刷新失败必须明说。
    assert "composeSummaryText" in control
    assert "任务部分失败" in control
    assert "未取得业务汇总" in control
    assert "没有需要补算的成熟标的" in control
    assert "列表刷新失败" in control
    assert "已保留上次有效数据" in control
    # 轮询失败不得暗示后端取消。
    assert "任务不会被取消" in control


def test_dragons_page_propagates_refresh_failure_without_fake_empty():
    page = _read(DRAGONS_PAGE)
    assert "LoadOutcome" in page
    assert 'kind: "error"' in page and 'kind: "stale"' in page
    assert "refreshed: false" in page
    assert "reviewSessionActive" in page
    # 刷新失败保留最近有效数据：错误分支不再清空 rows/summary。
    error_branch = page[page.index("} catch (exc) {"):]
    error_branch = error_branch[:error_branch.index("}, []);")]
    assert "setRawData([])" not in error_branch
    assert "setSummary(null)" not in error_branch


def test_review_api_uses_explicit_post_and_status_contract():
    api = _read(REVIEW_API)
    assert '"/api/review/jobs/status"' in api
    assert '"/api/review/jobs/incremental"' in api
    assert 'method: "POST"' in api
    assert "ReviewJobHttpError" in api
    assert "ReviewJobSummary" in api and "summary" in api
    # 409 由组件按状态码处理（转轮询而非报错）。
    assert "exc.status === 409" in _read(JOB_CONTROL)


def test_calendar_panel_is_shared_and_collapsible():
    panel = _read(CALENDAR_PANEL)
    for token in ("aria-expanded", "aria-controls", "收起", "展开",
                  "defaultOpen = true"):
        assert token in panel, token
    assert "panel-body" in panel
    for page in (PREP_PAGE, AUCTION_PAGE):
        text = _read(page)
        assert "CalendarPanel" in text
        assert "var(--dq-header-h" in text
        assert "sticky top-14" not in text
    shell = _read(APP_SHELL)
    assert "--dq-header-h" in shell
    assert "ResizeObserver" in shell
    # 日历在复盘页是第一个内容区块（不再被 order-* 视觉后置）。
    assert "order-4" not in _read(PREP_PAGE)


def test_accuracy_board_separates_ratio_and_percentage_points():
    board = _read(ACCURACY_BOARD)
    assert "function pctRate" in board
    assert "function pctPoints" in board
    assert "pctPoints(group.mean_return_pct)" in board
    assert "pctPoints(group.median_return_pct)" in board
    assert "pctPoints(group.date_equal_mean_return_pct)" in board
    assert "pctPoints(data.spread.value" in board
    assert "pctPoints(boot.low as number)" in board
    assert "pctRate(group.mean_return_pct)" not in board
    assert "pctRate(group.median_return_pct)" not in board
    assert "pctRate(group.date_equal_mean_return_pct)" not in board
    assert "pctRate(group.avoided_loss_rate)" in board
    assert "pctRate(data.summary.hit_rate)" in board


def test_account_core_calls_degrade_independently():
    page = _read(ACCOUNT_PAGE)
    assert "Promise.allSettled([" in page
    assert "failures.length === 4" in page
    assert "partialError" in page
    assert "requestSeq.current === requestId" in page


def test_research_page_has_ordered_requests_and_chinese_copy():
    page = _read(RESEARCH_PAGE)
    assert "requestSeq" in page
    assert "seq !== requestSeq.current" in page
    assert "扫描研究" in page
    assert "通过 / 否决" in page
    assert "首次 / 重复入榜" in page
    assert "read_errors" in page
    api = _read(SRC / "api" / "research.ts")
    assert "read_errors" in api
    assert "扫描研究接口返回" in api

    shell = _read(APP_SHELL)
    assert "研究/诊断" not in shell
    assert 'label: "扫描研究"' in shell


def test_dist_carries_new_dashboard_contract():
    js = _dist_js()
    for token in ("我知道了", "全量待处理池", "扫描研究", "收起", "展开",
                  "/api/review/jobs/incremental", "/api/review/jobs/status",
                  "dq.dragons.review-intro.v1"):
        assert token in js, token
    assert "研究/诊断" not in js
