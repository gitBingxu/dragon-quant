"""auction.registry — v2 集合竞价评分的唯一策略参数与语义契约。

设计（方案 §3.2 / §13.1）：
  - ``AUCTION_PROFILE`` 是唯一 Strategy 参数源（权重 / 分桶 / 窗口 / 最小样本 /
    历史目标 / 市场采样秒数 / 语义版本 / 验证状态）；
  - 任何 profile 修改都会改变 ``CONFIG_HASH``；因子层 ``params`` 必须与之一致
    （由联动测试强制）；
  - 本模块只含常量与纯计算，不读环境、不读时钟、不触 I/O。

旧常量别名（``STRENGTH_LEVELS`` / ``AUCTION_ITEMS_MISSING`` / 窗口秒数等）保留，
供展示层与既有消费方继续引用；``AUCTION_NEUTRAL`` 已删除（缺失不再写 50 分）。
"""

from __future__ import annotations

import hashlib
import json

# ─── 窗口（自 0 点起的秒数）───
AUCTION_OPEN = 9 * 3600 + 15 * 60               # 09:15:00
NO_CANCEL = 9 * 3600 + 20 * 60                  # 09:20:00（不可撤单分界）
TAIL_DEFAULT_START = 9 * 3600 + 24 * 60 + 30    # 09:24:30（末段默认起点）
TAIL_EXTENDED_START = 9 * 3600 + 24 * 60        # 09:24:00（根数不足时扩窗）
AUCTION_CLOSE = 9 * 3600 + 25 * 60              # 09:25:00

# ─── 最小样本 / 幅度（基线，可调）───
CORE_MIN_BARS = 2                 # 09:20–09:25 至少 2 条（含末点）
TAIL_MIN_BARS = 4                 # 末段默认窗口根数下限（不足则扩窗）
CONVERSION_MIN_BARS = 2           # 转换事件每侧最少 bars
CONVERSION_MIN_RATIO = 0.05       # 转换幅度相对末点匹配量
REJECT_HIGH_PCT = 3.0             # 高位判定涨幅（%）
HIGH_GREEN_MIN_BARS = 3           # 高位绿柱最少 bars
CANCEL_SPOOF_DROP_RATIO = 0.30    # 卡点撤单峰值骤降比例（仅证据，不 veto）
REJECT_TAIL_NEG_RATIO = 2.0       # 末段卖单极值放大倍数
SELL_PRESSURE_RATIO = 1.5         # 卖压放大最小倍数
VOL_PRICE_ACCEL_RATIO = 1.5       # 量价背离 / 末段加速阈值
PRICE_TOLERANCE = 0.001           # 「同价」相对容差
MIN_MATCHED_LOTS_FOR_RATIO = 100  # 极小迁移量分母保护（手）

# ─── 强度等级 ───
STRENGTH_LEVELS = ((70.0, "强"), (40.0, "中"), (0.0, "弱"))

# ─── 空明细哨兵（priority.py / 因子层共同引用）───
AUCTION_ITEMS_MISSING = "竞价明细缺失"

# ─── 语义块（写入每份快照，AC2）───
SEMANTICS_VERSION = "auction_sem_2026_10_29"
SEMANTICS = {
    "semantics_version": SEMANTICS_VERSION,
    "matched_unit": "lots",
    "matched_kind": "cumulative_virtual_match",
    "unmatched_sign": "positive_is_buy_remainder",
    "core_window": ["09:20:00", "09:25:00"],
    "pre_window": ["09:15:00", "09:20:00"],
    "price_source": "official_open_px",
    "confidence": "protocol_plus_real_sample",
}

# ─── 维度 ───
DIM_ORDER = ("direction", "liquidity", "supply", "vol_price", "tail_grab")
DIM_LABELS = {
    "direction": "价格表态",
    "liquidity": "竞价流动性",
    "supply": "供需与转换",
    "vol_price": "量价协同",
    "tail_grab": "末段执行",
}
WEIGHTS = {
    "direction": 0.25,
    "liquidity": 0.20,
    "supply": 0.25,
    "vol_price": 0.20,
    "tail_grab": 0.10,
}
REQUIRED_DIMS = ("direction", "supply")
MIN_SCORED_DIMS = 3

# ─── 分桶（不插值）───
# 上界型：取第一个 ``value <= upper`` 的分数；_CATCH_ALL 为极大上界（JSON 可序列化）。
_CATCH_ALL = 1e18
_UPPER_BUCKETS = {
    "open_gap": [(-5.0, 0.0), (-2.0, 15.0), (0.0, 30.0), (2.0, 55.0),
                 (5.0, 75.0), (8.0, 90.0), (_CATCH_ALL, 100.0)],
    "post20_return": [(-2.0, 0.0), (-0.5, 20.0), (0.0, 40.0), (0.5, 60.0),
                      (2.0, 85.0), (_CATCH_ALL, 100.0)],
    "slippage": [(-1.0, 0.0), (-0.2, 30.0), (0.0, 50.0), (0.2, 70.0),
                 (_CATCH_ALL, 90.0)],
    "supply_ratio": [(-1.0, 0.0), (-0.5, 15.0), (-0.2, 30.0), (0.0, 45.0),
                     (0.2, 60.0), (0.5, 75.0), (1.0, 90.0),
                     (_CATCH_ALL, 100.0)],
}
# 下界型：取最后一个 ``value >= lower`` 的分数。
_LOWER_BUCKETS = {
    "turnover": [(0.0, 10.0), (0.05, 25.0), (0.1, 45.0), (0.2, 65.0),
                 (0.4, 85.0), (0.8, 100.0)],
    "amount_log10": [(0.0, 10.0), (6.7, 30.0), (7.0, 50.0), (7.3, 70.0),
                     (7.7, 85.0), (8.0, 100.0)],
}
BUCKETS = {"upper": _UPPER_BUCKETS, "lower": _LOWER_BUCKETS}

# ─── 市场采样（仅用于诊断，不进入强度分）───
MARKET_SAMPLE_SECONDS = {"sh": 3, "sz": 9}

# ─── 子项权重 ───
DIRECTION_SUB_WEIGHTS = {"open": 0.55, "post20": 0.30, "slippage": 0.15}
LIQUIDITY_SUB_WEIGHTS = {"turnover": 0.65, "amount": 0.35}
SUPPLY_SUB_WEIGHTS = {"ratio": 0.45, "change": 0.35, "conversion": 0.20}
VOL_PRICE_SUB_WEIGHTS = {"agree": 0.70, "divergence": 0.30}
TAIL_SUB_WEIGHTS = {"accel": 0.60, "return": 0.25, "unmatched": 0.15}
SUPPLY_SELL_PRESSURE_CAP = 40.0

VALIDATION_STATUS = "baseline_unvalidated"
CONFIG_VERSION = "auction_v2_baseline"

_PROFILE_BODY = {
    "config_version": CONFIG_VERSION,
    "weights": WEIGHTS,
    "buckets": {
        "upper": {k: [[u, s] for u, s in v] for k, v in _UPPER_BUCKETS.items()},
        "lower": {k: [[u, s] for u, s in v] for k, v in _LOWER_BUCKETS.items()},
    },
    "windows": {
        "pre": ["09:15:00", "09:20:00"],
        "core": ["09:20:00", "09:25:00"],
        "tail_default": "09:24:30",
        "tail_extended": "09:24:00",
        "tail_min_bars": TAIL_MIN_BARS,
    },
    "min_samples": {
        "core": CORE_MIN_BARS,
        "conversion": CONVERSION_MIN_BARS,
        "high_green": HIGH_GREEN_MIN_BARS,
    },
    "history": {"target_days": 5, "max_days": 5},
    "market_sample_seconds": MARKET_SAMPLE_SECONDS,
    "semantics_version": SEMANTICS_VERSION,
    "validation_status": VALIDATION_STATUS,
}


def _canonical(profile: dict) -> str:
    return json.dumps(profile, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


CONFIG_HASH = hashlib.sha256(_canonical(_PROFILE_BODY).encode("utf-8")).hexdigest()[:12]

AUCTION_PROFILE = {
    **_PROFILE_BODY,
    "config_hash": CONFIG_HASH,
}


def weight_total() -> float:
    return sum(WEIGHTS.values())


# 旧名称兼容别名（展示/文档仍可引用）
REJECT_DROP_RATIO = CANCEL_SPOOF_DROP_RATIO
