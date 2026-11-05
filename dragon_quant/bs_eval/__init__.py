"""BS 策略假想回放（``bs-eval``）。

包门面保持惰性：``import dragon_quant.bs_eval`` 不 import 任何存储/评测实现，
也不创建目录或数据库（B1）。只有显式取 ``run_bs_eval`` 时才加载执行层。
"""

from __future__ import annotations

__all__ = ["run_bs_eval"]


def __getattr__(name: str):
    if name == "run_bs_eval":
        from dragon_quant.bs_eval.main import run_bs_eval

        return run_bs_eval
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
