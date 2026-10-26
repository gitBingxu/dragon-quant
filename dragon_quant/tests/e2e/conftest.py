"""E2E session guard: never touch the real data directory.

The guard only uses ``os.stat`` metadata (see ``harness/sentinel.py``).  It is
deliberately independent from application code: importing the
``dragon_quant.tests`` namespace only loads the lazily-initialized package shell
and never ``dragon_quant`` feature modules.
"""

import sys

# Keep child-process and harness imports from dropping ``__pycache__`` into the
# repository (the harness itself is stdlib-only and must stay cheap to audit).
sys.dont_write_bytecode = True

import pytest  # noqa: E402

from dragon_quant.tests.e2e.harness.sentinel import RealDataSentinel  # noqa: E402


@pytest.fixture(autouse=True)
def real_data_metadata_guard():
    """Fail the test if the real data directory metadata changes at all."""

    sentinel = RealDataSentinel()
    before = sentinel.snapshot()
    try:
        yield
    finally:
        sentinel.assert_unchanged(before, sentinel.snapshot())
