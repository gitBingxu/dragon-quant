"""首批 6 个财务因子的**纯函数**实现（P0.5，方案 §4.3.1）。

零 I/O、零 provider import：账本读取 / 字段映射 / as-of 展开都在
``factors/finance.py``，本模块只消费 ``ctx.source_rows``：

    current: {code: {period, values, available_at, pit_basis, layout_key, file_md5}}
    prior:   {code: {...}}          # P-4Q，同比用
    observed:{code: {...}}          # 账本无期时的当日观测回退
    cohort:  {modal_period, n_total, n_cohort, n_mismatch, ...}

口径（全部写死，评审可逐条对照）：
  - 累计口径 + 严格同报告期 cohort：非众数报告期不写行（跨期累计值不可横向排名）；
  - ROE 的 ``×4/季度序号`` 只是**近似年化**（仅用于同日截面排序）；
  - 同比用同季 P-4Q；缺期 / 负基数 → 不写行（绝不写 0 当哨兵）；
  - 净资产 ≤ 0 / 主营收入累计为 0 → 比率因子不写行，计 ``invalid_denominator``。
"""

from __future__ import annotations

import math
from typing import Optional

from dragon_quant.infra.factor.types import FactorContext, FactorFrame, FactorSpec

SOURCE = "fin"
IMPL_PREFIX = "builtin.financial"
_COHORT_PARAMS = {"cohort_policy": "modal"}


def _number(value) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _records(ctx: FactorContext) -> dict[str, dict]:
    """观测回退 + 账本选中记录（账本优先；load 已保证两者不相交）。"""
    rows = dict(ctx.source_rows.get("observed") or {})
    rows.update(ctx.source_rows.get("current") or {})
    return rows


def _base_meta(ctx: FactorContext) -> dict:
    cohort = dict(ctx.source_rows.get("cohort") or {})
    records = _records(ctx)
    pit_basis = sorted({row.get("pit_basis") for row in records.values()
                        if row.get("pit_basis")})
    return {
        "period": cohort.get("modal_period"),
        "pit_basis": (pit_basis[0] if len(pit_basis) == 1
                      else ("mixed" if pit_basis else None)),
        "pit_basis_mix": {
            basis: sum(1 for row in records.values() if row.get("pit_basis") == basis)
            for basis in pit_basis
        },
        "layout_keys": sorted({row.get("layout_key") for row in records.values()
                               if row.get("layout_key")}),
        "file_md5s": sorted({row.get("file_md5") for row in records.values()
                             if row.get("file_md5")}),
        "cohort": {
            "modal_period": cohort.get("modal_period"),
            "n_total": cohort.get("n_total"),
            "n_cohort": cohort.get("n_cohort"),
            "n_mismatch": cohort.get("n_mismatch"),
            "by_period": cohort.get("by_period"),
        },
        "cohort_excluded_profile": cohort.get("excluded_profile"),
        "cohort_policy": cohort.get("cohort_policy", "modal"),
        "stale_days": cohort.get("stale_days") or {},
        "basis_by_code": {code: row.get("pit_basis")
                          for code, row in records.items() if row.get("pit_basis")},
        "period_by_code": {code: row.get("period")
                           for code, row in records.items() if row.get("period")},
        "finance_data_versions": ctx.source_rows.get("finance_data_versions") or [],
        "retracted_count": int(ctx.source_rows.get("retracted_count") or 0),
        "layout_unverified": int(ctx.source_rows.get("layout_unverified") or 0),
        "warnings": ctx.source_rows.get("warnings") or [],
        "degraded": ctx.source_rows.get("degraded") or [],
        "earliest_available": ctx.source_rows.get("earliest_available"),
    }


def _frame(ctx: FactorContext, compute_one, *, meta_extra: Optional[dict] = None) -> FactorFrame:
    """逐 code 计算；``compute_one`` 返回 ``(value, reason)``。

    ``reason`` ∈ ``{None, "missing_field", "invalid_denominator", "error"}``：
    缺失与「分母无效」分开计数（P0.5 known_issue：不能都塞进 missing_field）。
    """
    values: dict[str, float] = {}
    skipped = {"missing_field": 0, "invalid_denominator": 0, "error": 0}
    records = _records(ctx)
    for code in ctx.codes:
        record = records.get(code)
        if not record:
            continue
        try:
            value, reason = compute_one(record.get("values") or {}, record.get("period"))
        except Exception:
            skipped["error"] += 1
            continue
        if value is None:
            skipped[reason or "missing_field"] = skipped.get(reason or "missing_field", 0) + 1
            continue
        numeric = _number(value)
        if numeric is None:
            skipped["error"] += 1
            continue
        values[code] = numeric
    meta = _base_meta(ctx)
    meta["skipped"] = {key: value for key, value in skipped.items() if value}
    if meta_extra:
        meta.update(meta_extra)
    return FactorFrame(
        values=values,
        available_at=ctx.source_rows.get("available_at"),
        source=SOURCE,
        meta=meta,
    )


def _quarter_index(period: Optional[str]) -> Optional[int]:
    if not period or len(period) != 10:
        return None
    return {"03": 1, "06": 2, "09": 3, "12": 4}.get(period[5:7])


def compute_bps(values: dict, period: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    return _number(values.get("meigujing_zichan")), "missing_field"


def compute_net_assets(values: dict, period: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    return _number(values.get("jing_zichan")), "missing_field"


def compute_roe(values: dict, period: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    quarter = _quarter_index(period)
    net_profit = _number(values.get("jing_lirun"))
    net_assets = _number(values.get("jing_zichan"))
    if not quarter or net_profit is None or net_assets is None:
        return None, "missing_field"
    if net_assets <= 0:
        return None, "invalid_denominator"
    return net_profit * 4.0 / quarter / net_assets * 100.0, None


def compute_net_margin(values: dict, period: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    net_profit = _number(values.get("jing_lirun"))
    revenue = _number(values.get("zhuying_shouru"))
    if net_profit is None or revenue is None:
        return None, "missing_field"
    if revenue == 0:
        return None, "invalid_denominator"
    return net_profit / revenue * 100.0, None


def _yoy(ctx: FactorContext, code: str,
         field: str) -> tuple[Optional[float], Optional[str]]:
    current = _records(ctx).get(code)
    prior = (ctx.source_rows.get("prior") or {}).get(code)
    if not current or not prior:
        return None, "missing_field"
    now = _number((current.get("values") or {}).get(field))
    before = _number((prior.get("values") or {}).get(field))
    # 负基数/零基数 → 同比无经济含义，不写行（绝不写 0 哨兵）
    if now is None or before is None:
        return None, "missing_field"
    if before <= 0:
        return None, "invalid_denominator"
    return (now / before - 1.0) * 100.0, None


def _yoy_frame(ctx: FactorContext, field: str) -> FactorFrame:
    values: dict[str, float] = {}
    skipped = {"missing_field": 0, "invalid_denominator": 0}
    records = _records(ctx)
    for code in ctx.codes:
        if code not in records:
            continue
        value, reason = _yoy(ctx, code, field)
        if value is None:
            key = reason or "missing_field"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        values[code] = value
    meta = _base_meta(ctx)
    meta["skipped"] = {key: value for key, value in skipped.items() if value}
    meta["missing_prior_or_invalid_base"] = sum(skipped.values())
    return FactorFrame(
        values=values,
        available_at=ctx.source_rows.get("available_at"),
        source=SOURCE,
        meta=meta,
    )


def compute_bps_factor(ctx: FactorContext) -> FactorFrame:
    return _frame(ctx, compute_bps)


def compute_roe_factor(ctx: FactorContext) -> FactorFrame:
    return _frame(ctx, compute_roe,
                  meta_extra={"annualization": "approx_4_over_quarter"})


def compute_net_margin_factor(ctx: FactorContext) -> FactorFrame:
    return _frame(ctx, compute_net_margin)


def compute_net_assets_factor(ctx: FactorContext) -> FactorFrame:
    return _frame(ctx, compute_net_assets)


def compute_net_profit_yoy(ctx: FactorContext) -> FactorFrame:
    return _yoy_frame(ctx, "jing_lirun")


def compute_revenue_yoy(ctx: FactorContext) -> FactorFrame:
    return _yoy_frame(ctx, "zhuying_shouru")


_SPEC_FIELDS = dict(
    frequency="1q",
    source=SOURCE,
    available_phase="prep",
    pit_rule="asof",
    lookback=0,
    default_enabled=False,
    owner="factors",
    params=_COHORT_PARAMS,
)

SPECS: tuple[FactorSpec, ...] = (
    FactorSpec(
        name="fin_bps", label="每股净资产", compute=compute_bps_factor,
        impl_key=f"{IMPL_PREFIX}.bps.v1",
        direction="higher_better", unit="cny_per_share",
        description="每股净资产（gpcw meigujing_zichan，as-of 展开）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="fin_roe", label="近似年化 ROE", compute=compute_roe_factor,
        impl_key=f"{IMPL_PREFIX}.roe.v1",
        direction="higher_better", unit="pct",
        description="净利润(累计)×4/季度序号÷净资产×100（仅截面排序，非绝对口径）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="fin_net_margin", label="净利率", compute=compute_net_margin_factor,
        impl_key=f"{IMPL_PREFIX}.net_margin.v1",
        direction="higher_better", unit="pct",
        description="净利润(累计)÷主营收入(累计)×100",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="fin_net_profit_yoy", label="净利润同比", compute=compute_net_profit_yoy,
        impl_key=f"{IMPL_PREFIX}.net_profit_yoy.v1",
        direction="higher_better", unit="pct",
        description="净利润(P)/净利润(P-4Q)-1（同季同比，缺 P-4Q/负基数不写行）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="fin_revenue_yoy", label="营收同比", compute=compute_revenue_yoy,
        impl_key=f"{IMPL_PREFIX}.revenue_yoy.v1",
        direction="higher_better", unit="pct",
        description="主营收入(P)/主营收入(P-4Q)-1（同季同比）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="fin_net_assets", label="净资产", compute=compute_net_assets_factor,
        impl_key=f"{IMPL_PREFIX}.net_assets.v1",
        direction="lower_better", unit="cny",
        description="净资产（规模代理，direction=lower_better 表达小市值倾向）",
        **_SPEC_FIELDS,
    ),
)
