"""
dragon_quant — 龙头战法五维量化筛选系统

公共 API:
  from dragon_quant import scan          # 可调用扫描包：scan(...) 等价 orchestrator.scan
  from dragon_quant.review import run_review  # 龙头回测
  from dragon_quant.infra.provider.data import (        # 原子数据查询
      get_sector_ranking, get_sector_components, get_sector_5min_kline,
      get_kline, get_minute_kline, get_quote, batch_get_quotes,
  )
  from dragon_quant.scan.logger.query import (  # 日志查询
      tail_logs, query_logs, clear_logs, list_logs, log_summary,
  )

``scan`` 自 2026-10-25 起是**可调用包**（``dragon_quant/scan/``）：``scan(...)`` 在
调用时转发到 ``scan.orchestrator.main.scan``，``from dragon_quant import scan``
得到的仍是可调用对象，且 ``dragon_quant.scan.scan`` 与
``scan.orchestrator.main.scan`` 是同一函数对象。``run_review``
为 PEP 562 惰性导出。

导入本包不加载 orchestrator / review / storage，也不创建任何数据目录或数据库
（B1）。首次访问属性时才导入对应模块。
"""

import importlib

from dragon_quant._version import __version__

__all__ = ["scan", "run_review"]

_LAZY_EXPORTS = {
    "run_review": "dragon_quant.review",
}
_SCAN_PACKAGE = "dragon_quant.scan"


def __getattr__(name: str):
    if name == "scan":
        value = importlib.import_module(_SCAN_PACKAGE)
        globals()[name] = value
        return value
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
