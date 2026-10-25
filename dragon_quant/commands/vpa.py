"""vpa 命令：执行独立的个股量价健康度分析并落库。"""

from __future__ import annotations

import json
import sys

from dragon_quant.runtime import clock


def handle(args, services):
    """个股量价分析命令。"""
    from dragon_quant.vpa import analyze
    from dragon_quant.vpa.report import render
    from dragon_quant._version import __version__

    report = analyze(args.code, source=args.source, days=args.days, services=services)
    print(render(report))

    if not args.no_save and not report.fallback:
        from dragon_quant.storage import db
        factors = [
            {"name": f.name, "title": f.title, "signal": f.signal,
             "score": f.score, "note": f.note,
             "evidence": f.evidence, "details": f.details}
            for f in report.factors
        ]
        try:
            db.upsert_vpa(
                trade_date=clock.now().strftime("%Y-%m-%d"),
                code=report.code,
                source=report.source,
                health_score=report.health_score,
                signal=report.signal,
                summary=report.summary,
                factors_json=json.dumps(factors, ensure_ascii=False),
                version=__version__,
            )
        except Exception as ex:
            print(f"⚠️ 写入数据库失败: {ex}", file=sys.stderr)
