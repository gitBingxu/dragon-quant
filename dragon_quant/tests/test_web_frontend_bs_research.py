"""C3 BS 研究普通用户投影：源码组件契约 + dist 静态契约。"""

from __future__ import annotations

from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "web_ui" / "frontend"
DIST = FRONTEND.parent / "dist"


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def _dist_js() -> str:
    assets = sorted((DIST / "assets").glob("*.js"))
    assert assets, "dist 未构建：web_ui/dist/assets 缺少 JS"
    return "\n".join(path.read_text(encoding="utf-8", errors="ignore")
                     for path in assets)


def test_panel_is_a_thin_orchestrator_with_dedicated_components():
    panel = _read("src", "pages", "research", "BsPanel.tsx")
    for token in ("BsInputs", "BsAdviceSummary", "BsLedgerView",
                  "BsExperimentDetail", "BsExperimentList",
                  "统一流程", "id=\"bs-panel\"", "nowIsoWithOffset",
                  "defaultDateRange", "entryHint"):
        assert token in panel, token
    # 不再硬编码 C2 教学日期作为默认。
    assert "\"2026-11-18\"" not in panel
    assert "\"2026-11-19\"" not in panel


def test_inputs_have_labels_validation_and_no_private_persistence():
    inputs = _read("src", "pages", "research", "bs", "BsInputs.tsx")
    for token in ("htmlFor", "id=\"bs-account\"", "id=\"bs-policy\"",
                  "id=\"bs-study\"", "id=\"bs-cutoff\"",
                  "aria-label=\"上传账户 JSON 文件\"",
                  "清空 / 重置输入", "风险参数不自动填充",
                  "new_attempt", "不会替你生成风险偏好"):
        assert token in inputs, token
    assert "localStorage" not in inputs and "sessionStorage" not in inputs
    panel = _read("src", "pages", "research", "BsPanel.tsx")
    assert "fmtMoney" in panel
    assert "review.summary?.equity ??" not in panel


def test_detail_separates_effect_from_engineering_and_units():
    detail = _read("src", "pages", "research", "bs",
                   "BsExperimentDetail.tsx")
    for token in ("工程：", "效果证据不足", "工程完成 ≠ 效果合格",
                  "策略 × 数据能力矩阵", "合格错过", "描述性差值",
                  "成熟与排除", "窗口分割", "执行支持", "恢复分段"):
        assert token in detail, token
    metrics = _read("src", "pages", "research", "bs", "BsMetricTable.tsx")
    for token in ("待证据", "样本 N", "分母", "成熟截止", "定义"):
        assert token in metrics, token


def test_ledger_view_has_partial_resume_and_event_history():
    ledger = _read("src", "pages", "research", "bs", "BsLedgerView.tsx")
    for token in ("预期事件", "已完成", "水位", "失败原因",
                  "不是完整结果", "加载更多事件", "持仓批次", "订单", "成交",
                  "只读 GET 不会触发重跑"):
        assert token in ledger, token


def test_entry_links_and_legacy_modal_block_bs_default():
    account = _read("src", "pages", "account", "AccountPage.tsx")
    assert "/research?bs=account#bs-panel" in account
    strategies = _read("src", "pages", "strategies", "StrategiesPage.tsx")
    assert "/research?bs=policy#bs-panel" in strategies
    modal = _read("src", "pages", "account", "CreateRunModal.tsx")
    for token in ("bsDefault", "BS 账户决策",
                  "/research?bs=policy", "不能默默",
                  "fetchStrategies", "strategy_id"):
        assert token in modal, token


def test_api_covers_shared_service_use_cases():
    api = _read("src", "api", "bsResearch.ts")
    for path in ("/api/research/bs-advice", "/api/research/bs-review",
                 "/api/research/bs-ledger", "/api/research/bs-experiments",
                 "/replay", "/resume", "include_details",
                 "enhanced_experiment_id", "new_attempt", "study"):
        assert path in api, path


def test_new_components_use_typography_tokens():
    for path in sorted((FRONTEND / "src" / "pages" / "research" / "bs")
                       .glob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        assert "text-xs" not in text, path
        assert "text-sm" not in text, path


def test_dist_bundle_contains_bs_research_product_tokens():
    js = _dist_js()
    for token in ("BS 账户决策（统一流程）", "待证据",
                  "策略 × 数据能力矩阵", "工程完成 ≠ 效果合格",
                  "合格错过", "加载更多事件", "清空 / 重置输入"):
        assert token in js, token
