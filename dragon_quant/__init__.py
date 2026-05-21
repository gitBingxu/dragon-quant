"""
dragon_quant — 龙头战法四维量化筛选系统

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
"""

from dragon_quant._version import __version__
from dragon_quant.orchestrator import scan
from dragon_quant.review import run_review

__all__ = ["scan", "run_review"]
