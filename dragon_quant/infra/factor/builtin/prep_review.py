"""prep 复盘分（review）系 18 个因子（M6，技术方案 §13）。

数据源：``assets.db / prep_snapshots.result_json`` 的 ``review`` /
``review_candidates`` / ``feedback`` 三个块（engine 的 ``_load_prep_source``
只读透传）。本模块**零 I/O**、不 import ``review_account/*``。

铁律：
  - 全部 ``default_enabled=False``，必须显式 ``--only`` / ``--all`` 才计算；
  - ``null`` / ``unknown`` 一律「无行」，绝不写 0 / 中性分；
    唯一例外是 ``position_pct=0``（明确不参与新建仓，是真实值）；
  - **分母 = ``len(review_candidates)``**，不是旧 ``FactorContext.codes``
    （旧 candidates 与详细池是两个作用域）；
  - ``params`` 含 ``review_config_version`` / ``review_config_hash``：权重或阈值
    变更时 ``test_factors_prep_review`` 强制同步常量 → ``params`` 变 →
    注册表版本哈希变（既有版本机制，factor query 自动解析新版本）；
  - ``review`` 缺失（旧快照 / ``enabled=false`` / ``compute_error``）→
    ``SourceUnavailable``，engine 记 ``missing_source`` 且不删旧行。

设计模式：Template Method —— 每个 compute 只负责「取值 + 缺失计数」，
版本/时点/落库骨架由 ``engine.build_factor`` 统一实现；枚举映射放在
``spec.params["mapping"]``，保证「被哈希的配置」与「被执行的配置」同一份。
"""

from __future__ import annotations

import math

from dragon_quant.infra.factor.types import (
    FactorContext,
    FactorFrame,
    FactorSpec,
    SourceUnavailable,
    SpecDefaults,
)

SOURCE = "prep"
IMPL_PREFIX = "builtin.prep_review"

#: 与 ``review_account.prep.review.config`` 的单一真源保持一致。
#: ``test_factors_prep_review.test_config_identity_matches_single_source``
#: 强制二者相等：调权重/阈值必须同 commit 更新这里，从而触发因子版本 bump。
REVIEW_CONFIG_VERSION = "review_baseline_v1"
REVIEW_CONFIG_HASH = "d464dae73323"

GRADE_MAPPING = {"S": 4.0, "A": 3.0, "B": 2.0, "C": 1.0, "D": 0.0}
ROLE_MAPPING = {
    "leader": 5.0, "vanguard": 4.0, "mainstay": 3.0,
    "capacity": 2.0, "follower": 1.0,
}
MARKET_MAPPING = {"strong": 3.0, "neutral": 2.0, "weak": 1.0, "ice": 0.0}
FEEDBACK_MAPPING = {"confirmed": 2.0, "weakened": 1.0, "invalidated": 0.0}

_BASE_PARAMS = {
    "review_config_version": REVIEW_CONFIG_VERSION,
    "review_config_hash": REVIEW_CONFIG_HASH,
}
_GRADE_PARAMS = {**_BASE_PARAMS, "mapping": GRADE_MAPPING}
_ROLE_PARAMS = {**_BASE_PARAMS, "mapping": ROLE_MAPPING}
_MARKET_PARAMS = {**_BASE_PARAMS, "mapping": MARKET_MAPPING}
_FEEDBACK_PARAMS = {**_BASE_PARAMS, "mapping": FEEDBACK_MAPPING}


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _review(ctx: FactorContext) -> dict:
    review = ctx.source_rows.get("review")
    if not isinstance(review, dict) or review.get("enabled") is not True:
        raise SourceUnavailable(
            "prep_snapshots.review 缺失或未启用（version<0.7 / disabled / compute_error）")
    return review


def _candidates(ctx: FactorContext) -> list[dict]:
    review = _review(ctx)
    source_hash = review.get("config_hash")
    current_hash = ctx.params.get("review_config_hash")
    if not isinstance(source_hash, str) or not source_hash:
        # 无 config_hash 无法归属版本：fail-closed，不把未知配置写成当前版本。
        raise SourceUnavailable(
            "config_hash_missing: prep_snapshots.review.config_hash 缺失，"
            "无法归属因子版本")
    if source_hash != current_hash:
        # 不同配置的观测不得混入当前 factor_version（main 验收必须修改项）。
        raise SourceUnavailable(
            f"config_hash_mismatch: snapshot={source_hash} factor={current_hash}")
    rows = ctx.source_rows.get("review_candidates")
    if not isinstance(rows, list):
        raise SourceUnavailable("prep_snapshots.review_candidates 缺失或不是数组")
    return [row for row in rows if isinstance(row, dict)]


def _meta(ctx: FactorContext, counters: dict,
          denominator: int | None = None) -> dict:
    source = ctx.source_rows
    review = source.get("review") if isinstance(source.get("review"), dict) else {}
    if denominator is None:
        candidates = source.get("review_candidates")
        denominator = len(candidates) if isinstance(candidates, list) else 0
    return {
        "snapshot_version": source.get("snapshot_version"),
        "snapshot_created_at": source.get("snapshot_created_at"),
        "review_version": review.get("review_version"),
        "source_config_version": review.get("config_version"),
        "source_config_hash": review.get("config_hash"),
        "review_config_version": ctx.params.get("review_config_version"),
        "review_config_hash": ctx.params.get("review_config_hash"),
        "denominator": denominator,
        "counters": dict(counters),
    }


def _frame(ctx: FactorContext, values: dict[str, float], counters: dict,
           *, expected_empty: bool = False,
           denominator: int | None = None) -> FactorFrame:
    meta = _meta(ctx, counters, denominator)
    if expected_empty and not values:
        meta["expected_empty"] = True
    return FactorFrame(values=values, available_at=None, source=SOURCE, meta=meta)


def _explained(counters: dict, denominator: int, keys) -> bool:
    if denominator <= 0:
        return False
    return sum(int(counters.get(key) or 0) for key in keys) == denominator


def _code(candidate: dict) -> str | None:
    code = candidate.get("code")
    return str(code) if code else None


def _dim_entry(candidate: dict, dim: str) -> dict | None:
    dimensions = candidate.get("dimensions")
    if not isinstance(dimensions, dict):
        return None
    entry = dimensions.get(dim)
    return entry if isinstance(entry, dict) else None


def _dim_score(candidate: dict, dim: str) -> float | None:
    entry = _dim_entry(candidate, dim)
    if entry is None:
        return None
    return _num(entry.get("score"))


def _sub_normalized(candidate: dict, dim: str, key: str) -> float | None:
    entry = _dim_entry(candidate, dim)
    if entry is None:
        return None
    sub = entry.get("sub")
    if not isinstance(sub, dict):
        return None
    return _num(sub.get(key))


def _raw_sub(candidate: dict, dim: str, key: str) -> float | None:
    """从 ``contributions[].sub_factors[].raw`` 取原始子项（快照唯一暴露处）。"""
    for contribution in candidate.get("contributions") or []:
        if not isinstance(contribution, dict) or contribution.get("dim") != dim:
            continue
        for item in contribution.get("sub_factors") or []:
            if isinstance(item, dict) and item.get("key") == key:
                return _num(item.get("raw"))
    return None


def _factor_errors(ctx: FactorContext) -> dict:
    sentiment = ctx.source_rows.get("sentiment")
    if not isinstance(sentiment, dict):
        return {}
    errors = sentiment.get("factor_errors")
    return errors if isinstance(errors, dict) else {}


# ─── 主分 / 档位 / 仓位 / 市场 / 主线 / 角色 ─────────────────────────────


def compute_review_score(ctx: FactorContext) -> FactorFrame:
    candidates = _candidates(ctx)
    values: dict[str, float] = {}
    counters = {"missing": 0}
    for candidate in candidates:
        code = _code(candidate)
        if not code:
            continue
        score = _num(candidate.get("review_score"))
        if score is None:
            counters["missing"] += 1
            continue
        values[code] = score
    return _frame(ctx, values, counters,
                  expected_empty=_explained(counters, len(candidates), ("missing",)))


def _mapped(ctx: FactorContext, field: str, mapping: dict,
            counters: dict, values: dict[str, float]) -> None:
    for candidate in _candidates(ctx):
        code = _code(candidate)
        if not code:
            continue
        raw = candidate.get(field)
        if raw is None:
            counters["missing"] += 1
            continue
        if isinstance(raw, str) and raw in mapping:
            values[code] = float(mapping[raw])
        else:
            counters["unknown_enum"] += 1


def compute_review_grade(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    counters = {"missing": 0, "unknown_enum": 0}
    _mapped(ctx, "review_grade", ctx.params.get("mapping") or {},
            counters, values)
    return _frame(ctx, values, counters, expected_empty=_explained(
        counters, len(_candidates(ctx)), ("missing", "unknown_enum")))


def compute_position_pct(ctx: FactorContext) -> FactorFrame:
    candidates = _candidates(ctx)
    values: dict[str, float] = {}
    counters = {"missing": 0, "invalid": 0}
    for candidate in candidates:
        code = _code(candidate)
        if not code:
            continue
        position = _num(candidate.get("position_pct"))
        if position is None:
            counters["missing"] += 1
            continue
        if position < 0:
            counters["invalid"] += 1
            continue
        # 0 是真实值（明确不参与新建仓），必须写行。
        values[code] = position
    return _frame(ctx, values, counters, expected_empty=_explained(
        counters, len(candidates), ("missing", "invalid")))


def compute_market_state(ctx: FactorContext) -> FactorFrame:
    candidates = _candidates(ctx)
    review = _review(ctx)
    market = review.get("market")
    state = market.get("state") if isinstance(market, dict) else None
    mapping = ctx.params.get("mapping") or {}
    denominator = len(candidates)
    values: dict[str, float] = {}
    counters = {"market_unknown": 0, "unknown_enum": 0}
    if state is None or state == "unknown":
        counters["market_unknown"] = denominator
        return _frame(ctx, values, counters, expected_empty=denominator > 0)
    if not isinstance(state, str) or state not in mapping:
        counters["unknown_enum"] = denominator
        return _frame(ctx, values, counters, expected_empty=denominator > 0)
    value = _num(mapping[state])
    if value is None:
        counters["unknown_enum"] = denominator
        return _frame(ctx, values, counters, expected_empty=denominator > 0)
    for candidate in candidates:
        code = _code(candidate)
        if code:
            values[code] = value
    return _frame(ctx, values, counters)


def compute_mainline_rank(ctx: FactorContext) -> FactorFrame:
    candidates = _candidates(ctx)
    values: dict[str, float] = {}
    counters = {"not_mainline": 0, "unknown_rank": 0}
    for candidate in candidates:
        code = _code(candidate)
        if not code:
            continue
        mainline = candidate.get("mainline")
        if not isinstance(mainline, dict):
            counters["not_mainline"] += 1
            continue
        rank = mainline.get("rank")
        if isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= 5:
            counters["unknown_rank"] += 1
            continue
        values[code] = float(rank)
    return _frame(ctx, values, counters, expected_empty=_explained(
        counters, len(candidates), ("not_mainline", "unknown_rank")))


def compute_role(ctx: FactorContext) -> FactorFrame:
    candidates = _candidates(ctx)
    values: dict[str, float] = {}
    counters = {"role_missing": 0, "unknown_enum": 0}
    mapping = ctx.params.get("mapping") or {}
    for candidate in candidates:
        code = _code(candidate)
        if not code:
            continue
        role = candidate.get("role")
        if role is None:
            # role=null 是 diagnostic 诊断档，不是角色，不写行。
            counters["role_missing"] += 1
            continue
        if isinstance(role, str) and role in mapping:
            values[code] = float(mapping[role])
        else:
            counters["unknown_enum"] += 1
    return _frame(ctx, values, counters, expected_empty=_explained(
        counters, len(candidates), ("role_missing", "unknown_enum")))


# ─── 六维 ─────────────────────────────────────────────────────────────────


def _compute_dim(ctx: FactorContext, dim: str) -> FactorFrame:
    candidates = _candidates(ctx)
    values: dict[str, float] = {}
    counters = {"missing": 0}
    for candidate in candidates:
        code = _code(candidate)
        if not code:
            continue
        score = _dim_score(candidate, dim)
        if score is None:
            counters["missing"] += 1
            continue
        values[code] = score
    return _frame(ctx, values, counters, expected_empty=_explained(
        counters, len(candidates), ("missing",)))


def compute_dim_d1(ctx: FactorContext) -> FactorFrame:
    return _compute_dim(ctx, "D1")


def compute_dim_d2(ctx: FactorContext) -> FactorFrame:
    return _compute_dim(ctx, "D2")


def compute_dim_d3(ctx: FactorContext) -> FactorFrame:
    return _compute_dim(ctx, "D3")


def compute_dim_d4(ctx: FactorContext) -> FactorFrame:
    return _compute_dim(ctx, "D4")


def compute_dim_d5(ctx: FactorContext) -> FactorFrame:
    return _compute_dim(ctx, "D5")


def compute_dim_d6(ctx: FactorContext) -> FactorFrame:
    return _compute_dim(ctx, "D6")


# ─── 子项（封板 / 资金 / 换手 / 位置 / 筹码）─────────────────────────────


def _sub_factor(ctx: FactorContext, dim: str, key: str, *,
                source: str = "raw") -> FactorFrame:
    candidates = _candidates(ctx)
    values: dict[str, float] = {}
    counters = {"missing": 0}
    for candidate in candidates:
        code = _code(candidate)
        if not code:
            continue
        value = (_raw_sub(candidate, dim, key) if source == "raw"
                 else _sub_normalized(candidate, dim, key))
        if value is None:
            counters["missing"] += 1
            continue
        values[code] = value
    return _frame(ctx, values, counters, expected_empty=_explained(
        counters, len(candidates), ("missing",)))


def compute_seal_ratio(ctx: FactorContext) -> FactorFrame:
    # D3.R = 封单额 / 成交额（快照只在 contributions[].raw 暴露原始比率）。
    return _sub_factor(ctx, "D3", "R")


def compute_main_net_ratio(ctx: FactorContext) -> FactorFrame:
    return _sub_factor(ctx, "D4", "F")


def compute_turnover_health(ctx: FactorContext) -> FactorFrame:
    return _sub_factor(ctx, "D4", "Q", source="normalized")


def compute_distance_high60(ctx: FactorContext) -> FactorFrame:
    return _sub_factor(ctx, "D5", "P")


def compute_chip_concentration(ctx: FactorContext) -> FactorFrame:
    return _sub_factor(ctx, "D5", "C")


# ─── T+1 反馈（close 阶段）────────────────────────────────────────────────


def compute_feedback_state(ctx: FactorContext) -> FactorFrame:
    # 仍需 review 块校验 config 身份；但观测集合是 T-1 计划，不是当日详细池。
    _candidates(ctx)
    mapping = ctx.params.get("mapping") or {}
    feedback = ctx.source_rows.get("feedback")
    if not isinstance(feedback, dict):
        if "feedback" in _factor_errors(ctx):
            raise SourceUnavailable(
                "prep_snapshots.feedback 计算失败（sentiment.factor_errors 有记录）")
        # 无 T-1 计划 = 合法缺席（计划集合为空），不能当成源故障。
        return _frame(ctx, {}, {"no_feedback": 0}, denominator=0,
                      expected_empty=True)
    items = feedback.get("items")
    if not isinstance(items, list):
        raise SourceUnavailable("prep_snapshots.feedback.items 缺失或不是数组")

    values: dict[str, float] = {}
    counters = {"missing": 0, "unknown_state": 0, "unknown_enum": 0}
    for item in items:
        if not isinstance(item, dict):
            counters["unknown_enum"] += 1
            continue
        code = _code(item)
        if not code:
            counters["missing"] += 1
            continue
        state = item.get("state")
        if state is None:
            counters["missing"] += 1
            continue
        if state == "unknown":
            counters["unknown_state"] += 1
            continue
        if isinstance(state, str) and state in mapping:
            values[code] = float(mapping[state])
        else:
            counters["unknown_enum"] += 1
    # 分母 = 计划集合大小（feedback.items 逐只对应 prev_plan.positions），
    # 不能用当日 review_candidates，否则计划代码不在今日详细池时 coverage 被稀释。
    explained = sum(counters.values()) == len(items)
    return _frame(ctx, values, counters, denominator=len(items),
                  expected_empty=explained)


# ─── 注册表 ───────────────────────────────────────────────────────────────

_SPEC_FIELDS = SpecDefaults(
    frequency="1d",
    source=SOURCE,
    available_phase="prep",
    pit_rule="derived",
    default_enabled=False,
    owner="factors",
)


def _spec(name: str, label: str, compute, impl: str, *, unit: str,
          direction: str, description: str, params,
          phase: str = "prep") -> FactorSpec:
    fields = dict(_SPEC_FIELDS)
    fields["available_phase"] = phase
    return FactorSpec(
        name=name, label=label, compute=compute,
        impl_key=f"{IMPL_PREFIX}.{impl}.v1",
        direction=direction, unit=unit,
        params=dict(params), description=description, **fields,
    )


SPECS: tuple[FactorSpec, ...] = (
    _spec("prep_review_score", "复盘总分", compute_review_score, "score",
          unit="score", direction="higher_better",
          description="review_candidates[].review_score（0-100；null 不写行）",
          params=_BASE_PARAMS),
    _spec("prep_review_grade", "复盘档位", compute_review_grade, "grade",
          unit="score", direction="higher_better",
          description="review_grade 档位序 S4/A3/B2/C1/D0（params mapping）",
          params=_GRADE_PARAMS),
    _spec("prep_position_pct", "单票建议仓位", compute_position_pct, "position_pct",
          unit="pct", direction="higher_better",
          description="position_pct：>0 写值、0 写 0、null 不写行；只作执行建议",
          params=_BASE_PARAMS),
    _spec("prep_market_state", "市场状态", compute_market_state, "market_state",
          unit="score", direction="higher_better",
          description="review.market.state：strong3/neutral2/weak1/ice0；unknown 不写行",
          params=_MARKET_PARAMS),
    _spec("prep_mainline_rank", "主线名次", compute_mainline_rank, "mainline_rank",
          unit="count", direction="lower_better",
          description="review_candidates[].mainline.rank（1-5；非主线不写行）",
          params=_BASE_PARAMS),
    _spec("prep_role", "主线角色", compute_role, "role",
          unit="score", direction="neutral",
          description="leader5/vanguard4/mainstay3/capacity2/follower1；role=null 不写行",
          params=_ROLE_PARAMS),
    _spec("prep_dim_d1", "D1 龙头质量", compute_dim_d1, "dim_d1",
          unit="score", direction="higher_better",
          description="dimensions.D1.score（缺失不写行）", params=_BASE_PARAMS),
    _spec("prep_dim_d2", "D2 主线地位", compute_dim_d2, "dim_d2",
          unit="score", direction="higher_better",
          description="dimensions.D2.score（缺失不写行）", params=_BASE_PARAMS),
    _spec("prep_dim_d3", "D3 封板质量", compute_dim_d3, "dim_d3",
          unit="score", direction="higher_better",
          description="dimensions.D3.score（缺失不写行）", params=_BASE_PARAMS),
    _spec("prep_dim_d4", "D4 资金流动", compute_dim_d4, "dim_d4",
          unit="score", direction="higher_better",
          description="dimensions.D4.score（缺失不写行）", params=_BASE_PARAMS),
    _spec("prep_dim_d5", "D5 筹码位置", compute_dim_d5, "dim_d5",
          unit="score", direction="higher_better",
          description="dimensions.D5.score（缺失不写行）", params=_BASE_PARAMS),
    _spec("prep_dim_d6", "D6 持续性", compute_dim_d6, "dim_d6",
          unit="score", direction="higher_better",
          description="dimensions.D6.score（缺失不写行）", params=_BASE_PARAMS),
    _spec("prep_seal_ratio", "封成比", compute_seal_ratio, "seal_ratio",
          unit="ratio", direction="higher_better",
          description="D3.R 原始比率 = 封单额/成交额（缺失不写行）",
          params=_BASE_PARAMS),
    _spec("prep_main_net_ratio", "主力净额占比", compute_main_net_ratio,
          "main_net_ratio", unit="ratio", direction="higher_better",
          description="D4.F 原始比率 = 主力净额/成交额（缺失不写行）",
          params=_BASE_PARAMS),
    _spec("prep_turnover_health", "换手健康度", compute_turnover_health,
          "turnover_health", unit="score", direction="higher_better",
          description="D4.Q 归一子分（0-100；缺失不写行）", params=_BASE_PARAMS),
    _spec("prep_distance_high60", "距 60 日高点回撤", compute_distance_high60,
          "distance_high60", unit="pct", direction="neutral",
          description="D5.P 原始回撤%（缺失不写行）", params=_BASE_PARAMS),
    _spec("prep_chip_concentration", "筹码集中度", compute_chip_concentration,
          "chip_concentration", unit="ratio", direction="lower_better",
          description="D5.C 原始比率 (P90-P10)/P50（缺失不写行）",
          params=_BASE_PARAMS),
    _spec("prep_feedback_state", "T+1 反馈状态", compute_feedback_state,
          "feedback_state", unit="score", direction="neutral",
          description="feedback.items[].state：confirmed2/weakened1/invalidated0；"
                      "unknown/缺失不写行",
          params=_FEEDBACK_PARAMS, phase="close"),
)
