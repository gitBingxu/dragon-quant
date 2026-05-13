"""
CLI 入口 — dragon-quant 命令行工具

命令:
  dragon-quant scan [--top 5] [--candidates 5] [--workers 2]
  dragon-quant logs {tail,query,clear,list} [options]
  dragon-quant data {sector,components,kline,minute,quote,batch-quote} [options]
  dragon-quant storage {status,size,clear} [options]
"""

import argparse
import json
import sys

from dragon_quant.orchestrator import scan as orchestrate_scan
from dragon_quant.storage.manager import StorageManager


def _cmd_scan(args):
    """扫描命令"""
    orchestrate_scan(
        top_n=args.top,
        candidates_n=args.candidates,
        workers=args.workers,
        verbose=True,
    )


def _cmd_logs(args):
    """日志命令"""
    from dragon_quant.logging.query import (
        tail_logs, query_logs, clear_logs, list_logs, log_summary,
    )

    if args.logs_action == "tail":
        entries = tail_logs(lines=args.lines)
        for e in entries:
            print(json.dumps(e, ensure_ascii=False))

    elif args.logs_action == "query":
        entries = query_logs(
            date=args.date,
            category=args.category,
            level=args.level,
            code=args.code,
            tail=args.tail,
        )
        for e in entries:
            print(json.dumps(e, ensure_ascii=False))

    elif args.logs_action == "clear":
        result = clear_logs(days=args.days)
        print(f"清除日志: {result['cleared']} 个文件")
        for f in result.get("files_removed", []):
            print(f"  ✓ {f}")
        print(f"保留: {result['kept']} 个文件")

    elif args.logs_action == "list":
        files = list_logs()
        if not files:
            print("(无日志文件)")
        else:
            print(f"{'文件名':30s} {'大小':>8s}  {'时间':20s}  {'行数':>6s}")
            print("-" * 74)
            for f in files:
                print(f"{f['name']:30s} {f['size']:>8s}  {f['mtime']:20s}  {f['lines']:6d}")

    elif args.logs_action == "summary":
        summary = log_summary(date=args.date)
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
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--top", type=int, default=25, help="最终候选股数量 (默认25)")
    shared.add_argument("--candidates", type=int, default=5, help="每板块取前N只 (默认5)")
    shared.add_argument("--workers", type=int, default=2, help="并发线程数 (默认2)")

    parser = argparse.ArgumentParser(
        description="龙头战法四维量化筛选系统",
    )
    parser.set_defaults(command="scan")
    sub = parser.add_subparsers(dest="command")

    # scan 子命令
    scan_p = sub.add_parser("scan", help="批量扫描龙头股", parents=[shared])

    # logs 子命令
    logs_p = sub.add_parser("logs", help="日志查询与管理")
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
    comp_p.add_argument("--sector", required=True, help="板块代码，如 BK0487")

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
    cf_p = data_subs.add_parser("cookie-fetch", help="刷新 Cookie")
    cf_p.add_argument("--source", default="all", choices=["all", "eastmoney", "xueqiu"])

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
    elif args.command == "logs":
        _cmd_logs(args)
    elif args.command == "data":
        _cmd_data(args)
    elif args.command == "storage":
        _cmd_storage(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
