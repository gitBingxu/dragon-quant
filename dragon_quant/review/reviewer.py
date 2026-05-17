"""
复盘编排 — 核心逻辑

流程:
  1. 从 SQLite 读取历史扫描股票列表
  2. 去重检查（同股 5 交易日内跳过）
  3. 并发拉取日 K 线 + 5 分钟 K 线
  4. 确定买入时机（涨停穿透 + 炸板检测）
  5. 确定出场日（买入日后第 N 个交易日）
  6. 计算收益率 + 超额收益
  7. 生成报告 + 持久化到 SQLite
"""

import time
from datetime import datetime
from typing import Dict, List, Optional, Any

from dragon_quant.storage import db
from dragon_quant.storage.paths import RESULTS_DIR
from dragon_quant.providers import create_providers
from dragon_quant.rate_limit import RateLimiter
from dragon_quant.review.entry import find_entry, find_exit, calculate_return
from dragon_quant.review.dedup import should_skip
from dragon_quant.review.reporter import build_table, build_summary


def list_scans(limit: int = 50) -> list[dict]:
    return db.list_scans(limit=limit)


def review_scan(scan_id: str = None, latest: bool = False,
                top_n: int = 5, trading_days: int = 5,
                workers: int = 2, verbose: bool = True) -> dict:
    """
    复盘指定扫描结果。

    Args:
        scan_id: 扫描 ID，如 "20260510_143000"
        latest: True 表示复盘最近一次扫描
        top_n: 复盘前 N 只股票
        trading_days: 持有交易日数
        workers: 并发线程数
        verbose: 是否打印进度

    Returns:
        复盘结果 dict
    """
    if latest:
        scans = db.list_scans(limit=1)
        if not scans:
            return {"error": "无历史扫描记录"}
        scan_id = scans[0]["id"]

    if not scan_id:
        return {"error": "请指定 --timestamp 或 --latest"}

    scan = db.get_scan(scan_id)
    if not scan:
        return {"error": f"扫描 {scan_id} 不存在"}

    stocks = db.get_scan_stocks(scan_id)
    if not stocks:
        return {"error": f"扫描 {scan_id} 无股票数据"}

    scan_date = scan["scan_date"]
    stocks = stocks[:top_n]

    if verbose:
        print(f"\n🐉 复盘: {scan_date} 扫描 → {trading_days} 交易日后")
        print(f"{'═' * 64}")

    providers = create_providers()
    xq = providers["xueqiu"]
    limiter = RateLimiter(max_workers=workers)

    # ── 并发拉取日 K 线 ──
    kline_cache: dict[str, list] = {}
    for s in stocks:
        code = s["code"]
        limiter.submit("xueqiu", "kline",
                       lambda c=code: kline_cache.setdefault(c, xq.get_kline(c, days=60)))
    limiter.wait_all()

    # ── 拉取买入日的 5 分钟 K 线 ──
    min_cache: dict[str, list] = {}
    for s in stocks:
        klines = kline_cache.get(s["code"], [])
        if not klines:
            continue
        # 先确定买入日
        entry_info = find_entry(klines, None, scan_date)
        if not entry_info:
            continue
        entry_date = entry_info["entry_date"]

        # 拉取该日期附近的 5 分钟 K 线（雪球回溯范围约 14 天）
        target_ts = int(datetime.strptime(entry_date, "%Y-%m-%d").timestamp() * 1000)
        limiter.submit("xueqiu", "5min_kline",
                       lambda c=s["code"], ts=target_ts:
                           min_cache.setdefault(c, xq.get_5min_kline_for(c, ts)))
    limiter.wait_all()

    # ── 拉取上证指数日 K ──
    market_code = "000001"
    market_klines = xq.get_kline(market_code, days=60)
    market_entry_info = find_entry(market_klines, None, scan_date)
    benchmark_return = 0.0
    if market_entry_info:
        market_exit = find_exit(market_klines, market_entry_info["entry_date"],
                                trading_days=trading_days)
        if market_exit:
            benchmark_return = calculate_return(
                market_entry_info["entry_price"], market_exit["exit_price"])

    # ── 逐股复盘 ──
    results = []
    skipped = []

    for s in stocks:
        code = s["code"]
        skip, reason = should_skip(code, scan_date)
        if skip:
            skipped.append({"code": code, "name": s["name"], "reason": reason})
            continue

        klines = kline_cache.get(code, [])
        min_klines = min_cache.get(code)

        entry_info = find_entry(klines, min_klines, scan_date)
        if not entry_info:
            results.append({
                "code": code, "name": s["name"],
                "scan_score": s["composite_score"],
                "entry_date": "", "entry_price": 0,
                "exit_date": "", "exit_price": 0,
                "return_pct": None, "excess_return": None,
                "entry_type": "daily",
                "note": "无买入机会（连续涨停未打开）",
            })
            continue

        exit_info = find_exit(klines, entry_info["entry_date"],
                              trading_days=trading_days)
        if not exit_info:
            results.append({
                "code": code, "name": s["name"],
                "scan_score": s["composite_score"],
                "entry_date": entry_info["entry_date"], "entry_price": entry_info["entry_price"],
                "exit_date": "", "exit_price": 0,
                "return_pct": None, "excess_return": None,
                "entry_type": entry_info["entry_type"],
                "note": "出场日数据不足",
            })
            continue

        return_pct = calculate_return(entry_info["entry_price"],
                                       exit_info["exit_price"])
        excess_return = round(return_pct - benchmark_return, 2)

        note = exit_info.get("note", "")
        if note:
            note = f"{note}; {entry_info.get('note', '')}".strip("; ")

        results.append({
            "code": code, "name": s["name"],
            "scan_score": s["composite_score"],
            "entry_date": entry_info["entry_date"],
            "entry_price": entry_info["entry_price"],
            "exit_date": exit_info["exit_date"],
            "exit_price": exit_info["exit_price"],
            "return_pct": return_pct,
            "excess_return": excess_return,
            "entry_type": entry_info["entry_type"],
            "note": note,
        })

    # ── 统计 ──
    valid = [r for r in results if r.get("return_pct") is not None]
    if valid:
        avg_return = round(sum(r["return_pct"] for r in valid) / len(valid), 2)
        win_count = sum(1 for r in valid if r["return_pct"] > 0)
        win_rate = round(win_count / len(valid), 4)
    else:
        avg_return = 0.0
        win_rate = 0.0

    # ── 输出 ──
    if verbose:
        if benchmark_return != 0:
            print(f"同期上证: {benchmark_return:+.2f}%")
        print()

        table = build_table(results, skipped)
        print(table)
        print("-" * 64)
        print(build_summary(results))

    # ── 持久化到 SQLite ──
    review_date = datetime.now().strftime("%Y-%m-%d")
    detail_for_db = []
    for r in results:
        detail_for_db.append({
            "code": r["code"], "name": r["name"],
            "scan_score": r["scan_score"],
            "entry_date": r["entry_date"],
            "entry_price": r["entry_price"],
            "exit_date": r["exit_date"],
            "exit_price": r["exit_price"],
            "return_pct": r.get("return_pct"),
            "excess_return": r.get("excess_return"),
            "entry_type": r.get("entry_type", "daily"),
            "note": r.get("note", ""),
        })

    review_id = db.save_review(scan_id, review_date, trading_days,
                                benchmark_return, avg_return, win_rate,
                                detail_for_db)

    # ── 持久化报告文件 ──
    now_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = RESULTS_DIR / f"review_report_{scan_id}_{now_ts}.txt"
    with open(report_path, "w") as f:
        f.write(f"复盘报告 — 扫描 {scan_id}\n")
        f.write(f"复盘日期: {review_date}\n")
        f.write(f"持有交易日: {trading_days}\n")
        f.write(f"同期上证: {benchmark_return:+.2f}%\n")
        f.write("=" * 64 + "\n\n")
        f.write(build_table(results, skipped))
        f.write("\n\n" + build_summary(results))

    if verbose:
        print(f"\n📊 复盘结果已保存: {report_path}")

    return {
        "scan_id": scan_id,
        "scan_date": scan_date,
        "review_date": review_date,
        "trading_days": trading_days,
        "benchmark_return": benchmark_return,
        "avg_return": avg_return,
        "win_rate": win_rate,
        "results": results,
        "skipped": skipped,
        "report_path": str(report_path),
        "review_id": review_id,
    }