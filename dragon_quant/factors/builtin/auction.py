"""auction 系 8 个因子（集合竞价强度快照只读迁移，方案 §15.1.2）。

数据源：``assets.db / auction_snapshots.analysis_json``（engine 的
``_load_auction_source`` 适配为纯 dict，逐行 JSON 损坏已隔离）。本模块不发
请求、不读库、不 import ``dragon_quant.auction``（哨兵字面量本地复制，联动断言
在 ``tests/test_factors_prep_auction.py``）。

缺失语义（方案 §15.1.2 / §15.7）：
  - ``_usable_row`` 是所有 auction 因子的公共门槛：无 ``error`` 键、
    ``unverified`` 严格 ``is False``、``reject_reason`` 不是明细缺失哨兵；
  - 真实否决路径（``unverified=False``、``dims={}``）只写 ``strength`` / ``is_weak``；
    ``direction`` 是硬编码占位值，四维子分与 ``rank`` 同样不写行；
  - ``rank=None`` 只缺 ``auction_rank`` 一行；子维度天然稀疏。
"""

from __future__ import annotations

import math

from dragon_quant.factors.types import FactorContext, FactorFrame, FactorSpec

SOURCE = "auction"
IMPL_PREFIX = "builtin.auction"

# 必须与 dragon_quant.auction.registry.AUCTION_ITEMS_MISSING 完全一致；因子层
# 不得 import 上层包（AST 隔离扫描），故本地复制并由测试联动断言。
_AUCTION_ITEMS_MISSING = "竞价明细缺失"
_DIRECTION_MAPPING = {"bull": 1.0, "neutral": 0.0, "bear": -1.0}


def _rows(ctx: FactorContext) -> dict[str, dict]:
    return ctx.source_rows.get("rows_by_code") or {}


def _usable_row(row) -> bool:
    """auction 族唯一的可用性判定（fail-closed）。"""
    if not isinstance(row, dict):
        return False
    if "error" in row:
        return False
    if row.get("unverified") is not False:   # 缺键视为不可用，不当作 False
        return False
    if row.get("reject_reason") == _AUCTION_ITEMS_MISSING:
        return False
    return True


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _counters(ctx: FactorContext, extra: dict | None = None) -> dict:
    rows = _rows(ctx)
    corrupt = ctx.source_rows.get("corrupt_rows") or []
    counters = {
        "corrupt_rows": len(corrupt) if isinstance(corrupt, (list, tuple)) else 0,
        "unverified_rows": sum(
            1 for row in rows.values()
            if isinstance(row, dict) and "error" not in row
            and row.get("unverified") is not False
        ),
    }
    counters.update(extra or {})
    return counters


def _base_meta(ctx: FactorContext, counters: dict) -> dict:
    source = ctx.source_rows
    meta = {
        "snapshot_version": source.get("snapshot_version"),
        "snapshot_created_at": source.get("snapshot_created_at"),
        "counters": dict(counters),
    }
    created = str(source.get("snapshot_created_at") or "")[:10]
    if len(created) == 10 and created > str(ctx.trade_date):
        meta["snapshot_backfilled"] = True
        meta["counters"]["snapshot_backfilled"] = True
    return meta


def _frame(ctx: FactorContext, values: dict[str, float], counters: dict,
           *, expected_empty: bool = False) -> FactorFrame:
    meta = _base_meta(ctx, counters)
    if expected_empty and not values:
        # 空结果可由 counters 完全解释（全员否决路径 / 全员未校准），报告不告警。
        meta["expected_empty"] = True
    return FactorFrame(
        values=values,
        available_at=None,          # derived：engine 用 phase（auction = T@09:25）
        source=SOURCE,
        meta=meta,
    )


# ─── 聚合分 / 旗标 / 方向 / 排名 ───────────────────────────────────────────


def compute_strength(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    rows = _rows(ctx)
    counters = _counters(ctx, {"missing": 0})
    for code in ctx.codes:
        row = rows.get(code)
        if not _usable_row(row):
            continue
        strength = _finite(row.get("strength"))
        if strength is None:
            counters["missing"] += 1
            continue
        values[code] = strength
    return _frame(ctx, values, counters)


def compute_is_weak(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    rows = _rows(ctx)
    counters = _counters(ctx, {"missing": 0})
    for code in ctx.codes:
        row = rows.get(code)
        if not _usable_row(row):
            continue
        flag = row.get("is_weak")
        if not isinstance(flag, bool):
            counters["missing"] += 1
            continue
        values[code] = 1.0 if flag else 0.0
    return _frame(ctx, values, counters)


def compute_direction(ctx: FactorContext) -> FactorFrame:
    mapping = ctx.params.get("mapping") or {}
    values: dict[str, float] = {}
    rows = _rows(ctx)
    counters = _counters(ctx, {"missing": 0, "unknown_enum": 0})
    for code in ctx.codes:
        row = rows.get(code)
        if not _usable_row(row):
            continue
        dims = row.get("dims")
        if not isinstance(dims, dict) or "direction" not in dims:
            # 否决路径的 direction="neutral" 是占位值，不是观测。
            counters["missing"] += 1
            continue
        raw = row.get("direction")
        if isinstance(raw, str) and raw in mapping:
            scored = _finite(mapping[raw])
            if scored is None:
                counters["unknown_enum"] += 1
                continue
            values[code] = scored
        else:
            counters["unknown_enum"] += 1
    expected_empty = (not values and len(ctx.codes) > 0
                      and counters["missing"] == len(ctx.codes))
    return _frame(ctx, values, counters, expected_empty=expected_empty)


def compute_rank(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    rows = _rows(ctx)
    counters = _counters(ctx, {"missing": 0})
    for code in ctx.codes:
        row = rows.get(code)
        if not _usable_row(row):
            continue
        rank = row.get("rank")
        if rank is None:
            counters["missing"] += 1
            continue
        number = _finite(rank)
        if number is None:
            counters["missing"] += 1
            continue
        values[code] = number
    expected_empty = (not values and len(ctx.codes) > 0
                      and counters["missing"] == len(ctx.codes))
    return _frame(ctx, values, counters, expected_empty=expected_empty)


# ─── 四维子分 ──────────────────────────────────────────────────────────────


def _dim(ctx: FactorContext, dim_name: str) -> FactorFrame:
    values: dict[str, float] = {}
    rows = _rows(ctx)
    counters = _counters(ctx, {"missing": 0})
    for code in ctx.codes:
        row = rows.get(code)
        if not _usable_row(row):
            continue
        dims = row.get("dims")
        if not isinstance(dims, dict):
            counters["missing"] += 1
            continue
        item = dims.get(dim_name)
        score = item.get("score") if isinstance(item, dict) else None
        number = _finite(score)
        if number is None:
            counters["missing"] += 1
            continue
        values[code] = number
    expected_empty = (not values and len(ctx.codes) > 0
                      and counters["missing"] == len(ctx.codes))
    return _frame(ctx, values, counters, expected_empty=expected_empty)


def compute_dim_direction(ctx: FactorContext) -> FactorFrame:
    return _dim(ctx, "direction")


def compute_dim_supply(ctx: FactorContext) -> FactorFrame:
    return _dim(ctx, "supply")


def compute_dim_vol_price(ctx: FactorContext) -> FactorFrame:
    return _dim(ctx, "vol_price")


def compute_dim_tail_grab(ctx: FactorContext) -> FactorFrame:
    return _dim(ctx, "tail_grab")


_SPEC_FIELDS = dict(
    frequency="1d",
    source=SOURCE,
    available_phase="auction",
    pit_rule="derived",
    default_enabled=False,
    owner="factors",
)

SPECS: tuple[FactorSpec, ...] = (
    FactorSpec(
        name="auction_strength", label="竞价强度", compute=compute_strength,
        impl_key=f"{IMPL_PREFIX}.strength.v1",
        direction="higher_better", unit="score",
        description="auction_snapshots.strength（0-100；真实否决为序关系地板值 0）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_is_weak", label="竞价否决旗标", compute=compute_is_weak,
        impl_key=f"{IMPL_PREFIX}.is_weak.v1",
        direction="neutral", unit="flag",
        description="auction_snapshots.is_weak → 1/0（明细缺失哨兵不写行）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_direction", label="竞价方向", compute=compute_direction,
        impl_key=f"{IMPL_PREFIX}.direction.v1",
        direction="higher_better", unit="score",
        params={"mapping": _DIRECTION_MAPPING},
        description="direction → bull=1/neutral=0/bear=-1；仅非否决路径（dims 含 direction）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_rank", label="竞价池内排名", compute=compute_rank,
        impl_key=f"{IMPL_PREFIX}.rank.v1",
        direction="lower_better", unit="count",
        description="auction_snapshots.rank（1 起；未否决且已验证者有值）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_dim_direction", label="方向表态分", compute=compute_dim_direction,
        impl_key=f"{IMPL_PREFIX}.dim_direction.v1",
        direction="higher_better", unit="score",
        description="dims.direction.score（0-100）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_dim_supply", label="供需结构分", compute=compute_dim_supply,
        impl_key=f"{IMPL_PREFIX}.dim_supply.v1",
        direction="higher_better", unit="score",
        description="dims.supply.score（0-100）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_dim_vol_price", label="量价配合分", compute=compute_dim_vol_price,
        impl_key=f"{IMPL_PREFIX}.dim_vol_price.v1",
        direction="higher_better", unit="score",
        description="dims.vol_price.score（0-100）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_dim_tail_grab", label="末段抢筹分", compute=compute_dim_tail_grab,
        impl_key=f"{IMPL_PREFIX}.dim_tail_grab.v1",
        direction="higher_better", unit="score",
        description="dims.tail_grab.score（0-100）",
        **_SPEC_FIELDS,
    ),
)
