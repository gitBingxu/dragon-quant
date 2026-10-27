"""五维系 7 个因子的**只读迁移**（不重算）。

数据源：``scan_stocks_v2``（经 engine 按 canonical scan 读取）。
迁移语义（方案 §4.3）：
  - ``composite_score`` / ``dim_*`` 为 NULL → 该 code 不写行；
  - ``is_true_dragon`` 为 NULL → 不写行（绝不落成 0）；
  - 本模块不发请求、不读库、不 import ``dragon_quant.scan.scores``。
"""

from __future__ import annotations

from dragon_quant.infra.factor.types import (
    FactorContext,
    FactorFrame,
    FactorSpec,
    SpecDefaults,
)

SOURCE = "scan"
IMPL_PREFIX = "builtin.five_dim"


def _rows(ctx: FactorContext) -> dict:
    return ctx.source_rows.get("rows_by_code") or {}


def _frame(ctx: FactorContext, column: str) -> FactorFrame:
    values: dict[str, float] = {}
    rows = _rows(ctx)
    for code in ctx.codes:
        row = rows.get(code)
        if not row:
            continue
        value = row.get(column)
        if value is None:
            continue
        values[code] = float(value)
    return FactorFrame(
        values=values,
        available_at=ctx.source_rows.get("created_at"),
        source=SOURCE,
        source_scan_id=ctx.source_rows.get("scan_id"),
        meta={
            "missing": len(ctx.codes) - len(values),
            "scan_id": ctx.source_rows.get("scan_id"),
        },
    )


def compute_composite(ctx: FactorContext) -> FactorFrame:
    return _frame(ctx, "composite_score")


def compute_drive(ctx: FactorContext) -> FactorFrame:
    return _frame(ctx, "dim_drive")


def compute_leadership(ctx: FactorContext) -> FactorFrame:
    return _frame(ctx, "dim_leadership")


def compute_anti_drop(ctx: FactorContext) -> FactorFrame:
    return _frame(ctx, "dim_anti_drop")


def compute_liquidity(ctx: FactorContext) -> FactorFrame:
    return _frame(ctx, "dim_liquidity")


def compute_absorption(ctx: FactorContext) -> FactorFrame:
    return _frame(ctx, "dim_absorption")


def compute_is_true_dragon(ctx: FactorContext) -> FactorFrame:
    """1/0 旗标；源列为 NULL 时不写行（缺失 ≠ 否）。"""
    values: dict[str, float] = {}
    rows = _rows(ctx)
    for code in ctx.codes:
        row = rows.get(code)
        if not row:
            continue
        flag = row.get("is_true_dragon")
        if flag is None:
            continue
        values[code] = 1.0 if flag else 0.0
    return FactorFrame(
        values=values,
        available_at=ctx.source_rows.get("created_at"),
        source=SOURCE,
        source_scan_id=ctx.source_rows.get("scan_id"),
        meta={
            "missing": len(ctx.codes) - len(values),
            "scan_id": ctx.source_rows.get("scan_id"),
        },
    )


_SPEC_FIELDS = SpecDefaults(
    frequency="1d",
    source=SOURCE,
    available_phase="close",
    pit_rule="recorded",
    default_enabled=True,
    owner="factors",
)

SPECS: tuple[FactorSpec, ...] = (
    FactorSpec(
        name="five_dim_composite", label="五维综合分", compute=compute_composite,
        impl_key=f"{IMPL_PREFIX}.composite_score.v1",
        direction="higher_better", unit="score",
        description="scan_stocks_v2.composite_score 只读迁移（诊断基线，不参与因子层决策）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="five_dim_drive", label="带动性", compute=compute_drive,
        impl_key=f"{IMPL_PREFIX}.drive.v1",
        direction="higher_better", unit="score",
        description="scan_stocks_v2.dim_drive 只读迁移",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="five_dim_leadership", label="领涨性", compute=compute_leadership,
        impl_key=f"{IMPL_PREFIX}.leadership.v1",
        direction="higher_better", unit="score",
        description="scan_stocks_v2.dim_leadership 只读迁移",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="five_dim_anti_drop", label="抗跌性", compute=compute_anti_drop,
        impl_key=f"{IMPL_PREFIX}.anti_drop.v1",
        direction="higher_better", unit="score",
        description="scan_stocks_v2.dim_anti_drop 只读迁移",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="five_dim_liquidity", label="流动性", compute=compute_liquidity,
        impl_key=f"{IMPL_PREFIX}.liquidity.v1",
        direction="higher_better", unit="score",
        description="scan_stocks_v2.dim_liquidity 只读迁移",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="five_dim_absorption", label="资金承接", compute=compute_absorption,
        impl_key=f"{IMPL_PREFIX}.absorption.v1",
        direction="higher_better", unit="score",
        description="scan_stocks_v2.dim_absorption 只读迁移",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="five_dim_is_true_dragon", label="真龙旗标", compute=compute_is_true_dragon,
        impl_key=f"{IMPL_PREFIX}.is_true_dragon.v1",
        direction="neutral", unit="flag",
        description="scan_stocks_v2.is_true_dragon 只读迁移（NULL 不写行）",
        **_SPEC_FIELDS,
    ),
)
