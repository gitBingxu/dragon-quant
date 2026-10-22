"""P2 决策接入：``FactorModel`` 协议 + ``EqualWeightRankModel`` + ``annotate_pool``。

边界（方案 §1.3-D1、§3.4、§4.4、§4.5）：

- 本模块只做「整池打分 + 候选注解」，**不加过滤、不加否决**；五维仍是唯一决策基线。
- 候选池是跨日并集，每只候选按**自己的** ``trade_date`` / ``candidate_trade_date``
  读取因子值；归一分组 = 同一 ``candidate_trade_date`` 的 cohort（跨日绝对因子值
  不可比，组内 rank 归一后同分布才可比）。
- 缺失 ≠ 中性：任一必需因子无值的候选**不得分**（``score=None`` → 排序层
  ``factor_rank=1`` 桶）；``n_cohort < 3`` 整组缺失并记 ``small_cohorts``。
- 模型快照（``factor_model_snapshots``）是复现一次决策的唯一真值；无快照 →
  整日 ``degraded``（``model_snapshot_missing``），**绝不隐式兜底默认模型**。
- 分层依赖：本模块不 import ``scorers/*`` / ``review_account/*`` / ``providers/*``；
  对 ``review_account`` 的接入由对方函数内惰性 import。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Protocol, Sequence, runtime_checkable

from dragon_quant.factors import storage as fstore

CROSS_SECTION = "pool_mixed_vintage_by_candidate_date"
DEFAULT_MODEL_NAME = "equal_rank_v1"
DEFAULT_MODEL_VERSION = "v1.0.0"
MIN_COHORT = 3
NORMALIZATION = "rank_pct_by_candidate_date"

DEGRADED_MODEL_MISSING = "model_snapshot_missing"
DEGRADED_MODEL_READ_ERROR = "model_snapshot_read_error"
DEGRADED_FACTOR_READ_ERROR = "factor_read_error"
DEGRADED_BELOW_MIN_COVERAGE = "factor_coverage_below_min"
DEGRADED_DATE_FALLBACK = "factor_date_fallback"
DEGRADED_EMPTY_POOL = "factor_pool_empty"
DEGRADED_NO_REQUIRED_FACTORS = "model_no_required_factors"


@dataclass(frozen=True)
class PoolItem:
    """决策池中的单只候选（``factor_date`` 是该候选自己的入选日）。"""

    code: str
    factor_date: str
    values: Mapping[str, float]


@dataclass(frozen=True)
class PoolInput:
    decision_date: str
    items: tuple[PoolItem, ...]


@runtime_checkable
class FactorModel(Protocol):
    """因子模型协议（方案 §4.4）；持久化只走 JSON 快照，禁止 pickle。"""

    name: str
    version: str

    def fit(self, panel) -> "FactorModel":  # 规则模型无状态，直接 return self
        ...

    def score(self, pool: PoolInput) -> dict[str, float]:
        ...

    def snapshot(self) -> dict:
        ...


def candidate_factor_date(candidate: Mapping, decision_date: str) -> str:
    """候选自己的因子读取日：``trade_date`` → ``candidate_trade_date`` → 决策日。

    兜底分支正常不会走到（两条现有池来源都带 ``trade_date``）；触发时调用方必须
    计入 ``factor_degraded``（按决策日读会读到 T 日盘中尚不存在的因子）。
    """
    if isinstance(candidate, Mapping):
        value = candidate.get("trade_date") or candidate.get("candidate_trade_date")
    else:
        value = None
    return str(value) if value else str(decision_date)


def _finite(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def average_ranks(values: Sequence[float]) -> list[float]:
    """并列取平均名次的 0 基秩（``scipy`` 不可用；O(n log n)）。"""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while (end + 1 < len(order)
               and values[order[end + 1]] == values[order[position]]):
            end += 1
        rank = (position + end) / 2.0          # 0 基：位置 position..end 的均值
        for index in range(position, end + 1):
            ranks[order[index]] = rank
        position = end + 1
    return ranks


class EqualWeightRankModel:
    """等权 rank 归一模型（P2 唯一实现）。

    ``factor_versions`` 声明模型成员与版本；``params.weights`` 给出权重。
    ``neutral`` 方向（以及权重 0）的因子不参与打分、也不算「必需因子」——
    要求中性因子有值只会把缺失噪声引入必需集合。
    """

    name = DEFAULT_MODEL_NAME

    def __init__(self, *, factor_versions: Mapping[str, str],
                 weights: Optional[Mapping[str, float]] = None,
                 lower_better: Sequence[str] = (),
                 neutral: Sequence[str] = (),
                 min_cohort: int = MIN_COHORT,
                 model_version: str = DEFAULT_MODEL_VERSION) -> None:
        self.factor_versions = {str(name): str(ver)
                                for name, ver in dict(factor_versions).items()}
        raw_weights = dict(weights or {})
        self.weights = {
            name: float(raw_weights.get(name, 1.0))
            for name in self.factor_versions
        }
        self.lower_better = frozenset(str(name) for name in lower_better)
        self.neutral = frozenset(
            {str(name) for name in neutral}
            | {name for name, weight in self.weights.items() if weight <= 0})
        self.min_cohort = int(min_cohort)
        if self.min_cohort < 2:
            raise ValueError("min_cohort 必须 ≥ 2（1 只截面的 rank 无意义）")
        self.version = str(model_version)
        self.last_meta: dict = {"small_cohorts": [], "missing_factor_counts": {},
                                "cohorts": 0, "scored": 0, "degraded": []}

    # ── 成员 ───────────────────────────────────────────────────────────────
    @property
    def required_factors(self) -> tuple[str, ...]:
        return tuple(name for name in self.factor_versions
                     if name not in self.neutral and self.weights.get(name, 0) > 0)

    @classmethod
    def from_snapshot(cls, snapshot: Mapping) -> "EqualWeightRankModel":
        """从快照重建模型；结构非法直接抛 ``ValueError``（由调用方降级）。

        快照是复现一次决策的唯一真值：``factor_versions`` 与 ``weights`` 键集合
        必须一致、版本非空、``min_cohort`` 为 ≥2 的整数。缺省/多余条目不被静默
        补 1.0 或丢弃，否则「跑了哪个模型」会漂移。
        """
        if not isinstance(snapshot, Mapping):
            raise ValueError("模型快照必须是对象")
        params = snapshot.get("params")
        if not isinstance(params, Mapping):
            raise ValueError("模型快照 params 必须是对象")
        factor_versions = snapshot.get("factor_versions")
        if not isinstance(factor_versions, Mapping) or not factor_versions:
            raise ValueError("factor_versions 必须是非空对象")
        for name, version in factor_versions.items():
            if not isinstance(name, str) or not name:
                raise ValueError("factor_versions 的因子名必须是非空字符串")
            if not isinstance(version, str) or not version:
                raise ValueError(f"factor_versions[{name}] 必须是非空字符串")

        weights = params.get("weights", {})
        if not isinstance(weights, Mapping):
            raise ValueError("weights 必须是对象")
        unknown = set(weights) - set(factor_versions)
        if unknown:
            raise ValueError(f"weights 含未声明因子: {sorted(unknown)}")
        missing = set(factor_versions) - set(weights)
        if missing:
            raise ValueError(f"weights 缺少已声明因子: {sorted(missing)}")
        for name, weight in weights.items():
            if _finite(weight) is None:
                raise ValueError(f"weights[{name}] 必须是有限数值")

        lower_better = params.get("lower_better") or ()
        neutral = params.get("neutral") or ()
        for label, values in (("lower_better", lower_better),
                              ("neutral", neutral)):
            if not isinstance(values, (list, tuple)):
                raise ValueError(f"{label} 必须是数组")
            undeclared = [value for value in values
                          if value not in factor_versions]
            if undeclared:
                raise ValueError(f"{label} 含未声明因子: {undeclared}")

        min_cohort = params.get("min_cohort", MIN_COHORT)
        if isinstance(min_cohort, bool) or not isinstance(min_cohort, int):
            raise ValueError("min_cohort 必须是整数")
        return cls(
            factor_versions=factor_versions,
            weights=weights,
            lower_better=lower_better,
            neutral=neutral,
            min_cohort=min_cohort,
            model_version=snapshot.get("model_version") or DEFAULT_MODEL_VERSION,
        )

    def snapshot(self) -> dict:
        return {
            "model_name": self.name,
            "model_version": self.version,
            "params": {
                "weights": dict(self.weights),
                "lower_better": sorted(self.lower_better),
                "neutral": sorted(self.neutral),
                "min_cohort": self.min_cohort,
                "normalization": NORMALIZATION,
            },
            "factor_versions": dict(self.factor_versions),
        }

    # ── 协议方法 ───────────────────────────────────────────────────────────
    def fit(self, panel=None) -> "EqualWeightRankModel":
        return self

    def score(self, pool: PoolInput) -> dict[str, float]:
        return self.score_with_meta(pool)[0]

    def score_with_meta(self, pool: PoolInput) -> tuple[dict[str, float], dict]:
        """整池打分；返回 ``(code -> 0-100, meta)``。

        每组（同一 ``factor_date``）样本 < ``min_cohort`` → 整组缺失；
        候选任一必需因子无值 → 该候选不得分；跨组得分同分布（0-100）。
        """
        groups: dict[str, list[PoolItem]] = {}
        for item in pool.items:
            groups.setdefault(item.factor_date, []).append(item)
        required = self.required_factors
        small_cohorts: list[dict] = []
        missing_counts = {name: 0 for name in required}
        scores: dict[str, float] = {}
        degraded: list[str] = []
        if not required:
            degraded.append(DEGRADED_NO_REQUIRED_FACTORS)
            self.last_meta = {"small_cohorts": [], "missing_factor_counts": {},
                              "cohorts": len(groups), "scored": 0,
                              "degraded": degraded}
            return {}, dict(self.last_meta)

        total_weight = sum(self.weights[name] for name in required)
        for factor_date in sorted(groups):
            items = groups[factor_date]
            ranked: list[int] = []
            for index, item in enumerate(items):
                eligible = True
                for name in required:
                    if _finite(item.values.get(name)) is None:
                        missing_counts[name] += 1
                        eligible = False
                if eligible:
                    ranked.append(index)
            # 缺失候选不参与截面秩（否则会把有分候选的 rank_pct 压到 <1）；
            # 可评分样本 < min_cohort → 整组缺失（1-2 个点的 rank 无意义）。
            if len(ranked) < self.min_cohort:
                small_cohorts.append({"date": factor_date, "n": len(ranked)})
                continue
            factor_ranks: dict[str, dict[int, float]] = {}
            for name in required:
                values = [_finite(items[index].values.get(name)) for index in ranked]
                ranks = average_ranks(values)
                denominator = len(ranked) - 1
                mapping: dict[int, float] = {}
                for index, rank in zip(ranked, ranks):
                    pct = rank / denominator if denominator else 0.0
                    if name in self.lower_better:
                        pct = 1.0 - pct
                    mapping[index] = pct
                factor_ranks[name] = mapping
            for index in ranked:
                raw = sum(self.weights[name] * factor_ranks[name][index]
                          for name in required) / total_weight
                value = 100.0 * raw
                if math.isfinite(value):
                    scores[items[index].code] = value

        self.last_meta = {
            "small_cohorts": small_cohorts,
            "missing_factor_counts": missing_counts,
            "cohorts": len(groups),
            "scored": len(scores),
            "degraded": degraded,
        }
        return scores, dict(self.last_meta)


# ─── 快照构造 ─────────────────────────────────────────────────────────────


def default_snapshot(*, names: Optional[Sequence[str]] = None,
                     model_version: str = DEFAULT_MODEL_VERSION,
                     exclude: Sequence[str] = ("five_dim_composite",)) -> dict:
    """按注册表 ``default_enabled`` 因子生成等权模型快照（显式 opt-in）。

    ``five_dim_composite`` 默认排除：它是五维诊断基线，与五个维度分同时入模会
    重复计数。生产/研究快照应通过 ``storage.save_model_snapshot`` 显式写入，
    本函数只提供可复现的起点。
    """
    from dragon_quant.factors.registry import compute_version_info, resolve_specs

    excluded = set(exclude or ())
    specs = resolve_specs(list(names) if names else None,
                          enabled_only=names is None)
    factor_versions: dict[str, str] = {}
    weights: dict[str, float] = {}
    lower_better: list[str] = []
    neutral: list[str] = []
    for spec in specs:
        if spec.name in excluded:
            continue
        version, _ = compute_version_info(spec)
        factor_versions[spec.name] = version
        if spec.direction == "neutral":
            weights[spec.name] = 0.0
            neutral.append(spec.name)
            continue
        weights[spec.name] = 1.0
        if spec.direction == "lower_better":
            lower_better.append(spec.name)
    return {
        "model_name": DEFAULT_MODEL_NAME,
        "model_version": model_version,
        "params": {
            "weights": weights,
            "lower_better": sorted(lower_better),
            "neutral": sorted(neutral),
            "min_cohort": MIN_COHORT,
            "normalization": NORMALIZATION,
        },
        "factor_versions": factor_versions,
    }


def save_default_model_snapshot(*, names: Optional[Sequence[str]] = None,
                                model_version: str = DEFAULT_MODEL_VERSION,
                                db_path=None) -> bool:
    """生成并写入默认等权模型快照；已存在同版本 → False。"""
    snapshot = default_snapshot(names=names, model_version=model_version)
    return fstore.save_model_snapshot(
        snapshot["model_name"], snapshot["model_version"],
        params=snapshot["params"],
        factor_versions=snapshot["factor_versions"], db_path=db_path)


# ─── 注解 ─────────────────────────────────────────────────────────────────


def _default_factor_loader(trade_date: str, names: Sequence[str],
                           codes: Sequence[str], versions: Mapping[str, str], *,
                           db_path=None) -> dict[str, dict[str, float]]:
    payload = fstore.get_day_values(
        trade_date, names=list(names), codes=list(codes),
        versions=dict(versions), db_path=db_path)
    return payload["values"]


def _factor_block(*, mode: str, score: Optional[float], model: FactorLike,
                  coverage: Optional[float], missing: bool,
                  degraded_reason: Optional[str]) -> dict:
    return {
        "mode": mode,
        "score": score,
        "model": model.name,
        "model_version": model.version,
        "coverage": coverage,
        "cross_section": CROSS_SECTION,
        "missing": bool(missing),
        "degraded_reason": degraded_reason,
    }


# 仅用于类型可读性（避免把 FactorModel 当值使用时的静态告警）。
FactorLike = FactorModel


def _annotate(candidates: Sequence[dict], *, mode: str, scores: dict[str, float],
              model: FactorLike, coverage: Optional[float],
              degraded_reason: Optional[str]) -> None:
    for candidate in candidates:
        code = candidate.get("code")
        missed = code not in scores
        block = _factor_block(
            mode=mode, score=(None if missed else scores.get(code)),
            model=model, coverage=coverage, missing=missed,
            degraded_reason=degraded_reason)
        candidate.setdefault("entry_priority", {})["factor"] = block


def annotate_degraded(candidates: Sequence[dict], *,
                      model_name: str = DEFAULT_MODEL_NAME,
                      model_version: str = "",
                      reason: Optional[str] = None) -> None:
    """整日降级注解（不读任何因子）：用于非 v2 池等「无法读取」场景。

    与 ``annotate_pool`` 的降级分支同形，保证 ``rank_key`` 的 ``factor_mode`` 判定
    只有一处语义来源。
    """
    for candidate in candidates:
        if not isinstance(candidate, dict) or not candidate.get("code"):
            continue
        candidate.setdefault("entry_priority", {})["factor"] = {
            "mode": "degraded", "score": None, "model": model_name,
            "model_version": model_version or DEFAULT_MODEL_VERSION,
            "coverage": None, "cross_section": CROSS_SECTION,
            "missing": True, "degraded_reason": reason,
        }


def annotate_pool(candidates: Sequence[dict], *, decision_date: str,
                  model_name: str = DEFAULT_MODEL_NAME,
                  model_version: str = "",
                  min_coverage: float = 0.6,
                  factor_loader=None, model: Optional[FactorLike] = None,
                  db_path=None) -> dict:
    """为整池候选写 ``entry_priority.factor`` 并返回上下文（in-place）。

    返回 ``{"factor_mode": "on"|"degraded", "factor_model": {...} | None,
    "factor_coverage": float | None, "factor_degraded": [str]}``。

    降级（整日排序回退，绝不中断回测/buy）：
    ``model_snapshot_missing`` / 快照或因子读取异常 / 候选缺日期（按决策日读取）/
    有分候选比例 < ``min_coverage``。降级时任何候选都不得分（``mode="degraded"``）。
    """
    degraded: list[str] = []
    pool = [cand for cand in candidates if isinstance(cand, dict) and cand.get("code")]
    if not pool:
        degraded.append(DEGRADED_EMPTY_POOL)
        return {"factor_mode": "degraded", "factor_model": None,
                "factor_coverage": None, "factor_degraded": degraded}

    # 1) 模型解析（显式注入的 model 优先；否则读快照，不兜底）
    if model is None:
        try:
            snapshot = fstore.get_model_snapshot(
                model_name, model_version or None, db_path=db_path)
        except Exception as exc:  # 读取异常 → 整日降级
            degraded.append(f"{DEGRADED_MODEL_READ_ERROR}: "
                            f"{type(exc).__name__}: {exc}")
            snapshot = None
        else:
            if snapshot is None:
                degraded.append(DEGRADED_MODEL_MISSING)
        if snapshot is None:
            annotate_degraded(pool, model_name=model_name,
                              model_version=model_version,
                              reason="；".join(degraded))
            return {"factor_mode": "degraded", "factor_model": None,
                    "factor_coverage": None, "factor_degraded": degraded}
        try:
            model = EqualWeightRankModel.from_snapshot(snapshot)
        except Exception as exc:  # 快照结构非法（半损坏 JSON）→ 整日降级
            degraded.append(f"{DEGRADED_MODEL_READ_ERROR}: invalid snapshot: "
                            f"{type(exc).__name__}: {exc}")
            annotate_degraded(pool,
                              model_name=snapshot.get("model_name") or model_name,
                              model_version=(snapshot.get("model_version")
                                             or model_version),
                              reason="；".join(degraded))
            return {"factor_mode": "degraded", "factor_model": None,
                    "factor_coverage": None, "factor_degraded": degraded}

    model_info = {
        "name": model.name, "version": model.version,
        "factors": sorted(model.required_factors),
    }

    # 2) 按每只候选自己的入选日分组读取因子值
    date_by_code: dict[str, str] = {}
    fallback_dates = 0
    for candidate in pool:
        raw = (candidate.get("trade_date")
               or candidate.get("candidate_trade_date"))
        factor_date = candidate_factor_date(candidate, decision_date)
        if not raw:
            fallback_dates += 1
        date_by_code[candidate["code"]] = factor_date

    names = sorted(model.factor_versions)
    by_date: dict[str, list[str]] = {}
    for candidate in pool:
        by_date.setdefault(date_by_code[candidate["code"]], []).append(
            candidate["code"])

    loader = factor_loader or _default_factor_loader
    values_by_code: dict[str, dict[str, float]] = {}
    try:
        for factor_date in sorted(by_date):
            codes = sorted(set(by_date[factor_date]))
            loaded = loader(factor_date, names, codes,
                            dict(model.factor_versions), db_path=db_path)
            for code, values in (loaded or {}).items():
                if isinstance(values, Mapping):
                    values_by_code[code] = {
                        str(name): number for name, number in values.items()
                        if _finite(number) is not None
                    }
    except Exception as exc:  # 读取失败 → 整日降级，不中断调用方
        degraded.append(f"{DEGRADED_FACTOR_READ_ERROR}: "
                        f"{type(exc).__name__}: {exc}")
        annotate_degraded(pool, model_name=model.name,
                          model_version=model.version,
                          reason="；".join(degraded))
        return {"factor_mode": "degraded", "factor_model": model_info,
                "factor_coverage": None, "factor_degraded": degraded}

    if fallback_dates:
        degraded.append(
            f"{DEGRADED_DATE_FALLBACK}: {fallback_dates} 只候选缺 trade_date，"
            "按决策日读取将引入盘中缺失/前视，整日降级")

    # 3) 整池打分（跨日 cohort 内 rank 归一）
    pool_input = PoolInput(
        decision_date=str(decision_date),
        items=tuple(PoolItem(code=candidate["code"],
                             factor_date=date_by_code[candidate["code"]],
                             values=values_by_code.get(candidate["code"], {}))
                    for candidate in pool))
    try:
        if hasattr(model, "score_with_meta"):
            scores, meta = model.score_with_meta(pool_input)
        else:
            scores, meta = model.score(pool_input), {}
    except Exception as exc:  # 打分异常同样只降级，绝不中断调用方
        degraded.append(f"{DEGRADED_FACTOR_READ_ERROR}: score failed: "
                        f"{type(exc).__name__}: {exc}")
        annotate_degraded(pool, model_name=model.name,
                          model_version=model.version,
                          reason="；".join(degraded))
        return {"factor_mode": "degraded", "factor_model": model_info,
                "factor_coverage": None, "factor_degraded": degraded}
    degraded.extend(str(item) for item in (meta.get("degraded") or []))
    # 小组（n<min_cohort）只记诊断，不触发整日降级：这些候选进入缺失桶，
    # 其余 cohort 仍按分排序（覆盖率门槛决定「整天是否用因子」）。
    model_info["small_cohorts"] = list(meta.get("small_cohorts") or [])

    coverage = (len(scores) / len(pool)) if pool else None
    if coverage is not None and coverage < float(min_coverage):
        degraded.append(
            f"{DEGRADED_BELOW_MIN_COVERAGE}: {coverage:.2f} < "
            f"{float(min_coverage):.2f}")

    mode = "degraded" if degraded else "on"
    reason = "；".join(degraded) if degraded else None
    _annotate(pool, mode=mode, scores=(scores if mode == "on" else {}),
              model=model, coverage=coverage, degraded_reason=reason)
    return {"factor_mode": mode, "factor_model": model_info,
            "factor_coverage": coverage, "factor_degraded": degraded}


def build_completion_rate(days: Sequence[str], *, db_path=None) -> Optional[float]:
    """区间内「有因子值」的交易日占比（A/B 前置门禁）。

    分母是传入的交易日列表（调用方给真实交易日历）；库缺失 → 0.0，绝不伪造 100%。
    """
    wanted = sorted({str(day) for day in days if day})
    if not wanted:
        return None
    covered = fstore.list_value_dates(wanted[0], wanted[-1], db_path=db_path)
    return len(set(wanted) & covered) / len(wanted)
