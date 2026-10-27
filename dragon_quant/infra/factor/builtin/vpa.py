"""vpa 系 5 个因子的**只读迁移**（不重算）。

数据源：``vpa_analysis``（dragon.db）。engine 在装载阶段已过滤跨日回填污染行
（``created_at.date() != trade_date``），本模块只做行形状适配：

  - ``vpa_health_score`` ← ``vpa_analysis.health_score``；
  - 4 个量价因子 ← ``factors_json[].score``（按 ``name`` 精确匹配）。
    ``name`` 缺失 / 重复 / 非 dict → 该 code 跳过并计数，不静默取第一个。

本模块不发请求、不读库、不 import ``dragon_quant.vpa``。
"""

from __future__ import annotations

from dragon_quant.infra.factor.types import (
    FactorContext,
    FactorFrame,
    FactorSpec,
    SpecDefaults,
)

SOURCE = "vpa"
IMPL_PREFIX = "builtin.vpa"


def _rows(ctx: FactorContext) -> list[dict]:
    return list(ctx.source_rows.get("rows") or [])


def _base_meta(ctx: FactorContext) -> dict:
    return {
        "skipped_backfilled": ctx.source_rows.get("skipped_backfilled", 0),
        "skipped_backfilled_samples": ctx.source_rows.get("skipped_backfilled_samples", []),
    }


def compute_health_score(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    missing = 0
    for row in _rows(ctx):
        code = row.get("code")
        if not code:
            continue
        score = row.get("health_score")
        if score is None:
            missing += 1
            continue
        values[code] = float(score)
    meta = _base_meta(ctx)
    meta["missing"] = missing
    return FactorFrame(
        values=values,
        available_at=ctx.source_rows.get("created_at"),
        source=SOURCE,
        meta=meta,
    )


def _factor_frame(ctx: FactorContext, vpa_name: str) -> FactorFrame:
    values: dict[str, float] = {}
    stats = {"missing": 0, "duplicate_name": 0, "malformed": 0, "factors_error": 0}
    for row in _rows(ctx):
        code = row.get("code")
        if not code:
            continue
        if row.get("factors_error"):
            stats["factors_error"] += 1
            continue
        factors = row.get("factors")
        if not isinstance(factors, list):
            stats["malformed"] += 1
            continue
        matches = [
            item for item in factors
            if isinstance(item, dict) and item.get("name") == vpa_name
        ]
        if not matches:
            stats["missing"] += 1
            continue
        if len(matches) > 1:
            stats["duplicate_name"] += 1
            continue
        score = matches[0].get("score")
        if score is None:
            stats["missing"] += 1
            continue
        values[code] = float(score)
    meta = _base_meta(ctx)
    meta.update(stats)
    return FactorFrame(
        values=values,
        available_at=ctx.source_rows.get("created_at"),
        source=SOURCE,
        meta=meta,
    )


def compute_vol_amount(ctx: FactorContext) -> FactorFrame:
    return _factor_frame(ctx, "vol_amount")


def compute_trend_verify(ctx: FactorContext) -> FactorFrame:
    return _factor_frame(ctx, "trend_verify")


def compute_breakout(ctx: FactorContext) -> FactorFrame:
    return _factor_frame(ctx, "breakout")


def compute_divergence(ctx: FactorContext) -> FactorFrame:
    return _factor_frame(ctx, "divergence")


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
        name="vpa_health_score", label="量价健康度", compute=compute_health_score,
        impl_key=f"{IMPL_PREFIX}.health_score.v1",
        direction="higher_better", unit="score",
        description="vpa_analysis.health_score 只读迁移",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="vpa_vol_amount", label="量价-量能", compute=compute_vol_amount,
        impl_key=f"{IMPL_PREFIX}.vol_amount.v1",
        direction="higher_better", unit="score",
        description="vpa_analysis.factors_json 中 vol_amount.score 只读迁移",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="vpa_trend_verify", label="量价-趋势验证", compute=compute_trend_verify,
        impl_key=f"{IMPL_PREFIX}.trend_verify.v1",
        direction="higher_better", unit="score",
        description="vpa_analysis.factors_json 中 trend_verify.score 只读迁移",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="vpa_breakout", label="量价-突破", compute=compute_breakout,
        impl_key=f"{IMPL_PREFIX}.breakout.v1",
        direction="higher_better", unit="score",
        description="vpa_analysis.factors_json 中 breakout.score 只读迁移",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="vpa_divergence", label="量价-背离", compute=compute_divergence,
        impl_key=f"{IMPL_PREFIX}.divergence.v1",
        direction="higher_better", unit="score",
        description="vpa_analysis.factors_json 中 divergence.score 只读迁移",
        **_SPEC_FIELDS,
    ),
)
