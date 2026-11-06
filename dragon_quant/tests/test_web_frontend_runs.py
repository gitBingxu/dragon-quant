"""dashboard 运行按钮前端契约：三页入口 / 禁用 hover / 自定义弹窗 / dist。

纯源码与 dist 文本扫描，零依赖、零网络。改前端后必须
``npm --prefix web_ui/frontend run build`` 让 dist 与源码同批更新。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "web_ui" / "frontend" / "src"
DIST_ASSETS = REPO_ROOT / "web_ui" / "dist" / "assets"

SCANS_PAGE = SRC / "pages" / "scans" / "ScansPage.tsx"
PREP_PAGE = SRC / "pages" / "prep" / "PrepPage.tsx"
AUCTION_PAGE = SRC / "pages" / "auction" / "AuctionPage.tsx"
RUN_ACTIONS = SRC / "components" / "RunActions.tsx"
HOVER_TIP = SRC / "components" / "HoverTip.tsx"
RUN_MODAL = SRC / "components" / "RunParamsModal.tsx"
RUN_API = SRC / "api" / "runs.ts"
RUN_HOOK = SRC / "lib" / "useRunJob.ts"
RUN_PARAMS = SRC / "lib" / "runParams.ts"
STYLES = SRC / "styles.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dist_js() -> str:
    files = sorted(DIST_ASSETS.glob("*.js"))
    assert files, "dist/assets 缺少 JS 产物；请先 npm --prefix web_ui/frontend run build"
    return "".join(_read(path) for path in files)


def _dist_css() -> str:
    files = sorted(DIST_ASSETS.glob("*.css"))
    assert files, "dist/assets 缺少 CSS 产物；请先 npm --prefix web_ui/frontend run build"
    return "".join(_read(path) for path in files)


def test_three_pages_wire_their_own_job() -> None:
    cases = ((SCANS_PAGE, "scan"), (PREP_PAGE, "prep"), (AUCTION_PAGE, "auction"))
    for path, job in cases:
        source = _read(path)
        assert 'from "../../components/RunActions"' in source
        assert f'job="{job}"' in source
        # 成功后刷新本页数据，而不是让用户手动刷新。
        assert "onCompleted" in source
        assert "reloadToken" in source or "setReloadToken" in source


def test_run_actions_use_aria_disabled_and_rendered_tooltip() -> None:
    source = _read(RUN_ACTIONS)
    # 原生 disabled 会吞掉 hover；禁用用 aria-disabled，悬浮原因用渲染式 tooltip
    # （不是浏览器原生 title）。
    assert 'aria-disabled={disabled}' in source
    assert "HoverTip" in source
    assert "tip={disabled ? reason : null}" in source
    assert "aria-describedby" in source
    assert "title={reason}" not in source
    assert "disabledReason" in source
    assert "quick_label" in source
    assert "custom_label" in source
    assert "RunParamsModal" in source


def test_hover_tip_renders_on_hover_and_focus() -> None:
    source = _read(HOVER_TIP)
    assert 'role="tooltip"' in source
    assert "onMouseEnter" in source and "onMouseLeave" in source
    assert "onFocus" in source and "onBlur" in source
    # 说明文本始终可被 aria-describedby 关联；视觉气泡不重复朗读。
    assert 'className="sr-only"' in source
    assert 'aria-hidden="true"' in source
    assert "pointer-events-none" in source


def test_view_model_never_hardcodes_window_or_validation() -> None:
    hook = _read(RUN_HOOK)
    assert "fetchRunEligibility" in hook
    assert "fetchRunStatus" in hook
    assert "startRun" in hook
    assert "onCompletedRef.current" in hook
    assert "POLL_INTERVAL_MS" in hook
    # 可点击判定只信服务端：前端不得自己判断交易日 / 时间窗。
    assert "window_start" not in hook
    assert "isTradingDay" not in hook
    assert "new Date(" not in hook

    modal = _read(RUN_MODAL)
    assert "spec.params.map" in modal
    assert "validateRunValues" in modal
    assert "depends_on" not in modal  # 依赖过滤在 runParams.ts
    assert "仅可在交易日" in modal

    params = _read(RUN_PARAMS)
    assert "isParamActive" in params
    assert "validateRunValues" in params


def test_api_surface_matches_backend_routes() -> None:
    source = _read(RUN_API)
    for path in ("/spec", "/eligibility", "/status"):
        assert path in source
    assert "method: \"POST\"" in source
    assert "blockers" in source


def test_disabled_buttons_are_styled_for_aria_state() -> None:
    source = _read(STYLES)
    assert '.btn[aria-disabled="true"]' in source
    assert ':hover:not(:disabled):not([aria-disabled="true"])' in source


def test_dist_ships_run_copy() -> None:
    # 按钮文案由后端 spec 下发；dist 只负责前端自有文案与状态机。
    js = _dist_js()
    for text in ("确认执行", "仅可在交易日", "正在读取可执行状态",
                 "任务正在执行中", "任务执行成功", "tooltip"):
        assert text in js, f"dist 缺少 {text}；请重新构建前端"
    css = _dist_css()
    # 压缩后选择器去掉引号：`[aria-disabled=true]`。
    assert "aria-disabled=true" in css


def test_frontend_does_not_use_blocking_alerts() -> None:
    for path in (RUN_ACTIONS, RUN_MODAL, RUN_HOOK, RUN_PARAMS, HOVER_TIP):
        assert "alert(" not in _read(path)
