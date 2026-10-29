"""集合竞价采集编排 — 候选池/日历/行情/盘口/资产与快照 payload。

只服务在线采集路径；离线重算见 :mod:`dragon_quant.review_account.auction.recompute`。
provider 装配：显式 ``services`` 优先，缺失时走 runtime factory（不 import app），
绝不静默把 prep 池降级为 legacy。
"""

from __future__ import annotations

from types import SimpleNamespace

from dragon_quant.review_account.auction.context import (
    build_strategy_context, build_strategy_context_from_snapshots,
)
from dragon_quant.review_account.auction.dates import _today
from dragon_quant.review_account.auction.scoring import (
    evaluate_batch, snapshot_summary,
)
from dragon_quant.review_account.auction.types import AuctionOutcome


def _resolve_collect_services(services):
    """采集路径的 provider 装配：显式 services 优先，缺失时走 runtime factory。

    显式 ``services`` 但 ``providers=None``（只读装配）无法提供行情链路，因此同样
    构造默认 bundle。返回对象只保证 ``.providers``（data.* 的唯一消费面）。
    """
    bundle = getattr(services, "providers", None) if services is not None else None
    if bundle is not None:
        return services
    from dragon_quant.infra.provider.bundle import build_provider_bundle

    return SimpleNamespace(providers=build_provider_bundle())


def _trade_calendar(services) -> list[str]:
    """T-1 交易日历：使用注入的 TDX provider（service 不构造 provider）。"""
    from dragon_quant.infra.market_calendar import tdx_trade_dates

    bundle = getattr(services, "providers", None)
    tdx = getattr(bundle, "tdx", None)
    if tdx is None:
        raise RuntimeError("缺少 TDX provider（services.providers.tdx）")
    return tdx_trade_dates(tdx)


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


def _fetch_auction_quotes(codes: list[str], services=None) -> tuple[dict, dict]:
    """批量取行情（TDX 主、tencent 兜底），返回 ({code: Quote}, 报告)。

    两条链路都是**整批**请求，绝不逐股请求。TDX 内部吞异常返回 []，
    因此降级判定必须基于 `matched < requested` 的结果，而非是否抛异常。
    """
    from dragon_quant.infra.provider.data import batch_get_quotes

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
            return _index_quotes(
                batch_get_quotes(codes, source=source, services=services))
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


def _render_names(by_code, persisted, saved) -> dict:
    names: dict = {}
    for item in ([*(persisted or []), *saved]):
        code = item.get("code")
        name = item.get("name") or ""
        if code and name and not names.get(code):
            names[code] = name
    for code, name in (by_code or {}).items():
        if code and name and not names.get(code):
            names[code] = name
    return names


def _load_history(assets_db, trade_date: str, score_inputs, warnings) -> dict:
    """只读历史末点匹配量（量比）；失败按缺失处理，不中断采集。"""
    try:
        return assets_db.list_auction_history(
            trade_date, codes=[asset["code"] for asset in score_inputs],
            limit=5) or {}
    except Exception as exc:
        warnings.append("⚠️ 竞价历史读取失败，量比按缺失处理"
                        f"（{type(exc).__name__}: {exc}）")
        return {}


def _score_inputs(assets_db, trade_date, score_inputs, by_code,
                  persisted, saved, warnings):
    names = _render_names(by_code, persisted, saved)
    history = _load_history(assets_db, trade_date, score_inputs, warnings)
    return evaluate_batch(score_inputs, history=history, names=names)


# 已存快照中至少含一个键才算「可解析」；排除 ``{}`` / 仅 code 的空壳。
_SNAPSHOT_USABLE_KEYS = ("version", "is_weak", "strength", "direction",
                         "level", "dims", "reject_reason", "unverified",
                         "rank", "data_quality")


def _load_snapshot_rows(trade_date: str) -> list[dict]:
    """只读当日竞价快照（既有接口，source=v2，不建库/不写行）。"""
    from dragon_quant.infra.assets import db as assets_db

    return assets_db.list_auction_snapshots(trade_date, source="v2")


def _split_snapshot_rows(rows) -> tuple[dict[str, dict], list[dict]]:
    """拆分为可用行与损坏/空壳行；逐行隔离，不中断整批。"""
    usable: dict[str, dict] = {}
    failures: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            failures.append({"code": None, "stage": "snapshot",
                             "error": "snapshot_incomplete: 行不是对象"})
            continue
        code = str(row.get("code") or "")
        if not code:
            failures.append({"code": None, "stage": "snapshot",
                             "error": "snapshot_incomplete: 缺少 code"})
            continue
        if row.get("error"):
            failures.append({"code": code, "stage": "snapshot",
                             "error": str(row["error"])})
            continue
        if not any(key in row for key in _SNAPSHOT_USABLE_KEYS):
            failures.append({"code": code, "stage": "snapshot",
                             "error": "snapshot_incomplete: analysis_json 无可用字段"})
            continue
        usable[code] = row
    return usable, failures


def _snapshot_outcome(trade_date: str, usable: dict[str, dict],
                      failures: list[dict]) -> AuctionOutcome | None:
    """已存快照只读输出：不采集、不评分、不写库。

    单行投影失败按损坏隔离；若全部投影失败则返回 ``None``，交回完整采集。
    """
    failed = list(failures or [])
    payloads: dict[str, dict] = {}
    snapshots: list[dict] = []
    for code in sorted(usable):
        try:
            summary = snapshot_summary(usable[code])
        except Exception as exc:      # 语义损坏（类型/结构异常）逐行隔离
            failed.append({"code": code, "stage": "snapshot",
                           "error": "snapshot_projection_failed: "
                                    f"{type(exc).__name__}: {exc}"})
            continue
        payloads[code] = usable[code]
        snapshots.append(summary)
    if not payloads:
        return None

    payload = {
        "ok": not failed,
        "mode": "snapshot",
        "trade_date": trade_date,
        "source": "v2",
        "from_cache": True,
        "pool_source": "snapshot",
        "fallback_reason": None,
        "prep_snapshot_date": None,
        "prep_unavailable_reason": None,
        "recommended_count": 0,
        "days_ignored": False,
        "lookback_dates": [],
        "candidate_codes": len(payloads),
        "saved_count": 0,
        "saved": [],
        "empty": [],
        "failed": failed,
        "quote": {"source": "none", "fallback_used": False, "requested": 0,
                  "matched": 0, "missing": [], "degraded": False,
                  "reason": None, "error": None},
        "quote_missing": [],
        "asset_read_error": None,
        "scored_count": len(payloads),
        "snapshot_saved_count": 0,
        "snapshot_ok": True,
        "snapshot_skipped": "cache_hit",
        "snapshot_error": None,
        "snapshots": snapshots,
        "strategy_context": build_strategy_context_from_snapshots(
            trade_date, payloads, failed=failed),
    }
    return AuctionOutcome(payload=payload, exit_code=0, render_names={},
                          mode="snapshot")


def run_auction(*, pool: str = "prep", days=None, services=None,
                today: str | None = None, force: bool = False) -> AuctionOutcome:
    """采集最近 N 日龙头的当日集合竞价盘口 + 行情，落原始资产并计算快照。

    ``pool`` 为 ``prep``（默认，严格 T-1 快照优先）或 ``legacy``；
    ``days`` 仅在 legacy/回退路径生效。``today`` 供命令层沿用既有日期 patch 点。
    默认先只读当日已存快照（命中即零采集/零评分/零写入）；``force=True`` 绕过缓存。
    """
    from dragon_quant.infra.assets import db as assets_db, dragons_mirror
    from dragon_quant.infra.provider.data import get_auction
    from dragon_quant.review_account.auction.serialize import quote_to_payload, quote_from_payload

    source = "v2"
    trade_date = today or _today()      # get_auction 仅返回当日竞价数据
    warnings: list[str] = []

    # 快照优先：早于 provider/日历/候选池/行情执行，命中即只读返回。
    if not force:
        try:
            rows = _load_snapshot_rows(trade_date)
        except Exception as exc:
            rows = None
            warnings.append("⚠️ 竞价快照读取失败，回退完整采集"
                            f"（{type(exc).__name__}: {exc}）")
        if rows is not None:
            usable, cache_failures = _split_snapshot_rows(rows)
            if usable:
                outcome = _snapshot_outcome(
                    trade_date, usable, cache_failures)
                if outcome is not None:
                    return outcome

    resolved_services = _resolve_collect_services(services)
    pool_mode = pool or "prep"
    days_arg = days
    lookback = days_arg or 5

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
            calendar = _trade_calendar(resolved_services)
        except Exception as exc:
            ctx["fallback_reason"] = "calendar"
            ctx["degraded"].append(f"T-1 交易日历不可用：{type(exc).__name__}: {exc}")
            warnings.append(
                f"⚠️ T-1 交易日历不可用（{type(exc).__name__}: {exc}），回退 legacy 池")
        if calendar:
            from dragon_quant.review_account.market import (
                candidate_dates, candidate_loader, collect_candidates_ex,
            )
            from dragon_quant.review_account.models import StrategyConfig
            cfg = StrategyConfig(source=source, candidate_lookback_days=lookback)
            try:
                candidates, ctx = collect_candidates_ex(
                    calendar, trade_date, cfg, candidate_loader(source))
            except Exception as exc:
                candidates = []
                ctx = {**ctx, "fallback_reason": "candidate_pool_error",
                       "degraded": [f"候选池构造失败：{type(exc).__name__}: {exc}"]}
                warnings.append(f"⚠️ 候选池构造失败（{exc}），回退 legacy 池")
            by_code = {c["code"]: (c.get("name") or "")
                       for c in (candidates or []) if c.get("code")}
            if ctx.get("pool_source") == "legacy":
                recent_dates = candidate_dates(calendar, trade_date, lookback)
            elif days_arg is not None:
                days_ignored = True
                warnings.append("⚠️ --days 仅在 legacy 路径生效，本次已忽略")

    if not by_code and not ctx.get("review_decision"):
        # 显式 legacy，或旧 prep 路径回退后仍无候选：走旧口径（最近 N 日龙头并集）。
        # 0.8 有效决策（review_decision=True）的空池绝不回落：第二套旧口径会
        # 重新给出推荐，破坏唯一权威。
        # 读源 = assets.db.dragons 镜像（AC5）；枚举型读路径必须逐日对账，
        # 否则镜像整体缺行会让日期集合前移、池静默换日 + 变小。
        try:
            dates = [d for d in dragons_mirror.list_dragon_trade_dates(source=source)
                     if d < trade_date]
            if dates:
                recent_dates = dates[-lookback:]
                stale = dragons_mirror.stale_dates(recent_dates)
                if stale:
                    # auction 本就有回退路径：不硬失败，但让缺行与缺日原因可见
                    if "dragons_mirror_missing_day" not in ctx["degraded"]:
                        ctx["degraded"].append("dragons_mirror_missing_day")
                    ctx["fallback_reason"] = "mirror_stale_day"
                for d in recent_dates:
                    for dr in dragons_mirror.get_dragons(d, source=source):
                        code = dr.get("code")
                        if not code:
                            continue
                        name = dr.get("name") or ""
                        if code not in by_code or name:
                            by_code[code] = name
                if ctx.get("fallback_reason") is None and pool_mode == "prep":
                    ctx["fallback_reason"] = "pool_unavailable"
            elif pool_mode == "legacy":
                return AuctionOutcome.error({"error": "最近 N 日无龙头，无需采集"})
        except Exception as exc:
            # 镜像不可用时 auction 不硬失败（无候选则走空池分支），但必须可见。
            ctx["degraded"].append(
                f"dragons_mirror_unavailable: {type(exc).__name__}: {exc}")
            if ctx.get("fallback_reason") is None:
                ctx["fallback_reason"] = "mirror_read_error"
            if pool_mode == "legacy":
                return AuctionOutcome.error(
                    {"error": f"龙头镜像读取失败：{type(exc).__name__}: {exc}"})

    if not by_code:
        ignore_reasons = ctx.get("ignore_reasons") or []
        empty_pool = {
            "source": ctx.get("pool_source"),
            "fallback_reason": ctx.get("fallback_reason"),
            "snapshot_date": ctx.get("prep_snapshot_date"),
            "candidate_codes": 0,
        }
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
            "strategy_context": build_strategy_context(
                trade_date, mode="collect", evaluations=[], names={},
                failed=[], empty=[], candidate_codes=[], pool=empty_pool,
                empty_reason="pool_empty"),
        }
        if ctx.get("prep_degraded"):
            warnings.append("⚠️ 本次未采集竞价原始资产（不可再生），可能因 prep 数据降级，"
                            "请先补跑 prep")
        return AuctionOutcome(payload=payload, exit_code=0, render_names={},
                              mode="empty", warnings=warnings)

    codes = sorted(by_code)

    # 行情整批获取（TDX 主、tencent 兜底），绝不逐股请求
    quote_map, quote_report = _fetch_auction_quotes(codes, resolved_services)

    saved, empty, failed, run_assets = [], [], [], []
    for code in codes:
        try:
            items = get_auction(code, services=resolved_services)
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

    payloads, score_failures, summaries, evaluations = _score_inputs(
        assets_db, trade_date, score_inputs, by_code, persisted, saved, warnings)
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
        warnings.append("⚠️ 竞价原始资产已写入，但快照未产出；请检查 failed/snapshot_error")

    names = _render_names(by_code, persisted, saved)
    strategy_context = build_strategy_context(
        trade_date, mode="collect", evaluations=evaluations, names=names,
        failed=failed, empty=empty, candidate_codes=codes,
        pool={"source": ctx.get("pool_source"),
              "fallback_reason": ctx.get("fallback_reason"),
              "snapshot_date": ctx.get("prep_snapshot_date"),
              "candidate_codes": len(codes)})

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
        "strategy_context": strategy_context,
    }

    exit_code = 1 if (len(saved) == 0 and (failed or snapshot_error)) else 0
    return AuctionOutcome(payload=payload, exit_code=exit_code, render_names=names,
                          mode="collect", warnings=warnings)
