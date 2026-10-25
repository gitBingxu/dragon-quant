"""Pin the E2E clock through the single ``runtime.clock`` seam (M9C).

``DQ_E2E_CLOCK_MODE`` is still read so a caller can be explicit, but ``runtime``
is the **only** accepted value: the legacy ``datetime``/``time`` global
replacement was removed once every production call site needed by E2E had a
verified runtime-clock path.

Installing a ``FixedClock`` into ``dragon_quant.runtime.clock`` leaves
``datetime.datetime`` and ``time.time`` untouched, so ``isinstance(x, datetime)``
and any stdlib/third-party use of the real clock keep their normal semantics.
``time.monotonic`` stays real; ``FixedClock.monotonic`` is a constant and is only
consumed for elapsed-duration measurement.

The application import is deliberately function-local: this module must stay
free of module-level application imports (architecture rule C12).
"""

from __future__ import annotations

import datetime as _datetime_module
import os

INSTALLED = False
FROZEN_ISO = None
MODE = "runtime"

RUNTIME_MODE = "runtime"
CLOCK_MODES = (RUNTIME_MODE,)


def install(now_iso: str) -> str:
    """Install the frozen instant on the runtime seam and return the mode."""
    global INSTALLED, FROZEN_ISO, MODE
    mode = os.environ.get("DQ_E2E_CLOCK_MODE", RUNTIME_MODE)
    if mode not in CLOCK_MODES:
        raise ValueError(
            f"未知 DQ_E2E_CLOCK_MODE: {mode!r}（M9C 只允许 {CLOCK_MODES}）")
    if INSTALLED:
        return MODE

    moment = _datetime_module.datetime.fromisoformat(now_iso)
    if moment.tzinfo is None:
        moment = moment.replace(
            tzinfo=_datetime_module.timezone(_datetime_module.timedelta(hours=8)))
    FROZEN_ISO = moment.isoformat()
    MODE = mode

    _install_runtime_clock(moment)
    INSTALLED = True
    return MODE


def _install_runtime_clock(moment: _datetime_module.datetime) -> None:
    """Pin the application clock seam; no global class replacement."""
    from dragon_quant.runtime import clock as runtime_clock

    runtime_clock.set_clock(runtime_clock.FixedClock(moment))
