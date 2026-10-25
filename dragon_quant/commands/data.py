"""data 命令：板块 / 成分股 / K 线 / 行情 / Cookie 的原子查询。"""

from __future__ import annotations

import json
import sys

from dragon_quant.commands._shared import kbar_to_dict, to_dict


def handle(args, services):
    """数据查询命令。"""
    from dragon_quant.data import (
        get_sector_ranking, get_sector_components, get_kline, get_minute_kline,
        get_quote, batch_get_quotes,
    )

    if args.data_action == "sector":
        sectors = get_sector_ranking(asc=args.asc, services=services)
        for s in sectors:
            print(f"{s.code:10s} {s.name:12s} {s.pct:>+8.2f}%")

    elif args.data_action == "components":
        if not args.sector:
            print("错误: 需要 --sector <板块代码>", file=sys.stderr)
            return
        stocks = get_sector_components(args.sector, services=services)
        for s in stocks:
            print(f"{s.code:8s} {s.name:8s} {s.pct:>+8.2f}%")

    elif args.data_action == "kline":
        if not args.code:
            print("错误: 需要 --code <股票代码>", file=sys.stderr)
            return
        klines = get_kline(args.code, source=args.source, days=args.days,
                           services=services)
        for k in klines:
            print(json.dumps(kbar_to_dict(k), ensure_ascii=False))

    elif args.data_action == "minute":
        if not args.code:
            print("错误: 需要 --code <股票代码>", file=sys.stderr)
            return
        klines = get_minute_kline(args.code, source=args.source, services=services)
        for k in klines:
            print(json.dumps(kbar_to_dict(k), ensure_ascii=False))

    elif args.data_action == "quote":
        if not args.code:
            print("错误: 需要 --code <股票代码>", file=sys.stderr)
            return
        q = get_quote(args.code, source=args.source, services=services)
        if q:
            print(json.dumps(to_dict(q), ensure_ascii=False, indent=2))
        else:
            print(f"获取行情失败: {args.code}")

    elif args.data_action == "batch-quote":
        if not args.codes:
            print("错误: 需要 --codes <代码列表,逗号分隔>", file=sys.stderr)
            return
        codes = [c.strip() for c in args.codes.split(",")]
        quotes = batch_get_quotes(codes, source=args.source, services=services)
        for q in quotes:
            if q:
                print(json.dumps(to_dict(q), ensure_ascii=False))

    elif args.data_action == "cookie-status":
        from dragon_quant.data import cookie_status
        print(json.dumps(cookie_status(), ensure_ascii=False, indent=2))

    elif args.data_action == "cookie-fetch":
        from dragon_quant.data import fetch_cookies
        result = fetch_cookies()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.data_action == "cookie-set":
        from dragon_quant.providers.cookie import set_xq
        set_xq(args.cookie)
