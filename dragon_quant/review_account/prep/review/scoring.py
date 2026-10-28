"""六维复盘分：纯函数评分骨架。

输入只有普通 ``dict`` / ``list`` / 标量，输出为普通 ``dict``；本模块**无 I/O**：

* 不读文件 / 数据库 / 环境变量 / 时钟；
* 不发网络请求；
* 不 import ``sqlite3``、``os``、``pathlib`` 等副作用模块。

设计模式：

* Strategy —— 角色权重由 :mod:`config` 的六个 profile 提供，评分算法与角色解耦；
* Template Method —— 每个维度都走 ``_finalize_dim``（子分归一 → 维分 → 缺失剔除），
  维度之间只替换「子分如何算」。
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .config import CONFIG, DIMENSIONS, ReviewConfig, weight_profile_for

_MIN_SCORE = 0.0
_MAX_SCORE = 100.0


# ---------------------------------------------------------------------------
# 标量工具
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    """只接受有限数值；``bool`` / 字符串 / ``None`` 一律视为缺失。"""

    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _clamp_score(value: float) -> float:
    return max(_MIN_SCORE, min(_MAX_SCORE, value))


def _parse_hhmm(value: Any) -> float | None:
    """把 ``HH:MM`` / ``HH:MM:SS`` 解析为「当日分钟数」，非法返回 ``None``。"""

    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return hour * 60 + minute + second / 60.0


def _finalize_dim(
    weights: Mapping[str, float],
    scores: Mapping[str, float | None],
    raws: Mapping[str, Any],
    min_weight: float,
    extra_sub: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """维度聚合（Template Method）：剩余子权重归一；不足门槛则整维 null。"""

    available = {key: weight for key, weight in weights.items() if scores.get(key) is not None}
    total_weight = sum(available.values())
    if total_weight < min_weight:
        score: float | None = None
    else:
        score = sum(available[key] * float(scores[key]) for key in available) / total_weight
    return {
        "score": score,
        "sub": dict(scores),
        "raw": dict(raws),
        "available_weight": total_weight,
        "extra_sub": dict(extra_sub or {}),
    }


# ---------------------------------------------------------------------------
# D1 龙头质量
# ---------------------------------------------------------------------------


def _compute_d1(cand: Mapping[str, Any], rule: Mapping[str, Any], min_weight: float) -> dict[str, Any]:
    height = _num(cand.get("height"))
    height_cap = float(rule["height_cap"])
    h_score = None
    if height is not None and height >= 0:
        h_score = _clamp_score(min(height, height_cap) / height_cap * 100.0)

    rank = _num(cand.get("intra_board_rank"))
    board_count = _num(cand.get("board_limit_up_num"))
    l_score = None
    if rank is not None and board_count is not None and rank >= 1 and board_count >= 1:
        l_score = _clamp_score(
            (1.0 - (rank - 1.0) / max(board_count - 1.0, 1.0)) * 100.0
        )

    board_cap = float(rule["board_limit_up_num_cap"])
    b_score = None
    if board_count is not None and board_count >= 0:
        b_score = _clamp_score(min(board_count / board_cap, 1.0) * 100.0)

    return _finalize_dim(
        rule["weights"],
        {"H": h_score, "L": l_score, "B": b_score},
        {"H": height, "L": rank, "B": board_count},
        min_weight,
    )


# ---------------------------------------------------------------------------
# D2 主线地位
# ---------------------------------------------------------------------------


def _compute_d2(cand: Mapping[str, Any], rule: Mapping[str, Any], min_weight: float) -> dict[str, Any]:
    rank = _num(cand.get("mainline_rank"))
    m1_score = None
    if rank is not None and rank == int(rank):
        m1_score = float(
            rule["mainline_rank_scores"].get(
                int(rank), rule["mainline_rank_non_mainline_score"]
            )
        )

    attr_match = _num(cand.get("attr_match"))
    attr_cap = float(rule["attr_match_cap"])
    m2_score = None
    if attr_match is not None:
        m2_score = _clamp_score(min(attr_match, attr_cap) / attr_cap * 100.0)

    days = _num(cand.get("mainline_days"))
    days_cap = float(rule["mainline_days_cap"])
    m3_score = None
    if days is not None:
        m3_score = _clamp_score(min(days, days_cap) / days_cap * 100.0)

    return _finalize_dim(
        rule["weights"],
        {"M1": m1_score, "M2": m2_score, "M3": m3_score},
        {"M1": rank, "M2": attr_match, "M3": days},
        min_weight,
    )


# ---------------------------------------------------------------------------
# D3 封板质量
# ---------------------------------------------------------------------------


def _seal_time_score(value: Any, rule: Mapping[str, Any]) -> float | None:
    minutes = _parse_hhmm(value)
    if minutes is None:
        return None
    # 午间休市不可能首封；落在 11:30–13:00 的值按上午收盘桶处理，避免落进下午桶。
    if 11 * 60 + 30 < minutes < 13 * 60:
        minutes = 11 * 60 + 30
    for bound_text, score in rule["seal_time_buckets"]:
        bound = _parse_hhmm(bound_text)
        if bound is not None and minutes <= bound:
            return float(score)
    return float(rule["seal_time_after_score"])


def _compute_d3(cand: Mapping[str, Any], rule: Mapping[str, Any], min_weight: float) -> dict[str, Any]:
    t_score = _seal_time_score(cand.get("first_seal_time"), rule)

    open_num = _num(cand.get("open_num"))
    o_score = None
    if open_num is not None and open_num >= 0 and open_num == int(open_num):
        o_score = float(
            rule["open_num_scores"].get(int(open_num), rule["open_num_cap_score"])
        )

    seal_amount = _num(cand.get("seal_amount"))
    float_cap = _num(cand.get("float_market_cap"))
    seal_ratio_raw = None
    s_score = None
    if seal_amount is not None and float_cap is not None and seal_amount >= 0 and float_cap > 0:
        seal_ratio_raw = seal_amount / float_cap
        s_score = _clamp_score(
            min(seal_ratio_raw / float(rule["seal_amount_to_float_cap"]), 1.0) * 100.0
        )

    ratio = _num(cand.get("seal_ratio"))
    amount = _num(cand.get("amount"))
    r_raw = None
    if ratio is not None and ratio >= 0:
        r_raw = ratio
    elif seal_amount is not None and amount is not None and amount > 0 and seal_amount >= 0:
        r_raw = seal_amount / amount
    r_score = None
    if r_raw is not None:
        r_score = _clamp_score(min(r_raw / float(rule["seal_ratio_cap"]), 1.0) * 100.0)

    return _finalize_dim(
        rule["weights"],
        {"T": t_score, "O": o_score, "S": s_score, "R": r_score},
        {
            "T": cand.get("first_seal_time"),
            "O": open_num,
            "S": seal_ratio_raw,
            "R": r_raw,
        },
        min_weight,
        extra_sub={"S_raw": seal_ratio_raw},
    )


# ---------------------------------------------------------------------------
# D4 资金与流动性
# ---------------------------------------------------------------------------


def _turnover_score(turnover: float, rule: Mapping[str, Any]) -> float:
    low = float(rule["turnover_low"])
    healthy_low = float(rule["turnover_healthy_low"])
    healthy_high = float(rule["turnover_healthy_high"])
    high = float(rule["turnover_high"])
    low_score = float(rule["turnover_low_score"])
    start_score = float(rule["turnover_start_score"])
    peak_score = float(rule["turnover_peak_score"])
    end_score = float(rule["turnover_end_score"])
    very_high_score = float(rule["turnover_very_high_score"])

    if turnover < low:
        return low_score
    if turnover <= healthy_low:
        return start_score + (turnover - low) / (healthy_low - low) * (peak_score - start_score)
    if turnover <= healthy_high:
        return peak_score
    if turnover <= high:
        return peak_score + (turnover - healthy_high) / (high - healthy_high) * (
            end_score - peak_score
        )
    return very_high_score


def _compute_d4(cand: Mapping[str, Any], rule: Mapping[str, Any], min_weight: float) -> dict[str, Any]:
    main_net = _num(cand.get("main_net_amount"))
    amount = _num(cand.get("amount"))
    f_score = None
    f_raw = None
    if main_net is not None and amount is not None and amount > 0:
        f_raw = main_net / amount
        f_score = _clamp_score(
            float(rule["main_net_ratio_base"]) + f_raw * float(rule["main_net_ratio_slope"])
        )

    v_score = None
    if amount is not None and amount >= 0:
        v_score = _clamp_score(min(amount / float(rule["amount_cap"]), 1.0) * 100.0)

    turnover = _num(cand.get("turnover_rate"))
    q_score = None
    if turnover is not None and turnover >= 0:
        q_score = _clamp_score(_turnover_score(turnover, rule))

    return _finalize_dim(
        rule["weights"],
        {"F": f_score, "V": v_score, "Q": q_score},
        {"F": f_raw, "V": amount, "Q": turnover},
        min_weight,
    )


# ---------------------------------------------------------------------------
# D5 筹码 / 位置
# ---------------------------------------------------------------------------


def _piecewise(value: float, start_x: float, end_x: float, start_y: float, end_y: float) -> float:
    if end_x == start_x:  # 配置退化时退化为常数，避免 ZeroDivisionError
        return start_y
    return start_y + (value - start_x) / (end_x - start_x) * (end_y - start_y)


def _compute_d5(cand: Mapping[str, Any], rule: Mapping[str, Any], min_weight: float) -> dict[str, Any]:
    drawdown_rule = rule["drawdown"]
    drawdown = _num(cand.get("distance_high60"))
    p_score = None
    if drawdown is not None:
        drawdown = max(drawdown, 0.0)
        if drawdown <= float(drawdown_rule["full_at"]):
            p_score = float(drawdown_rule["full"])
        elif drawdown <= float(drawdown_rule["mid_at"]):
            p_score = _piecewise(
                drawdown,
                float(drawdown_rule["full_at"]),
                float(drawdown_rule["mid_at"]),
                float(drawdown_rule["full"]),
                float(drawdown_rule["mid"]),
            )
        elif drawdown <= float(drawdown_rule["low_at"]):
            p_score = _piecewise(
                drawdown,
                float(drawdown_rule["mid_at"]),
                float(drawdown_rule["low_at"]),
                float(drawdown_rule["mid"]),
                float(drawdown_rule["low"]),
            )
        else:
            p_score = float(drawdown_rule["floor"])

    concentration_rule = rule["concentration"]
    concentration = _num(cand.get("chip_concentration"))
    c_score = None
    if concentration is not None:
        concentration = max(concentration, 0.0)
        if concentration <= float(concentration_rule["full_below"]):
            c_score = float(concentration_rule["full"])
        elif concentration <= float(concentration_rule["mid_at"]):
            c_score = _piecewise(
                concentration,
                float(concentration_rule["full_below"]),
                float(concentration_rule["mid_at"]),
                float(concentration_rule["full"]),
                float(concentration_rule["mid"]),
            )
        elif concentration <= float(concentration_rule["floor_at"]):
            # 产品 §8.1 未写 20–30%，按连续插值 60→20 补齐（见模块文档 / 交付报告）。
            c_score = _piecewise(
                concentration,
                float(concentration_rule["mid_at"]),
                float(concentration_rule["floor_at"]),
                float(concentration_rule["mid"]),
                float(concentration_rule["floor"]),
            )
        else:
            c_score = float(concentration_rule["floor"])

    profit_rule = rule["profit"]
    profit = _num(cand.get("profit_ratio"))
    x_score = None
    if profit is not None:
        if profit < 0:
            x_score = float(profit_rule["below"])
        elif profit <= float(profit_rule["peak_until"]):
            x_score = float(profit_rule["peak"])
        elif profit <= float(profit_rule["mid_at"]):
            x_score = _piecewise(
                profit,
                float(profit_rule["high_at"]),
                float(profit_rule["mid_at"]),
                float(profit_rule["high"]),
                float(profit_rule["mid"]),
            )
        elif profit <= float(profit_rule["low_at"]):
            # 产品 §8.1 未写 25–40%，按连续插值 50→20 补齐（见模块文档 / 交付报告）。
            x_score = _piecewise(
                profit,
                float(profit_rule["mid_at"]),
                float(profit_rule["low_at"]),
                float(profit_rule["mid"]),
                float(profit_rule["low"]),
            )
        else:
            x_score = float(profit_rule["low"])

    return _finalize_dim(
        rule["weights"],
        {"P": p_score, "C": c_score, "X": x_score},
        {"P": drawdown, "C": concentration, "X": profit},
        min_weight,
    )


# ---------------------------------------------------------------------------
# D6 持续性 / 股性
# ---------------------------------------------------------------------------


def _compute_d6(cand: Mapping[str, Any], rule: Mapping[str, Any], min_weight: float) -> dict[str, Any]:
    count = _num(cand.get("limit_up_cnt_20d"))
    s1_score = None
    if count is not None and count >= 0 and count == int(count):
        s1_score = float(
            rule["limit_up_count_scores"].get(int(count), rule["limit_up_count_cap_score"])
        )

    rate = _num(cand.get("next_day_up_rate_20d"))
    sample = _num(cand.get("next_day_up_sample"))
    s2_score = None
    # 未知不猜：样本数必须显式存在且 >= 3 才计算 S2；缺失或 < 3 一律 null。
    if (
        rate is not None
        and sample is not None
        and sample >= float(rule["next_day_up_sample_min"])
    ):
        s2_score = _clamp_score(rate * 100.0)

    return _finalize_dim(
        rule["weights"],
        {"S1": s1_score, "S2": s2_score},
        {"S1": count, "S2": rate},
        min_weight,
    )


_DIM_BUILDERS = {
    "D1": _compute_d1,
    "D2": _compute_d2,
    "D3": _compute_d3,
    "D4": _compute_d4,
    "D5": _compute_d5,
    "D6": _compute_d6,
}


# ---------------------------------------------------------------------------
# 评级、置信度与总分
# ---------------------------------------------------------------------------


def grade_for_score(score: float | None, config: ReviewConfig = CONFIG) -> str | None:
    """按 S/A/B/C/D 档位返回评级；分数缺失返回 ``None``。"""

    if score is None:
        return None
    threshold_s, threshold_a, threshold_b, threshold_c = config.grade_thresholds
    if score >= threshold_s:
        return "S"
    if score >= threshold_a:
        return "A"
    if score >= threshold_b:
        return "B"
    if score >= threshold_c:
        return "C"
    return "D"


def _source_health(ctx: Mapping[str, Any], config: ReviewConfig) -> float:
    """解析数据源健康度；显式值优先，其次按降级数量 / 涨停池可用性推导。"""

    bands = config.source_health_bands
    explicit = _num(ctx.get("source_health"))
    if explicit is not None:
        return max(0.0, min(1.0, explicit))
    if ctx.get("limit_up_pool_available") is False:
        return float(bands["basis_missing"])
    degraded = ctx.get("degraded")
    if isinstance(degraded, (list, tuple)):
        count = len(degraded)
        if count <= 0:
            return float(bands["all"])
        if count == 1:
            return float(bands["degraded_1"])
        return float(bands["degraded_2"])
    return float(bands["all"])


def _render_sub(dim_result: Mapping[str, Any]) -> dict[str, Any]:
    rendered: dict[str, Any] = {
        key: (None if value is None else round(float(value), 1))
        for key, value in dim_result["sub"].items()
    }
    for key, value in dim_result["extra_sub"].items():
        rendered[key] = None if value is None else round(float(value), 4)
    return rendered


def _build_contributions(
    dim_results: Mapping[str, Mapping[str, Any]],
    available: list[str],
    weights_pct: Mapping[str, float],
    coverage: float,
    config: ReviewConfig,
) -> list[dict[str, Any]]:
    contributions: list[dict[str, Any]] = []
    for dim in DIMENSIONS:
        if dim not in available:
            continue
        raw_score = float(dim_results[dim]["score"])  # type: ignore[arg-type]
        weight_norm = (float(weights_pct[dim]) / 100.0) / coverage
        sub_weights = config.scoring_rules[dim]["weights"]
        available_weight = float(dim_results[dim]["available_weight"])
        sub_factors: list[dict[str, Any]] = []
        for key, sub_weight in sub_weights.items():
            normalized = dim_results[dim]["sub"].get(key)
            raw = dim_results[dim]["raw"].get(key)
            if normalized is None or available_weight <= 0:
                points = None
            else:
                points = round(weight_norm * (float(sub_weight) / available_weight) * normalized, 2)
            sub_factors.append(
                {
                    "key": key,
                    "raw": raw,
                    "normalized": None if normalized is None else round(float(normalized), 1),
                    "points": points,
                }
            )
        contributions.append(
            {
                "dim": dim,
                "weight_norm": round(weight_norm, 4),
                "dim_score": round(raw_score, 1),
                "points": round(weight_norm * raw_score, 2),
                "sub_factors": sub_factors,
            }
        )
    return contributions


def rank_drivers_drags(
    contributions: list[dict[str, Any]], config: ReviewConfig = CONFIG
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按维度 ``points`` 确定性排序出 top_drivers / top_drags（各至多 2 条）。

    排序规则：drivers 按 points 降序、drags 按 points 升序；**并列按
    ``DIMENSIONS`` 顺序**（D1→D6）。空贡献（总分 null / 数据不足）返回两个空列表。
    """

    if not contributions:
        return [], []
    order = {dim: index for index, dim in enumerate(DIMENSIONS)}
    indexed = [
        (order.get(str(item.get("dim")), len(DIMENSIONS)), item)
        for item in contributions
    ]

    def _entry(item: Mapping[str, Any]) -> dict[str, Any]:
        dim = str(item["dim"])
        label = config.dimension_labels.get(dim, dim)
        points = float(item["points"])
        score = float(item["dim_score"])
        return {
            "dim": dim,
            "points": points,
            "reason": f"{label} {score:.1f}（贡献 {points:.2f} 分）",
        }

    drivers = sorted(indexed, key=lambda pair: (-float(pair[1]["points"]), pair[0]))[:2]
    drags = sorted(indexed, key=lambda pair: (float(pair[1]["points"]), pair[0]))[:2]
    return [_entry(item) for _, item in drivers], [_entry(item) for _, item in drags]


def score_candidate(
    cand: dict,
    ctx: dict | None = None,
    config: ReviewConfig = CONFIG,
) -> dict:
    """对单只候选做六维复盘评分（纯函数）。

    ``cand`` 约定字段（缺失 / ``None`` = 该子项 unknown，绝不填 0/50）：

    ====== ===========================================================
    维度   字段
    ====== ===========================================================
    D1     ``height``、``intra_board_rank``、``board_limit_up_num``
    D2     ``mainline_rank``（0 = 非主线）、``attr_match``、``mainline_days``
    D3     ``first_seal_time``、``open_num``、``seal_amount``、
           ``float_market_cap``、``seal_ratio``（缺省时用 ``seal_amount/amount``）
    D4     ``main_net_amount``、``amount``、``turnover_rate``
    D5     ``distance_high60``（百分比）、``chip_concentration``、``profit_ratio``
    D6     ``limit_up_cnt_20d``、``next_day_up_rate_20d``、``next_day_up_sample``
    ====== ===========================================================

    ``ctx`` 约定：``role``（None = 诊断 profile）、``source_health`` /
    ``degraded`` / ``limit_up_pool_available``（置信度）。本函数不消费
    ``composite_score``。

    返回键：``dimensions`` / ``contributions`` / ``top_drivers`` /
    ``top_drags`` / ``coverage`` / ``confidence`` / ``review_score`` /
    ``review_grade`` / ``missing_dims`` / ``weight_profile``。
    """

    if not isinstance(cand, dict):
        raise TypeError("cand must be a dict")
    context: Mapping[str, Any] = ctx if isinstance(ctx, Mapping) else {}
    profile = weight_profile_for(context.get("role"))
    weights_pct = config.role_weights[profile]

    dim_results: dict[str, dict[str, Any]] = {}
    for dim in DIMENSIONS:
        builder = _DIM_BUILDERS[dim]
        dim_results[dim] = builder(cand, config.scoring_rules[dim], config.sub_weight_min)

    dimensions_out: dict[str, Any] = {}
    available: list[str] = []
    for dim in DIMENSIONS:
        raw_score = dim_results[dim]["score"]
        dimensions_out[dim] = {
            "score": None if raw_score is None else round(float(raw_score), 1),
            "weight": round(float(weights_pct[dim]) / 100.0, 4),
            "sub": _render_sub(dim_results[dim]),
        }
        if raw_score is not None:
            available.append(dim)

    coverage = sum(float(weights_pct[dim]) for dim in available) / 100.0
    coverage = round(coverage, 4)

    if coverage < config.coverage_min:
        review_score: float | None = None
        contributions: list[dict[str, Any]] = []
    else:
        weighted = sum(float(weights_pct[dim]) / 100.0 * float(dim_results[dim]["score"]) for dim in available)
        review_score = round(weighted / coverage, 1)
        contributions = _build_contributions(
            dim_results, available, weights_pct, coverage, config
        )

    confidence = round(coverage * _source_health(context, config), 2)
    top_drivers, top_drags = rank_drivers_drags(contributions, config)

    return {
        "dimensions": dimensions_out,
        "contributions": contributions,
        "top_drivers": top_drivers,
        "top_drags": top_drags,
        "coverage": coverage,
        "confidence": confidence,
        "review_score": review_score,
        "review_grade": grade_for_score(review_score, config),
        "missing_dims": [dim for dim in DIMENSIONS if dim not in available],
        "weight_profile": profile,
    }


__all__ = ["score_candidate", "grade_for_score", "rank_drivers_drags"]
