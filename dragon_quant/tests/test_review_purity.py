"""M6 纯函数边界（技术方案 §15.4）。

``review_account/prep/review/*`` 在 **import 期与调用期** 都不得触发
``open`` / ``socket`` / ``sqlite3.connect``。用真实子进程验证 import 期，
调用期用 monkeypatch 抛错的哨兵函数验证。
"""

import builtins
import socket
import sqlite3
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.review_account.prep.review import (
    CONFIG,
    build_context,
    compute_feedback,
    compute_plan_review,
    compute_review,
    derive_anchors,
    derive_pool_codes,
    evaluate_gates,
    portfolio_summary,
    score_candidate,
    size_position,
)
from dragon_quant.review_account.prep.review.mainline import compute_mainlines
from dragon_quant.review_account.prep.review.market import compute_market_state
from dragon_quant.review_account.prep.review.roles import assign_roles

REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEW_DIR = REPO_ROOT / "dragon_quant" / "review_account" / "prep" / "review"
DAY = "2026-10-28"

_IMPORT_PROBE = textwrap.dedent(
    """
    import os, sys

    review_dir = os.path.abspath(sys.argv[1])
    failures = []

    def hook(event, args):
        if event == "open":
            path = args[0]
            if not isinstance(path, (str, bytes)):
                return
            target = os.path.abspath(os.fsdecode(path))
            # importlib 读取 .py/.pyc 是导入机制本身，不是模块主动 I/O。
            if target.endswith((".py", ".pyc", ".pyo")):
                return
            if target.startswith(review_dir):
                failures.append("review package opened file: " + target)
                raise AssertionError(failures[-1])
        elif event in ("socket.__new__", "socket.connect", "socket.getaddrinfo"):
            failures.append("network call: " + event)
            raise AssertionError(failures[-1])
        elif event in ("sqlite3.connect", "sqlite3.connect/handle"):
            failures.append("sqlite3.connect")
            raise AssertionError(failures[-1])

    sys.addaudithook(hook)
    import dragon_quant.review_account.prep.review as review
    assert review.CONFIG_VERSION
    """
)


class TestImportPurity(unittest.TestCase):
    def test_import_does_not_touch_io(self):
        result = subprocess.run(
            [sys.executable, "-c", _IMPORT_PROBE, str(REVIEW_DIR)],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0,
                         f"stdout={result.stdout}\nstderr={result.stderr}")


class TestCallPurity(unittest.TestCase):
    def _guard(self):
        def boom(*args, **kwargs):
            raise AssertionError("call-time I/O detected")
        return patch.multiple(builtins, open=boom), \
            patch.object(socket, "socket", boom), \
            patch.object(sqlite3, "connect", boom)

    def test_pure_calls_do_not_touch_io(self):
        candidate = {
            "height": 3, "intra_board_rank": 1, "board_limit_up_num": 5,
            "mainline_rank": 1, "attr_match": 2, "mainline_days": 3,
            "first_seal_time": "09:41", "open_num": 0, "seal_amount": 1e8,
            "float_market_cap": 1e10, "seal_ratio": 0.4,
            "main_net_amount": 5e7, "amount": 5e8, "turnover_rate": 12.0,
            "distance_high60": 3.0, "chip_concentration": 0.02,
            "profit_ratio": 0.8, "limit_up_cnt_20d": 2,
            "next_day_up_rate_20d": 0.6, "next_day_up_sample": 5,
        }
        guards = self._guard()
        for guard in guards:
            guard.start()
        try:
            score_candidate(candidate, {"role": "leader"}, CONFIG)
            evaluate_gates({"role": "leader", "review_score": 80.0,
                            "review_grade": "A", "confidence": 0.9,
                            "coverage": 0.9, "pool_available": True,
                            "t_pct": 10.0, "amount": 5e8,
                            "risk": {"level": "none"}}, {}, CONFIG)
            compute_market_state({}, None, CONFIG)
            compute_mainlines({}, None, CONFIG)
            derive_pool_codes({}, CONFIG)
            derive_anchors({})
            assign_roles([], [], CONFIG)
            size_position({"role": "leader", "review_score": 80.0,
                           "review_grade": "A", "confidence": 0.9,
                           "gate_status": "eligible", "risk_coef": 1.0,
                           "liq_coef": 1.0, "one_word_coef": 1.0},
                          {"state": "neutral", "coeff": 1.0}, CONFIG)
            portfolio_summary([], CONFIG)
            review = compute_review({}, DAY)
            build_context(review, CONFIG)
            compute_feedback({}, {}, None, {}, DAY, config=CONFIG)
            compute_plan_review({}, {}, None, {}, DAY, config=CONFIG)
        finally:
            for guard in guards:
                guard.stop()


if __name__ == "__main__":
    unittest.main()
