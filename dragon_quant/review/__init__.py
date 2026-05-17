"""
复盘模块 — 对历史扫描结果进行收益回顾

用法:
  from dragon_quant.review import list_scans, review_scan

  scans = list_scans()          # 列出所有历史扫描
  result = review_scan(scan_id) # 复盘指定扫描

CLI:
  python -m dragon_quant review list
  python -m dragon_quant review run --latest
  python -m dragon_quant review run --timestamp 20260510_143000
  python -m dragon_quant review stats
"""

from dragon_quant.review.reviewer import review_scan, list_scans