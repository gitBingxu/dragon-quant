"""
Review 用例 — 龙头回测验证（双口径）。

对 dragons 表中的入选龙头进行回测：

* **宽松口径**（历史行为，写入 ``dragons_*`` 现有 review 字段）：
  入选日后首个 ``high != low`` 的交易日按**最低价**买入。
* **严格口径**（写入 ``dragon_review_strict`` 侧表）：
  入选后**首个交易日**按**开盘价**买入；次日一字涨停也按开盘价（涨停价）成交，
  不顺延到下一个可介入日。

两种口径共享同一收益 / 回撤窗口与公式：买入后第 2~5 个交易日为收益窗口，
最大回撤统计「买入日 → 最大收益出现日」。胜利条件均为
``max_return_5d > 0 且 max_drawdown_5d > -5``（严格大于）。

公共入口：``run_review`` / ``review_dragon``。
"""

import sys
from datetime import datetime, timedelta
from typing import Optional, TypeGuard

from dragon_quant.infra.provider.xueqiu import XueqiuProvider
from dragon_quant.infra import clock
from dragon_quant.scan.storage import dragons as dragon_store
from dragon_quant.scan.storage import review_strict as strict_store
from dragon_quant.vpa import storage as vpa_store
from dragon_quant.infra.market_calendar import (
    build_trade_calendar,
    kbar_to_dict,
    find_entry_day,
)

__all__ = ["run_review", "review_dragon"]

# 收益窗口：买入后第 2~5 个交易日（跳过买入日与 T+1）。
_WINDOW_SKIP = 1
_WINDOW_SIZE = 4

CANNOT_EVALUATE_REASON = "停牌或数据缺失无法区分"
HISTORY_SHORT_REASON = "历史数据不足（K 线未覆盖入选日）"
PARTIAL_WINDOW_REASON = "partial_window"


# ─── 结果结构 ────────────────────────────────────────────────────────────


def _loose_result(status: str, *, error: Optional[str] = None, **fields) -> dict:
    return {
        "buy_date": fields.get("buy_date"),
        "buy_price": fields.get("buy_price"),
        "max_return_5d": fields.get("max_return_5d"),
        "max_drawdown_5d": fields.get("max_drawdown_5d"),
        "max_return_hold_days": fields.get("max_return_hold_days"),
        "status": status,
        "error": error,
        "strict": None,
    }


def _strict_result(status: str, *, reason: Optional[str] = None, **fields) -> dict:
    return {
        "buy_date": fields.get("buy_date"),
        "buy_price": fields.get("buy_price"),
        "max_return_5d": fields.get("max_return_5d"),
        "max_drawdown_5d": fields.get("max_drawdown_5d"),
        "max_return_hold_days": fields.get("max_return_hold_days"),
        "review_status": status,
        "reason": reason,
    }


def _cannot_evaluate() -> dict:
    return _strict_result("cannot_evaluate", reason=CANNOT_EVALUATE_REASON)


# ─── 共享窗口 / 数据校验 ──────────────────────────────────────────────────


def _is_valid_bar(bar: object) -> TypeGuard[dict]:
    """次日 K 线是否可用于严格口径（缺 bar / 无效 OHLC / 零成交平盘占位即不可用）。"""
    if not isinstance(bar, dict):
        return False
    open_px = bar.get("open")
    high = bar.get("high")
    low = bar.get("low")
    close = bar.get("close")
    if open_px is None or high is None or low is None or close is None:
        return False
    if min(open_px, high, low, close) <= 0:
        return False
    if high < low:
        return False
    if not (low <= open_px <= high and low <= close <= high):
        return False
    volume = bar.get("volume")
    if volume is not None and volume <= 0 and high == low:
        # 停牌占位行（平盘 + 零成交）；与真实一字板（有成交）区分开。
        return False
    return True


def _window_metrics(klines: list[dict], calendar: set[str],
                    buy_date: str, buy_price: float) -> Optional[dict]:
    """计算共享收益 / 回撤窗口；窗口为空返回 ``None``。"""
    after_buy = sorted(d for d in calendar if d > buy_date)
    future_dates = after_buy[_WINDOW_SKIP:_WINDOW_SKIP + _WINDOW_SIZE]
    if not future_dates:
        return None

    by_date = {k["date"]: k for k in klines}
    future_klines = [by_date[d] for d in future_dates if d in by_date]
    if not future_klines:
        return None

    peak_k = max(future_klines, key=lambda k: k["high"])
    max_high = peak_k["high"]
    peak_date = peak_k["date"]

    drawdown_klines = [k for k in klines
                       if buy_date <= k["date"] <= peak_date]
    min_low = min(k["low"] for k in drawdown_klines) if drawdown_klines else buy_price

    hold_days = sum(1 for d in calendar if buy_date < d <= peak_date)
    partial = (len(future_dates) < _WINDOW_SIZE
               or len(future_klines) < len(future_dates))
    return {
        "max_return_5d": round((max_high - buy_price) / buy_price * 100, 2),
        "max_drawdown_5d": round((min_low - buy_price) / buy_price * 100, 2),
        "max_return_hold_days": hold_days,
        "partial": partial,
    }


def _strict_review(klines: list[dict], calendar: set[str],
                   trade_date: str) -> dict:
    """严格口径：入选后首个交易日按开盘价买入，不顺延。"""
    next_days = sorted(d for d in calendar if d > trade_date)
    if not next_days:
        return _cannot_evaluate()

    buy_date = next_days[0]
    bar = next((k for k in klines if k["date"] == buy_date), None)
    if not _is_valid_bar(bar):
        return _cannot_evaluate()

    buy_price = bar["open"]
    metrics = _window_metrics(klines, calendar, buy_date, buy_price)
    if metrics is None:
        return _cannot_evaluate()

    return _strict_result(
        "completed",
        reason=PARTIAL_WINDOW_REASON if metrics["partial"] else None,
        buy_date=buy_date,
        buy_price=buy_price,
        max_return_5d=metrics["max_return_5d"],
        max_drawdown_5d=metrics["max_drawdown_5d"],
        max_return_hold_days=metrics["max_return_hold_days"],
    )


def _build_review_calendar(from_date: str, to_date: str) -> set[str]:
    try:
        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")
        needed = max(90, (end - start).days + 30)
    except ValueError:
        needed = 90
    return build_trade_calendar(from_date, to_date, days=needed)


# ─── 单只回测 ────────────────────────────────────────────────────────────


def review_dragon(code: str, trade_date: str,
                  provider: Optional[XueqiuProvider] = None, *,
                  klines: Optional[list[dict]] = None,
                  calendar: Optional[set[str]] = None,
                  as_of: Optional[str] = None) -> dict:
    """对单只龙头做双口径回测，返回结果 dict。

    宽松口径字段在顶层（兼容旧调用）；严格口径在 ``strict`` 子字典：

    ``strict`` 为 ``None`` 表示本次不写严格结果（传输失败 / 数据不足等）；
    否则包含 ``review_status``（``completed`` / ``cannot_evaluate``）、
    ``reason`` 与买入 / 收益 / 回撤 / 持有天字段。

    Args:
        klines: 预取的日 K（dict 列表）；给出时不触网（``run_review`` 按 code
            聚合后复用）。
        calendar: 预取的交易日集合；给出时不重复拉日历。
        as_of: 观测日（默认运行时钟当天），用于截断日历。
    """
    today = as_of or clock.now().strftime("%Y-%m-%d")

    if klines is None:
        if provider is None:
            provider = XueqiuProvider()
        days = _single_record_kline_days(trade_date, today)
        try:
            klines_raw = provider.get_kline(code, days=days, fq_type="normal")
        except Exception as e:
            return _loose_result("error", error=f"K线拉取失败: {e}")
        if not klines_raw:
            return _loose_result("error", error="无K线数据")
        klines = [kbar_to_dict(k) for k in klines_raw]

    if calendar is None:
        calendar = _build_review_calendar(trade_date, today)
    if as_of is not None:
        calendar = {d for d in calendar if d <= as_of}

    if not klines:
        return _loose_result("error", error="无K线数据")
    klines = [k if isinstance(k, dict) else kbar_to_dict(k) for k in klines]

    result = _loose_review(klines, calendar, trade_date)
    result["strict"] = _strict_review(klines, calendar, trade_date)
    return result


def _single_record_kline_days(trade_date: str, today: str) -> int:
    try:
        span = (datetime.strptime(today, "%Y-%m-%d")
                - datetime.strptime(trade_date, "%Y-%m-%d")).days
    except ValueError:
        span = 30
    return max(90, span + 30)


def _loose_review(klines: list[dict], calendar: set[str],
                  trade_date: str) -> dict:
    """宽松口径（历史行为）：首个 ``high != low`` 日按最低价买入。"""
    entry_k = find_entry_day(klines, trade_date)
    if not entry_k:
        return _loose_result("no_entry")

    buy_date = entry_k["date"]
    buy_price = entry_k["low"]
    metrics = _window_metrics(klines, calendar, buy_date, buy_price)
    if metrics is None:
        # 旧契约：窗口为空时宽松口径记 0/0 completed。
        return _loose_result(
            "completed", buy_date=buy_date, buy_price=buy_price,
            max_return_5d=0, max_drawdown_5d=0, max_return_hold_days=0)

    return _loose_result(
        "completed", buy_date=buy_date, buy_price=buy_price,
        max_return_5d=metrics["max_return_5d"],
        max_drawdown_5d=metrics["max_drawdown_5d"],
        max_return_hold_days=metrics["max_return_hold_days"],
    )


def _run_vpa(code: str, name: str, trade_date: str, verbose: bool = True) -> None:
    """对单只个股做量价分析并打印 + 入库（异常隔离，不影响回测主流程）。"""
    try:
        import json
        from dragon_quant.vpa.main import analyze
        from dragon_quant.vpa.report import render_block
        from dragon_quant._version import __version__

        report = analyze(code)
        if verbose:
            print(render_block(report))

        if not report.fallback:
            factors = [
                {"name": f.name, "title": f.title, "signal": f.signal,
                 "score": f.score, "note": f.note,
                 "evidence": f.evidence, "details": f.details}
                for f in report.factors
            ]
            vpa_store.upsert_vpa(
                trade_date=trade_date, code=code, name=name,
                source=report.source, health_score=report.health_score,
                signal=report.signal, summary=report.summary,
                factors_json=json.dumps(factors, ensure_ascii=False),
                version=__version__,
            )
    except Exception as ex:
        if verbose:
            print(f"   量价: ⚠️ 分析失败: {ex}", file=sys.stderr)


# ─── 批量回测 ────────────────────────────────────────────────────────────


def _maturity_cutoff(calendar: set[str]) -> Optional[str]:
    """返回「5 个交易日之前」的日期上界；日历不足 6 天时返回 None。"""
    trading_days = sorted(calendar, reverse=True)
    return trading_days[5] if len(trading_days) > 5 else None


def _calendar_span_days(calendar: set[str], start: str, end: str) -> int:
    return sum(1 for d in calendar if start <= d <= end)


def _fetch_klines(provider, entries: list[dict], calendar: set[str],
                  today: str) -> tuple[dict, dict]:
    """按 code 聚合拉取日 K（每个 code 一次），深度满足其最老记录。"""
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        grouped.setdefault(entry["code"], []).append(entry)

    klines_by_code: dict[str, list[dict]] = {}
    errors_by_code: dict[str, str] = {}
    for code, group in grouped.items():
        oldest = min(e["trade_date"] for e in group)
        days = max(60, _calendar_span_days(calendar, oldest, today) + 10)
        try:
            raw = provider.get_kline(code, days=days, fq_type="normal")
        except Exception as ex:
            errors_by_code[code] = f"K线拉取失败: {ex}"
            continue
        if not raw:
            errors_by_code[code] = "无K线数据"
            continue
        klines_by_code[code] = [kbar_to_dict(k) for k in raw]
    return klines_by_code, errors_by_code


def _entry_short_history(entry: dict, klines: list[dict]) -> bool:
    """K 线未覆盖入选日 → 历史不足（绝不拿最近窗口冒充）。"""
    if not klines:
        return True
    return min(k["date"] for k in klines) > entry["trade_date"]


def run_review(trade_date: Optional[str] = None,
               top_n: Optional[int] = None,
               force: bool = False,
               verbose: bool = True,
               source: str = "v2",
               provider: Optional[XueqiuProvider] = None,
               services=None,
               *,
               recompute_all: bool = False,
               dry_run: bool = False,
               limit: Optional[int] = None) -> list[dict]:
    """批量执行龙头回测（双口径）。

    默认行为（``trade_date`` 未指定）：从 dragons 表中筛选
    ``review_status='pending'`` 且入选日期距当日 **≥5 个交易日**的记录，
    **不再限制 20 个交易日上限**（所有成熟 pending 都参与）。

    ``recompute_all``：全部状态、按 ``trade_date`` 升序（最老优先）重算；
    默认跳过已有严格行的记录，``force=True`` 时覆盖。``dry_run`` 只统计、
    不写库、不拉任何个股 K 线。``limit`` 只用于最老 N 条 canary。

    ``source='v1'`` 保持旧口径与旧 5~20 交易日窗口，且不写严格结果。

    Args:
        trade_date: 指定日期时只回测该日（绕过成熟度筛选）
        top_n: 只回测 top N
        force: 普通模式=所有状态重算；recompute_all 模式=覆盖已有严格行
        verbose: 打印进度
        source: 回测数据来源体系，默认 v2；v1 仅用于历史记录查询。
        provider: 显式 K 线 provider（优先级最高，兼容旧调用）。
        services: 可选 M2A 装配注入。
        recompute_all: 全量重算模式（仅 v2）。
        dry_run: 配合 recompute_all，只输出统计。
        limit: 配合 recompute_all，只处理最老 N 条。
    """
    if recompute_all and source != "v2":
        raise ValueError("v1 不支持 --recompute-all（严格口径仅写入 v2）")
    if (dry_run or limit is not None) and not recompute_all:
        raise ValueError("--dry-run / --limit 只能与 --recompute-all 一起使用")
    if recompute_all and trade_date:
        raise ValueError("--recompute-all 不接受 --date（全量重算按最老记录顺序）")
    if recompute_all and top_n is not None:
        raise ValueError("--recompute-all 不接受 --top（请用 --limit 控制最老 N 条）")

    today = clock.now().strftime("%Y-%m-%d")
    skipped_existing = 0
    maturity_skipped = 0

    if recompute_all:
        entries = dragon_store.get_pending_dragons(
            trade_date=None, top_n=None, review_status=None, source=source)
        entries.sort(key=lambda e: (e["trade_date"], e["code"]))
        existing = set() if force else strict_store.list_keys(source)
        selected: list[dict] = []
        for entry in entries:
            key = (entry["trade_date"], entry["code"])
            if key in existing:
                skipped_existing += 1
                continue
            selected.append(entry)
        entries = selected
        if limit is not None:
            entries = entries[:limit]

        if dry_run:
            if verbose:
                print(f"🔎 重算预检 [{source}] — 全部状态（成熟度未过滤）")
                print(f"   候选记录: {len(selected) + skipped_existing} 条")
                print(f"   已有严格结果跳过: {skipped_existing} 条")
                print(f"   预计处理上界: {len(entries)} 条"
                      + (f"（limit={limit}）" if limit is not None else ""))
                print("   注：成熟度（距当日 ≥5 交易日）在正式执行时按交易日历过滤，"
                      "上界不等同精确待处理数")
            return []

        if not entries:
            if verbose:
                print(f"✅ 没有需要重算的龙头记录（跳过已有严格 {skipped_existing} 条）")
            return []
    else:
        if force:
            entries = dragon_store.get_pending_dragons(
                trade_date=trade_date, top_n=top_n,
                review_status=None, source=source)
        else:
            entries = dragon_store.get_pending_dragons(
                trade_date=trade_date, top_n=top_n, source=source)

    if not entries:
        if verbose:
            print("✅ 没有待回测的龙头记录")
        return []

    # 交易日历一次构建，覆盖最早记录 → 当日。
    earliest = min(e["trade_date"] for e in entries)
    calendar_start = min(earliest,
                         (clock.now() - timedelta(days=45)).strftime("%Y-%m-%d"))
    calendar = _build_review_calendar(calendar_start, today)

    # 成熟度筛选：v2 默认/force/recompute_all 都只处理 ≥5 交易日前的记录；
    # 显式 --date 绕过（既有语义）。v1 继续旧 5~20 交易日窗口。
    if not trade_date:
        skipped = 0
        if source == "v1":
            entries, skipped = _filter_v1_window(entries, calendar)
            cutoff = None
        else:
            cutoff = _maturity_cutoff(calendar)
            if cutoff is not None:
                kept = [e for e in entries if e["trade_date"] <= cutoff]
                skipped = len(entries) - len(kept)
                entries = kept
        if verbose and skipped:
            print(f"🔍 自动筛选：跳过 {skipped} 条"
                  + ("（不在 5~20 交易日窗口）" if source == "v1"
                     else "（距当日不足 5 个交易日）"))
        maturity_skipped = skipped

    if not entries:
        if verbose:
            print("✅ 没有待回测的龙头记录")
        return []

    if provider is None and services is not None:
        bundle = getattr(services, "providers", None)
        if bundle is not None:
            provider = bundle.xueqiu
    if provider is None:
        provider = XueqiuProvider()

    if recompute_all:
        if verbose:
            print(f"🐉 双口径重算 [{source}] — 共 {len(entries)} 条"
                  f"（跳过已有严格 {skipped_existing} 条）\n")
    else:
        if verbose:
            print(f"🐉 龙头回测 [{source}] — 共 {len(entries)} 条待处理\n")

    klines_by_code, errors_by_code = _fetch_klines(
        provider, entries, calendar, today)

    results: list[dict] = []
    for i, entry in enumerate(entries):
        code = entry["code"]
        name = entry.get("name", "")
        td = entry["trade_date"]

        if verbose:
            print(f"[{i+1}/{len(entries)}] {code} {name} (入选 {td}) ...",
                  end=" ", flush=True)

        if code in errors_by_code:
            result = _loose_result("error", error=errors_by_code[code])
        elif _entry_short_history(entry, klines_by_code[code]):
            result = _loose_result("error", error=HISTORY_SHORT_REASON)
        else:
            result = review_dragon(
                code, td, klines=klines_by_code[code],
                calendar=calendar, as_of=today)

        if verbose:
            _print_progress(result)

        # 量价分析：普通 review 维持旧行为；全量重算跳过，避免历史 VPA 污染。
        if not recompute_all:
            _run_vpa(code, name, td, verbose=verbose)

        _persist_result(entry, result, recompute_all=recompute_all,
                        verbose=verbose)

        results.append({**entry, **result})

    if verbose:
        strict_results = [r["strict"] for r in results if r.get("strict")]
        if recompute_all:
            processed = len(results)
            completed = sum(1 for s in strict_results
                            if s["review_status"] == "completed")
            cannot_evaluate = sum(1 for s in strict_results
                                  if s["review_status"] == "cannot_evaluate")
            failed = processed - completed - cannot_evaluate
            skipped = skipped_existing + maturity_skipped
            print(f"\n{'═'*56}")
            print(f"📊 重算完成: processed={processed}")
            print(f"   completed={completed}")
            print(f"   cannot_evaluate={cannot_evaluate}")
            print(f"   failed={failed}")
            print(f"   skipped={skipped}"
                  f"（已有严格 {skipped_existing} / 未成熟 {maturity_skipped}）")
            print(f"   汇总校验: processed == completed + cannot_evaluate + failed"
                  f" -> {processed == completed + cannot_evaluate + failed}")
        else:
            print(f"\n{'═'*56}")
            print(f"📊 回测完成: completed="
                  f"{sum(1 for r in results if r['status'] == 'completed')}")
            print(f"   no_entry="
                  f"{sum(1 for r in results if r['status'] == 'no_entry')}")
            print(f"   error="
                  f"{sum(1 for r in results if r['status'] == 'error')}")
            print(f"   strict_completed="
                  f"{sum(1 for s in strict_results if s['review_status'] == 'completed')}")
            print(f"   strict_cannot_evaluate="
                  f"{sum(1 for s in strict_results if s['review_status'] == 'cannot_evaluate')}")

    return results


def _filter_v1_window(entries: list[dict],
                      calendar: set[str]) -> tuple[list[dict], int]:
    """v1 历史窗口：5~20 个交易日内入选（保持旧语义）。"""
    trading_days = sorted(calendar, reverse=True)
    upper = trading_days[5] if len(trading_days) > 5 else None
    lower = trading_days[19] if len(trading_days) > 19 else None
    kept: list[dict] = []
    for entry in entries:
        td = entry["trade_date"]
        if lower is not None and td < lower:
            continue
        if upper is not None and td > upper:
            continue
        kept.append(entry)
    return kept, len(entries) - len(kept)


def _print_progress(result: dict) -> None:
    if result["status"] == "completed":
        hold = result.get("max_return_hold_days", "?")
        print(f"买入 {result['buy_date']} @ {result['buy_price']:.2f}  "
              f"收益 {result['max_return_5d']:+.1f}%({hold}d)  "
              f"回撤 {result['max_drawdown_5d']:+.1f}%")
    elif result["status"] == "no_entry":
        print("无可介入日 ❌")
    else:
        print(f"错误: {result.get('error', '?')}")


def _persist_result(entry: dict, result: dict, *,
                    recompute_all: bool, verbose: bool) -> None:
    """写回宽松 + 严格结果；全量重算遇到 error 不覆盖既有数据。"""
    is_v2 = entry.get("source", "v2") == "v2"
    if result["status"] == "error":
        if recompute_all:
            return
        strict = None
    else:
        strict = result.get("strict") if is_v2 else None

    try:
        dragon_store.save_dual_review(
            trade_date=entry["trade_date"],
            code=entry["code"],
            loose=result,
            strict=strict,
            source=entry.get("source", "v2"),
        )
    except Exception as ex:
        if verbose:
            print(f"  ⚠️ 写入失败: {ex}", file=sys.stderr)
