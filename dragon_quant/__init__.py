"""
dragon_quant — 龙头战法五维量化筛选系统

公共 API:
  from dragon_quant import scan          # 编排器：完整扫描
  from dragon_quant.review import run_review  # 龙头回测
  from dragon_quant.data import (        # 原子数据查询
      get_sector_ranking, get_sector_components, get_sector_5min_kline,
      get_kline, get_minute_kline, get_quote, batch_get_quotes,
  )
  from dragon_quant.logging.query import (  # 日志查询
      tail_logs, query_logs, clear_logs, list_logs, log_summary,
  )

``scan`` / ``run_review`` 为 PEP 562 惰性导出：导入本包不加载 orchestrator /
review / storage，也不创建任何数据目录或数据库（B1）。首次访问属性时才导入，
并把结果缓存进模块命名空间，``from dragon_quant import scan`` 与
``from dragon_quant.orchestrator import scan`` 保持同一函数对象。
"""

import importlib

from dragon_quant._version import __version__

__all__ = ["scan", "run_review"]

_LAZY_EXPORTS = {
    "scan": "dragon_quant.orchestrator",
    "run_review": "dragon_quant.review",
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
