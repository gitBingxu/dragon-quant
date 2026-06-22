"""
数据模型
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class KBar:
    """一根 K 线"""
    timestamp: int          # Unix 毫秒
    volume: float           # 成交量
    open: float
    high: float
    low: float
    close: float
    chg: float              # 涨跌额
    pct: float              # 涨跌幅 %
    turnover: float         # 换手率 %
    amount: float           # 成交额


@dataclass
class StockInfo:
    """股票基本信息 + 当日行情快照"""
    code: str               # 如 "600519"
    name: str               # 如 "贵州茅台"
    exchange: str = ""      # "SH" / "SZ"
    sector_code: str = ""   # 所属板块代码
    sector_name: str = ""   # 所属板块名称
    pct: float = 0.0        # 涨跌幅 %
    price: float = 0.0      # 最新价
    five_day_return: float = 0.0  # 5日累计涨幅 %


@dataclass
class Quote:
    """实时行情快照"""
    code: str
    name: str
    price: float            # 现价
    prev_close: float       # 昨收
    open_px: float          # 开盘
    high: float
    low: float
    pct: float              # 涨跌幅 %
    chg: float              # 涨跌额
    turnover_rate: float    # 换手率 %
    amplitude: float        # 振幅 %
    volume: float           # 成交量
    amount: float           # 成交额
    market_cap: float       # 总市值
    float_market_cap: float # 流通市值
    volume_ratio: float     # 量比
    pe: float               # 市盈率
    limit_up: float         # 涨停价
    limit_down: float       # 跌停价
    avg_price: float        # 均价
    bid1_price: float = 0.0    # 买一价（gtimg f[9]）
    bid1_volume: float = 0.0   # 买一量/封单量（gtimg f[10]，单位手）
    ask1_volume: float = 0.0   # 卖一量（gtimg f[20]，单位手）


@dataclass
class SectorPerformance:
    """板块行情"""
    code: str               # BK1145
    name: str               # 机器人执行器
    pct: float              # 涨跌幅 %
    amplitude: float        # 振幅 %
    turnover_rate: float = 0.0


@dataclass
class Candidate:
    """候选股"""
    code: str
    name: str
    concepts: list[str] = field(default_factory=list)   # 所属领涨概念列表
    board_count: int = 0                                # 连板高度
    fived_pct: float = 0.0                              # 5日总涨幅 %（Phase C 写入，leadership 用）
    primary_sector: str = ""                            # 主选板块（涨停股最多的）
    score: float = 0.0                                  # 综合评分（填充后）


@dataclass
class ScoreResult:
    """单维度评分结果"""
    dim: str                # drive / anti_drop / leadership / absorption
    score: float            # 0-100
    weight: float           # 权重
    details: dict = field(default_factory=dict)  # 子维度得分明细
