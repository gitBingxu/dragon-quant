"""auction.registry — 集合竞价强度评分的权重/阈值/窗口常量。

全部集中于此，便于回测调参；算法文件只引用，不写死。
"""

# ─── 维度权重 ───
AUCTION_DIM_WEIGHTS = {
    "direction": 0.25,   # 方向表态
    "supply": 0.30,      # 供需结构
    "vol_price": 0.25,   # 量价配合
    "tail_grab": 0.20,   # 末段抢筹
}

# ─── 数据语义约定（待实测，见方法论 §7.1）───
# buy_side = unmatched * UNMATCHED_SIGN；>0 表示买单/红柱，<0 表示卖单/绿柱。
UNMATCHED_SIGN = 1
# matched/unmatched 是否为「累计值」（True=累计，False=时点快照/逐笔）。实测后固化/翻转。
MATCHED_CUMULATIVE = True     # matched：累计成交（单调不减）
UNMATCHED_CUMULATIVE = False  # unmatched：时点排队快照（非累计）
# 语义（符号方向/累计 vs 逐笔）是否已实测固化。实测前为 False，evaluate 结果恒 unverified。
SEMANTICS_VERIFIED = False

# ─── 时间窗口（自 0 点起的秒数）───
AUCTION_OPEN = 9 * 3600 + 15 * 60               # 09:15:00
NO_CANCEL = 9 * 3600 + 20 * 60                  # 09:20:00（不可撤单分界）
TAIL_DEFAULT_START = 9 * 3600 + 24 * 60 + 30    # 09:24:30（末段默认起点）
TAIL_EXTENDED_START = 9 * 3600 + 24 * 60        # 09:24:00（深交所根数不足时扩窗）
AUCTION_CLOSE = 9 * 3600 + 25 * 60              # 09:25:00
TAIL_MIN_BARS = 4                                # 末段窗口内根数下限

# ─── 强度等级分桶（strength → level）───
STRENGTH_LEVELS = ((70.0, "强"), (40.0, "中"), (0.0, "弱"))

# ─── 中性分 / 降级 ───
AUCTION_NEUTRAL = 50.0
PRICE_TOLERANCE = 0.001

# ─── 否决闸门阈值 ───
REJECT_HIGH_PCT = 3.0        # 0.1 高位判定：竞价涨幅下限（%）
REJECT_DROP_RATIO = 0.30     # 0.3 卡点撤单：9:20 时刻买单相对峰值骤降比例（<30%）
REJECT_TAIL_NEG_RATIO = 2.0  # 0.4 跌停排队卖：末段卖单极值相对前段极值的放大倍数

# ─── 方向表态（1.1）高开分桶 (lo, hi, score) ───
HIGH_OPEN_BUCKETS = (
    (-100.0, 0.0, 20.0),    # 低开 ≤0 → 弱表态
    (0.0, 3.0, 50.0),       # 0–3% → 中
    (3.0, 7.0, 80.0),       # 3–7% → 强
    (7.0, 100.0, 100.0),    # >7% → 最强（不递减）
)

# ─── 末段价格斜率（1.2）───
SLOPE_R2_MIN = 0.3          # 线性拟合决定系数下限，低于则斜率不可信

# ─── 量价匹配度（1.6）───
VOL_PRICE_AGREE_RATIO = 0.6  # Δmatched 与 Δprice 同号占比阈值

# ─── 末段抢筹（1.8/1.9）───
TAIL_GRAB_RATIO = 1.5        # 末段 Δmatched 均值相对前段均值的放大倍数
