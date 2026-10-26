"""扫描报告文件写入（Phase F 的「报告文本」段）。

只负责把调用方组装好的完整报告文本落到 ``RESULTS_DIR``；不生成内容、不读写
数据库。``RESULTS_DIR`` 在调用时经 :mod:`dragon_quant.infra.storage.paths`
动态读取，测试 patch 路径常量即可重定向（与旧 Phase F 一致）。
"""

from __future__ import annotations

from pathlib import Path

from dragon_quant.infra.storage import paths


def write_scan_report(*, source: str, timestamp: str, text: str) -> Path:
    """写入扫描报告并返回路径；目录不存在时按需创建。"""
    report_path = paths.RESULTS_DIR / f"scan_report_{source}_{timestamp}.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(text)
    return report_path
