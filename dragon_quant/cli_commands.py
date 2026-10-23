"""CLI 子命令处理函数。

从 `cli.py` 拆分而来：所有 `_cmd_*` 处理函数集中于此，供 `dragon_quant.cli.main()`
的 dispatch 调用。`cli.py` 保留 argparse 解析树、共享 helper 与 `main()`。

两个会被测试 patch 的符号（`orchestrate_scan` / `_strategy_params`）必须经 `cli`
模块在**调用时**动态解析，故这里提供转发包装，避免 import 时绑定死引用导致 patch
无法生效（与 `storage/_base.py` 同一模式）。
"""

import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dragon_quant import cli as _cli
from dragon_quant.cli import _normalize_cli_date, _resolve_trade_date, _kbar_to_dict, _to_dict
from dragon_quant.storage.manager import StorageManager

SHANGHAI = ZoneInfo("Asia/Shanghai")


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


def _prep_plan_hint(plan, trade_date=None, auto_date=False):
    """增量采集计划提示（stderr）。

    走 stderr 是为了保持 stdout 的「4 行摘要 + 标的清单」契约不被污染；
    cron 只按退出码判失败，stderr 输出不改变退出码。
    ``trade_date`` 展示复盘锚定日期；``auto_date``（未显式传 --date）且锚定日早于
    今天时追加原因说明，避免「资产已就绪」被误读为「该采的没采」。
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
    msg = "；".join(parts)
    if trade_date:
        msg = f"复盘日期 {trade_date}：{msg}"
        now = datetime.now(SHANGHAI)
        # 仅工作日追加补救提示：周末/节假日锚定日必然早于今天，且当日无法补 scan，
        # 提示「先运行 scan」会形成「怎么弄都没用」的循环（节假日残留由假设语气覆盖）。
        if auto_date and trade_date < now.strftime("%Y-%m-%d") and now.weekday() < 5:
            msg += "（已锚定最新有龙头数据的交易日；若今日为交易日，16:40 后先运行 scan 再 prep 即可复盘今日）"
    print(msg, file=sys.stderr)


def _emit_prep_error(payload: dict, as_json: bool):
    """prep 错误输出：--json 保持原 JSON 到 stdout；默认人读一行到 stderr。"""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"❌ 复盘失败：{payload.get('error', '未知错误')}", file=sys.stderr)
    sys.exit(1)


def _emit_auction_error(payload: dict, as_json: bool):
    """auction 错误输出：--json 保持原 JSON 到 stdout；默认人读一行到 stderr。"""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"❌ 集合竞价失败：{payload.get('error', '未知错误')}", file=sys.stderr)
    sys.exit(1)


def _cmd_prep(args):
    """盘后复盘：增量采集数据资产 + 计算候选池/情绪快照，落 assets.db。

    默认输出 4 行中文摘要（是否买入/仓位/等级/理由）；--json 输出完整 JSON。
    """
    from dragon_quant.review_account.prep import PrepError, run_prep
    from dragon_quant.storage import db

    as_json = getattr(args, "json", False)
    auto_date = not args.date
    if args.date:
        trade_date = _normalize_cli_date(args.date)
    else:
        dates = db.list_dragon_trade_dates(source="v2")
        if not dates:
            _emit_prep_error({"error": "无 v2 龙头记录，无法确定复盘日"}, as_json)
        trade_date = dates[-1]

    try:
        result = run_prep(trade_date, recompute=args.recompute,
                          force=getattr(args, "force", False),
                          on_plan=lambda plan: _prep_plan_hint(plan, trade_date, auto_date))
    except PrepError as e:
        _emit_prep_error({"error": str(e)}, as_json)
    except Exception as e:  # 与其它命令一致：错误而非抛栈
        _emit_prep_error({"error": f"{type(e).__name__}: {e}"}, as_json)
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 降级信息已并入理由行（details 见 prep_assets.collection_report / --json）
    from dragon_quant.review_account.advice import (
        format_advice_lines, format_candidate_lines,
    )
    try:
        lines = format_advice_lines(result)
    except Exception as e:  # 快照已落库：展示失败不应让 cron 误判整次复盘失败
        print(f"复盘已完成并落库，但摘要生成失败：{type(e).__name__}: {e}"
              f"（可用 --json 查看完整结果）", file=sys.stderr)
        return
    try:
        lines = lines + [""] + format_candidate_lines(result)
    except Exception as e:  # 清单失败仍打印 4 行摘要，不让整次输出丢失
        print(f"标的清单生成失败：{type(e).__name__}: {e}"
              f"（可用 --json 查看完整结果）", file=sys.stderr)
    print("\n".join(lines))


def _parse_asset_date(raw, field: str):
    """资产同步日期：支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD，拒绝未来日期。"""
    from datetime import datetime, timezone, timedelta

    if raw is None:
        return None
    text = str(raw).strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    elif len(text) == 10 and text[4] == "/" and text[7] == "/":
        text = f"{text[:4]}-{text[5:7]}-{text[8:10]}"
    try:
        moment = datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            f"{field} 日期非法: {raw}（支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD）"
        ) from exc
    today = datetime.now(timezone(timedelta(hours=8))).date()
    if moment.date() > today:
        raise ValueError(f"{field} 不能是未来日期: {raw}")
    return moment.strftime("%Y-%m-%d")


def _emit_assets(payload: dict, exit_code: int) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    summary = payload.get("summary") or {}
    if payload.get("ok"):
        if summary:
            print(f"{payload.get('command', 'assets')} 完成："
                  f"ok={payload['ok']} fatal={summary.get('fatal', False)}",
                  file=sys.stderr)
    else:
        reasons = [item.get("reason", str(item)) for item in payload.get("failed") or []]
        if reasons:
            print(f"{payload.get('command', 'assets')} 失败：{reasons[0]}",
                  file=sys.stderr)
    sys.exit(exit_code)


def _assets_human_summary(command: str, result: dict) -> str:
    summary = result.get("summary") or {}
    if command.endswith("push"):
        return (f"推送：pushed={summary.get('pushed', 0)} "
                f"skipped={summary.get('skipped', 0)} "
                f"blobs={summary.get('blobs_uploaded', 0)} "
                f"manifests={summary.get('manifests_uploaded', 0)}")
    if command.endswith("pull"):
        text = (f"恢复：restored={summary.get('restored', 0)} "
                f"skipped={summary.get('skipped', 0)} "
                f"已判定冲突={len(summary.get('conflicts') or [])} "
                f"source={summary.get('manifest_source', 'remote')}")
        pending = int(summary.get("merge_pending") or 0)
        if pending:
            text += (f"\n待判定：{pending} 个行级分片需 --apply 下载后逐行比对"
                     "（dry-run 不代表 0 冲突）")
        deferred = summary.get("deferred") or []
        if deferred:
            text += f"\n延后补齐：{len(deferred)} 条 raw_blob（元数据行缺失）"
        return text
    if command.endswith("status"):
        tables = summary.get("tables") or {}
        synced = sum(1 for item in tables.values() if item.get("remote_entries"))
        text = (f"状态：missing={len(summary.get('dates_missing') or [])} "
                f"mismatched={len(summary.get('dates_mismatched') or [])} "
                f"drift={len(summary.get('dates_drift') or [])} "
                f"local={summary.get('bytes_local', 0)}B "
                f"remote={summary.get('bytes_remote', 0)}B\n"
                f"同步覆盖：{synced}/{len(tables)} 个同步单元有远端数据")
        if summary.get("not_synced"):
            for name, state in summary["not_synced"].items():
                text += f"\n{name}: {state}"
        else:
            text += "\nfactor_* / fin_*: 已纳入同步"
        if summary.get("dates_drift"):
            text += "\ndrift 仅为弱信号（本地行数更多）；定性请用 verify"
        return text
    return (f"校验：dates={summary.get('checked_dates', 0)} "
            f"blobs={summary.get('checked_blobs', 0)} "
            f"mismatched={len(summary.get('mismatched') or [])}")


def _period_filter_hint(summary: dict) -> str | None:
    """显式 --from/--to 排除报告期分区时的一行人读提示（不失败、不改退出码）。"""
    excluded = summary.get("period_excluded") or []
    if not excluded:
        return None
    names = "、".join(item["partition"] for item in excluded[:3])
    suffix = " 等" if len(excluded) > 3 else ""
    return (f"提示：--from/--to 按报告期末过滤，已排除 {len(excluded)} 个报告期分区"
            f"（{names}{suffix}）；如需同步请放宽区间。")


def _cmd_assets_sync(args) -> None:
    from pathlib import Path

    from dragon_quant.assets_sync.backends.s3 import S3Backend
    from dragon_quant.assets_sync.credentials import load_config
    from dragon_quant.assets_sync.errors import AssetsSyncError, LockBusyError
    from dragon_quant.assets_sync.service import pull, push, status, verify
    from dragon_quant.assets_sync.sources import build_sources
    from dragon_quant.assets_sync.state import default_paths

    action = args.assets_action
    command = f"assets {action}"
    try:
        date_from = _parse_asset_date(getattr(args, "from_date", None), "--from")
        date_to = _parse_asset_date(getattr(args, "to_date", None), "--to")
        if date_from and date_to and date_from > date_to:
            raise ValueError("--from 不能晚于 --to")
        if action == "pull" and (not date_from or not date_to):
            raise ValueError("pull 必须同时指定 --from 与 --to")
        samples = getattr(args, "samples", None)
        if action == "verify":
            if samples is not None and samples < 1:
                raise ValueError("--samples 必须 >= 1")
            if bool(date_from) != bool(date_to):
                raise ValueError("--from 与 --to 必须成对指定")
            if args.deep and not (date_from and date_to) and samples is None:
                raise ValueError("--deep 需配合 --from/--to 或 --samples")

        target = getattr(args, "target_dir", None)
        data_dir = Path(target).expanduser() if target else None
        if data_dir is not None:
            data_dir.mkdir(parents=True, exist_ok=True)
        sources = build_sources(db_path=(data_dir / "assets.db") if data_dir else None)
        paths = default_paths(data_dir)

        config = load_config(profile=args.profile)
        backend = S3Backend(config, timeout=args.timeout)

        if action == "push":
            result = push(
                backend, sources, date_from=date_from, date_to=date_to,
                all_dates=args.all_dates, dry_run=args.dry_run,
                allow_shrink=args.allow_shrink, paths=paths)
        elif action == "pull":
            result = pull(
                backend, sources, date_from=date_from, date_to=date_to,
                apply=args.apply, force=args.force,
                pick_latest=(args.pick == "latest"), paths=paths)
        elif action == "status":
            result = status(
                backend, sources, all_dates=args.all_dates,
                orphans=args.orphans, paths=paths)
        else:
            result = verify(
                backend, sources, date_from=date_from, date_to=date_to,
                samples=samples, deep=args.deep, show_md5=args.show_md5,
                paths=paths)
    except ValueError as exc:
        _emit_assets({"ok": False, "command": command,
                      "failed": [{"reason": str(exc)}], "summary": {}}, 2)
        return
    except LockBusyError as exc:
        _emit_assets({"ok": False, "command": command,
                      "failed": [{"reason": str(exc), "kind": "lock"}],
                      "summary": {}}, 1)
        return
    except AssetsSyncError as exc:
        _emit_assets({"ok": False, "command": command,
                      "failed": [{"reason": str(exc), "kind": type(exc).__name__}],
                      "summary": {}}, 2)
        return
    except Exception as exc:
        _emit_assets({"ok": False, "command": command,
                      "failed": [{"reason": f"{type(exc).__name__}: {exc}"}],
                      "summary": {}}, 2)
        return

    print(_assets_human_summary(command, result), file=sys.stderr)
    hint = _period_filter_hint(result.get("summary") or {})
    if hint:
        print(hint, file=sys.stderr)
    exit_code = 2 if result.get("fatal") else (0 if result.get("ok") else 1)
    _emit_assets({
        "ok": result.get("ok", False),
        "command": command,
        "failed": result.get("failed") or [],
        "summary": result.get("summary") or {},
    }, exit_code)


def _cmd_assets(args):
    """数据资产云端同步：doctor / push / pull / status / verify。"""
    from dragon_quant.assets_sync.backends.s3 import S3Backend
    from dragon_quant.assets_sync.credentials import load_config
    from dragon_quant.assets_sync.doctor import run_doctor
    from dragon_quant.assets_sync.errors import AssetsSyncError

    action = getattr(args, "assets_action", None)
    if action in ("push", "pull", "status", "verify"):
        return _cmd_assets_sync(args)
    if action != "doctor":
        print(json.dumps({"ok": False, "command": "assets",
                          "failed": [{"reason": "未知 assets 子命令"}],
                          "summary": {}}, ensure_ascii=False, indent=2))
        sys.exit(2)

    command = "assets doctor"
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
    normalized = _normalize_cli_date(raw)
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            f"无效日期: {raw}（支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD）"
        ) from exc


def _cmd_auction_recompute(raw_date: str, as_json: bool = False):
    """用已存原始资产重算某日竞价快照（不触网）。"""
    from dragon_quant.storage import assets_db
    from dragon_quant.auction.serialize import quote_from_payload

    try:
        trade_date = _parse_auction_date(raw_date)
    except ValueError as exc:
        _emit_auction_error({"ok": False, "mode": "recompute",
                             "trade_date": raw_date, "error": str(exc)}, as_json)

    today = _resolve_trade_date(None)
    if trade_date > today:
        _emit_auction_error({"ok": False, "mode": "recompute", "trade_date": trade_date,
                             "error": f"未来日期不可重算: {trade_date} > {today}"}, as_json)

    try:
        assets = assets_db.list_auction_assets(trade_date)
    except Exception as exc:
        _emit_auction_error(
            {"ok": False, "mode": "recompute", "trade_date": trade_date,
             "error": f"读取竞价原始资产失败: {type(exc).__name__}: {exc}"}, as_json)
    if not assets:
        _emit_auction_error(
            {"ok": False, "mode": "recompute", "trade_date": trade_date,
             "error": f"{trade_date} 无竞价原始资产，无法重算"}, as_json)

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
        _emit_auction_error(
            {"ok": False, "mode": "recompute", "trade_date": trade_date,
             "asset_count": len(assets), "skipped_empty": skipped_empty,
             "failed": failed,
             "error": "无可计算资产（items 全为空或行情 payload 全部损坏）"}, as_json)

    payloads, score_failures, summaries = _score_auction_assets(usable)
    failed.extend(score_failures)
    if not payloads:
        _emit_auction_error(
            {"ok": False, "mode": "recompute", "trade_date": trade_date,
             "asset_count": len(assets), "usable_count": len(usable),
             "skipped_empty": skipped_empty, "quote_missing": quote_missing,
             "failed": failed, "error": "全部评分/序列化失败，快照未写入"}, as_json)

    try:
        saved = assets_db.replace_auction_snapshots(trade_date, payloads, source="v2")
    except Exception as exc:
        _emit_auction_error(
            {"ok": False, "mode": "recompute", "trade_date": trade_date,
             "error": f"快照写入失败: {type(exc).__name__}: {exc}"}, as_json)

    payload = {
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
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    from dragon_quant.auction.report import format_auction_report
    names = {asset["code"]: (asset.get("name") or "") for asset in assets}
    try:
        print("\n".join(format_auction_report(
            payload["snapshots"], trade_date=trade_date, mode="recompute",
            failed_count=len(failed), empty_count=len(skipped_empty),
            saved_count=len(payloads), names=names,
        )))
    except Exception as exc:  # 展示失败不应改变退出码（与 collect/prep 同策略）
        print(f"⚠️ 人读摘要生成失败：{type(exc).__name__}: {exc}"
              "（可用 --json 查看完整结果）", file=sys.stderr)


def _cmd_auction(args):
    """采集最近 N 日龙头的当日集合竞价盘口 + 行情，落原始资产并计算快照。

    默认输出人读摘要（参与标的强弱 + 汇总）；--json 输出完整 JSON。
    """
    from dragon_quant.storage import db, assets_db
    from dragon_quant.data import get_auction
    from dragon_quant.auction.serialize import quote_to_payload, quote_from_payload

    as_json = getattr(args, "json", False)
    recompute = getattr(args, "recompute", None)
    if recompute is not None:
        _cmd_auction_recompute(recompute, as_json)
        return

    source = "v2"
    trade_date = _resolve_trade_date(None)  # get_auction 仅返回当日竞价数据
    pool_mode = getattr(args, "pool", "prep")
    days_arg = getattr(args, "days", None)
    days = days_arg or 3

    ctx = {
        "pool_source": "legacy",
        "fallback_reason": None,
        "prep_snapshot_date": None,
        "prep_unavailable_reason": None,
        "recommended_count": 0,
        "ignore_reasons": [],
        "degraded": [],
    }
    by_code: dict = {}
    recent_dates: list = []
    days_ignored = False

    if pool_mode == "prep":
        calendar = None
        try:
            from dragon_quant.providers.tdx import TdxProvider
            from dragon_quant.utils.trading import tdx_trade_dates
            calendar = tdx_trade_dates(TdxProvider())
        except Exception as exc:
            ctx["fallback_reason"] = "calendar"
            ctx["degraded"].append(f"T-1 交易日历不可用：{type(exc).__name__}: {exc}")
            print(f"⚠️ T-1 交易日历不可用（{type(exc).__name__}: {exc}），回退 legacy 池",
                  file=sys.stderr)
        if calendar:
            from dragon_quant.review_account.market import candidate_dates, collect_candidates_ex
            from dragon_quant.review_account.models import StrategyConfig
            cfg = StrategyConfig(source=source, candidate_lookback_days=days)
            try:
                candidates, ctx = collect_candidates_ex(
                    calendar, trade_date, cfg, db.get_dragons_by_date)
            except Exception as exc:
                candidates = []
                ctx = {**ctx, "fallback_reason": "candidate_pool_error",
                       "degraded": [f"候选池构造失败：{type(exc).__name__}: {exc}"]}
                print(f"⚠️ 候选池构造失败（{exc}），回退 legacy 池", file=sys.stderr)
            by_code = {c["code"]: (c.get("name") or "")
                       for c in (candidates or []) if c.get("code")}
            if ctx.get("pool_source") == "legacy":
                recent_dates = candidate_dates(calendar, trade_date, days)
            elif days_arg is not None:
                days_ignored = True
                print("⚠️ --days 仅在 legacy 路径生效，本次已忽略", file=sys.stderr)

    if not by_code:
        # 显式 legacy，或 prep 路径回退后仍无候选：走旧口径（最近 N 日龙头并集）。
        dates = db.list_dragon_trade_dates(source=source)
        if dates:
            recent_dates = dates[-days:]
            for d in recent_dates:
                for dr in db.get_dragons(d, source=source):
                    code = dr.get("code")
                    if not code:
                        continue
                    name = dr.get("name") or ""
                    if code not in by_code or name:
                        by_code[code] = name
            if ctx.get("fallback_reason") is None and pool_mode == "prep":
                ctx["fallback_reason"] = "pool_unavailable"
        elif pool_mode == "legacy":
            _emit_auction_error({"error": "最近 N 日无龙头，无需采集"}, as_json)

    if not by_code:
        ignore_reasons = ctx.get("ignore_reasons") or []
        payload = {
            "ok": True, "mode": "collect", "trade_date": trade_date, "source": source,
            "pool_source": ctx.get("pool_source"),
            "fallback_reason": ctx.get("fallback_reason"),
            "prep_snapshot_date": ctx.get("prep_snapshot_date"),
            "prep_unavailable_reason": ctx.get("prep_unavailable_reason"),
            "pool_empty": True, "candidate_codes": 0,
            "recommended_count": ctx.get("recommended_count", 0),
            "ignore_reason_count": len(ignore_reasons),
            "ignore_reason_samples": ignore_reasons[:3],
            "prep_degraded": bool(ctx.get("prep_degraded")),
            "days_ignored": days_ignored,
            "message": "prep 与 legacy 均无可用候选，本次不采集竞价数据",
        }
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            from dragon_quant.auction.report import format_auction_report
            print("\n".join(format_auction_report(
                [], trade_date=trade_date, pool_source=payload["pool_source"],
                fallback_reason=payload["fallback_reason"],
                candidate_count=0, empty_count=0,
            )))
        if ctx.get("prep_degraded"):
            print("⚠️ 本次未采集竞价原始资产（不可再生），可能因 prep 数据降级，"
                  "请先补跑 prep", file=sys.stderr)
        return

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

    payload = {
        "ok": ok,
        "mode": "collect",
        "trade_date": trade_date,
        "source": source,
        "pool_source": ctx.get("pool_source"),
        "fallback_reason": ctx.get("fallback_reason"),
        "prep_snapshot_date": ctx.get("prep_snapshot_date"),
        "prep_unavailable_reason": ctx.get("prep_unavailable_reason"),
        "recommended_count": ctx.get("recommended_count", 0),
        "days_ignored": days_ignored,
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
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        from dragon_quant.auction.report import format_auction_report
        names: dict = {}
        for item in ([*(persisted or []), *saved]):
            code = item.get("code")
            name = item.get("name") or ""
            if code and name and not names.get(code):
                names[code] = name
        for code, name in by_code.items():
            if code and name and not names.get(code):
                names[code] = name
        try:
            print("\n".join(format_auction_report(
                payload["snapshots"], trade_date=trade_date, mode="collect",
                pool_source=payload["pool_source"],
                candidate_count=len(codes),
                fallback_reason=payload["fallback_reason"],
                failed_count=len(failed), empty_count=len(empty),
                saved_count=len(saved), names=names,
            )))
        except Exception as exc:  # 展示失败不应改变退出码（与 prep 同策略）
            print(f"⚠️ 人读摘要生成失败：{type(exc).__name__}: {exc}"
                  "（可用 --json 查看完整结果）", file=sys.stderr)

    if len(saved) == 0 and (failed or snapshot_error):
        # 原始资产一条都没写成功通常是系统性故障（schema/磁盘/权限），
        # 不能让定时任务把「一条都没写成功」当作成功。
        if not as_json:
            detail = f"候选 {len(codes)} 只，失败 {len(failed)} 条"
            if snapshot_error:
                detail += f"，快照错误：{snapshot_error}"
            print(f"❌ 集合竞价失败：{detail}（详情加 --json）", file=sys.stderr)
        sys.exit(1)


def _emit_factor_error(payload: dict, as_json: bool, exit_code: int) -> None:
    """factor 错误输出：默认人读一行到 stderr；--json 保持 JSON 到 stdout。

    与 prep/auction 同策略：参数错误 exit 2，运行时失败 exit 1。
    """
    body = {"ok": False, **payload}
    if as_json:
        print(json.dumps(body, ensure_ascii=False, indent=2))
    else:
        print(f"❌ 因子操作失败：{body.get('error', '未知错误')}", file=sys.stderr)
    sys.exit(exit_code)


def _print_factor_human(action: str, payload: dict) -> None:
    """factor 子命令的人读输出（默认模式；JSON 契约不受影响）。"""
    if action == "list":
        print(f"{'因子':28s} {'版本':14s} {'源':6s} {'阶段':8s} {'PIT':10s} 默认")
        print("-" * 78)
        for item in payload.get("factors") or []:
            print(f"{item['name']:28s} {item['version']:14s} {item['source']:6s} "
                  f"{item['available_phase']:8s} {item['pit_rule']:10s} "
                  f"{'开' if item['default_enabled'] else '关'}")
        return

    if action == "build":
        for report in payload.get("reports") or []:
            entries = list((report.get("factors") or {}).values())
            counts: dict[str, int] = {}
            for entry in entries:
                counts[entry.get("status", "?")] = counts.get(entry.get("status", "?"), 0) + 1
            status_text = " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "-"
            rows = sum(int(entry.get("rows") or 0) for entry in entries)
            sector = report.get("sector_snapshot") or {}
            print(f"{report.get('trade_date')}: 因子 {len(entries)} 个 / 行 {rows} "
                  f"[{status_text}] 主板块 {sector.get('rows', 0)} "
                  f"build_id={report.get('build_id')}")
            for warning in report.get("warnings") or []:
                print(f"  ⚠️ {warning}")
        if not payload.get("reports"):
            print("(区间内无扫描记录，未执行 build)")
        if payload.get("mode") == "range":
            print(f"合计 {len(payload.get('dates') or [])} 个交易日，"
                  f"build_ids={payload.get('build_ids')}")
        for warning in payload.get("warnings") or []:
            print(f"⚠️ {warning}")
        return

    if action == "query":
        if payload.get("mode") == "day":
            values = payload.get("values") or {}
            if not values:
                print(f"{payload.get('trade_date')}: (无因子值)")
            for code in sorted(values):
                for name in sorted(values[code]):
                    available = ((payload.get("available_at") or {}).get(code) or {}).get(name)
                    print(f"{payload.get('trade_date')} {code} {name} "
                          f"{values[code][name]:g} available_at={available}")
        else:
            rows = payload.get("rows") or []
            if not rows:
                print(f"{payload.get('factor')}: (区间内无因子值)")
            for row in rows:
                print(f"{row['trade_date']} {row['code']} {row['factor_version']} "
                      f"{row['value']:g} available_at={row['available_at']}")
        print(f"resolved_versions={payload.get('resolved_versions')}")
        return

    if action == "status":
        values = payload.get("factor_values") or {}
        registry = payload.get("factor_registry") or {}
        sector = payload.get("factor_sector_snapshots") or {}
        runs = payload.get("build_runs") or {}
        last = runs.get("last") or {}
        print(f"因子值：{values.get('rows', 0)} 行 / {values.get('factors', 0)} 因子 / "
              f"{values.get('dates', 0)} 交易日 "
              f"({values.get('first_date')} ~ {values.get('last_date')})")
        print(f"注册表快照：{registry.get('rows', 0)} 行 / {registry.get('factors', 0)} 因子")
        print(f"主板块快照：{sector.get('rows', 0)} 行 / {sector.get('dates', 0)} 交易日 "
              f"{sector.get('by_category') or {}}")
        print(f"build 审计：{runs.get('rows', 0)} 次"
              + (f"，最近 build_id={last.get('id')} @ {last.get('trade_date')}" if last else ""))
        missing = [f["name"] for f in payload.get("factors") or []
                   if not f.get("registered_version")]
        if missing:
            print(f"未注册（尚未 build）：{', '.join(missing)}")
        return

    if action == "export":
        print(f"导出矩阵：{payload.get('date_from')} ~ {payload.get('date_to')}，"
              f"{len(payload.get('dates') or [])} 日 × {len(payload.get('codes') or [])} 只，"
              f"列 {len(payload.get('columns') or [])} 个")
        print(f"resolved_versions={payload.get('resolved_versions')}")
        print(f"missing_rate_by_factor={payload.get('missing_rate_by_factor')}")
        if payload.get("winsorize_skipped"):
            print(f"⚠️ winsorize_skipped={payload.get('winsorize_skipped')}")
        if payload.get("normalize_skipped"):
            print(f"⚠️ normalize_skipped={payload.get('normalize_skipped')}")
        if payload.get("dropped_rows"):
            print(f"drop 缺失行：{payload.get('dropped_rows')} 行")
        print(f"out={payload.get('out_path')} meta={payload.get('meta_path')}")
        for warning in payload.get("warnings") or []:
            print(f"⚠️ {warning}")
        return

    if action == "eval":
        print(f"评估 {payload.get('factor')}@{payload.get('resolved_version')}："
              f"{payload.get('status')}"
              + (f"（{payload.get('reason')}）" if payload.get("reason") else ""))
        print(f"horizon={payload.get('horizon')} nw_lag={payload.get('nw_lag')} "
              f"days={payload.get('days')} obs={payload.get('observations')} "
              f"effective_days={payload.get('effective_independent_days')}")
        ic = payload.get("ic") or {}
        print(f"RankIC mean={ic.get('mean')} std={ic.get('std')} "
              f"ICIR={ic.get('icir')} t={ic.get('t_stat')} nw_t={ic.get('nw_t_stat')}")
        coverage = payload.get("coverage") or {}
        print(f"coverage mean={coverage.get('mean')} min={coverage.get('min')} "
              f"missing_rate={coverage.get('missing_rate')}")
        for group in payload.get("groups") or []:
            print(f"  group={group.get('group')} n={group.get('n')} "
                  f"mean_return={group.get('mean_return')} "
                  f"hit_rate={group.get('hit_rate')}")
        print(f"pit_policy={payload.get('pit_policy')} "
              f"pit_basis_mix={payload.get('pit_basis_mix')}")
        for warning in payload.get("warnings") or []:
            print(f"⚠️ {warning}")
        for item in payload.get("degraded") or []:
            print(f"⚠️ degraded: {item}")
        return


def _cmd_factor(args):
    """因子层命令：build / list / query / status / fin。"""
    from dragon_quant.factors import service

    action = getattr(args, "factor_action", None)
    if action == "fin":
        _cmd_factor_fin(args)
        return
    as_json = getattr(args, "json", False)
    try:
        if action == "build":
            payload = service.build(
                date=args.date,
                date_from=getattr(args, "from_date", None),
                date_to=getattr(args, "to_date", None),
                only=args.only,
            )
        elif action == "list":
            payload = {"ok": True, "factors": service.list_factors()}
        elif action == "query":
            payload = service.query(
                date=args.date,
                date_from=getattr(args, "from_date", None),
                date_to=getattr(args, "to_date", None),
                name=args.name,
                code=args.code,
                version=args.version,
            )
        elif action == "status":
            payload = service.status()
        elif action == "export":
            from dragon_quant.factors.matrix import CleanConfig, identity_clean
            names = service.parse_only(args.names)
            if not names:
                raise ValueError("export 至少需要一个 --name")
            low, high = (float(part) for part in str(args.limits).split(",", 1))
            if args.clean:
                clean = CleanConfig(
                    missing=args.missing or "nan",
                    winsorize=args.winsorize or "mad",
                    limits=(low, high),
                    normalize=args.normalize or "rank")
            elif args.missing or args.winsorize or args.normalize:
                clean = CleanConfig(
                    missing=args.missing or "nan",
                    winsorize=args.winsorize or "none",
                    limits=(low, high),
                    normalize=args.normalize or "none")
            else:
                clean = identity_clean()
            payload = service.export_matrix(
                args.from_date, args.to_date, names=names,
                version=args.version, codes=service.parse_only(args.codes),
                clean=clean, fmt=args.fmt, out_path=args.out_path)
        elif action == "eval":
            from dragon_quant.review_account.data import MarketData
            payload = service.evaluate_factor(
                args.name, args.from_date, args.to_date, data=MarketData(),
                horizon=args.horizon, groups=args.groups, version=args.version,
                min_days=args.min_days, min_cross_section=args.min_cross_section,
                pit_policy=args.pit_policy)
        else:
            _emit_factor_error(
                {"error": "未知 factor 子命令（build/list/query/status/export/eval/fin）"},
                as_json, 2)
            return
    except ValueError as exc:
        _emit_factor_error({"error": str(exc)}, as_json, 2)
        return
    except Exception as exc:  # schema/IO 等运行时失败：不抛栈
        _emit_factor_error({"error": f"{type(exc).__name__}: {exc}"}, as_json, 1)
        return

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_factor_human(action, payload)
    if not payload.get("ok"):
        sys.exit(1)


def _cmd_factor_fin(args):
    """``factor fin``：sync（snapshot/backfill）/ calibrate / status。"""
    from dragon_quant.factors import service

    sub = getattr(args, "fin_action", None)
    as_json = getattr(args, "json", False)
    try:
        if sub == "sync":
            payload = service.fin_sync(
                snapshot=getattr(args, "snapshot", False),
                backfill=getattr(args, "backfill", False),
                date=getattr(args, "date", None),
                from_period=getattr(args, "from_period", None),
                to_period=getattr(args, "to_period", None),
                force=getattr(args, "force", False),
                host=getattr(args, "host", None),
            )
        elif sub == "calibrate":
            payload = service.fin_calibrate(
                sample=getattr(args, "sample", 20),
                period=getattr(args, "period", None),
                host=getattr(args, "host", None),
            )
        elif sub == "status":
            payload = service.fin_status()
        else:
            _emit_factor_error(
                {"error": "未知 factor fin 子命令（sync/calibrate/status）"},
                as_json, 2)
            return
    except ValueError as exc:
        _emit_factor_error({"error": str(exc)}, as_json, 2)
        return
    except Exception as exc:
        _emit_factor_error({"error": f"{type(exc).__name__}: {exc}"}, as_json, 1)
        return

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_fin_human(sub, payload)
    if not payload.get("ok"):
        sys.exit(1)


def _print_fin_human(action: str, payload: dict) -> None:
    if action == "sync":
        mode = payload.get("mode")
        if mode == "snapshot":
            print(f"财务观测 {payload.get('trade_date')}：候选 {payload.get('codes', 0)} 只，"
                  f"成功 {payload.get('ok', 0)}，状态 {payload.get('status')}")
            for updated, count in sorted((payload.get("updated_dates") or {}).items()):
                print(f"  updated_date={updated}: {count}")
            for error in payload.get("errors") or []:
                print(f"  ❌ {error.get('code')}: {error.get('error')}")
            return
        print(f"财务回填：报告期 {payload.get('periods') or '-'}")
        print(f"  下载 {payload.get('downloaded', 0)} / 跳过 {payload.get('skipped', 0)} / "
              f"新增行 {payload.get('inserted', 0)} / 值未变 {payload.get('unchanged', 0)} / "
              f"撤销 {payload.get('retracted', 0)} / 观测失配 {payload.get('observed_unmatched', 0)}")
        if payload.get("layouts"):
            print(f"  布局：{payload.get('layouts')}")
        for failure in payload.get("failed") or []:
            print(f"  ❌ {failure.get('period')} {failure.get('md5')}: {failure.get('error')}")
        return

    if action == "calibrate":
        print(f"字段校准 {payload.get('layout_key')}：{payload.get('status')} "
              f"(sample={payload.get('sample_n')}, period={payload.get('report_period')})")
        anchors = payload.get("anchors") or {}
        print(f"  锚定：恒等式={anchors.get('identity')} 层级不等式={anchors.get('hierarchy')}")
        for field, spec in sorted((payload.get("mapping") or {}).items()):
            print(f"  {field}: index={spec.get('index')} scale={spec.get('scale')}")
        return

    if action == "status":
        observed = payload.get("observed") or {}
        files = payload.get("report_files") or {}
        records = payload.get("report_records") or {}
        mix = payload.get("pit_basis_mix") or {}
        print(f"观测快照：{observed.get('rows', 0)} 行 / {observed.get('dates', 0)} 日 "
              f"(最近 {observed.get('last_date')})")
        print(f"期文件：{files.get('rows', 0)} 行 / {files.get('periods', 0)} 期 "
              f"{files.get('by_status') or {}}")
        print(f"账本：{records.get('rows', 0)} 行 / {records.get('periods', 0)} 期 / "
              f"{records.get('codes', 0)} code / 撤销 {records.get('retracted', 0)} "
              f"{records.get('by_basis') or {}}")
        print(f"PIT 口径：observed 占比 {mix.get('observed_ratio')}，"
              f"回填不可证伪 {mix.get('restatement_unverifiable', 0)} 行")
        print(f"校准布局：{payload.get('verified_layouts') or '-'}")
        print(f"最近报告期：{payload.get('latest_period') or '-'}"
              f"（下一截止日 {payload.get('next_deadline') or '-'}，"
              f"逾期={payload.get('overdue')}）")
        print(f"依赖：pandas={((payload.get('dependencies') or {}).get('pandas'))}")
        return


def _cmd_buy(args):
    """执行当前时点的买入信号（纯信号记账，非模拟账户）。"""
    from dragon_quant.live_trade import run_buy
    as_json = getattr(args, "json", False)
    raw_date = getattr(args, "date", None)
    trade_date = raw_date
    try:
        trade_date = _resolve_trade_date(raw_date)
        result = run_buy(trade_date, source=args.source, verbose=not as_json,
                         as_of=args.at, strategy_params=_strategy_params(args.config),
                         force=getattr(args, "force", False))
        if as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValueError as e:
        if as_json:
            print(json.dumps({"ok": False, "mode": "buy",
                              "trade_date": trade_date or _resolve_trade_date(None),
                              "error": str(e)}, ensure_ascii=False, indent=2))
        else:
            print(f"⏸ 暂不执行买入：{e}", file=sys.stderr)
        sys.exit(1)


def _cmd_sell(args):
    """执行当前时点的卖出信号（纯信号记账，非模拟账户）。"""
    from dragon_quant.live_trade import run_sell
    trade_date = _resolve_trade_date(args.date)
    try:
        run_sell(trade_date, source=args.source, verbose=True,
                 as_of=args.at, strategy_params=_strategy_params(args.config),
                 force=getattr(args, "force", False))
    except ValueError as e:
        print(f"⏸ 暂不执行卖出：{e}", file=sys.stderr)
        sys.exit(1)


def _cmd_vpa(args):
    """个股量价分析命令"""
    import json
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
