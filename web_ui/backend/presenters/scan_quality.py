"""兼容转发：scan-quality 纯计算已迁至 ``dragon_quant.research.scan_quality``。

Web 路由与既有测试继续从本模块导入；实现只有一份（研究模块），避免 CLI / Web
两套口径漂移。本模块不建连接、不写库。
"""

from dragon_quant.research.scan_quality import (  # noqa: F401
    DEFAULT_COST_BPS,
    DEFAULT_HOLD_DAYS,
    MISSED_OPPORTUNITY_PCT,
    SCAN_QUALITY_VERSION,
    build_scan_quality,
    canonical_candidates,
    run_scan_quality,
)

__all__ = [
    "DEFAULT_COST_BPS",
    "DEFAULT_HOLD_DAYS",
    "MISSED_OPPORTUNITY_PCT",
    "SCAN_QUALITY_VERSION",
    "build_scan_quality",
    "canonical_candidates",
    "run_scan_quality",
]
