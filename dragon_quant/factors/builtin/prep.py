"""prep 系 7 个因子（盘后复盘快照只读迁移，方案 §15.1.1）。

数据源：``assets.db / prep_snapshots.result_json``（engine 的 ``_load_prep_source``
适配为纯 dict）。本模块不发请求、不读库、不 import ``review_account``。

铁律（方案 §15.1.1 / §15.3）：
  - 枚举映射放在 ``spec.params["mapping"]`` 且 compute 从 ``ctx.params`` 读取，
    保证「被哈希的配置」与「被执行的配置」是同一份对象；
  - ``unrated`` 主选不写行（``unrated_policy="skip"``）；
  - ``ignore`` 是真实最低档，写 0；缺失一律用「无行」表达；
  - ``high_days`` 是字符串（``"3天3板"``），只解析出板数，解析失败不猜 1；
  - ``limit_up_suc_rate`` 是 0-1 比率（``unit="ratio"``）；
  - ``in_pool_today`` 仅在 ``facts.pool_available is True`` 时成立。
"""

from __future__ import annotations

import math
import re

from dragon_quant.factors.types import FactorContext, FactorFrame, FactorSpec

SOURCE = "prep"
IMPL_PREFIX = "builtin.prep"

_VERDICT_MAPPING = {"strong": 3.0, "light": 2.0, "ignore": 0.0}
_UNRATED_POLICY = "skip"
_HIGH_DAYS_RE = re.compile(r"(\d+)天(\d+)板")


def _candidates(ctx: FactorContext) -> list[dict]:
    return [
        item for item in (ctx.source_rows.get("candidates") or [])
        if isinstance(item, dict)
    ]


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _base_meta(ctx: FactorContext, counters: dict) -> dict:
    source = ctx.source_rows
    meta = {
        "snapshot_version": source.get("snapshot_version"),
        "snapshot_created_at": source.get("snapshot_created_at"),
        "counters": dict(counters),
    }
    # 候选自身入选日只作血缘（因子行 trade_date 必须 = 观测日 T，避免前视）。
    by_code = source.get("candidates_by_code") or {}
    lineage = {
        str(code): str(candidate.get("trade_date"))
        for code, candidate in by_code.items()
        if isinstance(candidate, dict) and candidate.get("trade_date")
    }
    if lineage:
        meta["candidate_trade_date"] = lineage
    created = str(source.get("snapshot_created_at") or "")[:10]
    if len(created) == 10 and created > str(ctx.trade_date):
        # 全量重算回填：不改变 available_at（derived 不看 created_at），只作审计证据。
        meta["snapshot_backfilled"] = True
        meta["counters"]["snapshot_backfilled"] = True
    return meta


def _frame(ctx: FactorContext, values: dict[str, float], counters: dict,
           *, expected_empty: bool = False) -> FactorFrame:
    meta = _base_meta(ctx, counters)
    if expected_empty and not values:
        # 空结果可由 counters 完全解释（全 unrated / 全池不可用），报告不必告警。
        meta["expected_empty"] = True
    return FactorFrame(
        values=values,
        available_at=None,          # derived：engine 用 phase（prep = T@16:40）
        source=SOURCE,
        meta=meta,
    )


# ─── 聚合档位 ──────────────────────────────────────────────────────────────


def compute_verdict(ctx: FactorContext) -> FactorFrame:
    mapping = ctx.params.get("mapping") or {}
    policy = ctx.params.get("unrated_policy", "skip")
    values: dict[str, float] = {}
    counters = {"missing": 0, "unknown_enum": 0, "unrated": 0}
    for candidate in _candidates(ctx):
        code = candidate.get("code")
        if not code:
            continue
        raw = candidate.get("verdict")
        if raw is None:
            counters["missing"] += 1
            continue
        if isinstance(raw, str) and raw in mapping:
            scored = _finite(mapping[raw])
            if scored is None:
                counters["unknown_enum"] += 1
                continue
            values[str(code)] = scored
        elif isinstance(raw, str) and raw == "unrated" and policy == "skip":
            counters["unrated"] += 1
        else:
            counters["unknown_enum"] += 1
    expected_empty = (not values and len(ctx.codes) > 0
                      and counters["unrated"] == len(ctx.codes))
    return _frame(ctx, values, counters, expected_empty=expected_empty)


def compute_composite(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    counters = {"missing": 0}
    for candidate in _candidates(ctx):
        code = candidate.get("code")
        if not code:
            continue
        score = _finite(candidate.get("composite_score"))
        if score is None:
            counters["missing"] += 1
            continue
        values[str(code)] = score
    return _frame(ctx, values, counters)


# ─── T 日事实 ──────────────────────────────────────────────────────────────


def _facts(candidate: dict) -> dict | None:
    facts = candidate.get("facts")
    return facts if isinstance(facts, dict) else None


def compute_t_pct(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    counters = {"missing": 0, "missing_facts": 0}
    for candidate in _candidates(ctx):
        code = candidate.get("code")
        if not code:
            continue
        facts = _facts(candidate)
        if facts is None:
            counters["missing_facts"] += 1
            continue
        number = _finite(facts.get("t_pct"))
        if number is None:
            counters["missing"] += 1
            continue
        values[str(code)] = number
    return _frame(ctx, values, counters)


def compute_t_turnover(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    counters = {"missing": 0, "missing_facts": 0}
    for candidate in _candidates(ctx):
        code = candidate.get("code")
        if not code:
            continue
        facts = _facts(candidate)
        if facts is None:
            counters["missing_facts"] += 1
            continue
        number = _finite(facts.get("t_turnover"))
        if number is None:
            counters["missing"] += 1
            continue
        values[str(code)] = number
    return _frame(ctx, values, counters)


def compute_limit_up_suc_rate(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    counters = {"missing": 0, "missing_facts": 0}
    for candidate in _candidates(ctx):
        code = candidate.get("code")
        if not code:
            continue
        facts = _facts(candidate)
        if facts is None:
            counters["missing_facts"] += 1
            continue
        rate = _finite(facts.get("limit_up_suc_rate"))
        if rate is None:
            counters["missing"] += 1
            continue
        values[str(code)] = rate
    return _frame(ctx, values, counters)


def compute_high_days(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    counters = {"missing": 0, "missing_facts": 0, "unparsed_high_days": 0}
    for candidate in _candidates(ctx):
        code = candidate.get("code")
        if not code:
            continue
        facts = _facts(candidate)
        if facts is None:
            counters["missing_facts"] += 1
            continue
        high_days = facts.get("high_days")
        if high_days is None:
            counters["missing"] += 1
            continue
        match = _HIGH_DAYS_RE.search(str(high_days))
        if not match:
            # 「首板」等异常文本不猜 1（方案 §15.1.1）。
            counters["unparsed_high_days"] += 1
            continue
        values[str(code)] = float(int(match.group(2)))
    return _frame(ctx, values, counters)


def compute_in_pool_today(ctx: FactorContext) -> FactorFrame:
    values: dict[str, float] = {}
    counters = {"missing": 0, "missing_facts": 0, "pool_unavailable": 0}
    for candidate in _candidates(ctx):
        code = candidate.get("code")
        if not code:
            continue
        facts = _facts(candidate)
        if facts is None:
            counters["missing_facts"] += 1
            continue
        if facts.get("pool_available") is not True:
            # 池缺失时 in_pool_today=False 不代表「没进池」，而是「无法判断」。
            counters["pool_unavailable"] += 1
            continue
        flag = facts.get("in_pool_today")
        if not isinstance(flag, bool):
            counters["missing"] += 1
            continue
        values[str(code)] = 1.0 if flag else 0.0
    expected_empty = (not values and len(ctx.codes) > 0
                      and counters["pool_unavailable"] == len(ctx.codes))
    return _frame(ctx, values, counters, expected_empty=expected_empty)


_SPEC_FIELDS = dict(
    frequency="1d",
    source=SOURCE,
    available_phase="prep",
    pit_rule="derived",
    default_enabled=False,
    owner="factors",
)

SPECS: tuple[FactorSpec, ...] = (
    FactorSpec(
        name="prep_verdict", label="复盘评级", compute=compute_verdict,
        impl_key=f"{IMPL_PREFIX}.verdict.v1",
        direction="higher_better", unit="score",
        params={"mapping": _VERDICT_MAPPING, "unrated_policy": _UNRATED_POLICY},
        description="prep_snapshots.candidates[].verdict 档位序（strong=3/light=2/ignore=0，unrated 不写行）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="prep_composite", label="复盘综合分", compute=compute_composite,
        impl_key=f"{IMPL_PREFIX}.composite.v1",
        direction="higher_better", unit="score",
        description="prep_snapshots.candidates[].composite_score（候选入选日五维综合分，落快照日）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="prep_high_days", label="T 日连板高度", compute=compute_high_days,
        impl_key=f"{IMPL_PREFIX}.high_days.v1",
        direction="higher_better", unit="count",
        description='facts.high_days 字符串按 "(\\d+)天(\\d+)板" 解析取板数',
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="prep_t_pct", label="T 日涨幅", compute=compute_t_pct,
        impl_key=f"{IMPL_PREFIX}.t_pct.v1",
        direction="higher_better", unit="pct",
        description="facts.t_pct（百分数，非 0-1）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="prep_t_turnover", label="T 日换手率", compute=compute_t_turnover,
        impl_key=f"{IMPL_PREFIX}.t_turnover.v1",
        direction="higher_better", unit="pct",
        description="facts.t_turnover（百分数）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="prep_limit_up_suc_rate", label="封板成功率", compute=compute_limit_up_suc_rate,
        impl_key=f"{IMPL_PREFIX}.limit_up_suc_rate.v1",
        direction="higher_better", unit="ratio",
        description="facts.limit_up_suc_rate（0-1 比率，展示层 ×100）",
        **_SPEC_FIELDS,
    ),
    FactorSpec(
        name="prep_in_pool_today", label="T 日涨停池成员", compute=compute_in_pool_today,
        impl_key=f"{IMPL_PREFIX}.in_pool_today.v1",
        direction="neutral", unit="flag",
        description="facts.in_pool_today → 1/0；仅 pool_available is True 时写行",
        **_SPEC_FIELDS,
    ),
)
