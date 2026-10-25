"""scan 命令：五维「识别真龙」批量扫描，或导出历史扫描记录。"""

from __future__ import annotations

import json
import sys


def handle(args, services):
    """扫描命令（五维「识别真龙」评分器）。"""
    if args.date:
        _scan_history(args, source="v2")
        return

    from dragon_quant.orchestrator import scan as orchestrate_scan

    orchestrate_scan(
        top_n=args.top,
        candidates_n=args.candidates,
        workers=args.workers,
        verbose=True,
        force=args.force,
        scorers="v2",
        refresh_provider_cache=args.no_cache,
        services=services,
    )


def _scan_history(args, source: str = "v2"):
    """查询历史扫描记录"""
    d = args.date
    if len(d) == 8:
        date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    else:
        print("错误: --date 格式应为 YYYYMMDD", file=sys.stderr)
        return

    from dragon_quant.storage import db
    scan = db.get_latest_scan_by_date(date_str, args.top, source=source)
    if scan and scan.get("raw_output"):
        output = json.loads(scan["raw_output"])
        print(json.dumps(output, ensure_ascii=False, indent=2))
    elif scan:
        print(json.dumps({
            "error": "scan raw_output is empty",
            "scan_id": scan["id"],
            "scan_date": scan["scan_date"],
            "top_n": scan["top_n"],
            "source": source,
        }, ensure_ascii=False, indent=2))
    else:
        scans = db.get_scans_by_date(date_str, source=source)
        if scans:
            tops = sorted(set(s["top_n"] for s in scans))
            print(json.dumps({
                "error": f"未找到 {date_str} 下 {source} top_n={args.top} 的记录",
                "available_top_n": tops,
            }, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({
                "error": f"未找到 {date_str} 的 {source} 扫描记录",
            }, ensure_ascii=False, indent=2))
