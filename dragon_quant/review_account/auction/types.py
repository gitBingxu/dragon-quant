"""auction.types — 集合竞价强度判定产物。

AuctionVerdict 镜像 scorers.base.DragonVerdict 的「门槛+加权聚合」结构，
增量 level（强度等级）与 unverified（口径未实测/数据缺失）字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from dragon_quant.infra.models import ScoreResult, Quote  # noqa: F401  (re-export)


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


@dataclass
class AuctionOutcome:
    """一次 auction 用例调用的结果（CLI 负责渲染与退出码）。

    ``mode`` 是**渲染模式**，与 payload 里的 ``mode`` 字段无关：

    - ``collect``：采集成功（可能含空盘口/评分失败）；
    - ``empty``：prep 与 legacy 均无候选（payload 为 16 键空池契约）；
    - ``recompute``：重算成功；
    - ``error``：错误通道（payload 只带 ``error`` 等错误键）。

    ``warnings`` 是需要写 stderr 的提示行（保持旧命令的通道与时序语义）。
    """

    payload: dict
    exit_code: int = 0
    render_names: dict = field(default_factory=dict)
    mode: str = "collect"
    warnings: list = field(default_factory=list)

    @classmethod
    def error(cls, payload: dict) -> "AuctionOutcome":
        """错误通道结果：CLI 按 ``--json``/人读分别写 stdout/stderr 并退出 1。"""
        return cls(payload=payload, exit_code=1, mode="error")
