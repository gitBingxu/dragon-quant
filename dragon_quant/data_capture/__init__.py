"""持续采集公共门面（TDX 优先、本地留证）。

导航：``cli → commands/assets.py → data_capture.cli → 用例 → collectors → provider``；
原始仓储固定在 :mod:`dragon_quant.infra.assets.observations`（assets.db）。

边界：

- 本包不 import 买卖评分/策略算法（``review_account.strategy`` 等），也不 import
  ``app``/``cli``；持仓代码由命令装配层读取后传入；
- 本包不做自动交易、不安装系统定时器、不写真实账户账本；
- 无付费 key 是正常场景：只消费本地合格证据，缺证时显式限制而不是静默回退。
"""

from __future__ import annotations

from .cli import handle_capture
from .coverage import build_coverage
from .plans import (
    DEFAULT_RECENT_DAYS,
    DEFAULT_SLOTS,
    DEFAULT_TRACK_DAYS,
    build_plan_payload,
    freeze_plan,
)
from .schedule import SCHEDULE_KINDS, render_schedule
from .session import collect_once, resolve_plan, run_session
from .types import (
    CODE_TOPICS,
    QUALITY_RECOVERED,
    QUALITY_UNVERIFIED,
    QUALITY_VERIFIED,
    REASON_CODES,
    TOPIC_WINDOWS,
    TOPICS,
    BarRecord,
    CaptureError,
    CaptureRequest,
    CaptureResult,
    ObservationRecord,
    PlanMember,
    window_state,
)

__all__ = [
    "BarRecord",
    "CODE_TOPICS",
    "CaptureError",
    "CaptureRequest",
    "CaptureResult",
    "DEFAULT_RECENT_DAYS",
    "DEFAULT_SLOTS",
    "DEFAULT_TRACK_DAYS",
    "ObservationRecord",
    "PlanMember",
    "QUALITY_RECOVERED",
    "QUALITY_UNVERIFIED",
    "QUALITY_VERIFIED",
    "REASON_CODES",
    "SCHEDULE_KINDS",
    "TOPICS",
    "TOPIC_WINDOWS",
    "build_coverage",
    "build_plan_payload",
    "collect_once",
    "freeze_plan",
    "handle_capture",
    "render_schedule",
    "resolve_plan",
    "run_session",
    "window_state",
]
