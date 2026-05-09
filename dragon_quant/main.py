#!/usr/bin/env python3
"""
龙头战法批量筛选 — 主流程 (v2 subprocess并行版)

工作流：
  0. 预加载共享数据 → /tmp/lsa_YYYYMMDD.json（自动清理3天前旧文件）
  1. 拉取当日涨停榜，过滤出主板 + 非ST
  2. 按连板数排序，取前 N 候选
  3. subprocess 并行调 analyze.py --shared-data（默认2并发）
  4. 按加权综合分排序，取前 N
  5. 输出报告

用法:
  python3 main.py                    # 终端输出报告（默认 top 5，候选 20）
  python3 main.py --json             # JSON 输出
  python3 main.py --top 10           # 自定义 Top N
  python3 main.py --workers 3        # 并行数（默认2，防风控）
  python3 main.py --workers 1        # 串行（风控严格时使用）
"""

import sys
import json
import argparse
import time
import os
import glob
import subprocess
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from dragon_quant.eastmoney_api import (
    get_limit_up_list,
    get_stock_kline,
    infer_consecutive_boards,
    get_market_index_kline,
    get_api_calls_and_clear,
)
from dragon_quant.persist_logger import PersistLogger

OUTPUT_DIR = "/tmp"
MAX_AGE_DAYS = 3


# ─── Step 0: 预加载共享数据 ──────────────────────────

def _preload_shared_data(logger=None):
    """清理旧文件 + 拉取涨停榜/大盘K线 → 写 /tmp/lsa_YYYYMMDD.json。"""
    cutoff = datetime.now() - timedelta(days=MAX_AGE_DAYS)
    for f in glob.glob(os.path.join(OUTPUT_DIR, "lsa_*.json")):
        try:
            if datetime.fromtimestamp(os.path.getmtime(f)) < cutoff:
                os.remove(f)
        except OSError:
            pass

    try:
        limit_up_list = get_limit_up_list()
        api_stats = get_api_calls_and_clear()
        if logger:
            for s in api_stats:
                logger.api(
                    name="get_limit_up_list" if "clist/get" in s["url"] else s["url"],
                    elapsed_ms=s["elapsed_ms"], ok=s["ok"],
                    attempts=s.get("attempts", 1),
                    reason=s.get("reason", ""),
                    last_http_status=s.get("last_http_status"),
                    last_body_snippet=s.get("last_body_snippet"),
                )

        market_kline = get_market_index_kline("1.000001", 20)
        api_stats = get_api_calls_and_clear()
        if logger:
            for s in api_stats:
                logger.api(
                    name="get_market_index_kline",
                    elapsed_ms=s["elapsed_ms"], ok=s["ok"],
                    attempts=s.get("attempts", 1),
                    reason=s.get("reason", ""),
                    last_http_status=s.get("last_http_status"),
                    last_body_snippet=s.get("last_body_snippet"),
                )
    except Exception as e:
        print(f"lsa: 拉取数据失败 ({type(e).__name__}): {e}", file=sys.stderr)
        if logger:
            logger.error_from_exc("preload", e)
        sys.exit(1)

    trading_date = datetime.now().strftime("%Y%m%d")
    if limit_up_list:
        td = limit_up_list[0].get("date", "")
        if td:
            trading_date = td

    data = {
        "generated_at": datetime.now().isoformat(),
        "trading_date": trading_date,
        "limit_up_list": limit_up_list,
        "market_kline": market_kline,
    }

    filepath = os.path.join(OUTPUT_DIR, f"lsa_{trading_date}.json")
    with open(filepath, "w") as f:
        json.dump(data, f, ensure_ascii=False, default=str)

    return filepath, limit_up_list


# ─── Step 1: 过滤涨停榜 ──────────────────────────

def fetch_and_filter(limit_up_list):
    """过滤：主板 + 非ST。返回 filtered_list。"""
    filtered = []
    for s in limit_up_list:
        code = s["code"]
        name = s["name"]
        if code.startswith(("30", "68")):
            continue
        if "ST" in name.upper():
            continue
        filtered.append(s)

    return filtered


# ─── Step 2: 推算连板、排序 ──────────────────

def rank_by_consecutive(stocks, logger=None):
    """推算连板数，按连板降序排列。"""
    for s in stocks:
        code = s["code"]
        try:
            t_api = time.time()
            kl = get_stock_kline(code, 20)
            api_elapsed = (time.time() - t_api) * 1000
            time.sleep(0.05)
            s["est_cons"] = infer_consecutive_boards(code, kl) if kl else 1
            if logger:
                logger.api(
                    name=f"get_stock_kline({code})",
                    elapsed_ms=api_elapsed, ok=bool(kl),
                    attempts=1, reason="" if kl else "empty kline",
                )
        except Exception as e:
            api_elapsed = (time.time() - t_api) * 1000
            s["est_cons"] = 1
            if logger:
                logger.api(
                    name=f"get_stock_kline({code})",
                    elapsed_ms=api_elapsed, ok=False,
                    attempts=1, reason=f"{type(e).__name__}: {e}",
                )

    stocks.sort(key=lambda x: (x["est_cons"], x.get("pct", 0)), reverse=True)
    return stocks


# ─── Step 3: 并行 subprocess 调 analyze.py ───

def _run_single_analysis(code, shared_path, timeout=60):
    """子进程调用 analyze.py --shared-data --json，返回 (result_dict, status, reason)。"""
    # 用 -m 模块方式调用，确保包内导入正确
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = f"{pkg_dir}:{existing}" if existing else pkg_dir
    cmd = [sys.executable, '-m', 'dragon_quant.analyze', code, "--shared-data", shared_path, "--json"]
    try:
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            reason = result.stderr.strip()[-500:] if result.stderr else "unknown error"
            return None, "non_zero_rc", reason, result.returncode
        data = json.loads(result.stdout)
        return data, "ok", "", 0
    except subprocess.TimeoutExpired:
        return None, "timeout", f"{timeout}s timeout", None
    except json.JSONDecodeError as e:
        return None, "json_error", str(e), None
    except Exception as e:
        return None, "error", f"{type(e).__name__}: {e}", None


def _log_subprocess_scores(logger, code, data):
    """记录子进程返回的维度得分和 API 调用打点。"""
    for dim in ["drive", "anti_drop", "leading", "absorption"]:
        d = data.get(dim, {})
        score = d.get("score", 0)
        fallback = d.get("fallback", False)
        dim_time = data.get("_dim_times", {}).get(dim, 0)
        reason = ""
        if fallback:
            bk = d.get("breakdown", {}) or {}
            reason = bk.get("error", bk.get("note", "fallback"))
        logger.dimension_score(code, dim, score, elapsed_ms=dim_time,
                               fallback=fallback, reason=reason)

    for s in data.get("_api_calls", []):
        logger.api(
            name=s.get("url", ""),
            elapsed_ms=s["elapsed_ms"], ok=s["ok"],
            attempts=s.get("attempts", 1),
            reason=s.get("reason", ""),
            last_http_status=s.get("last_http_status"),
            last_body_snippet=s.get("last_body_snippet"),
        )

    elapsed_ms = data.get("_elapsed_ms", 0)
    logger.subprocess(code, "ok", elapsed_ms=elapsed_ms)

    for err in data.get("_errors", []):
        ctx, msg = err[0], err[1] if len(err) > 1 else str(err)
        logger.error(context=f"{code}/{ctx}", message=msg, error_type="SubError")


def run_parallel(candidates, shared_path, max_workers=2, logger=None):
    """并行 subprocess 分析所有候选股。返回结果列表（已排序）。"""
    results = []
    total = len(candidates)
    cons_map = {s["code"]: s.get("est_cons", 1) for s in candidates}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for stock in candidates:
            code = stock["code"]
            futures[executor.submit(_run_single_analysis, code, shared_path)] = stock
            time.sleep(0.3)

        for future in as_completed(futures):
            stock = futures[future]
            code = stock["code"]
            name = stock["name"]
            ind_name = stock.get("industry_name", "")
            cons = cons_map.get(code, 1)
            t_sub = time.time()

            try:
                data, status, reason, exit_code = future.result()
            except Exception as e:
                data, status, reason, exit_code = None, "error", f"{type(e).__name__}: {e}", None

            sub_elapsed = (time.time() - t_sub) * 1000
            if logger:
                logger.subprocess(code, status, elapsed_ms=sub_elapsed,
                                  exit_code=exit_code, reason=reason)

            if data is None:
                results.append({
                    "code": code,
                    "name": name,
                    "industry": ind_name,
                    "est_cons": cons,
                    "pct": stock.get("pct", 0),
                    "composite_score": 0,
                    "rating": "❌ 分析失败",
                    "drive": {"score": 0},
                    "anti_drop": {"score": 0},
                    "leading": {"score": 0},
                    "absorption": {"score": 0},
                    "logs": {},
                    "_errors": [("分析", reason)],
                    "_fallback": True,
                })
            else:
                if logger:
                    _log_subprocess_scores(logger, code, data)
                data["est_cons"] = data.get("est_cons", cons)
                results.append(data)

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    return results


# ─── 输出 ──────────────────────────────────────────────

def print_results(results):
    """终端格式化输出 Top N。"""
    print()
    print("=" * 70)
    print("  🐉 龙头战法批量筛选 — 最新交易日")
    print("=" * 70)

    for rank, r in enumerate(results, 1):
        name = r["name"]
        code = r["code"]
        ind = r.get("industry", "")
        cons = r.get("est_cons", 1)
        score = r["composite_score"]
        rating = r["rating"].replace("🐉 ", "").replace("⭐ ", "").replace("📊 ", "").replace("🐔 ", "")

        print(f"\n{name}({code})——{ind}——{cons}连板")
        print(f"    1. 综合评分: {score:.1f}，{rating}")

        ds = r["drive"]["score"]
        dl = r.get("logs", {}).get("drive", "")
        print(f"    - 🐉 带动性({ds:.0f}): {dl}")

        ads = r["anti_drop"]["score"]
        al = r.get("logs", {}).get("anti_drop", "")
        print(f"    - 🛡️ 抗跌性({ads:.0f}): {al}")

        lds = r["leading"]["score"]
        ll = r.get("logs", {}).get("leading", "")
        print(f"    - 📊 领涨性({lds:.0f}): {ll}")

        abs_ = r["absorption"]["score"]
        abl = r.get("logs", {}).get("absorption", "")
        print(f"    - 💰 资金承接({abs_:.0f}): {abl}")

        print(f"    2. 买点建议：")
        print(f"    - xxx 后续迭代")


# ─── 主流程 ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="龙头战法批量筛选")
    parser.add_argument("--top", type=int, default=5, help="输出前 N 名（默认5）")
    parser.add_argument("--candidates", type=int, default=20,
                        help="分析候选数（默认20）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--workers", type=int, default=2,
                        help="并行分析进程数（默认2，防风控）")
    args = parser.parse_args()

    t_start = time.time()

    logger = PersistLogger(log_dir="./logs", max_days=7)
    logger.session_start(command="main.py", args=sys.argv[1:])

    print(f"lsa: 开始分析，日志 → ./logs/lsa_{datetime.now().strftime('%Y%m%d')}.jsonl",
          file=sys.stderr)

    # Step 0: 预加载共享数据
    t_stage = time.time()
    shared_path, limit_up_list = _preload_shared_data(logger)
    logger.stage("preload", elapsed_ms=(time.time() - t_stage) * 1000,
                 stocks=len(limit_up_list))
    get_api_calls_and_clear()

    # Step 1: 过滤
    t_stage = time.time()
    stocks = fetch_and_filter(limit_up_list)
    logger.stage("filter", elapsed_ms=(time.time() - t_stage) * 1000,
                 in_count=len(limit_up_list), out_count=len(stocks))

    if not stocks:
        print("lsa: 无符合条件的涨停股", file=sys.stderr)
        logger.error(context="filter", message="无符合条件的涨停股")
        logger.session_end(total_elapsed_ms=(time.time() - t_start) * 1000,
                           success=0, failed=0)
        logger.close()
        sys.exit(1)

    # Step 2: 推算连板 + 排序
    t_stage = time.time()
    stocks = rank_by_consecutive(stocks, logger)
    get_api_calls_and_clear()

    candidates = stocks[:args.candidates]
    logger.stage("rank", elapsed_ms=(time.time() - t_stage) * 1000,
                 candidates=len(candidates),
                 max_cons=candidates[0].get("est_cons", 1) if candidates else 0,
                 min_cons=candidates[-1].get("est_cons", 1) if candidates else 0)

    if not candidates:
        print("lsa: 无候选股", file=sys.stderr)
        logger.error(context="rank", message="无候选股")
        logger.session_end(total_elapsed_ms=(time.time() - t_start) * 1000,
                           success=0, failed=0)
        logger.close()
        sys.exit(1)

    # Step 3: 并行分析
    t_stage = time.time()
    analyzed = run_parallel(candidates, shared_path, max_workers=args.workers, logger=logger)
    stage_elapsed = (time.time() - t_stage) * 1000
    success_count = sum(1 for r in analyzed if not r.get("_fallback"))
    failed_count = len(analyzed) - success_count
    logger.stage("analyze", elapsed_ms=stage_elapsed,
                 candidates=len(analyzed), success=success_count, failed=failed_count)

    # Step 4: 取前 N
    top_n = analyzed[:args.top]
    elapsed = time.time() - t_start
    total_ms = elapsed * 1000

    top_scores = [r.get("composite_score", 0) for r in top_n]
    logger.session_end(total_elapsed_ms=total_ms,
                       success=success_count, failed=failed_count,
                       top_scores=[round(s, 1) for s in top_scores])
    logger.close()

    print(f"lsa: 完成 ({elapsed:.0f}s), {success_count}/{len(analyzed)} 成功",
          file=sys.stderr)

    # Step 5: 输出
    if args.json:
        print(json.dumps(top_n, ensure_ascii=False, indent=2, default=str))
    else:
        print_results(top_n)


if __name__ == "__main__":
    main()
