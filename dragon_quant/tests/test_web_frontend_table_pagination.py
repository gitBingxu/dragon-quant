"""复盘 / 竞价表格分页前端契约测试（方案 2026-11-02-dashboard-table-pagination §6）。

纯源码 / dist 文本扫描，零依赖、零网络：

1. 共享 hook ``usePagination`` 是唯一页码计算入口：``resetKey`` /
   ``minTotalToShow`` / ``showPagination`` / clamp / 切片，且不排序；
2. ``PaginatedDataTable`` 组合 ``DataTable`` + ``Pagination``，属性原样透传；
3. `/prep`、`/auction` 全部表格迁移，含两页共用的 ``AccuracyBoard``；
4. 其他页面保持 opt-out：``DataTable`` 不含分页运行时逻辑，
   ``Pagination`` 不含硬编码的 ``total > 10``；
5. 共享 ``Pagination`` 的可访问性 / 窄屏增强；
6. dist 与源码同批构建，新增可访问性文案进入产物。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "web_ui" / "frontend"
SRC = FRONTEND / "src"
DIST_ASSETS = REPO_ROOT / "web_ui" / "dist" / "assets"

PAGINATION_LIB = SRC / "lib" / "pagination.ts"
PAGINATED = SRC / "components" / "PaginatedDataTable.tsx"
DATA_TABLE = SRC / "components" / "DataTable.tsx"
PAGINATION = SRC / "components" / "Pagination.tsx"
ACCURACY_BOARD = SRC / "components" / "AccuracyBoard.tsx"
PREP_PAGE = SRC / "pages" / "prep" / "PrepPage.tsx"
AUCTION_PAGE = SRC / "pages" / "auction" / "AuctionPage.tsx"

# 方案 §3 / §7：prep / auction 两页的 6 个源文件、10 张表（StockGroup 复用一个组件）。
MIGRATED_FILES = (
    "components/AccuracyBoard.tsx",
    "pages/prep/MainlineTable.tsx",
    "pages/prep/PrepCoreResult.tsx",
    "pages/prep/PrepDayEffectSection.tsx",
    "pages/prep/AssetDayTable.tsx",
    "pages/auction/AuctionPage.tsx",
)

# 每个文件里 ``<PaginatedDataTable`` 的源码出现次数（StockGroup 渲染两次 = 1 处定义）。
EXPECTED_PAGINATED_INSTANCES = {
    "components/AccuracyBoard.tsx": 1,
    "pages/prep/MainlineTable.tsx": 1,
    "pages/prep/PrepCoreResult.tsx": 1,
    "pages/prep/PrepDayEffectSection.tsx": 3,
    "pages/prep/AssetDayTable.tsx": 1,
    "pages/auction/AuctionPage.tsx": 2,
}

# 日期驱动的表必须把数据身份传给 resetKey。
RESET_KEY_SOURCES = {
    "pages/prep/PrepCoreResult.tsx": "snapshot.trade_date",
    "pages/prep/PrepDayEffectSection.tsx": "effect.trade_date",
    "pages/prep/AssetDayTable.tsx": "watermark.trade_date",
    "pages/auction/AuctionPage.tsx": "day.trade_date",
}

# 本期不迁移、保持直接使用 DataTable / 旧 usePagination 接线的页面。
OPT_OUT_FILES = (
    "pages/dragons/DragonTable.tsx",
    "pages/account/AccountTimeline.tsx",
    "pages/account/PositionsTable.tsx",
    "pages/account/TradesTable.tsx",
    "pages/account/EquityDataTable.tsx",
    "pages/scans/ScansPage.tsx",
    "pages/scans/ScanDetailPage.tsx",
    "pages/factors/BuildAudit.tsx",
    "pages/factors/DayValues.tsx",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _src(relative: str) -> str:
    return _read(SRC / relative)


def _dist_js() -> str:
    files = sorted(DIST_ASSETS.glob("*.js"))
    assert files, "dist/assets 缺少 JS 产物；请先 npm --prefix web_ui/frontend run build"
    return "".join(_read(path) for path in files)


# ─── 1. 共享 hook ─────────────────────────────────────────────────────────


def test_use_pagination_exports_shared_constants() -> None:
    source = _read(PAGINATION_LIB)
    assert "export const TABLE_DEFAULT_PAGE_SIZE = 10" in source
    assert "export const TABLE_PAGE_SIZE_OPTIONS = [10, 25, 50, 100]" in source
    assert "export const TABLE_MIN_TOTAL_TO_SHOW = 10" in source


def test_use_pagination_has_reset_clamp_and_visibility() -> None:
    source = _read(PAGINATION_LIB)
    for token in (
        "export interface PaginationOptions",
        "export interface PaginationResult<T>",
        "export function usePagination<T>(",
        "resetKey",
        "minTotalToShow",
        "showPagination",
        "Math.min",
        "Math.max",
        "items.slice",
        "pageCount",
    ):
        assert token in source, f"usePagination 缺少 {token!r}"

    # 分页只切片，不持有 / 不改变排序。
    assert ".sort(" not in source, "usePagination 不得排序（排序由页面负责）"
    assert "showPagination: total > minTotalToShow" in source, (
        "分页可见性必须以 total > minTotalToShow 为准"
    )


def test_use_pagination_was_extended_not_duplicated() -> None:
    """只有 lib/pagination.ts 定义页码计算；调用点不得自算 pageCount。"""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.tsx")) + sorted(SRC.rglob("*.ts")):
        if path == PAGINATION_LIB or path == PAGINATED:
            continue
        text = _read(path)
        if "Math.ceil(" in text and "pageCount" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"发现重复页码计算: {offenders}"


# ─── 2. PaginatedDataTable 组合壳 ─────────────────────────────────────────


def test_paginated_data_table_wraps_shared_components() -> None:
    source = _read(PAGINATED)
    for token in (
        'from "./DataTable"',
        'from "./Pagination"',
        "usePagination",
        "TABLE_DEFAULT_PAGE_SIZE",
        "TABLE_PAGE_SIZE_OPTIONS",
        "TABLE_MIN_TOTAL_TO_SHOW",
        "extends DataTableProps<T>",
        "...tableProps",
        "showPagination",
        "onPageSizeChange",
        "setPage(1)",
        "defaultPageSize = TABLE_DEFAULT_PAGE_SIZE",
        "pageSizeOptions = TABLE_PAGE_SIZE_OPTIONS",
        "minTotalToShow = TABLE_MIN_TOTAL_TO_SHOW",
        "<DataTable<T> {...tableProps}",
    ):
        assert token in source, f"PaginatedDataTable 缺少 {token!r}"


def test_data_table_props_are_exported_without_runtime_pagination() -> None:
    source = _read(DATA_TABLE)
    assert "export interface DataTableProps<T>" in source
    for token in ("sort?: SortState", "onSort?:", "rowClassName?:", "maxHeight?:"):
        assert token in source, f"DataTableProps 缺少 {token!r}"
    for banned in ("usePagination", "pageSize", "pageCount", "Pagination"):
        assert banned not in source, f"DataTable 不应含分页运行时逻辑: {banned!r}"


# ─── 3. prep / auction 全覆盖 ─────────────────────────────────────────────


def test_all_prep_and_auction_tables_use_paginated_data_table() -> None:
    for relative in MIGRATED_FILES:
        text = _src(relative)
        assert "PaginatedDataTable" in text, f"{relative} 未迁移到 PaginatedDataTable"
        assert "<DataTable" not in text, f"{relative} 仍有裸 <DataTable"
        count = text.count("<PaginatedDataTable")
        assert count == EXPECTED_PAGINATED_INSTANCES[relative], (
            f"{relative} 应有 {EXPECTED_PAGINATED_INSTANCES[relative]} 处 "
            f"PaginatedDataTable，实际 {count}"
        )


def test_accuracy_board_shared_table_is_paginated() -> None:
    board = _read(ACCURACY_BOARD)
    assert "PaginatedDataTable" in board
    assert "<DataTable" not in board
    # 全历史固定数据，不随选中日变化，因此不传 resetKey。
    assert "resetKey" not in board


def test_date_driven_tables_reset_on_data_identity_change() -> None:
    mainline = _src("pages/prep/MainlineTable.tsx")
    assert "resetKey" in mainline, "MainlineTable 必须接收并透传 resetKey"

    for relative, token in RESET_KEY_SOURCES.items():
        text = _src(relative)
        assert "resetKey" in text, f"{relative} 缺少 resetKey"
        assert token in text, f"{relative} 应把 {token} 作为 resetKey"


def test_accuracy_board_is_shared_by_prep_and_auction_only() -> None:
    users = {
        path.relative_to(SRC / "pages").as_posix()
        for path in sorted((SRC / "pages").rglob("*.tsx"))
        if "AccuracyBoard" in _read(path)
    }
    assert users == {"prep/PrepPage.tsx", "auction/AuctionPage.tsx"}, users


# ─── 4. 其他页面 opt-out ──────────────────────────────────────────────────


def test_other_pages_stay_opt_out() -> None:
    for relative in OPT_OUT_FILES:
        text = _src(relative)
        assert "PaginatedDataTable" not in text, f"{relative} 不应迁移分页"
    # 账户 / 龙头页继续走旧接线（DataTable + usePagination + Pagination）。
    for relative in (
        "pages/dragons/DragonTable.tsx",
        "pages/account/PositionsTable.tsx",
        "pages/account/TradesTable.tsx",
    ):
        assert "usePagination" in _src(relative)


def test_pagination_component_has_no_hardcoded_threshold() -> None:
    source = _read(PAGINATION)
    assert "total > 10" not in source, "可见性阈值只能由 PaginatedDataTable 决定"
    assert "minTotalToShow" not in source, "Pagination 不感知阈值"


# ─── 5. 可访问性 / 窄屏 ───────────────────────────────────────────────────


def test_pagination_component_accessibility_and_wrapping() -> None:
    source = _read(PAGINATION)
    for token in (
        "<nav",
        'aria-label="表格分页"',
        'aria-label="每页条数"',
        "aria-current",
        'aria-live="polite"',
    ):
        assert token in source, f"Pagination 缺少 {token!r}"
    assert source.count("flex-wrap") >= 2, "摘要与控件组都需允许窄屏换行"


def test_migrated_files_respect_theme_tokens() -> None:
    pattern = re.compile(r"(?:bg|text|border|ring)-[a-z-]+/")
    for relative in MIGRATED_FILES + ("components/PaginatedDataTable.tsx",):
        for match in pattern.finditer(_src(relative)):
            raise AssertionError(f"{relative} 使用斜杠透明度: {match.group(0)}")


# ─── 6. 无新增依赖 + dist 同批构建 ────────────────────────────────────────


def test_frontend_has_no_new_runtime_dependencies() -> None:
    package = json.loads(_read(FRONTEND / "package.json"))
    assert set(package["dependencies"]) == {"react", "react-dom"}


def test_dist_contains_pagination_accessibility_copy() -> None:
    js = _dist_js()
    for token in ("表格分页", "每页条数", "条/页"):
        assert token in js, f"dist JS 缺少分页文案: {token!r}"
