"""扫描/回测体系标识：合法 source 白名单与物理分表名映射。

表名只来自 ``VALID_SOURCES`` 白名单，安全用于 SQL 拼接；任何新 source 必须先在
此处登记。
"""

from __future__ import annotations

VALID_SOURCES = {"v1", "v2"}


def _normalize_source(source: str = "v2") -> str:
    """规范化扫描/回测体系来源，只允许 v1 / v2。"""
    s = (source or "v2").lower().strip()
    if s not in VALID_SOURCES:
        raise ValueError(f"invalid source: {source!r}, expected one of {sorted(VALID_SOURCES)}")
    return s


def _tables(source: str = "v2") -> dict[str, str]:
    """返回指定体系的物理表名。表名只来自白名单 source，安全用于 SQL 拼接。"""
    s = _normalize_source(source)
    return {
        "scans": f"scans_{s}",
        "scan_stocks": f"scan_stocks_{s}",
        "scan_logs": f"scan_logs_{s}",
        "dragons": f"dragons_{s}",
    }
