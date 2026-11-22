"""证据层的版本、能力常量与错误类型（无 I/O）。"""

from __future__ import annotations

from datetime import timedelta, timezone

EVIDENCE_SCHEMA_VERSION = "2"
SUPPORTED_SCHEMA_VERSIONS = frozenset({EVIDENCE_SCHEMA_VERSION})
CALIBRATION_VERSION = "1"
CST = timezone(timedelta(hours=8))
DEFAULT_FACT_LIMIT = 5000
DEFAULT_BAR_LIMIT = 20000

VERIFIED = "verified"
UNVERIFIED = "unverified"
QUALITY_VERIFIED = "semantics_verified"
QUALITY_UNVERIFIED = "semantics_unverified"

#: 方案 §4 的能力集合（本层只做数据能力，不承载策略结论）。
CAPABILITIES = (
    "daily_bars",
    "intraday_bars",
    "auction_final",
    "auction_path",
    "market_context",
    "theme_membership",
    "account_state",
    "tradability",
    "probability_sample",
)

OBS_CAPABILITIES = {
    "auction_path": "auction_path",
    "auction_final": "auction_final",
    "quote": "market_context",
    "sector_membership": "theme_membership",
}

BAR_CAPABILITIES = {
    "1min": "intraday_bars",
    "5min": "intraday_bars",
    "daily": "daily_bars",
}

#: 只有本地输入（不触网）就能判定的输入名；账户/预算/可卖量由 W2/W3 提供。
INPUT_ACCOUNT_STATE = "account_state"
INPUT_RISK_BUDGET = "risk_budget"
INPUT_SELLABLE_QTY = "sellable_quantity"
INPUT_EXTERNAL_SOURCES = ("source:meoz", "source:tushare")

PROTECT_NEW_RISK = "new_risk"
PROTECT_EXISTING = "existing_protection"

#: 竞价终态 payload 必须声明的真实终态来源；末笔虚拟匹配永远不是这个值。
TERMINAL_SOURCE_EXCHANGE_FINAL = "exchange_final"

_PROTECTED_CAPABILITIES = frozenset(CAPABILITIES)
_VERIFIED = QUALITY_VERIFIED
_UNVERIFIED = QUALITY_UNVERIFIED



class EvidenceError(RuntimeError):
    """证据契约错误：损坏、不支持版本、指纹/语义不一致。"""

    def __init__(self, message: str, *, reason: str = "evidence_error") -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


CST = timezone(timedelta(hours=8))
