"""R3：evidence 公共门面必须惰性提供 I/O 用例，纯核导入不得加载仓储。"""

from __future__ import annotations

import subprocess
import sys

_PROBE = r"""
import sys
import dragon_quant.review_account.strategy.bs_policy  # noqa: F401  纯核导入
leaked = sorted(
    name for name in (
        "dragon_quant.infra.assets.db",
        "dragon_quant.infra.assets.observations.write",
        "dragon_quant.review_account.evidence.store_adapter",
    ) if name in sys.modules)
print("LEAKED=" + ",".join(leaked))
import dragon_quant.review_account.evidence as ev
_ = ev.build_snapshot  # 显式用例才加载只读仓储适配
print("LOADED=" + str("dragon_quant.review_account.evidence.store_adapter"
                      in sys.modules))
"""


def test_bs_policy_import_does_not_load_store_io(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True,
        env={"DQ_DATA_DIR": str(tmp_path / "dq"), "PYTHONPATH": "."},
    )
    assert completed.returncode == 0, completed.stderr
    lines = dict(line.split("=", 1) for line in completed.stdout.split()
                 if "=" in line)
    assert lines["LEAKED"] == ""
    assert lines["LOADED"] == "True"
    # 纯核导入不得创建任何数据目录。
    assert not (tmp_path / "dq").exists()
