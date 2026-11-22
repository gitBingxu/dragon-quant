"""采集器注册表稳定入口（按主题选择 Collector）。"""

from __future__ import annotations

from .auction import AuctionFinalCollector, AuctionPathCollector
from .bars import DailyBarCollector, MinuteBarCollector
from .market import MembershipCollector, QuoteCollector
from .support import Collector

COLLECTORS: tuple[Collector, ...] = (
    AuctionPathCollector(),
    AuctionFinalCollector(),
    MinuteBarCollector(),
    DailyBarCollector(),
    QuoteCollector(),
    MembershipCollector(),
)
_REGISTRY = {collector.topic: collector for collector in COLLECTORS}


def collector_for(topic: str) -> Collector:
    try:
        return _REGISTRY[topic]
    except KeyError as exc:
        from ..types import CaptureError
        raise CaptureError("unknown_topic", f"未知采集主题: {topic}",
                           topic=topic) from exc


def registered_topics() -> tuple[str, ...]:
    return tuple(_REGISTRY)


__all__ = [
    "COLLECTORS", "Collector", "AuctionPathCollector", "AuctionFinalCollector",
    "MinuteBarCollector", "DailyBarCollector", "QuoteCollector",
    "MembershipCollector", "collector_for", "registered_topics",
]
