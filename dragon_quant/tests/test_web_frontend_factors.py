"""因子注册表前端契约（源码 / dist 文本扫描，零依赖、零网络）。

覆盖本期「默认全量构建 + 归属展示 + 分页筛选」：

1. API 客户端不再暴露 ``enabled`` / ``enabled_only``，新增 ``origin`` / ``owner``；
2. 纯函数 ``factorFilters`` 实现搜索 / 归属 / 数据来源组合筛选与 resetKey；
3. ``RegistryTable`` 删除「默认启用」列，新增「归属」「数据来源」，复用共享分页；
4. 查询区下拉显示 ``label（name）``，点击行只同步选择、不自动请求；
5. dist 与源码同批构建。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "web_ui" / "frontend" / "src"
DIST_ASSETS = REPO_ROOT / "web_ui" / "dist" / "assets"

API = SRC / "api" / "factors.ts"
FILTERS = SRC / "pages" / "factors" / "factorFilters.ts"
REGISTRY = SRC / "pages" / "factors" / "RegistryTable.tsx"
PAGE = SRC / "pages" / "factors" / "FactorsPage.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dist_js() -> str:
    files = sorted(DIST_ASSETS.glob("*.js"))
    assert files, "dist/assets 缺少 JS 产物；请先 npm --prefix web_ui/frontend run build"
    return "".join(_read(path) for path in files)


def test_api_client_drops_enabled_and_exposes_origin() -> None:
    source = _read(API)
    assert "enabled_only" not in source
    assert "enabled: boolean" not in source
    assert 'origin: "builtin" | "workspace"' in source
    assert "owner: string | null" in source


def test_factor_filters_are_pure_and_cover_search_origin_source() -> None:
    source = _read(FILTERS)
    for token in (
        "export type OriginFilter",
        "export interface FactorFilterState",
        "export function matchesFilters(",
        "export function filterFactors(",
        "export function sourceOptions(",
        "export function isFilterActive(",
        "export function filterResetKey(",
        "toLowerCase",
        "factor.name",
        "factor.label",
        "factor.description",
        "factor.origin",
        "factor.source",
        ".sort(",
    ):
        assert token in source, f"factorFilters 缺少 {token!r}"
    assert "useState" not in source, "筛选纯函数不得持有 React 状态"


def test_registry_table_reuses_pagination_and_new_columns() -> None:
    source = _read(REGISTRY)
    for token in (
        "PaginatedDataTable",
        "resetKey={filterResetKey(filters)}",
        'header: "归属"',
        'header: "数据来源"',
        '"系统内建"',
        '"Workspace"',
        "清空筛选",
        "筛选结果",
        "没有符合筛选条件的因子",
    ):
        assert token in source, f"RegistryTable 缺少 {token!r}"
    assert "默认启用" not in source
    assert "<DataTable" not in source
    # 点击行只回调选择，不在组件内发请求。
    assert "onRowClick={(row) => onSelect(row.name)}" in source
    assert "fetchFactorValues" not in source


def test_query_select_shows_label_and_name() -> None:
    source = _read(PAGE)
    assert "`${factor.label}（${factor.name}）`" in source
    assert "factor.label" in source and "factor.name" in source
    # 点击注册表行只同步选择并清理错误，不触发生成请求。
    assert "setSelected(name)" in source
    assert "setError(null)" in source


def test_dist_contains_factor_registry_copy() -> None:
    js = _dist_js()
    for token in ("归属", "数据来源", "筛选结果", "清空筛选", "没有符合筛选条件的因子"):
        assert token in js, f"dist JS 缺少因子注册表文案: {token!r}"
