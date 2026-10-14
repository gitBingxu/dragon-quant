"""auction.types — 集合竞价强度判定产物。

AuctionVerdict 镜像 scorers.base.DragonVerdict 的「门槛+加权聚合」结构，
增量 level（强度等级）与 unverified（口径未实测/数据缺失）字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from dragon_quant.models.types import ScoreResult, Quote  # noqa: F401  (re-export)


@dataclass
class AuctionVerdict:
    """竞价强度判定（否决闸门 + 四维加权聚合产物）。"""

    code: str
    is_weak: bool                        # 是否触发一票否决（弱/回避）
    strength: float                      # 加权强度分 0-100（否决时=0）
    direction: str                       # "bull" / "bear" / "neutral"
    level: str                           # "强" / "中" / "弱"
    dims: dict[str, ScoreResult] = field(default_factory=dict)  # 四维独立分
    reject_reason: Optional[str] = None  # 若被否决，卡在哪个信号
    unverified: bool = False             # 关键数据/口径未实测或缺失，禁止回测结论与决策消费
    rank: Optional[int] = None           # 通过否决且已验证的池内排名（其余 None）
