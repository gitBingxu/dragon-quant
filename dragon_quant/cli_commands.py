"""CLI 子命令处理函数。

从 `cli.py` 拆分而来：所有 `_cmd_*` 处理函数集中于此，供 `dragon_quant.cli.main()`
的 dispatch 调用。`cli.py` 保留 argparse 解析树、共享 helper 与 `main()`。

两个会被测试 patch 的符号（`orchestrate_scan` / `_strategy_params`）必须经 `cli`
模块在**调用时**动态解析，故这里提供转发包装，避免 import 时绑定死引用导致 patch
无法生效（与 `storage/_base.py` 同一模式）。
"""

import json
import sys

from dragon_quant import cli as _cli
from dragon_quant.cli import _normalize_cli_date, _resolve_trade_date, _kbar_to_dict, _to_dict
from dragon_quant.storage.manager import StorageManager


def orchestrate_scan(*args, **kwargs):
    return _cli.orchestrate_scan(*args, **kwargs)


def _strategy_params(*args, **kwargs):
    return _cli._strategy_params(*args, **kwargs)



def _cmd_scan(args):
    """扫描命令（五维「识别真龙」评分器）"""
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


def _cmd_scan_history(args, source: str = "v2"):
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
    """板块黑名单管理"""
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
            print(f"板块黑名单（{len(names)} 个）:")
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
        get_sector_ranking, get_sector_components, get_kline, get_minute_kline, get_quote, batch_get_quotes,
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
        result = fetch_cookies()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.data_action == "cookie-set":
        from dragon_quant.providers.cookie import set_xq
        set_xq(args.cookie)


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
                 default_source=getattr(args, "source", "v2"))


def _cmd_review_account(args):
    """账户级模拟交易 review 命令。"""
    if args.ui_only:
        _cmd_review_account_ui(args)
        return

    if not args.date_from or not args.date_to:
        print("错误: review-account 需要 --from 和 --to，或使用 --ui-only", file=sys.stderr)
        return

    from dragon_quant.review_account import run_review_account
    options: dict = {"strategy_params": _strategy_params(args.config)} if args.config else {}
    run_review_account(
        date_from=_normalize_cli_date(args.date_from),
        date_to=_normalize_cli_date(args.date_to),
        initial_cash=args.capital,
        source=args.source,
        strategy_name=args.strategy,
        verbose=True,
        **(options or {}),
    )

    if args.ui:
        _cmd_review_account_ui(args)


def _cmd_review_account_ui(args):
    """启动账户级 review Web UI。"""
    from web_ui.server import start_server
    start_server(
        port=args.port,
        open_browser=not args.no_browser,
        default_source=getattr(args, "source", "v2"),
        default_page="account",
    )


def _cmd_dragons(args):
    """导出扫描物化的龙头股列表（JSON，固定读取 dragons_v2）。"""
    from dragon_quant.storage import db

    source = "v2"
    if args.date:
        trade_date = _normalize_cli_date(args.date)
    else:
        dates = db.list_dragon_trade_dates(source=source)
        if not dates:
            print(json.dumps({"error": f"无 {source} 龙头记录"},
                             ensure_ascii=False, indent=2))
            sys.exit(1)
        trade_date = dates[-1]

    dragons = db.get_dragons(trade_date, source=source)
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


def _cmd_buy(args):
    """执行当前时点的买入信号（纯信号记账，非模拟账户）。"""
    from dragon_quant.live_trade import run_buy
    trade_date = _resolve_trade_date(args.date)
    try:
        run_buy(trade_date, source=args.source, verbose=True,
                as_of=args.at, strategy_params=_strategy_params(args.config))
    except ValueError as e:
        print(f"⏸ 暂不执行买入：{e}", file=sys.stderr)
        sys.exit(1)


def _cmd_sell(args):
    """执行当前时点的卖出信号（纯信号记账，非模拟账户）。"""
    from dragon_quant.live_trade import run_sell
    trade_date = _resolve_trade_date(args.date)
    try:
        run_sell(trade_date, source=args.source, verbose=True,
                 as_of=args.at, strategy_params=_strategy_params(args.config))
    except ValueError as e:
        print(f"⏸ 暂不执行卖出：{e}", file=sys.stderr)
        sys.exit(1)


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
