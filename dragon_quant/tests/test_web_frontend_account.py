"""``/account`` 增量回测按钮 + API 的源码 / dist 文本契约。

只扫描源码与构建产物，零网络、零数据库：按钮文案、tooltip 逐字文本、端点、
自动选中与错误状态必须在源码与 dist 同批出现；按钮与 ``HintIcon`` 必须是兄弟
节点（``HintIcon`` 自身渲染 button，嵌套 button 非法）。
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "web_ui" / "frontend" / "src"
DIST = REPO / "web_ui" / "dist"

TOOLTIP = "在当前回测记录与操盘策略的基础上对增量数据执行回测"
INCREMENTAL_PATH = "/api/account/runs/incremental"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dist_js() -> str:
    assets = sorted((DIST / "assets").glob("*.js"))
    assert assets, "dist 未构建：web_ui/dist/assets 缺少 JS；请先 npm run build"
    return "\n".join(_read(path) for path in assets)


def _account_page() -> str:
    return _read(FRONTEND / "pages" / "account" / "AccountPage.tsx")


def _account_api() -> str:
    return _read(FRONTEND / "api" / "account.ts")


def test_api_exposes_incremental_endpoint_and_base_equity():
    api = _account_api()
    assert INCREMENTAL_PATH in api
    assert "createIncrementalAccountRun" in api
    assert "base_equity" in api


def test_page_has_incremental_button_and_exact_tooltip():
    page = _account_page()
    for token in ("createIncrementalAccountRun", "增量回测中…", "增量回测",
                  'label="增量回测说明"', TOOLTIP, "actionError",
                  "loadRuns(resp.data.id)", "disabled={!runId || incrementing}"):
        assert token in page, token
    assert "HintIcon" in page


def test_button_and_hint_are_siblings_not_nested_buttons():
    page = _account_page()
    start = page.index("增量回测中…")
    # 增量按钮的 <button> 标签到 </button> 之间不得出现 HintIcon（避免 button 嵌套）。
    button_open = page.rindex("<button", 0, start)
    button_close = page.index("</button>", start)
    assert "HintIcon" not in page[button_open:button_close]
    wrapper = page.rindex("<span", 0, button_open)
    wrapper_close = page.index("</span>", button_close)
    assert "HintIcon" in page[button_close:wrapper_close]
    assert "inline-flex items-center gap-1.5" in page


def test_account_page_uses_typography_tokens():
    for path in (FRONTEND / "pages" / "account" / "AccountPage.tsx",
                 FRONTEND / "api" / "account.ts"):
        text = _read(path)
        assert "text-xs" not in text, path
        assert "text-sm" not in text, path


def test_dist_is_built_and_carries_incremental_contract():
    assert (DIST / "index.html").is_file()
    js = _dist_js()
    for token in (TOOLTIP, "增量回测中…", INCREMENTAL_PATH):
        assert token in js, token
