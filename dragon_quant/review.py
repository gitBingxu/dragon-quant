"""
Review 模块 — 龙头回测验证

对 dragons 表中的入选龙头进行回测：
1. 寻找入选日后第一次断板日的日K最低价作为买入价
2. 计算买入后收益窗口内的最大收益
3. 计算从买入日到最大收益出现日之间的最大回撤

用法：
    python -m dragon_quant review [--date 20260519] [--top 5] [--force]
"""

import sys
from datetime import datetime, timedelta
from typing import Optional

from dragon_quant.providers.xueqiu import XueqiuProvider
from dragon_quant.storage import db
from dragon_quant.utils.trading import (
    build_trade_calendar,
    kbar_to_dict,
    find_entry_day,
)


def review_dragon(code: str, trade_date: str, provider: Optional[XueqiuProvider] = None) -> dict:
    """对单只龙头进行回测，返回结果 dict。

    流程：
    1. 拉取该 code 最近 20 天日K
    2. 从 trade_date 次日开始找第一个非涨停日 → 买入日
    3. 计算买入后收益窗口内最大收益
    4. 计算买入日至最大收益出现日之间的最大回撤

    返回：
    {
        "buy_date": str or None,
        "buy_price": float or None,
        "max_return_5d": float or None,       # 百分比
        "max_drawdown_5d": float or None,     # 百分比
        "max_return_hold_days": int or None,  # 达到最大收益的交易日数
        "status": "completed" | "no_entry" | "error",
        "error": str or None,
    }
    """
    if provider is None:
        provider = XueqiuProvider()

    try:
        klines_raw = provider.get_kline(code, days=20, fq_type="normal")
    except Exception as e:
        return {
            "buy_date": None, "buy_price": None,
            "max_return_5d": None, "max_drawdown_5d": None,
            "max_return_hold_days": None,
            "status": "error", "error": f"K线拉取失败: {e}",
        }

    if not klines_raw:
        return {
            "buy_date": None, "buy_price": None,
            "max_return_5d": None, "max_drawdown_5d": None,
            "max_return_hold_days": None,
            "status": "error", "error": "无K线数据",
        }

    klines = [kbar_to_dict(k) for k in klines_raw]

    # 1. 找买入日
    entry_k = find_entry_day(klines, trade_date)
    if not entry_k:
        return {
            "buy_date": None, "buy_price": None,
            "max_return_5d": None, "max_drawdown_5d": None,
            "max_return_hold_days": None,
            "status": "no_entry",
        }

    buy_date = entry_k["date"]
    buy_price = entry_k["low"]  # 断板日最低价作为买入价

    # 2. 构建交易日历，找买入后 5 日
    # A 股 T+1：买入当天不能卖，最少持有 2 天 → 跳过买入日及次日
    end_date = (datetime.strptime(buy_date, "%Y-%m-%d") + timedelta(days=15)).strftime("%Y-%m-%d")
    calendar = build_trade_calendar(buy_date, end_date)

    # 买入后第 2 个交易日开始算收益（跳过买入日 + T+1 日）
    after_buy = sorted([d for d in calendar if d > buy_date])
    future_dates = after_buy[1:5]  # 第 2~5 个交易日，共 4 天
    if not future_dates:
        return {
            "buy_date": buy_date, "buy_price": buy_price,
            "max_return_5d": 0, "max_drawdown_5d": 0,
            "max_return_hold_days": 0,
            "status": "completed",
        }

    # 3. 从已拉取的日K中筛选买入后的 K 线
    future_klines = [k for k in klines if k["date"] in future_dates]

    if not future_klines:
        return {
            "buy_date": buy_date, "buy_price": buy_price,
            "max_return_5d": 0, "max_drawdown_5d": 0,
            "max_return_hold_days": 0,
            "status": "completed",
        }

    # 找最大收益出现日 → 持有交易日数
    peak_k = max(future_klines, key=lambda k: k["high"])
    max_high = peak_k["high"]
    peak_date = peak_k["date"]
    hold_days = sum(1 for d in calendar if buy_date < d <= peak_date)

    # 最大回撤改为只统计“买入日至最大收益出现日”窗口
    drawdown_klines = [k for k in klines if buy_date <= k["date"] <= peak_date]
    min_low = min(k["low"] for k in drawdown_klines) if drawdown_klines else buy_price

    max_return = round((max_high - buy_price) / buy_price * 100, 2)
    max_drawdown = round((min_low - buy_price) / buy_price * 100, 2)

    return {
        "buy_date": buy_date,
        "buy_price": buy_price,
        "max_return_5d": max_return,
        "max_drawdown_5d": max_drawdown,
        "max_return_hold_days": hold_days,
        "status": "completed",
    }


def _run_vpa(code: str, name: str, trade_date: str, verbose: bool = True) -> None:
    """对单只个股做量价分析并打印 + 入库（异常隔离，不影响回测主流程）。"""
    try:
        import json
        from dragon_quant.vpa import analyze
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
            db.upsert_vpa(
                trade_date=trade_date, code=code, name=name,
                source=report.source, health_score=report.health_score,
                signal=report.signal, summary=report.summary,
                factors_json=json.dumps(factors, ensure_ascii=False),
                version=__version__,
            )
    except Exception as ex:
        if verbose:
            print(f"   量价: ⚠️ 分析失败: {ex}", file=sys.stderr)


def run_review(trade_date: Optional[str] = None,
               top_n: Optional[int] = None,
               force: bool = False,
               verbose: bool = True,
               source: str = "v1") -> list[dict]:
    """批量执行龙头回测。

    默认行为（trade_date 未指定时）：
    从 dragons 表中筛选 review_status='pending' 且入选日期距今
    超过 5 个交易日但不足 20 个交易日的记录，一次性回测。

    Args:
        trade_date: 指定日期时只回测该日（手动覆盖自动筛选）
        top_n: 只回测 top N
        force: True=无视 review_status 全部重算
        verbose: 打印进度
        source: 回测数据来源体系（v1/v2）
    """
    if force:
        entries = db.get_pending_dragons(trade_date=trade_date, top_n=top_n,
                                         review_status=None, source=source)
    else:
        entries = db.get_pending_dragons(trade_date=trade_date, top_n=top_n,
                                         source=source)

    # 未指定日期时，自动筛选 5~20 交易日内入选的票
    if not trade_date and entries:
        today_str = datetime.now().strftime("%Y-%m-%d")
        lookback_start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        calendar = build_trade_calendar(lookback_start, today_str)
        trading_days = sorted(calendar, reverse=True)  # 从近到远

        cutoff_upper = trading_days[5] if len(trading_days) > 5 else None   # 5 交易日前
        cutoff_lower = trading_days[19] if len(trading_days) > 19 else None  # 19 交易日前

        filtered = []
        skipped = 0
        for e in entries:
            td_ = e["trade_date"]
            if cutoff_lower is not None and td_ < cutoff_lower:
                skipped += 1
                continue
            if cutoff_upper is not None and td_ > cutoff_upper:
                skipped += 1
                continue
            filtered.append(e)

        if verbose and skipped:
            print(f"🔍 自动筛选：跳过 {skipped} 条（不在 5~20 交易日窗口）")
        entries = filtered

    if not entries:
        if verbose:
            print("✅ 没有待回测的龙头记录")
        return []

    if verbose:
        print(f"🐉 龙头回测 [{source}] — 共 {len(entries)} 条待处理\n")

    provider = XueqiuProvider()
    results = []

    for i, e in enumerate(entries):
        code = e["code"]
        name = e.get("name", "")
        td = e["trade_date"]

        if verbose:
            print(f"[{i+1}/{len(entries)}] {code} {name} (入选 {td}) ...", end=" ", flush=True)

        r = review_dragon(code, td, provider=provider)

        if verbose:
            if r["status"] == "completed":
                hold = r.get("max_return_hold_days", "?")
                print(f"买入 {r['buy_date']} @ {r['buy_price']:.2f}  "
                      f"收益 {r['max_return_5d']:+.1f}%({hold}d)  回撤 {r['max_drawdown_5d']:+.1f}%")
            elif r["status"] == "no_entry":
                print("无可介入日 ❌")
            else:
                print(f"错误: {r.get('error', '?')}")

        # 量价分析（独立模块，异常隔离，不影响回测主流程）
        _run_vpa(code, name, td, verbose=verbose)

        # 写入 DB
        try:
            db.update_dragon_review(
                trade_date=td, code=code,
                buy_date=r.get("buy_date"),
                buy_price=r.get("buy_price"),
                max_return_5d=r.get("max_return_5d"),
                max_drawdown_5d=r.get("max_drawdown_5d"),
                max_return_hold_days=r.get("max_return_hold_days"),
                review_status=r["status"],
                source=source,
            )
        except Exception as ex:
            if verbose:
                print(f"  ⚠️ 写入失败: {ex}", file=sys.stderr)

        results.append({**e, **r})

    if verbose:
        print(f"\n{'═'*56}")
        print(f"📊 回测完成: completed={sum(1 for r in results if r['status']=='completed')}")
        print(f"   no_entry={sum(1 for r in results if r['status']=='no_entry')}")
        print(f"   error={sum(1 for r in results if r['status']=='error')}")

    return results
