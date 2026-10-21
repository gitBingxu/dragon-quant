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


def _prep_plan_hint(plan):
    """增量采集计划提示（stderr）。

    走 stderr 是为了保持 stdout 的「4 行摘要 + 标的清单」契约不被污染；
    cron 只按退出码判失败，stderr 输出不改变退出码。
    """
    collect = list(getattr(plan, "collect", None) or [])
    repairs = sorted(getattr(plan, "repairs", None) or {})
    parts = []
    if collect:
        parts.append(f"增量补采 {len(collect)} 项资产：{', '.join(collect)}")
    if repairs:
        prefix = "" if collect else "资产已就绪，"
        parts.append(f"{prefix}修复 {len(repairs)} 项历史状态标记：{', '.join(repairs)}")
    if not parts:
        parts.append("资产已就绪（无需采集），直接重算快照")
    print("；".join(parts), file=sys.stderr)


def _cmd_prep(args):
    """盘后复盘：增量采集数据资产 + 计算候选池/情绪快照，落 assets.db。

    默认输出 4 行中文摘要（是否买入/仓位/等级/理由）；--verbose 输出完整 JSON。
    """
    from dragon_quant.review_account.prep import PrepError, run_prep
    from dragon_quant.storage import db

    if args.date:
        trade_date = _normalize_cli_date(args.date)
    else:
        dates = db.list_dragon_trade_dates(source="v2")
        if not dates:
            print(json.dumps({"error": "无 v2 龙头记录，无法确定复盘日"},
                             ensure_ascii=False, indent=2))
            sys.exit(1)
        trade_date = dates[-1]

    try:
        result = run_prep(trade_date, recompute=args.recompute,
                          force=getattr(args, "force", False),
                          on_plan=_prep_plan_hint)
    except PrepError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2))
        sys.exit(1)
    except Exception as e:  # 与其它命令一致：输出 JSON 错误而非抛栈
        print(json.dumps({"error": f"{type(e).__name__}: {e}"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)
    if getattr(args, "verbose", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 降级信息已并入理由行（details 见 prep_assets.collection_report / --verbose）
    from dragon_quant.review_account.advice import (
        format_advice_lines, format_candidate_lines,
    )
    try:
        lines = format_advice_lines(result)
    except Exception as e:  # 快照已落库：展示失败不应让 cron 误判整次复盘失败
        print(f"复盘已完成并落库，但摘要生成失败：{type(e).__name__}: {e}"
              f"（可用 --verbose 查看完整结果）", file=sys.stderr)
        return
    try:
        lines = lines + [""] + format_candidate_lines(result)
    except Exception as e:  # 清单失败仍打印 4 行摘要，不让整次输出丢失
        print(f"标的清单生成失败：{type(e).__name__}: {e}"
              f"（可用 --verbose 查看完整结果）", file=sys.stderr)
    print("\n".join(lines))


def _cmd_assets(args):
    """数据资产云端同步（M1：doctor 连通性门槛）。"""
    from dragon_quant.assets_sync.backends.s3 import S3Backend
    from dragon_quant.assets_sync.credentials import load_config
    from dragon_quant.assets_sync.doctor import run_doctor
    from dragon_quant.assets_sync.errors import AssetsSyncError

    command = "assets doctor"
    action = getattr(args, "assets_action", None)
    if action != "doctor":
        print(json.dumps({"ok": False, "command": "assets",
                          "failed": [{"reason": "当前版本仅支持 assets doctor"}],
                          "summary": {}}, ensure_ascii=False, indent=2))
        sys.exit(2)
    if args.samples < 1:
        print(json.dumps({"ok": False, "command": command,
                          "failed": [{"reason": "--samples 必须 >= 1"}],
                          "summary": {}}, ensure_ascii=False, indent=2))
        sys.exit(2)

    try:
        config = load_config(profile=args.profile)
        backend = S3Backend(config, timeout=args.timeout)
        summary = run_doctor(backend, samples=args.samples)
    except AssetsSyncError as exc:
        print(json.dumps({"ok": False, "command": command,
                          "failed": [{"reason": str(exc)}], "summary": {}},
                         ensure_ascii=False, indent=2))
        sys.exit(2)
    except Exception as exc:  # 配置/网络之外的意外错误也按配置错误退出
        print(json.dumps({"ok": False, "command": command,
                          "failed": [{"reason": f"{type(exc).__name__}: {exc}"}],
                          "summary": {}}, ensure_ascii=False, indent=2))
        sys.exit(2)

    failed = list(summary.get("errors") or [])
    failed += [{"op": "cleanup", "error": reason}
               for reason in (summary.get("cleanup_errors") or [])]
    print(json.dumps({"ok": summary["ok"], "command": command,
                      "failed": failed, "summary": summary},
                     ensure_ascii=False, indent=2))
    sys.exit(0 if summary["ok"] else 1)


def _norm_auction_code(code) -> str:
    """行情/候选代码归一：去 SH/SZ 前后缀，取末 6 位数字。"""
    text = str(code or "").strip().upper()
    if "." in text:
        parts = text.split(".")
        text = next((p for p in parts if p.isdigit()), parts[-1])
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:].zfill(6) if digits else text


def _index_quotes(quotes) -> dict:
    """Quote 列表 → {归一化 code: Quote}；重复 code 保留首个。"""
    result = {}
    for quote in quotes or []:
        code = _norm_auction_code(getattr(quote, "code", ""))
        if code and code not in result:
            result[code] = quote
    return result


def _fetch_auction_quotes(codes: list[str]) -> tuple[dict, dict]:
    """批量取行情（TDX 主、tencent 兜底），返回 ({code: Quote}, 报告)。

    两条链路都是**整批**请求，绝不逐股请求。TDX 内部吞异常返回 []，
    因此降级判定必须基于 `matched < requested` 的结果，而非是否抛异常。
    """
    from dragon_quant.data import batch_get_quotes

    requested = len(codes)
    report = {
        "source": "none",
        "fallback_used": False,
        "requested": requested,
        "matched": 0,
        "missing": sorted(codes),
        "degraded": False,
        "reason": None,
        "error": None,
    }
    if not codes:
        return {}, report

    def _request(source: str):
        try:
            return _index_quotes(batch_get_quotes(codes, source=source))
        except Exception as exc:              # 兜底：正常链路返回 []，不抛异常
            report["error"] = f"{type(exc).__name__}: {exc}"
            return {}

    wanted = set(codes)
    quote_map = _request("tdx")
    matched = len(set(quote_map) & wanted)
    if matched:
        report["source"] = "tdx"
    else:
        report["fallback_used"] = True
        quote_map = _request("tencent")
        matched = len(set(quote_map) & wanted)
        if matched:
            report["source"] = "tencent"

    report["matched"] = matched
    report["missing"] = sorted(wanted - set(quote_map))
    if matched == 0:
        report["degraded"] = True
        report["reason"] = "error" if report["error"] else "empty"
        report["source"] = "none"
    elif matched < requested:
        report["degraded"] = True
        report["reason"] = "partial"
    return quote_map, report


def _snapshot_summary(verdict) -> dict:
    return {
        "code": verdict.code,
        "is_weak": verdict.is_weak,
        "strength": verdict.strength,
        "direction": verdict.direction,
        "level": verdict.level,
        "unverified": verdict.unverified,
        "rank": verdict.rank,
        "reject_reason": verdict.reject_reason,
    }


def _score_auction_assets(assets: list[dict]) -> tuple[dict, list, dict]:
    """evaluate → rank_auctions → verdict_to_payload（顺序固定）。

    assets: [{"code", "items", "quote"}]；返回 (payloads, failures, summaries)。
    单股异常隔离，不中断整批。
    """
    from dragon_quant.auction import evaluate, rank_auctions
    from dragon_quant.auction.serialize import verdict_to_payload

    verdicts, failures, summaries = [], [], {}
    for asset in assets:
        code = asset["code"]
        try:
            verdicts.append(evaluate(code, asset["items"], asset.get("quote")))
        except Exception as exc:
            failures.append({"code": code, "stage": "score",
                             "error": f"{type(exc).__name__}: {exc}"})

    rank_auctions(verdicts)          # 必须先 rank 再序列化，否则 rank 永远丢失

    payloads = {}
    for verdict in verdicts:
        try:
            payloads[verdict.code] = verdict_to_payload(verdict)
            summaries[verdict.code] = _snapshot_summary(verdict)
        except Exception as exc:
            failures.append({"code": verdict.code, "stage": "serialize",
                             "error": f"{type(exc).__name__}: {exc}"})
    return payloads, failures, summaries


def _parse_auction_date(raw: str) -> str:
    """归一 + 严格校验交易日；非法格式/不存在的日期抛 ValueError。"""
    from datetime import datetime
    normalized = _normalize_cli_date(raw)
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            f"无效日期: {raw}（支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD）"
        ) from exc


def _cmd_auction_recompute(raw_date: str):
    """用已存原始资产重算某日竞价快照（不触网）。"""
    from dragon_quant.storage import assets_db
    from dragon_quant.auction.serialize import quote_from_payload

    try:
        trade_date = _parse_auction_date(raw_date)
    except ValueError as exc:
        print(json.dumps({"ok": False, "mode": "recompute",
                          "trade_date": raw_date, "error": str(exc)},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    today = _resolve_trade_date(None)
    if trade_date > today:
        print(json.dumps({"ok": False, "mode": "recompute", "trade_date": trade_date,
                          "error": f"未来日期不可重算: {trade_date} > {today}"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    try:
        assets = assets_db.list_auction_assets(trade_date)
    except Exception as exc:
        print(json.dumps({"ok": False, "mode": "recompute", "trade_date": trade_date,
                          "error": f"读取竞价原始资产失败: {type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)
    if not assets:
        print(json.dumps({"ok": False, "mode": "recompute", "trade_date": trade_date,
                          "error": f"{trade_date} 无竞价原始资产，无法重算"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    usable, skipped_empty, failed, quote_missing = [], [], [], []
    for asset in assets:
        code = asset["code"]
        if asset.get("items_error"):
            failed.append({"code": code, "stage": "asset",
                           "error": asset["items_error"]})
            continue
        if asset.get("quote_error"):
            failed.append({"code": code, "stage": "quote",
                           "error": asset["quote_error"]})
            continue
        if not asset.get("items"):
            skipped_empty.append(code)
            continue
        raw_quote = asset.get("quote")
        try:
            quote = quote_from_payload(raw_quote)
        except Exception as exc:          # 有 quote_json 但损坏 → 单独计失败
            failed.append({"code": code, "stage": "quote",
                           "error": f"{type(exc).__name__}: {exc}"})
            continue
        if raw_quote is None:
            quote_missing.append(code)
        usable.append({"code": code, "items": asset["items"], "quote": quote})

    if not usable:
        print(json.dumps({"ok": False, "mode": "recompute", "trade_date": trade_date,
                          "asset_count": len(assets), "skipped_empty": skipped_empty,
                          "failed": failed,
                          "error": "无可计算资产（items 全为空或行情 payload 全部损坏）"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    payloads, score_failures, summaries = _score_auction_assets(usable)
    failed.extend(score_failures)
    if not payloads:
        print(json.dumps({"ok": False, "mode": "recompute", "trade_date": trade_date,
                          "asset_count": len(assets), "usable_count": len(usable),
                          "skipped_empty": skipped_empty, "quote_missing": quote_missing,
                          "failed": failed, "error": "全部评分/序列化失败，快照未写入"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    try:
        saved = assets_db.replace_auction_snapshots(trade_date, payloads, source="v2")
    except Exception as exc:
        print(json.dumps({"ok": False, "mode": "recompute", "trade_date": trade_date,
                          "error": f"快照写入失败: {type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    print(json.dumps({
        "ok": not failed,
        "mode": "recompute",
        "trade_date": trade_date,
        "source": "v2",
        "asset_count": len(assets),
        "usable_count": len(usable),
        "skipped_empty": skipped_empty,
        "quote_missing": quote_missing,
        "scored_count": len(payloads),
        "snapshot_saved_count": saved,
        "snapshot_ok": True,
        "snapshot_skipped": None,
        "snapshot_error": None,
        "failed": failed,
        "snapshots": [summaries[c] for c in sorted(summaries)],
    }, ensure_ascii=False, indent=2))


def _cmd_auction(args):
    """采集最近 N 日龙头的当日集合竞价盘口 + 行情，落原始资产并计算快照。"""
    from dragon_quant.storage import db, assets_db
    from dragon_quant.data import get_auction
    from dragon_quant.auction.serialize import quote_to_payload, quote_from_payload

    recompute = getattr(args, "recompute", None)
    if recompute is not None:
        _cmd_auction_recompute(recompute)
        return

    source = "v2"
    dates = db.list_dragon_trade_dates(source=source)
    if not dates:
        print(json.dumps({"error": f"无 {source} 龙头记录，无法确定候选"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    recent_dates = dates[-args.days:]
    # 最近 N 日龙头并集，按 code 去重（保留最近出现的非空 name）
    by_code: dict = {}
    for d in recent_dates:
        for dr in db.get_dragons(d, source=source):
            code = dr.get("code")
            if not code:
                continue
            name = dr.get("name") or ""
            if code not in by_code or name:
                by_code[code] = name

    if not by_code:
        print(json.dumps({"error": "最近 N 日无龙头，无需采集"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    trade_date = _resolve_trade_date(None)  # get_auction 仅返回当日竞价数据
    codes = sorted(by_code)

    # 行情整批获取（TDX 主、tencent 兜底），绝不逐股请求
    quote_map, quote_report = _fetch_auction_quotes(codes)

    saved, empty, failed, run_assets = [], [], [], []
    for code in codes:
        try:
            items = get_auction(code)
        except Exception as exc:
            failed.append({"code": code, "stage": "auction",
                           "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not items:
            empty.append(code)
            continue

        raw_quote = quote_map.get(code)
        try:
            quote_payload = quote_to_payload(raw_quote)
        except Exception as exc:
            failed.append({"code": code, "stage": "quote",
                           "error": f"{type(exc).__name__}: {exc}"})
            quote_payload = None

        try:
            assets_db.save_auction_asset(trade_date, code, by_code[code], items,
                                         quote=quote_payload)
        except Exception as exc:   # 单股失败不中断整批（原始资产逐日积累）
            failed.append({"code": code, "stage": "asset",
                           "error": f"{type(exc).__name__}: {exc}"})
            continue

        # M2：评分输入与落库同源（payload 往返），保证采集与重算一致
        try:
            score_quote = quote_from_payload(quote_payload)
        except Exception as exc:
            failed.append({"code": code, "stage": "quote",
                           "error": f"{type(exc).__name__}: {exc}"})
            score_quote = None

        saved.append({"code": code, "name": by_code[code], "n": len(items)})
        run_assets.append({"code": code, "items": items, "quote": score_quote})

    # 评分输入 = 该日全部已存资产（含同日早前批次），保证
    # 「快照 code 集合 = 有 items 的资产 code 集合」。读取失败时回退本轮内存数据。
    asset_read_error = None
    try:
        persisted = assets_db.list_auction_assets(trade_date)
    except Exception as exc:
        persisted = None
        asset_read_error = f"{type(exc).__name__}: {exc}"
        failed.append({"code": None, "stage": "asset_read", "error": asset_read_error})

    score_inputs, quote_missing = [], []
    if persisted is None:
        score_inputs = run_assets
    else:
        for asset in persisted:
            code = asset["code"]
            if asset.get("items_error"):
                failed.append({"code": code, "stage": "asset",
                               "error": asset["items_error"]})
                continue
            if asset.get("quote_error"):
                failed.append({"code": code, "stage": "quote",
                               "error": asset["quote_error"]})
                continue
            if not asset.get("items"):
                continue
            try:
                score_quote = quote_from_payload(asset.get("quote"))
            except Exception as exc:      # 有 quote_json 但损坏 → 单独计失败
                failed.append({"code": code, "stage": "quote",
                               "error": f"{type(exc).__name__}: {exc}"})
                continue
            if asset.get("quote") is None:
                quote_missing.append(code)
            score_inputs.append({"code": code, "items": asset["items"],
                                 "quote": score_quote})

    payloads, score_failures, summaries = _score_auction_assets(score_inputs)
    failed.extend(score_failures)
    snapshot_saved_count, snapshot_error, snapshot_skipped = 0, None, None
    if payloads:
        try:
            snapshot_saved_count = assets_db.replace_auction_snapshots(
                trade_date, payloads, source=source)
        except Exception as exc:
            snapshot_error = f"{type(exc).__name__}: {exc}"
    else:
        # 不调用 replace：空 payload 不删除既有快照
        snapshot_skipped = "no_payloads"

    snapshot_ok = (snapshot_error is None
                   and (snapshot_saved_count > 0 or (not saved and not failed)))
    ok = not failed and snapshot_error is None and snapshot_ok
    if saved and not snapshot_ok:
        print("⚠️ 竞价原始资产已写入，但快照未产出；请检查 failed/snapshot_error",
              file=sys.stderr)

    print(json.dumps({
        "ok": ok,
        "mode": "collect",
        "trade_date": trade_date,
        "source": source,
        "lookback_dates": recent_dates,
        "candidate_codes": len(codes),
        "saved_count": len(saved),
        "saved": saved,
        "empty": empty,
        "failed": failed,
        "quote": quote_report,
        "quote_missing": quote_missing,
        "asset_read_error": asset_read_error,
        "scored_count": len(payloads),
        "snapshot_saved_count": snapshot_saved_count,
        "snapshot_ok": snapshot_ok,
        "snapshot_skipped": snapshot_skipped,
        "snapshot_error": snapshot_error,
        "snapshots": [summaries[c] for c in sorted(summaries)],
    }, ensure_ascii=False, indent=2))
    if len(saved) == 0 and (failed or snapshot_error):
        # 原始资产一条都没写成功通常是系统性故障（schema/磁盘/权限），
        # 不能让定时任务把「一条都没写成功」当作成功。
        sys.exit(1)


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
        # 数据库文件（clear --all 不清理，仅展示）
        print(f"{'数据库':10s} {'':>6s} {'大小':>8s}")
        print("-" * 28)
        for key in ("dragon_db", "assets_db"):
            d = s[key]
            print(f"{key:10s} {'':>6s} {d['size']:>8s}" if d["exists"]
                  else f"{key:10s}  (不存在)")
        legacy = s["legacy_prep_db"]
        if legacy["exists"]:
            print(f"legacy_prep_db (历史库，不再读写，确认 assets.db 有数据后可删除) "
                  f"{legacy['size']}")

    elif args.storage_action == "clear":
        if args.all:
            r = mgr.clear_all()
            for k, v in r.items():
                if k == "protected_databases":
                    continue
                print(f"  清理 {k}: {v} 个文件")
            if r.get("protected_databases"):
                print("  保留数据库（不清理）:")
                for path in r["protected_databases"]:
                    note = "（历史库，确认 assets.db 有数据后可删除）" \
                        if path.endswith("prep.db") else ""
                    print(f"    {path}{note}")
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
