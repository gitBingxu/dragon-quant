"""scorers_v2.registry —「识别真龙」五维评分体系的权重/门槛/阈值常量。

全部集中于此，便于回测调参；算法文件只引用，不写死。
依据《评分器Refactor.md》§9。
"""

# ─── 9.1 维度权重 & 门槛 ───
# 四大特征设硬门槛（任一低于门槛一票否决）；absorption 不设门槛，仅加权贡献。
DIM_WEIGHTS = {
    "drive": 0.30,
    "leadership": 0.25,
    "anti_drop": 0.15,
    "liquidity": 0.20,
    "absorption": 0.10,
}
DIM_FLOORS = {
    "drive": 40.0,
    "leadership": 40.0,
    "anti_drop": 35.0,
    "liquidity": 35.0,
    # absorption 无门槛
}

# ─── 9.2 带动性 Drive ───
LIMIT_UP_PCT = 9.9          # 涨停判定阈值（%）
EARLY_W = 0.40              # 封板最早 子因子权重
LEAD_W = 0.35              # 带动板块 子因子权重
VOICE_W = 0.25             # 板块共鸣 子因子权重
FOLLOW_PCT = 3.0           # 板块共鸣「强势小弟」涨幅下限（%）
VOICE_FULL = 0.10          # 板块共鸣满分对应涨停家数占比
FOLLOW_FULL = 0.30         # 板块共鸣满分对应强势(>3%)家数占比
VOICE_LIMIT_W = 0.6        # 板块共鸣内部：涨停占比 子权重
VOICE_STRONG_W = 0.4       # 板块共鸣内部：强势占比 子权重
LEAD_FOLLOW_BARS = 3       # 带动：个股脉冲后板块跟随窗口（1分K → 3分钟）
THRUST_WIN = 3             # 个股拉升脉冲滑窗（分钟）
THRUST_PCT = 3.0           # 个股脉冲净振幅 Δh_s 阈值（%，3分钟至少拉3%）
SECTOR_FOLLOW_PCT = 0.3    # 板块跟随振幅达标阈值（兼作板块抢跑阈值，%）
LEAD_BASE = 70.0           # 首次带动基准分
LEAD_STEP = 10.0           # 每多一次带动加分（封顶100）
FOLLOW_PENALTY = 20.0      # 每次 follow_event 扣分（仅 n_lead≥1 生效）
FOLLOW_PENALTY_CAP = 100.0 # 跟随扣分上限
CORR_TH = 0.5              # 时滞互相关确认阈值
CORR_BONUS = 10.0          # 整体领先 bonus

# ─── 9.3 领涨性 Leadership ───
BOARD_W = 0.50             # 连板最多 子因子权重
PCT_W = 0.50              # 5日涨幅最大 子因子权重
BOARD_DECAY = 10.0        # 每低于板块最高连板 1 板的扣分

# ─── 9.4 抗跌性 AntiDrop ───
DIP_TH = -0.5             # 基准跳水段判定（1分K滑窗净跌幅 %）
DIP_WIN = 3              # 跳水段识别滑窗（分钟）
MARKET_W = 0.6           # 大盘维度权重
SECTOR_W = 0.4           # 板块维度权重
HOLD_W = 0.6            # 单维内部：横盘稳住 子权重
REBOUND_W = 0.4        # 单维内部：率先起飞 子权重
REBOUND_LEAD_BARS = 3   # 企稳拐点后率先反弹判定窗口（1分K → 3分钟）
REBOUND_LEAD_W = 0.6   # 率先起飞内部：早见底 子权重
REBOUND_AMP_W = 0.4    # 率先起飞内部：反弹幅度 子权重
ANTIDROP_NEUTRAL = 65.0 # 无跳水段时该维中性分

# ─── 9.5 流动性 Liquidity ───
TURNOVER_W = 0.5         # 换手充沛度 子因子权重
SEAL_W = 0.5           # 封板质量 子因子权重
TO_ABS_W = 0.5         # 换手充沛度内部：绝对分 子权重
TO_REL_W = 0.5         # 换手充沛度内部：相对分 子权重
TURNOVER_FULL = 15.0   # 换手率绝对分满分阈值（%）
SEAL_STRENGTH_W = 0.5  # 封板质量内部：封单强度 子权重
SEAL_STABLE_W = 0.5    # 封板质量内部：封板稳定性 子权重
SEAL_STRENGTH_REF = 0.3  # 封单强度参考（bid1_volume / 当日成交量）满分阈值

# ─── 9.6 资金承接性 Absorption ───
ABS_WINDOW = 6           # 滑动窗口（5分K根数，30分钟）
ABS_TARGET_MIN_UP = 0.003   # 目标板块窗口涨幅下限
ABS_TARGET_MIN_YANG = 4     # 目标板块窗口最少阳线数
ABS_DROP_TH = -0.003        # 其他板块跳水阈值
ABS_MIN_AFFECTED = 2        # 最少受影响板块数
ABS_MAX_DRAWDOWN_RATIO = 0.3  # 目标板块窗口回撤比例上限
ABS_MAX_TIME_DIFF_MS = 600_000  # 跳水→拉升最大时间差（10分钟）
ABS_MAX_TRADE_DAYS = 10     # 回看交易日数
ABS_INTENSITY_W = 0.40      # 事件三维：虹吸强度
ABS_BREADTH_W = 0.20        # 事件三维：广度
ABS_SUSTAIN_W = 0.40        # 事件三维：持续性
# 强度（正向口径）：目标拉升越高 + 出逃规模(数量×均跌)越大 → 分越高
ABS_INT_TARGET_W = 0.5      # 强度内部：目标拉升分量 子权重
ABS_INT_FLIGHT_W = 0.5      # 强度内部：出逃规模分量 子权重
ABS_INT_TARGET_REF = 2.0    # 目标拉升满分参考（窗口涨幅 %，≥2% 给满分）
ABS_INT_FLIGHT_REF = 5.0    # 出逃规模满分参考（|均跌%|×出逃板块数，≥5 给满分）
ABS_MULTI_BONUS_STEP = 5    # 每多一个事件加分
ABS_MULTI_BONUS_CAP = 15    # 多事件 bonus 上限
ABS_BUCKET_MS = 300_000     # 5 分钟 bucket
ABS_NEUTRAL = 50.0          # 无信号/数据不足 中性分
