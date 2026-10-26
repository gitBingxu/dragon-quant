"""dragons 命令：导出扫描物化的龙头股列表（JSON，固定读取 dragons_v2）。"""

from __future__ import annotations

import json
import sys

from dragon_quant.commands._shared import normalize_cli_date


def handle(args, services):
    """导出扫描物化的龙头股列表（JSON，固定读取 dragons_v2）。"""
    from dragon_quant.scan.storage.dragons import (
        get_dragons, list_dragon_trade_dates,
    )

    source = "v2"
    if args.date:
        trade_date = normalize_cli_date(args.date)
    else:
        dates = list_dragon_trade_dates(source=source)
        if not dates:
            print(json.dumps({"error": f"无 {source} 龙头记录"},
                             ensure_ascii=False, indent=2))
            sys.exit(1)
        trade_date = dates[-1]

    dragons = get_dragons(trade_date, source=source)
    if args.true_only:
        dragons = [d for d in dragons if d.get("is_true_dragon") is True]

    if not dragons:
        print(json.dumps({"error": f"未找到 {trade_date} 的 {source} 龙头记录"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    print(json.dumps({
        "trade_date": trade_date,
        "source": source,
        "count": len(dragons),
        "dragons": dragons,
    }, ensure_ascii=False, indent=2))
