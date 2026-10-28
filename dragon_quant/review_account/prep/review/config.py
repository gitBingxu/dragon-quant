"""复盘分纯评分配置（单一真源）。

本模块只承载**数字**：角色权重矩阵、六维子分、市场七子分、主线四子分、角色阈值、
分段阈值、档位/仓位系数与预期基准。评分代码（:mod:`scoring`）、市场代码
（:mod:`market`）、主线代码（:mod:`mainline`）与角色代码（:mod:`roles`）不得内嵌
任何阈值，全部经 ``ReviewConfig`` 读取。

数字来源：产品方案 §5–§11（市场 / 主线 / 角色 / 六维公式 / 缺失语义 / 角色权重 /
档位 / 仓位系数 / T+1 预期）。

设计模式：Strategy —— 「角色预期差异」隔离在 ``role_weights`` 配置行里；
新增角色只加一行权重，不改评分算法（产品 §9.1 动机）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Tuple

CONFIG_VERSION = "review_baseline_v1"

DIMENSIONS: Tuple[str, ...] = ("D1", "D2", "D3", "D4", "D5", "D6")

WEIGHT_PROFILES: Tuple[str, ...] = (
    "leader",
    "vanguard",
    "mainstay",
    "capacity",
    "follower",
    "diagnostic_non_mainline",
)

ROLES: Tuple[str, ...] = WEIGHT_PROFILES[:-1]

DIAGNOSTIC_PROFILE = "diagnostic_non_mainline"


@dataclass(frozen=True)
class ReviewConfig:
    """不可变配置对象。任何评分/仓位数字都必须来自这里。

    ``frozen=True`` 只阻止字段重绑定；嵌套 ``dict`` 仍应视为只读，调用方不得原地
    修改 ``CONFIG``（评分函数也只读）。
    """

    # 6 个 profile × 6 维，单位 = 百分点，每行合计 100。
    role_weights: Mapping[str, Mapping[str, float]]
    # 维度可读名称（用于 top_drivers / top_drags 的 reason 文案）。
    dimension_labels: Mapping[str, str]
    # 每维的子分权重、分段阈值与插值系数（单位见各键注释）。
    scoring_rules: Mapping[str, Mapping[str, Any]]
    # 维度整体为 null 的剩余子权重门槛（产品 §8.1）。
    sub_weight_min: float
    # 覆盖率门槛：coverage < 该值 → review_score = null（产品 §8.2）。
    coverage_min: float
    # 置信度 source_health 档位（产品 §8.3）。
    source_health_bands: Mapping[str, float]
    # 档位阈值 S/A/B/C/D（产品 §8.4）。
    grade_thresholds: Tuple[float, ...]
    # 档位默认仓位基数（产品 §10.2）。
    grade_base_pct: Mapping[str, float]
    # 单票角色仓位上限（产品 §10.2）。
    role_cap_pct: Mapping[str, float]
    # 市场状态系数（产品 §10.2）。
    market_state_coef: Mapping[str, float]
    # 置信度系数区间 (下界, 系数)，从高到低匹配（产品 §10.2）。
    confidence_coef_bands: Tuple[Tuple[float, float], ...]
    # 风险系数：none / soft / hard（产品 §10.2）；hard 已由 G1 短路。
    risk_coef: Mapping[str, float]
    # 一字板执行风险系数（产品 §10.1）。
    one_word_coef: float
    # 流动性门槛 (G3a 下限, G3b 上限)，单位元（产品 §10.1）。
    liquidity_thresholds: Tuple[float, float]
    # 仓位向下取整步长与最小单位（产品 §10.2），单位百分点。
    position_step_pct: float
    position_min_pct: float
    # 组合和告警阈值（产品 §10.3），单位百分点。
    portfolio_sum_limit_pct: float
    # T+1 冻结预期基准（产品 §11.1）。
    expectations: Mapping[str, Mapping[str, Any]]
    # 市场七子分权重（产品 §5.1），键顺序即输出/证据顺序。
    weights_market: Mapping[str, float]
    # 七子分分段线性锚点：key → ((input, score), ...)（产品 §5.1）。
    market_anchors: Mapping[str, Tuple[Tuple[float, float], ...]]
    # 市场状态阈值 (strong, neutral, weak)，score ≥ 阈值即命中（产品 §5.2）。
    market_state_thresholds: Tuple[float, float, float]
    # 判定基座：任一缺失 → market_score=null、state=unknown（产品 §5.1）。
    market_basis: Tuple[str, ...]
    # 主线四子分权重（产品 §6.1）。
    weights_mainline: Mapping[str, float]
    # 主线四子分封顶：N/H/C/S（产品 §6.1）。
    mainline_caps: Mapping[str, float]
    # 前 N 名为当日主线，第 top_n+1..watchlist_n 名为观察（产品 §6.1）。
    mainline_top_n: int
    mainline_watchlist_n: int
    # 主线成员样例条数（产品 §6.3）。
    mainline_members_sample_limit: int
    # 主线五态阈值（产品 §6.2）。
    mainline_state_score_delta: float
    mainline_state_rank_up: int
    mainline_state_rank_down: int
    # 角色判定阈值（产品 §7.1）。
    role_mainstay_min_float_cap: float
    role_capacity_min_amount: float
    role_capacity_max_height: int
    # 详细池代码上限（技术方案 §10.3）；便于压测时下调，默认 60。
    review_max_codes: int = 60
    # T+1 反馈判定阈值（产品 §11.3–§11.4）；T+1 只读冻结预期，本组阈值只用于
    # 「确认/破坏/回踩」判定，不参与 T 日评分或仓位。
    feedback_rules: Mapping[str, float] = dataclasses.field(default_factory=dict)


CONFIG = ReviewConfig(
    role_weights={
        "leader": {"D1": 30.0, "D2": 20.0, "D3": 20.0, "D4": 20.0, "D5": 5.0, "D6": 5.0},
        "vanguard": {"D1": 25.0, "D2": 15.0, "D3": 25.0, "D4": 25.0, "D5": 5.0, "D6": 5.0},
        "mainstay": {"D1": 20.0, "D2": 20.0, "D3": 15.0, "D4": 25.0, "D5": 15.0, "D6": 5.0},
        "capacity": {"D1": 15.0, "D2": 15.0, "D3": 15.0, "D4": 30.0, "D5": 20.0, "D6": 5.0},
        "follower": {"D1": 20.0, "D2": 20.0, "D3": 25.0, "D4": 20.0, "D5": 10.0, "D6": 5.0},
        # 独立配置项：数值可同 follower，但不得合并（产品 §9.1 诊断 profile 规则）。
        "diagnostic_non_mainline": {
            "D1": 20.0,
            "D2": 20.0,
            "D3": 25.0,
            "D4": 20.0,
            "D5": 10.0,
            "D6": 5.0,
        },
    },
    dimension_labels={
        "D1": "龙头质量",
        "D2": "主线地位",
        "D3": "封板质量",
        "D4": "资金与流动性",
        "D5": "筹码/位置",
        "D6": "持续性/股性",
    },
    scoring_rules={
        "D1": {
            # 产品 §8.1：D1 = 0.40*H + 0.35*L + 0.25*B
            "weights": {"H": 0.40, "L": 0.35, "B": 0.25},
            "height_cap": 6,
            "board_limit_up_num_cap": 8,
        },
        "D2": {
            # 产品 §8.1：D2 = 0.50*M1 + 0.30*M2 + 0.20*M3
            "weights": {"M1": 0.50, "M2": 0.30, "M3": 0.20},
            "mainline_rank_scores": {1: 100.0, 2: 80.0, 3: 65.0, 4: 40.0, 5: 40.0},
            "mainline_rank_non_mainline_score": 0.0,
            "attr_match_cap": 2,
            "mainline_days_cap": 3,
        },
        "D3": {
            # 产品 §8.1：D3 = 0.35*T + 0.25*O + 0.25*S + 0.15*R
            "weights": {"T": 0.35, "O": 0.25, "S": 0.25, "R": 0.15},
            "seal_time_buckets": (
                ("09:35", 100.0),
                ("10:00", 85.0),
                ("11:30", 65.0),
                ("14:00", 45.0),
            ),
            "seal_time_after_score": 25.0,
            "open_num_scores": {0: 100.0, 1: 75.0, 2: 50.0},
            "open_num_cap_score": 20.0,
            "seal_amount_to_float_cap": 0.01,
            "seal_ratio_cap": 0.5,
        },
        "D4": {
            # 产品 §8.1：D4 = 0.40*F + 0.35*V + 0.25*Q
            "weights": {"F": 0.40, "V": 0.35, "Q": 0.25},
            # F = base + slope * (main_net / amount)，-10%→0 / 0→50 / +10%→100
            "main_net_ratio_base": 50.0,
            "main_net_ratio_slope": 500.0,
            "amount_cap": 2_000_000_000.0,  # 20 亿
            # 换手健康分分段（百分比）
            "turnover_low": 2.0,
            "turnover_healthy_low": 5.0,
            "turnover_healthy_high": 20.0,
            "turnover_high": 35.0,
            "turnover_low_score": 20.0,
            "turnover_start_score": 50.0,
            "turnover_peak_score": 100.0,
            "turnover_end_score": 50.0,
            "turnover_very_high_score": 20.0,
        },
        "D5": {
            # 产品 §8.1：D5 = 0.40*P + 0.30*C + 0.30*X
            "weights": {"P": 0.40, "C": 0.30, "X": 0.30},
            # P：距 60 日高点回撤（百分比）
            "drawdown": {
                "full_at": 5.0,
                "mid_at": 20.0,
                "low_at": 40.0,
                "full": 100.0,
                "mid": 60.0,
                "low": 30.0,
                "floor": 10.0,
            },
            # C：筹码集中度 (P90-P10)/P50
            # 产品只写到 10–20% 与 >30%；20–30% 按连续插值 60→20 补齐。
            "concentration": {
                "full_below": 0.10,
                "mid_at": 0.20,
                "floor_at": 0.30,
                "full": 100.0,
                "mid": 60.0,
                "floor": 20.0,
            },
            # X：现价/平均成本 - 1
            # 产品只写到 10–25% 与 >40%；25–40% 按连续插值 50→20 补齐。
            "profit": {
                "below": 60.0,
                "peak_until": 0.10,
                "high_at": 0.10,
                "mid_at": 0.25,
                "low_at": 0.40,
                "peak": 100.0,
                "high": 80.0,
                "mid": 50.0,
                "low": 20.0,
            },
        },
        "D6": {
            # 产品 §8.1：D6 = 0.50*S1 + 0.50*S2
            "weights": {"S1": 0.50, "S2": 0.50},
            "limit_up_count_scores": {0: 0.0, 1: 30.0, 2: 60.0, 3: 60.0, 4: 85.0, 5: 85.0},
            "limit_up_count_cap_score": 100.0,
            "next_day_up_sample_min": 3,
        },
    },
    sub_weight_min=0.5,
    coverage_min=0.5,
    source_health_bands={
        "all": 1.00,
        "degraded_1": 0.85,
        "degraded_2": 0.70,
        "basis_missing": 0.00,
    },
    grade_thresholds=(85.0, 75.0, 65.0, 50.0),
    grade_base_pct={"S": 12.0, "A": 8.0, "B": 4.0, "C": 0.0, "D": 0.0},
    role_cap_pct={
        "leader": 15.0,
        "vanguard": 10.0,
        "mainstay": 12.0,
        "capacity": 12.0,
        "follower": 5.0,
    },
    market_state_coef={
        "strong": 1.0,
        "neutral": 0.8,
        "weak": 0.6,
        "ice": 0.4,
        "unknown": 0.5,
    },
    confidence_coef_bands=((0.90, 1.00), (0.75, 0.85), (0.60, 0.70)),
    risk_coef={"none": 1.0, "soft": 0.5, "hard": 0.0},
    one_word_coef=0.5,
    liquidity_thresholds=(200_000_000.0, 500_000_000.0),
    position_step_pct=0.5,
    position_min_pct=0.5,
    portfolio_sum_limit_pct=100.0,
    expectations={
        "leader": {"gap_low": 3.0, "gap_high": 7.0, "ratio_low": 1.2, "seal_by": "10:00"},
        "vanguard": {"gap_low": 2.0, "gap_high": 6.0, "ratio_low": 1.0, "seal_by": "10:30"},
        "mainstay": {"gap_low": 0.0, "gap_high": 3.0, "ratio_low": 0.9, "seal_by": "14:00"},
        "capacity": {"gap_low": -1.0, "gap_high": 2.0, "ratio_low": 0.8, "seal_by": None},
        "follower": {"gap_low": -2.0, "gap_high": 2.0, "ratio_low": 0.7, "seal_by": None},
    },
    # 产品 §5.1：0.22*LU + 0.18*LD + 0.18*PRE + 0.14*PRO + 0.10*HGT + 0.08*BRK + 0.10*LIQ
    weights_market={
        "LU": 0.22,
        "LD": 0.18,
        "PRE": 0.18,
        "PRO": 0.14,
        "HGT": 0.10,
        "BRK": 0.08,
        "LIQ": 0.10,
    },
    market_anchors={
        "LU": ((15.0, 0.0), (80.0, 90.0), (120.0, 100.0)),
        "LD": ((0.0, 100.0), (5.0, 90.0), (10.0, 70.0), (30.0, 20.0), (50.0, 0.0)),
        "PRE": ((-5.0, 0.0), (-2.0, 30.0), (0.0, 55.0), (2.0, 80.0), (5.0, 100.0)),
        "PRO": ((0.05, 10.0), (0.15, 50.0), (0.25, 80.0), (0.40, 100.0)),
        "HGT": ((2.0, 20.0), (3.0, 50.0), (4.0, 70.0), (6.0, 90.0), (8.0, 100.0)),
        "BRK": ((0.10, 100.0), (0.20, 75.0), (0.30, 45.0), (0.45, 10.0), (0.60, 0.0)),
        "LIQ": ((5000.0, 10.0), (7000.0, 40.0), (10000.0, 75.0), (15000.0, 100.0)),
    },
    market_state_thresholds=(70.0, 50.0, 30.0),
    market_basis=("LU", "LD"),
    # 产品 §6.1：0.35*N + 0.25*H + 0.20*C + 0.20*S
    weights_mainline={"N": 0.35, "H": 0.25, "C": 0.20, "S": 0.20},
    mainline_caps={
        "limit_up_num": 10.0,
        "high_num": 6.0,
        "continuous_plate_num": 3.0,
        "concentration": 0.15,
    },
    mainline_top_n=3,
    mainline_watchlist_n=5,
    mainline_members_sample_limit=10,
    # 产品 §6.2：排名 ±1 / 分数 ±10 内为 stable
    mainline_state_score_delta=10.0,
    mainline_state_rank_up=1,
    mainline_state_rank_down=2,
    # 产品 §7.1：中军流通市值 ≥ 200 亿；容量核心成交额 ≥ 10 亿且高度 ≤ 2
    role_mainstay_min_float_cap=20_000_000_000.0,
    role_capacity_min_amount=1_000_000_000.0,
    role_capacity_max_height=2,
    review_max_codes=60,
    feedback_rules={
        # 产品 §11.3：confirmed 的补充确认条件（收盘涨幅）。
        "confirm_close_pct_min": 3.0,
        # 产品 §11.3：invalidated 的破坏条件。
        "invalidate_close_pct_max": -2.0,
        "invalidate_gap_margin_pct": 3.0,     # gap < expected_low - 3%
        "invalidate_broken_count_min": 2,     # 炸板 ≥ 2 次
        # 产品 §11.3：weakened → confirmed 的回踩确认（量能回升下限）。
        "weakened_recover_ratio_min": 1.0,
        # 产品 §12：plan_review 的 pattern 归因阈值。
        "pattern_outcome_max_pct": -3.0,
        "pattern_limit_up_pct": 9.5,
        # 涨停价推导（涨停板口径：主板 10%、创业板/科创板 20%）。
        "limit_up_ratio_main": 0.10,
        "limit_up_ratio_growth": 0.20,
        "limit_up_tolerance": 0.001,
    },
)


def _canonical(value: Any) -> Any:
    """把配置递归转成可稳定 JSON 序列化的结构。

    ``Mapping`` 按键的字符串形式排序；``float`` 固定 10 位小数，避免二进制
    表示噪声导致同一配置在不同平台产生不同 hash。
    """

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        return float(f"{value:.10f}")
    return value


def config_hash(cfg: ReviewConfig = CONFIG) -> str:
    """Canonical JSON（sorted keys、固定小数位）→ SHA-256 前 12 位。"""

    payload = {"config_version": CONFIG_VERSION, "config": _canonical(cfg)}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def weight_profile_for(role: str | None) -> str:
    """把角色映射到权重 profile；``role=null`` 显式使用诊断 profile。

    未知角色直接 fail loud（新增角色必须先在配置里加一行权重），
    绝不静默回退到 follower。
    """

    if role is None or role == "":
        return DIAGNOSTIC_PROFILE
    if role not in CONFIG.role_weights:
        raise ValueError(f"unknown review role: {role!r}")
    return role


__all__ = [
    "CONFIG",
    "CONFIG_VERSION",
    "DIMENSIONS",
    "DIAGNOSTIC_PROFILE",
    "ROLES",
    "ReviewConfig",
    "WEIGHT_PROFILES",
    "config_hash",
    "weight_profile_for",
]
