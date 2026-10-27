"""logs 命令：扫描日志查询、汇总与清理。"""

from __future__ import annotations

import json


def handle(args, services):
    """日志命令。"""
    from dragon_quant.scan.logger.main import (
        tail_logs, query_logs, clear_logs, list_logs, log_summary,
    )

    if args.logs_action == "tail":
        entries = tail_logs(lines=args.lines, source=args.source)
        for e in entries:
            print(json.dumps(e, ensure_ascii=False))

    elif args.logs_action == "query":
        entries = query_logs(
            date=args.date,
            category=args.category,
            level=args.level,
            code=args.code,
            tail=args.tail,
            source=args.source,
        )
        for e in entries:
            print(json.dumps(e, ensure_ascii=False))

    elif args.logs_action == "clear":
        result = clear_logs(days=args.days, source=args.source)
        print(f"清除日志: {result['cleared']} 条记录")
        print(f"保留: {result['kept']} 条记录")

    elif args.logs_action == "list":
        folders = list_logs(source=args.source)
        if not folders:
            print("(无日志记录)")
        else:
            print(f"{'扫描ID':22s} {'条数':>6s}")
            print("-" * 32)
            for f in folders:
                print(f"{f['scan_id']:22s} {f['entries']:6d}")

    elif args.logs_action == "summary":
        summary = log_summary(date=args.date, source=args.source)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
