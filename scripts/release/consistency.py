"""Bounded registry-propagation waiting, shared by the PyPI and npm adapters.

A registry accepts an upload before every edge cache and read replica agrees the
artifact exists.  PyPI's project JSON is served with ``Cache-Control: max-age=900``
and npm's read replica lags by seconds, so a single immediate query cannot tell
"the upload never happened" apart from "the upload happened and is still
propagating" — the real incident saw twine succeed and a probe 9 s later report
the version missing.

This module is the *strategy* layer: a probe either settles or raises
:class:`PendingVisibility`, and :func:`wait_for_settled` re-probes on a fixed
interval until a monotonic deadline.  The adapters keep owning registry
specifics (endpoints, JSON/CLI parsing, conflict detection) and only classify
"not visible yet" versus "terminal" here, so PyPI and npm do not grow two
bespoke retry loops.

Nothing here writes: the wait is a read-only reconciliation, so a resume that
already uploaded never publishes again.
"""

from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, NoReturn

from release.common import ProgressReporter, ReleaseError, progress_task

# 15 s keeps a whole 900 s project-JSON cache window inside the budget while
# still reporting progress often enough to look alive.
PROPAGATION_INTERVAL = 15.0
PROPAGATION_BUDGET = 960.0
BUDGET_ENV = "DQ_RELEASE_PROPAGATION_BUDGET"
INTERVAL_ENV = "DQ_RELEASE_PROPAGATION_INTERVAL"


class PendingVisibility(ReleaseError):
    """The registry has not converged yet; re-probing is safe within the budget.

    It subclasses :class:`ReleaseError` on purpose: a caller that is *not*
    waiting (preflight, a dry-run-adjacent probe) then still fails closed with a
    normal release error instead of leaking an unexpected exception type, and
    a network failure can never be read as "the artifact does not exist".
    """


class PropagationExhausted(ReleaseError):
    """The bounded budget ran out; the step stays resumable, nothing was undone."""


@dataclass(frozen=True)
class WaitPolicy:
    """Injectable budget / interval / clock / sleep so tests never really wait.

    ``budget`` and ``interval`` must be finite and strictly positive: an
    infinite budget turns the bounded wait back into an unbounded poll, and an
    infinite interval makes ``sleep`` raise.  Environment overrides are
    sanitized separately (a bad value falls back to the default); an explicit
    policy is a deliberate statement and is rejected outright instead.
    """

    budget: float = PROPAGATION_BUDGET
    interval: float = PROPAGATION_INTERVAL
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        for name in ("budget", "interval"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value) or value <= 0:
                raise ValueError(
                    f"WaitPolicy.{name} 必须是有限正数（秒），收到 {value!r}")


def _positive_env(name: str, default: float) -> float:
    """Finite, positive env override, else the default (never inf/nan/0)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


def default_policy() -> WaitPolicy:
    """Production policy, overridable for tests/ops via env (seconds)."""
    return WaitPolicy(
        budget=_positive_env(BUDGET_ENV, PROPAGATION_BUDGET),
        interval=_positive_env(INTERVAL_ENV, PROPAGATION_INTERVAL),
    )


def emitter(progress: ProgressReporter | None) -> Callable[[str], None]:
    """Progress goes through the reporter (stderr); fall back to stderr."""
    if progress is not None:
        return progress.emit

    def emit(text: str) -> None:
        print(text, file=sys.stderr, flush=True)

    return emit


def wait_for_settled(
    probe: Callable[[float], object],
    *,
    stage: str,
    subject: str,
    policy: WaitPolicy | None = None,
    progress: ProgressReporter | None = None,
    label: str | None = None,
):
    """Call ``probe(remaining_budget)`` until it settles or the budget is spent.

    ``probe`` returns the settled value, or raises :class:`PendingVisibility`
    for a state that may still converge.  Any other exception fails at once, so
    a genuine hash/integrity conflict is never hidden behind a wait.  Each probe
    receives the remaining budget so a single slow call cannot be given more
    time than the budget has left.

    The whole settle context — first probe, waits and every re-probe — runs
    inside one :class:`ProgressReporter` task, so a slow ``urlopen`` or
    ``npm view`` is covered by the reporter's heartbeat instead of going silent
    until it returns.  Without a reporter the waiter still works (wait notices
    fall back to stderr) and no thread is created.
    """
    policy = policy or default_policy()
    emit = emitter(progress)
    started = policy.clock()
    deadline = started + policy.budget
    attempt = 0

    def exhausted(cause: BaseException | None) -> NoReturn:
        message = (
            f"{subject}在 {policy.budget:.0f} 秒内仍未传播可见；"
            "上传已成功，这不是‘不存在’，可用 --resume 重新对账继续"
        )
        if cause is None:
            raise PropagationExhausted(stage, message)
        raise PropagationExhausted(stage, message) from cause

    with progress_task(progress, label or f"等待{subject}传播"):
        while True:
            remaining = deadline - policy.clock()
            if remaining <= 0:
                # The deadline is a hard stop: never launch another query with
                # a non-positive timeout after the budget is gone.
                exhausted(None)
            attempt += 1
            try:
                return probe(remaining)
            except PendingVisibility as exc:
                elapsed = policy.clock() - started
                remaining = deadline - policy.clock()
                emit(
                    f"… 等待{subject}传播：第 {attempt} 次仍未就绪（{exc}）；"
                    f"已耗时 {elapsed:.0f}s，剩余预算 {max(remaining, 0.0):.0f}s"
                )
                if remaining <= 0:
                    exhausted(exc)
                policy.sleep(min(policy.interval, remaining))
