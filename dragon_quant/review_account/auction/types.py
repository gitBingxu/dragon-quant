"""auction.types — 集合竞价强度判定产物（v2 四轴分离）。

四轴（方案 §11.2）：
  - 强度 ``strength``（可算才有值，veto 时 legacy 0，真实分在 ``pre_veto_strength``）；
  - 风险 ``risk_events``（含 ``veto``）；
  - 数据质量 ``data_quality``（``ok/degraded/unavailable`` + 稳定原因码）；
  - 模型验证 ``validation_status``（固定 ``baseline_unvalidated``）。

旧 ``AuctionVerdict`` 字段（``code/is_weak/strength/direction/level/dims/reject_reason/
unverified/rank``）全部保留，新增字段只增不改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from dragon_quant.infra.models import ScoreResult, Quote  # noqa: F401  (re-export)


@dataclass
class DimensionScore:
    """单维结果（v2）。``score=None`` 表示该维不可计算（绝不写 0/50）。"""

    dim: str
    label: str
    score: Optional[float]
    weight: float
    effective_weight: Optional[float] = None
    status: str = "scored"           # scored / degraded / missing
    details: dict = field(default_factory=dict)

    @property
    def is_scored(self) -> bool:
        return self.score is not None


@dataclass
class RiskEvent:
    """盘口估计事件（不是订单级成交）。"""

    code: str
    event_id: str
    family: str                      # risk / conversion / absorption / context / data
    severity: str                    # high / medium / low / info
    veto: bool = False
    window: list = field(default_factory=list)
    owner_dim: Optional[str] = None
    evidence: dict = field(default_factory=dict)
    note: str = "盘口估计，不是订单级成交"

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "code": self.code,
            "family": self.family,
            "severity": self.severity,
            "veto": bool(self.veto),
            "window": list(self.window),
            "owner_dim": self.owner_dim,
            "evidence": dict(self.evidence),
            "note": self.note,
        }


@dataclass
class DataQuality:
    status: str = "ok"
    completeness: float = 0.0
    reasons: list = field(default_factory=list)   # [{"code","severity","field"}]

    @property
    def reason_codes(self) -> list:
        seen, codes = set(), []
        for reason in self.reasons:
            code = reason.get("code") if isinstance(reason, dict) else None
            if code and code not in seen:
                seen.add(code)
                codes.append(code)
        return codes

    def add(self, code: str, severity: str, field_name: Optional[str] = None) -> None:
        reason = {"code": code, "severity": severity, "field": field_name}
        if reason not in self.reasons:
            self.reasons.append(reason)

    def has_severity(self, severity: str) -> bool:
        return any(isinstance(r, dict) and r.get("severity") == severity
                   for r in self.reasons)


@dataclass
class AuctionVerdict:
    """竞价强度判定（v2）。"""

    code: str
    is_weak: bool = False
    strength: Optional[float] = None
    direction: str = "neutral"
    level: Optional[str] = None
    dims: dict[str, DimensionScore] = field(default_factory=dict)
    reject_reason: Optional[str] = None
    unverified: bool = False
    rank: Optional[int] = None
    # ── v2 新增 ──
    rank_reason: Optional[str] = None
    pre_veto_strength: Optional[float] = None
    validation_status: str = "baseline_unvalidated"
    config_version: str = ""
    config_hash: str = ""
    semantics: dict = field(default_factory=dict)
    data_quality: DataQuality = field(default_factory=DataQuality)
    risk_events: list = field(default_factory=list)
    raw_factors: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    reference_prices: dict = field(default_factory=dict)
    windows: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)
    name: str = ""


# 兼容旧名称：Evaluation 是 v2 的判定对象
Evaluation = AuctionVerdict


@dataclass
class AuctionOutcome:
    """一次 auction 用例调用的结果（CLI 负责渲染与退出码）。"""

    payload: dict
    exit_code: int = 0
    render_names: dict = field(default_factory=dict)
    mode: str = "collect"
    warnings: list = field(default_factory=list)

    @classmethod
    def error(cls, payload: dict) -> "AuctionOutcome":
        return cls(payload=payload, exit_code=1, mode="error")
