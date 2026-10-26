"""Minimal clock contract plus the process-wide runtime seam.

``SystemClock`` is the real adapter and ``FixedClock`` is the test adapter.
There is exactly **one** active clock per process: production code reads current
time only through the module-level seam (``from dragon_quant.infra import
clock``), ``app.build_services`` binds the active instance via ``get_clock()``
instead of constructing a second clock, and the E2E harness installs a
``FixedClock`` with ``set_clock``.  ``datetime``/``time`` are never globally
replaced.

Wall-clock epoch values use ``now().timestamp()``; ``monotonic()`` is only for
elapsed-duration measurement and never for persisted wall-clock deadlines.
"""

import time
from datetime import date, datetime, tzinfo
from typing import Protocol, runtime_checkable

DEFAULT_STAMP_FORMAT = "%Y%m%d_%H%M%S"


@runtime_checkable
class Clock(Protocol):
    def now(self, tz: tzinfo | None = None) -> datetime: ...

    def today(self, tz: tzinfo | None = None) -> date: ...

    def stamp(self, fmt: str = DEFAULT_STAMP_FORMAT,
              tz: tzinfo | None = None) -> str: ...

    def monotonic(self) -> float: ...


class SystemClock:
    """Wall-clock adapter."""

    def now(self, tz: tzinfo | None = None) -> datetime:
        return datetime.now(tz)

    def today(self, tz: tzinfo | None = None) -> date:
        return self.now(tz).date()

    def stamp(self, fmt: str = DEFAULT_STAMP_FORMAT,
              tz: tzinfo | None = None) -> str:
        return self.now(tz).strftime(fmt)

    def monotonic(self) -> float:
        return time.monotonic()


class FixedClock:
    """Test adapter pinned to one moment.

    ``monotonic`` is a constant: a fixed wall clock has no advancing
    monotonic reading, and callers must not infer progress from it.
    """

    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, tz: tzinfo | None = None) -> datetime:
        if tz is None:
            return self._moment
        return self._moment.astimezone(tz)

    def today(self, tz: tzinfo | None = None) -> date:
        return self.now(tz).date()

    def stamp(self, fmt: str = DEFAULT_STAMP_FORMAT,
              tz: tzinfo | None = None) -> str:
        return self.now(tz).strftime(fmt)

    def monotonic(self) -> float:
        return 0.0


_DEFAULT_CLOCK: Clock = SystemClock()
_active_clock: Clock = _DEFAULT_CLOCK


def get_clock() -> Clock:
    """Return the active process-wide clock."""
    return _active_clock


def set_clock(clock: Clock) -> Clock:
    """Install ``clock`` as the active seam; return the previous clock."""
    for attribute in ("now", "today", "stamp", "monotonic"):
        if not callable(getattr(clock, attribute, None)):
            raise TypeError(f"clock 缺少可调用的 {attribute}()")
    global _active_clock
    previous = _active_clock
    _active_clock = clock
    return previous


def reset_clock() -> Clock:
    """Restore the default ``SystemClock``; return the previous clock."""
    global _active_clock
    previous = _active_clock
    _active_clock = _DEFAULT_CLOCK
    return previous


def now(tz: tzinfo | None = None) -> datetime:
    return _active_clock.now(tz)


def today(tz: tzinfo | None = None) -> date:
    return _active_clock.today(tz)


def stamp(fmt: str = DEFAULT_STAMP_FORMAT,
          tz: tzinfo | None = None) -> str:
    return _active_clock.stamp(fmt, tz)


def monotonic() -> float:
    return _active_clock.monotonic()
