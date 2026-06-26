"""
CLI 入口 — dragon-quant 命令行工具

命令:
  dragon-quant -v | --version
  dragon-quant scan [--top 5] [--candidates 5] [--workers 2]
  dragon-quant logs {tail,query,clear,list} [options]
  dragon-quant data {sector,components,kline,minute,quote,batch-quote} [options]
  dragon-quant review [--date DATE] [--top N] [--force]
  dragon-quant storage {status,size,clear} [options]
"""

import argparse
import json
import sys

from dragon_quant.orchestrator import scan as orchestrate_scan
from dragon_quant.storage.manager import StorageManager


def _cmd_scan(args):
    """扫描命令（v1 四维评分器）"""
    if args.date:
        _cmd_scan_history(args, source="v1")
        return

    orchestrate_scan(
        top_n=args.top,
        candidates_n=args.candidates,
        workers=args.workers,
        verbose=True,
        force=args.force,
        scorers="v1",
        refresh_provider_cache=args.no_cache,
    )


def _cmd_scan_v2(args):
    """扫描命令（v2 五维「识别真龙」评分器）"""
    if args.date:
        _cmd_scan_history(args, source="v2")
        return

    orchestrate_scan(
        top_n=args.top,
        candidates_n=args.candidates,
        workers=args.workers,
        verbose=True,
        force=args.force,
        scorers="v2",
        refresh_provider_cache=args.no_cache,
    )


def _cmd_scan_history(args, source: str = "v1"):
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


def _cmd_blacklist(args):
    """概念板块黑名单管理"""
    from dragon_quant.storage import db
    action = getattr(args, "blacklist_action", None)
    if action == "add":
        db.add_sector_blacklist(args.name)
        print(f"✅ 已加入黑名单: {args.name}")
    elif action == "remove":
        db.remove_sector_blacklist(args.name)
        print(f"✅ 已移除黑名单: {args.name}")
    else:  # list / 默认
        names = db.get_sector_blacklist()
        if names:
            print(f"概念板块黑名单（{len(names)} 个）:")
            for n in names:
                print(f"  - {n}")
        else:
            print("黑名单为空")


def _cmd_logs(args):
    """日志命令"""
    from dragon_quant.logging.query import (
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


def _cmd_data(args):
    """数据查询命令"""
    from dragon_quant.data import (
        get_sector_ranking, get_sector_components, get_sector_5min_kline,
        get_kline, get_minute_kline, get_quote, batch_get_quotes,
    )

    if args.data_action == "sector":
        sectors = get_sector_ranking(asc=args.asc)
        for s in sectors:
            print(f"{s.code:10s} {s.name:12s} {s.pct:>+8.2f}%")

    elif args.data_action == "components":
        if not args.sector:
            print("错误: 需要 --sector <板块代码>", file=sys.stderr)
            return
        stocks = get_sector_components(args.sector)
        for s in stocks:
            print(f"{s.code:8s} {s.name:8s} {s.pct:>+8.2f}%")

    elif args.data_action == "kline":
        if not args.code:
            print("错误: 需要 --code <股票代码>", file=sys.stderr)
            return
        klines = get_kline(args.code, source=args.source, days=args.days)
        for k in klines:
            print(json.dumps(_kbar_to_dict(k), ensure_ascii=False))

    elif args.data_action == "minute":
        if not args.code:
            print("错误: 需要 --code <股票代码>", file=sys.stderr)
            return
        klines = get_minute_kline(args.code, source=args.source)
        for k in klines:
            print(json.dumps(_kbar_to_dict(k), ensure_ascii=False))

    elif args.data_action == "quote":
        if not args.code:
            print("错误: 需要 --code <股票代码>", file=sys.stderr)
            return
        q = get_quote(args.code, source=args.source)
        if q:
            print(json.dumps(_to_dict(q), ensure_ascii=False, indent=2))
        else:
            print(f"获取行情失败: {args.code}")

    elif args.data_action == "batch-quote":
        if not args.codes:
            print("错误: 需要 --codes <代码列表,逗号分隔>", file=sys.stderr)
            return
        codes = [c.strip() for c in args.codes.split(",")]
        quotes = batch_get_quotes(codes, source=args.source)
        for q in quotes:
            if q:
                print(json.dumps(_to_dict(q), ensure_ascii=False))

    elif args.data_action == "cookie-status":
        from dragon_quant.data import cookie_status
        print(json.dumps(cookie_status(), ensure_ascii=False, indent=2))

    elif args.data_action == "cookie-fetch":
        from dragon_quant.data import fetch_cookies
        result = fetch_cookies(source=args.source)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.data_action == "cookie-set":
        from dragon_quant.providers.cookie import set_em, set_em_his, set_xq
        setters = {"em": set_em, "em_his": set_em_his, "xq": set_xq}
        setters[args.source](args.cookie)


def _cmd_review(args):
    """龙头回测命令"""
    from dragon_quant.review import run_review

    date_str = None
    if args.date:
        d = args.date
        if len(d) == 8:
            date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        else:
            date_str = d

    # --ui-only: 只启动 UI
    if args.ui_only:
        _cmd_review_ui(args)
        return

    # 正常回测
    run_review(
        trade_date=date_str,
        top_n=args.top,
        force=args.force,
        verbose=True,
        source=args.source,
    )

    # --ui: 回测后启动 UI
    if args.ui:
        _cmd_review_ui(args)


def _cmd_review_ui(args):
    """启动 Web UI 服务器"""
    from web_ui.server import start_server
    start_server(port=args.port, open_browser=not args.no_browser,
                 default_source=getattr(args, "source", "v1"))


def _cmd_vpa(args):
    """个股量价分析命令"""
    import json
    from datetime import datetime
    from dragon_quant.vpa import analyze
    from dragon_quant.vpa.report import render
    from dragon_quant._version import __version__

    report = analyze(args.code, source=args.source, days=args.days)
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
                trade_date=datetime.now().strftime("%Y-%m-%d"),
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


def _cmd_storage(args):
    """存储管理命令"""
    mgr = StorageManager()

    if args.storage_action == "status":
        s = mgr.status()
        print(f"数据根目录: {s['data_dir']}")
        print(f"{'目录':10s} {'文件数':>6s} {'大小':>8s}")
        print("-" * 28)
        for key in ("cookies", "cache", "logs", "results", "shared"):
            d = s[key]
            if d["exists"]:
                print(f"{key:10s} {d['files']:6d} {d['size']:>8s}")
            else:
                print(f"{key:10s}  (不存在)")

    elif args.storage_action == "clear":
        if args.all:
            r = mgr.clear_all()
            for k, v in r.items():
                print(f"  清理 {k}: {v} 个文件")
        else:
            if args.cache:
                n = mgr.clear_cache()
                print(f"  清理 cache: {n} 个文件")
            if args.results:
                n = mgr.clear_results(days=args.days)
                print(f"  清理 results: {n} 个文件")
                if args.days:
                    print(f"    (保留最近 {args.days} 天)")
            if args.logs:
                n = mgr.clear_logs(days=args.days)
                print(f"  清理 logs: {n} 个文件")
                if args.days:
                    print(f"    (保留最近 {args.days} 天)")

    elif args.storage_action == "size":
        s = mgr.size()
        print(f"总占用: {s['total']}")
        from dragon_quant.storage.manager import _fmt_size
        for k, b in s["by_dir"].items():
            print(f"  {k}: {_fmt_size(b)}")



def _kbar_to_dict(kbar) -> dict:
    """KBar → dict"""
    d = {}
    for f in ("time", "open", "close", "high", "low", "volume", "pct"):
        v = getattr(kbar, f, None)
        if isinstance(v, float):
            v = round(v, 4)
        d[f] = v
    return d


def _to_dict(obj) -> dict:
    """dataclass → dict"""
    if hasattr(obj, '__dataclass_fields__'):
        return {f.name: _to_dict(getattr(obj, f.name)) for f in obj.__dataclass_fields__.values()}
    if isinstance(obj, list):
        return [_to_dict(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj


def main():
    from dragon_quant._version import __version__

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--top", type=int, default=25, help="最终候选股数量 (默认25)")
    shared.add_argument("--candidates", type=int, default=5, help="每板块取前N只 (默认5)")
    shared.add_argument("--workers", type=int, default=2, help="并发线程数 (默认2)")
    shared.add_argument("--force", action="store_true",
                        help="强制执行 (跳过交易时段拦截 + DB 结果缓存；仍复用 provider 磁盘缓存)")
    shared.add_argument("--no-cache", action="store_true",
                        help="跳过 provider 按交易日磁盘缓存，强制各数据源重新拉取并刷新缓存")

    parser = argparse.ArgumentParser(
        prog="dragon-quant",
        description="龙头战法量化筛选系统",
    )
    parser.add_argument("-v", "--version", action="version",
                        version=f"%(prog)s {__version__}")
    parser.set_defaults(command="scan")
    sub = parser.add_subparsers(dest="command")

    # scan 子命令（v1 四维评分器）
    scan_p = sub.add_parser("scan", help="批量扫描龙头股（v1 四维）", parents=[shared])
    scan_p.add_argument("--date", default=None,
                        help="查询历史扫描记录 (YYYYMMDD)，指定后不执行实时扫描")

    # scan_v2 子命令（v2 五维「识别真龙」评分器）
    scan_v2_p = sub.add_parser("scan_v2", help="批量扫描龙头股（v2 五维识别真龙）",
                               parents=[shared])
    scan_v2_p.add_argument("--date", default=None,
                           help="查询历史扫描记录 (YYYYMMDD)，指定后不执行实时扫描")

    # logs 子命令
    logs_p = sub.add_parser("logs", help="日志查询与管理")
    logs_p.add_argument("--source", default="v1", choices=["v1", "v2"],
                        help="日志来源体系 (默认 v1)")
    logs_subs = logs_p.add_subparsers(dest="logs_action")

    tail_p = logs_subs.add_parser("tail", help="查看最新日志")
    tail_p.add_argument("-n", "--lines", type=int, default=20, help="返回行数 (默认20)")

    query_p = logs_subs.add_parser("query", help="按条件查询日志")
    query_p.add_argument("--date", help="日期 (YYYYMMDD)")
    query_p.add_argument("--category", help="类别过滤，如 phase、api、scorer:drive")
    query_p.add_argument("--level", help="级别过滤，如 info、warn、error")
    query_p.add_argument("--code", help="股票代码过滤")
    query_p.add_argument("--tail", type=int, default=200, help="最多返回条数 (默认200)")

    clear_logs_p = logs_subs.add_parser("clear", help="清除旧日志")
    clear_logs_p.add_argument("--days", type=int, default=7, help="保留最近N天 (默认7)")

    logs_subs.add_parser("list", help="列出所有日志文件")
    sum_p = logs_subs.add_parser("summary", help="最新扫描摘要")
    sum_p.add_argument("--date", help="日期过滤")

    # data 子命令
    data_p = sub.add_parser("data", help="原子数据查询")
    data_subs = data_p.add_subparsers(dest="data_action")

    sector_p = data_subs.add_parser("sector", help="板块排行榜")
    sector_p.add_argument("--asc", action="store_true", help="跌幅榜（默认涨幅榜）")

    comp_p = data_subs.add_parser("components", help="板块成分股")
    comp_p.add_argument("--sector", required=True, help="同花顺概念板块 6 位代码，如 301558")

    kline_p = data_subs.add_parser("kline", help="个股日K线")
    kline_p.add_argument("--code", required=True, help="股票代码")
    kline_p.add_argument("--source", default="xueqiu", choices=["xueqiu", "tencent"])
    kline_p.add_argument("--days", type=int, default=20)

    min_p = data_subs.add_parser("minute", help="个股1分钟K线（分时）")
    min_p.add_argument("--code", required=True, help="股票代码")
    min_p.add_argument("--source", default="xueqiu", choices=["xueqiu", "tencent"])

    quote_p = data_subs.add_parser("quote", help="个股实时行情")
    quote_p.add_argument("--code", required=True, help="股票代码")
    quote_p.add_argument("--source", default="tencent", choices=["tencent", "xueqiu"])

    bq_p = data_subs.add_parser("batch-quote", help="批量实时行情")
    bq_p.add_argument("--codes", required=True, help="股票代码，逗号分隔")
    bq_p.add_argument("--source", default="tencent", choices=["tencent", "xueqiu"])

    data_subs.add_parser("cookie-status", help="查看 Cookie 状态")
    cf_p = data_subs.add_parser("cookie-fetch",
                                help="刷新 Cookie（默认仅雪球；东财需显式 --source eastmoney）")
    cf_p.add_argument("--source", default="all", choices=["all", "eastmoney", "xueqiu"],
                      help="all=仅雪球(默认) eastmoney=东财 xueqiu=雪球")

    cs_p = data_subs.add_parser("cookie-set", help="手动设置 Cookie")
    cs_p.add_argument("--cookie", "-c", required=True, help="完整 Cookie 字符串")
    cs_p.add_argument("--source", required=True, choices=["em", "em_his", "xq"],
                      help="em=东财push2 em_his=东财push2his xq=雪球")

    # blacklist 子命令（概念板块黑名单，拉取领涨/领跌板块时过滤）
    bl_p = sub.add_parser("blacklist", help="概念板块黑名单管理")
    bl_subs = bl_p.add_subparsers(dest="blacklist_action")
    bl_subs.add_parser("list", help="列出黑名单")
    bl_add_p = bl_subs.add_parser("add", help="新增黑名单概念")
    bl_add_p.add_argument("name", help="概念名称（子串匹配，如 次新股）")
    bl_rm_p = bl_subs.add_parser("remove", help="移除黑名单概念")
    bl_rm_p.add_argument("name", help="概念名称")

    # review 子命令
    rev_p = sub.add_parser("review", help="龙头回测验证")
    rev_p.add_argument("--date", default=None, help="只回测指定日期 (YYYYMMDD)")
    rev_p.add_argument("--top", type=int, default=None, help="只回测 top N")
    rev_p.add_argument("--force", action="store_true", help="无视 review_status 全部重算")
    rev_p.add_argument("--source", default="v1", choices=["v1", "v2"],
                       help="回测数据来源体系：v1=dragons_v1，v2=dragons_v2 (默认 v1)")
    rev_p.add_argument("--ui", action="store_true", help="回测后启动 Web UI")
    rev_p.add_argument("--ui-only", action="store_true", help="仅启动 Web UI（不执行回测）")
    rev_p.add_argument("--port", type=int, default=8765, help="Web UI 端口 (默认 8765)")
    rev_p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    # vpa 子命令
    vpa_p = sub.add_parser("vpa", help="个股量价分析")
    vpa_p.add_argument("--code", required=True, help="股票代码，如 600519")
    vpa_p.add_argument("--source", default="xueqiu", choices=["xueqiu", "tencent"])
    vpa_p.add_argument("--days", type=int, default=60, help="拉取日K线根数 (默认60)")
    vpa_p.add_argument("--no-save", action="store_true", help="不写入数据库")

    # storage 子命令
    st_p = sub.add_parser("storage", help="持久化数据管理")
    st_subs = st_p.add_subparsers(dest="storage_action")

    st_subs.add_parser("status", help="查看存储状态")
    st_subs.add_parser("size", help="查看磁盘占用")

    clear_p = st_subs.add_parser("clear", help="清理数据")
    clear_p.add_argument("--all", action="store_true", help="清理全部(cache+results+logs)")
    clear_p.add_argument("--cache", action="store_true", help="清理缓存")
    clear_p.add_argument("--results", action="store_true", help="清理结果")
    clear_p.add_argument("--logs", action="store_true", help="清理日志")
    clear_p.add_argument("--days", type=int, default=None, help="保留最近N天")

    args = parser.parse_args()

    if args.command == "scan":
        _cmd_scan(args)
    elif args.command == "scan_v2":
        _cmd_scan_v2(args)
    elif args.command == "logs":
        _cmd_logs(args)
    elif args.command == "data":
        _cmd_data(args)
    elif args.command == "storage":
        _cmd_storage(args)
    elif args.command == "review":
        _cmd_review(args)
    elif args.command == "vpa":
        _cmd_vpa(args)
    elif args.command == "blacklist":
        _cmd_blacklist(args)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
