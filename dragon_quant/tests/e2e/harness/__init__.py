"""Offline E2E safety harness (stdlib only).

Modules here must never import ``dragon_quant`` or ``pytest``: the harness has
to bootstrap the E2E gate before any production refactor exists, and a broken
application import must not be able to break the safety net itself.
"""

from .sandbox import E2EHarnessError, EnvSandbox

__all__ = ["E2EHarnessError", "EnvSandbox"]
