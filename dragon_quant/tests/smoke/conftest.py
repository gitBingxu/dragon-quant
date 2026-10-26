"""Opt-in, non-gating real-network smoke harness.

Default behavior is SKIP: no socket is opened unless the operator explicitly sets
``DQ_RUN_NETWORK_SMOKE=1``.  These cases are never part of an M0/M1 gate and are
not collected by ``pytest dragon_quant/tests/e2e`` or by the unit gate (which ignores
``dragon_quant/tests/smoke``).

Rules enforced by the cases themselves:

* only read-only commands;
* a throwaway ``DQ_DATA_DIR`` / ``HOME`` so no real database or cookie is read
  or written;
* failures are reported but never change a gate verdict.
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("DQ_RUN_NETWORK_SMOKE") == "1":
        return
    marker = pytest.mark.skip(
        reason="网络冒烟默认关闭；设置 DQ_RUN_NETWORK_SMOKE=1 才运行（非门禁）"
    )
    for item in items:
        item.add_marker(marker)
