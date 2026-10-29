"""auction 系 8 个升级因子 + 9 个新增因子（方案 §15）。

数据源：``assets.db / auction_snapshots.analysis_json``（engine 的
``_load_auction_source`` 适配为纯 dict，逐行 JSON 损坏已隔离）。本模块不发请求、
不读库、不 import ``review_account.auction``（哨兵字面量与 config 版本本地复制，
联动断言在 ``dragon_quant/tests/test_factors_prep_auction.py``）。

缺失语义（方案 §15.1 / §15.2）：
  - 公共门槛 ``_usable_row``：无 ``error``、``unverified`` 严格 ``is False``、
    ``reject_reason`` 不是明细缺失哨兵；
  - 仅接受 ``version == "2"`` 快照；v1 行计 ``schema_v1_rows`` 并跳过；
  - ``auction_strength`` / ``auction_rank`` 只在 ``data_quality.status == "ok"`` 时
    写行（重算权重分不与完整分混入同一截面）；
  - veto（``is_weak``）只写 ``strength`` / ``is_weak``，其余因子不写行；
  - 缺失 = 无行，绝不写 0 / 50 / 中性分。
"""

from __future__ import annotations

import math
from typing import TypeGuard

from dragon_quant.infra.factor.types import (
    FactorContext,
    FactorFrame,
    FactorSpec,
    SpecDefaults,
)

SOURCE = "auction"
IMPL_PREFIX = "builtin.auction"

# 必须与 review_account.auction.registry.AUCTION_ITEMS_MISSING 完全一致；因子层
# 不得 import 上层包（AST 隔离扫描），故本地复制并由测试联动断言。
_AUCTION_ITEMS_MISSING = "竞价明细缺失"
_DIRECTION_MAPPING = {"bull": 1.0, "neutral": 0.0, "bear": -1.0}
# 必须与 review_account.auction.registry.CONFIG_VERSION / CONFIG_HASH 一致（联动测试）。
CONFIG_VERSION = "auction_v2_baseline"
CONFIG_HASH = "d16623308182"
_CONFIG_PARAMS = {"config_version": CONFIG_VERSION, "config_hash": CONFIG_HASH}


def _rows(ctx: FactorContext) -> dict[str, dict]:
    return ctx.source_rows.get("rows_by_code") or {}


def _usable_row(row: object) -> TypeGuard[dict]:
    """auction 族唯一的可用性判定（fail-closed，不含版本检查）。"""
    if not isinstance(row, dict):
        return False
    if "error" in row:
        return False
    if row.get("unverified") is not False:   # 缺键视为不可用，不当作 False
        return False
    if row.get("reject_reason") == _AUCTION_ITEMS_MISSING:
        return False
    return True


def _v2_row(row: object) -> bool:
    return _usable_row(row) and str(row.get("version")) == "2"


def _status(row: dict) -> str | None:
    quality = row.get("data_quality")
    return quality.get("status") if isinstance(quality, dict) else None


def _not_vetoed(row: dict) -> bool:
    return row.get("is_weak") is not True


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
            and row.get("unverified") is not False),
        "schema_v1_rows": sum(
            1 for row in rows.values()
            if isinstance(row, dict) and "error" not in row
            and str(row.get("version")) != "2"),
        "unavailable_rows": sum(
            1 for row in rows.values()
            if isinstance(row, dict) and _status(row) == "unavailable"),
        "partial_data_rows": sum(
            1 for row in rows.values()
            if isinstance(row, dict) and _status(row) == "degraded"),
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
        meta["expected_empty"] = True
    return FactorFrame(values=values, available_at=None, source=SOURCE, meta=meta)


def _raw_value(ctx: FactorContext, key: str, *, require=None):
    """通用原始因子读取：仅 v2、非 veto、值有限。"""
    values: dict[str, float] = {}
    rows = _rows(ctx)
    counters = _counters(ctx, {"missing": 0})
    for code in ctx.codes:
        row = rows.get(code)
        if not _v2_row(row) or not _not_vetoed(row):
            continue
        raw = row.get("raw_factors")
        value = raw.get(key) if isinstance(raw, dict) else None
        if require is not None and not require(row, raw or {}, value):
            counters["missing"] += 1
            continue
        number = _finite(value)
        if number is None:
            counters["missing"] += 1
            continue
        values[code] = number
    return _frame(ctx, values, counters,
                  expected_empty=(not values and len(ctx.codes) > 0
                                  and counters["missing"] == len(ctx.codes)))


# ─── 聚合分 / 旗标 / 方向 / 排名 ───────────────────────────────────────────


def compute_strength(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    rows = _rows(ctx)
    counters = _counters(ctx, {"missing": 0})
    for code in ctx.codes:
        row = rows.get(code)
        if not _v2_row(row) or _status(row) != "ok":
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
        if not _v2_row(row) or _status(row) == "unavailable":
            continue
        flag = row.get("is_weak")
        if not isinstance(flag, bool):
            counters["missing"] += 1
            continue
        values[code] = 1.0 if flag else 0.0
    return _frame(ctx, values, counters)


def compute_direction(ctx: FactorContext) -> FactorFrame:
    mapping = ctx.params.get("mapping") or _DIRECTION_MAPPING
    values: dict[str, float] = {}
    rows = _rows(ctx)
    counters = _counters(ctx, {"missing": 0, "unknown_enum": 0})
    for code in ctx.codes:
        row = rows.get(code)
        if not _v2_row(row) or not _not_vetoed(row):
            continue
        dims = row.get("dims")
        if not isinstance(dims, dict) or "direction" not in dims:
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
        if not _v2_row(row) or _status(row) != "ok":
            continue
        rank = row.get("rank")
        number = _finite(rank)
        if number is None:
            counters["missing"] += 1
            continue
        values[code] = number
    expected_empty = (not values and len(ctx.codes) > 0
                      and counters["missing"] == len(ctx.codes))
    return _frame(ctx, values, counters, expected_empty=expected_empty)


# ─── 维度子分 ──────────────────────────────────────────────────────────────


def _dim(ctx: FactorContext, dim_name: str) -> FactorFrame:
    values: dict[str, float] = {}
    rows = _rows(ctx)
    counters = _counters(ctx, {"missing": 0})
    for code in ctx.codes:
        row = rows.get(code)
        if not _v2_row(row) or not _not_vetoed(row):
            continue
        dims = row.get("dims")
        item = dims.get(dim_name) if isinstance(dims, dict) else None
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


def compute_dim_liquidity(ctx: FactorContext) -> FactorFrame:
    return _dim(ctx, "liquidity")


def compute_dim_supply(ctx: FactorContext) -> FactorFrame:
    return _dim(ctx, "supply")


def compute_dim_vol_price(ctx: FactorContext) -> FactorFrame:
    return _dim(ctx, "vol_price")


def compute_dim_tail_grab(ctx: FactorContext) -> FactorFrame:
    return _dim(ctx, "tail_grab")


# ─── 原始因子 ──────────────────────────────────────────────────────────────


def compute_open_gap(ctx: FactorContext) -> FactorFrame:
    return _raw_value(ctx, "official_open_gap_pct")


def compute_final_slippage(ctx: FactorContext) -> FactorFrame:
    return _raw_value(ctx, "final_indicative_slippage_pct")


def compute_turnover(ctx: FactorContext) -> FactorFrame:
    return _raw_value(ctx, "auction_turnover_pct")


def compute_unmatched_ratio(ctx: FactorContext) -> FactorFrame:
    return _raw_value(ctx, "final_unmatched_matched_ratio")


def compute_tail_acceleration(ctx: FactorContext) -> FactorFrame:
    return _raw_value(ctx, "tail_matched_acceleration")


def _require_history_5(row, raw, value):
    return _finite(raw.get("volume_ratio_history_n")) == 5 and _finite(value) is not None


def compute_volume_ratio_5d(ctx: FactorContext) -> FactorFrame:
    return _raw_value(ctx, "volume_ratio_5d", require=_require_history_5)


def _flag(ctx: FactorContext, key: str) -> FactorFrame:
    values: dict[str, float] = {}
    rows = _rows(ctx)
    counters = _counters(ctx, {"missing": 0})
    for code in ctx.codes:
        row = rows.get(code)
        if not _v2_row(row) or not _not_vetoed(row):
            continue
        raw = row.get("raw_factors")
        flag = raw.get(key) if isinstance(raw, dict) else None
        if flag is True:
            values[code] = 1.0
        else:
            counters["missing"] += 1
    return _frame(ctx, values, counters)


def compute_weak_to_strong(ctx: FactorContext) -> FactorFrame:
    return _flag(ctx, "weak_to_strong")


def compute_strong_to_weak(ctx: FactorContext) -> FactorFrame:
    return _flag(ctx, "strong_to_weak")


_SPEC_FIELDS = SpecDefaults(
    frequency="1d",
    source=SOURCE,
    available_phase="auction",
    pit_rule="derived",
    default_enabled=False,
    owner="factors",
)


def _params(**extra) -> dict:
    params = dict(_CONFIG_PARAMS)
    params.update(extra)
    return params


SPECS: tuple[FactorSpec, ...] = (
    # ── 8 个升级因子 ──
    FactorSpec(
        name="auction_strength", label="竞价强度", compute=compute_strength,
        impl_key=f"{IMPL_PREFIX}.strength.v2",
        direction="higher_better", unit="score",
        params=_params(),
        description="v2 strength（0-100；仅 data_quality=ok；veto 为 legacy 0）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_is_weak", label="竞价否决旗标", compute=compute_is_weak,
        impl_key=f"{IMPL_PREFIX}.is_weak.v2",
        direction="neutral", unit="flag",
        params=_params(),
        description="真实 veto → 1，否则 0；unavailable → 无行",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_direction", label="竞价方向", compute=compute_direction,
        impl_key=f"{IMPL_PREFIX}.direction.v2",
        direction="higher_better", unit="score",
        params=_params(mapping=_DIRECTION_MAPPING),
        description="bull=1/neutral=0/bear=-1；仅非 veto 路径",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_rank", label="竞价池内排名", compute=compute_rank,
        impl_key=f"{IMPL_PREFIX}.rank.v2",
        direction="lower_better", unit="count",
        params=_params(),
        description="rank（1 起；仅 strength 非空且 data_quality=ok）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_dim_direction", label="价格表态分", compute=compute_dim_direction,
        impl_key=f"{IMPL_PREFIX}.dim_direction.v2",
        direction="higher_better", unit="score",
        params=_params(),
        description="dims.direction.score（0-100）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_dim_liquidity", label="竞价流动性分", compute=compute_dim_liquidity,
        impl_key=f"{IMPL_PREFIX}.dim_liquidity.v2",
        direction="higher_better", unit="score",
        params=_params(),
        description="dims.liquidity.score（0-100）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_dim_supply", label="供需与转换分", compute=compute_dim_supply,
        impl_key=f"{IMPL_PREFIX}.dim_supply.v2",
        direction="higher_better", unit="score",
        params=_params(),
        description="dims.supply.score（0-100）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_dim_vol_price", label="量价协同分", compute=compute_dim_vol_price,
        impl_key=f"{IMPL_PREFIX}.dim_vol_price.v2",
        direction="higher_better", unit="score",
        params=_params(),
        description="dims.vol_price.score（0-100）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_dim_tail_grab", label="末段执行分", compute=compute_dim_tail_grab,
        impl_key=f"{IMPL_PREFIX}.dim_tail_grab.v2",
        direction="higher_better", unit="score",
        params=_params(),
        description="dims.tail_grab.score（0-100）",
        **_SPEC_FIELDS,
    ),
    # ── 9 个新增因子（全部 default_enabled=False）──
    FactorSpec(
        name="auction_open_gap_pct", label="官方开盘缺口", compute=compute_open_gap,
        impl_key=f"{IMPL_PREFIX}.open_gap_pct.v2",
        direction="higher_better", unit="pct",
        params=_params(),
        description="official_open_gap_pct；官方开盘缺失不写行（不用指示价冒充）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_final_slippage_pct", label="末段指示价滑移",
        compute=compute_final_slippage,
        impl_key=f"{IMPL_PREFIX}.final_slippage_pct.v2",
        direction="higher_better", unit="pct",
        params=_params(),
        description="final_indicative_slippage_pct；任一价格缺失不写行",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_turnover_pct", label="竞价换手率", compute=compute_turnover,
        impl_key=f"{IMPL_PREFIX}.turnover_pct.v2",
        direction="higher_better", unit="pct",
        params=_params(),
        description="auction_turnover_pct（已修正 ×100）；流通市值/价格缺失不写行",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_volume_ratio_5d", label="竞价量比5日", compute=compute_volume_ratio_5d,
        impl_key=f"{IMPL_PREFIX}.volume_ratio_5d.v2",
        direction="higher_better", unit="ratio",
        params=_params(),
        description="volume_ratio_5d；严格 history_n==5，否则无行",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_unmatched_matched_ratio", label="未匹配/匹配比",
        compute=compute_unmatched_ratio,
        impl_key=f"{IMPL_PREFIX}.unmatched_matched_ratio.v2",
        direction="higher_better", unit="ratio",
        params=_params(),
        description="final_unmatched_matched_ratio（正买负卖）；匹配量过低不写行",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_weak_to_strong", label="弱转强事件", compute=compute_weak_to_strong,
        impl_key=f"{IMPL_PREFIX}.weak_to_strong.v2",
        direction="neutral", unit="flag",
        params=_params(),
        description="weak_to_strong=True 写 1；未发生/样本不足不写行",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_strong_to_weak", label="强转弱事件", compute=compute_strong_to_weak,
        impl_key=f"{IMPL_PREFIX}.strong_to_weak.v2",
        direction="neutral", unit="flag",
        params=_params(),
        description="strong_to_weak=True 写 1；未发生/样本不足不写行",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="auction_tail_matched_acceleration", label="末段匹配加速度",
        compute=compute_tail_acceleration,
        impl_key=f"{IMPL_PREFIX}.tail_matched_acceleration.v2",
        direction="higher_better", unit="ratio",
        params=_params(),
        description="tail_matched_acceleration；front_rate≤0 不写行",
        **_SPEC_FIELDS,
    ),
)
