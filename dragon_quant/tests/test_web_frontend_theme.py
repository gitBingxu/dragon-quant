"""Web 前端构建产物契约测试（纯离线，零第三方依赖）。

守护 dashboard v2 收敛与三态主题的「源码 / dist 同步」：

1. 源码 ``index.html`` 与构建产物 ``dist/index.html`` 都包含首屏主题 bootstrap
   契约键（``dq.theme`` / ``prefers-color-scheme`` / ``dataset.theme`` /
   ``theme-color``）。
2. ``src/theme/theme.ts`` 与内联脚本共享同一存储键与三个合法偏好值
   （双处契约，防止只改一处导致首屏与 React 状态漂移）。
3. dist 的内联主题脚本是经典同步脚本、位于 ``</head>`` 之前，先于 defer 的
   module bundle 执行。
4. dist CSS 含浅色覆盖选择器与 ``--color-void`` 覆盖。
5. dist JS 不再回流 v1 专属文案，且包含主题控件文案。
6. dist index 引用的每个 ``/assets/*`` 文件真实存在（防止半构建 / 旧 index）。

只读仓库文件：不打开数据库、不发网络请求。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "web_ui" / "frontend"
DIST = REPO_ROOT / "web_ui" / "dist"
DIST_ASSETS = DIST / "assets"

V1_COPY = ("数据体系", "v1 历史", "v1 无严格口径", "v1 历史固定旧口径")
THEME_LABELS = ("跟随系统", "浅色", "深色")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _script_tags(html: str) -> list[tuple[int, int, str]]:
    """返回 (start, end, 完整 script 标签文本) 列表。"""
    return [
        (match.start(), match.end(), match.group(0))
        for match in re.finditer(r"<script\b[^>]*>.*?</script>", html, re.DOTALL)
    ]


def _opening_tag(script: str) -> str:
    return script[: script.index(">") + 1]


def _assert_theme_bootstrap_contract(html: str, label: str) -> None:
    for needle in ("dq.theme", "prefers-color-scheme", "dataset.theme", "theme-color"):
        assert needle in html, f"{label} 缺少主题 bootstrap 契约: {needle!r}"


def test_frontend_index_has_theme_bootstrap() -> None:
    _assert_theme_bootstrap_contract(_read(FRONTEND / "index.html"), "frontend/index.html")


def test_dist_index_has_theme_bootstrap() -> None:
    _assert_theme_bootstrap_contract(_read(DIST / "index.html"), "dist/index.html")


def test_theme_ts_shares_storage_key_and_values() -> None:
    source = _read(FRONTEND / "src" / "theme" / "theme.ts")
    assert "dq.theme" in source
    for value in ('"system"', '"light"', '"dark"'):
        assert value in source, f"theme.ts 缺少偏好字面量 {value}"


def test_dist_inline_theme_script_is_classic_and_before_head_end() -> None:
    html = _read(DIST / "index.html")
    scripts = _script_tags(html)
    inline = [item for item in scripts if "dq.theme" in item[2]]
    assert inline, "dist/index.html 缺少内联主题脚本（dq.theme）"
    _, start, script = inline[0]
    assert start < html.index("</head>"), "内联主题脚本必须在 </head> 之前执行"
    assert 'type="module"' not in _opening_tag(script), "内联主题脚本必须是经典同步脚本"
    assert any('type="module"' in _opening_tag(item[2]) for item in scripts), (
        "dist/index.html 缺少 module bundle"
    )


def test_dist_css_has_light_theme_overrides() -> None:
    css_files = sorted(DIST_ASSETS.glob("*.css"))
    assert css_files, "dist/assets 缺少 CSS 产物"
    css = "".join(_read(path) for path in css_files)
    assert re.search(r"\[data-theme=[\"']?light[\"']?\]", css), "浅色主题缺少 data-theme 覆盖"
    assert "--color-void" in css, "浅色主题缺少 --color-void 覆盖"
    assert re.search(r"#f4f6fb", css, re.IGNORECASE), "浅色主题缺少浅色背景值"


def test_dist_js_drops_v1_copy_and_keeps_theme_labels() -> None:
    js_files = sorted(DIST_ASSETS.glob("*.js"))
    assert js_files, "dist/assets 缺少 JS 产物"
    js = "".join(_read(path) for path in js_files)
    for banned in V1_COPY:
        assert banned not in js, f"dist JS 仍在回流 v1 文案: {banned!r}"
    for label in THEME_LABELS:
        assert label in js, f"dist JS 缺少主题控件文案: {label!r}"


def test_dist_index_referenced_assets_exist() -> None:
    html = _read(DIST / "index.html")
    refs = re.findall(r"""(?:src|href)=["'](/assets/[^"']+)["']""", html)
    assert refs, "dist/index.html 未引用任何构建资产"
    for ref in refs:
        target = DIST / ref.lstrip("/")
        assert target.is_file(), f"dist/index.html 引用了不存在的资产: {ref}"
