"""
Orchestrator — 编排层主流程

阶段：
  A. 板块排行 → 前10涨/前10跌
  B. 候选股筛选 → 每板块按5日累计涨幅取前5（过滤ST/双创/北交所） + 多概念跟踪
  C. 连板高度 + 排序（连板优先） → top_n
  D. 并发加载评分数据 → 全部写共享缓存
  E. 主进程四维打分 → 只对 top_n 评分
  F. 输出报告（表格 + 自然语言 + 持久化）

对外接口：
  scan(top_n, candidates_n, workers) → dict   Programmtic API
  run_scan(top_n, candidates_n, workers)       CLI 入口（带 print 输出）
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from dragon_quant.models.types import Candidate, StockInfo
from dragon_quant.cache.data_cache import DataCache
from dragon_quant.rate_limit import RateLimiter
from dragon_quant.providers import create_providers
from dragon_quant.logging.logger import ScanLogger
from dragon_quant.logging.reporter import ReportBuilder
from dragon_quant.storage.paths import DATA_DIR, SHARED_DIR, RESULTS_DIR

STATISTICAL_CONCEPT_PREFIXES = (
    "昨日涨停", "昨日连板", "昨日首板", "昨日打二板",
    "昨日涨停_含一字", "昨日连板_含一字",
    "昨日炸板", "昨日跌停", "昨日触板",
    "最近多板", "东方财富热股",
)

FULL_EVAL_COUNT = 25  # 每次扫描固定对前 25 只候选做四维评分


def _is_valid_candidate(stock: StockInfo) -> bool:
    """过滤：非ST、非双创(30/68)、非北交所(8/92)"""
    name = stock.name or ""
    code = stock.code or ""
    if not code:
        return False
    # ST
    if "ST" in name.upper():
        return False
    # 双创 + 北交所
    if code.startswith(("30", "68", "8")):
        return False
    if code.startswith("92"):
        return False
    return True


def _compute_consecutive_boards(klines: list) -> int:
    """从日K线倒推连板高度。klines 按时间升序排列。
    返回连续涨停天数（>=9.9%视为涨停）。"""
    if not klines:
        return 0
    count = 0
    # 倒序遍历（从最近往前）
    for bar in reversed(klines):
        pct = getattr(bar, "pct", 0)
        if pct >= 9.9:
            count += 1
        else:
            break
    return count


def _compute_5day_return(klines: list) -> float:
    """从日K线计算5日累计涨幅(%)。klines 按时间升序排列。"""
    if len(klines) < 6:
        return 0.0
    if klines[-6].close <= 0:
        return 0.0
    return (klines[-1].close / klines[-6].close - 1) * 100


def _to_serializable(obj):
    """递归转换 dataclass 为 dict"""
    if hasattr(obj, '__dataclass_fields__'):
        return {
            f.name: _to_serializable(getattr(obj, f.name))
            for f in obj.__dataclass_fields__.values()
        }
    if isinstance(obj, list):
        return [_to_serializable(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    # Convert bytes/timestamp objects that might cause issues
    if isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')
    return obj


def _save_shared(cache: DataCache):
    """导出缓存快照到共享文件，子进程读取"""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    path = SHARED_DIR / f"dq_shared_{timestamp}.json"
    snapshot = cache.snapshot()
    serializable = _to_serializable(snapshot)
    with open(path, "w") as f:
        json.dump(serializable, f, ensure_ascii=False)
    return str(path)


def _load_shared(path: str, cache: DataCache):
    """从共享文件恢复缓存"""
    with open(path) as f:
        data = json.load(f)
    cache.load_snapshot(data)


# ════════════════════════════════════════════════════════════
# 单只打分
# ════════════════════════════════════════════════════════════

def _score_one(cand: Candidate, cache: DataCache,
               candidate_pool: list[Candidate],
               all_sector_codes: list[str],
               sector_name_map: dict[str, str],
               logger) -> dict:
    from dragon_quant.scorers.drive import score as score_drive
    from dragon_quant.scorers.anti_drop import score as score_anti_drop
    from dragon_quant.scorers.leadership import score as score_leadership
    from dragon_quant.scorers.absorption import score as score_absorption

    scorers = [
        ("drive",      score_drive,      0.35),
        ("anti_drop",  score_anti_drop,  0.15),
        ("leadership", score_leadership, 0.25),
        ("absorption", score_absorption, 0.25),
    ]

    dims = {}
    composite = 0.0
    for dim_name, score_fn, weight in scorers:
        try:
            kwargs = {"code": cand.code, "cache": cache}
            if dim_name == "drive":
                kwargs["candidate_pool"] = candidate_pool
            if dim_name in ("drive", "leadership", "absorption"):
                kwargs["primary_sector"] = cand.primary_sector
            if dim_name == "absorption":
                kwargs["all_sector_codes"] = all_sector_codes
                kwargs["sector_name_map"] = sector_name_map

            sr = score_fn(**kwargs)
            dims[dim_name] = {"score": sr.score, "weight": sr.weight, "details": sr.details}
            composite += sr.score * sr.weight

            # 结构化日志
            logger.scorer(dim_name, cand.code, score=sr.score, weight=sr.weight,
                          **sr.details)
        except Exception as e:
            logger.error(f"scorer:{dim_name}", f"打分异常: {e}",
                         code=cand.code, exception=str(e))
            dims[dim_name] = {"score": 50.0, "weight": weight, "details": {"error": str(e)}}
            composite += 50.0 * weight

    return {
        "code": cand.code, "name": cand.name,
        "concepts": cand.concepts, "board_count": cand.board_count,
        "primary_sector": cand.primary_sector,
        "primary_sector_name": sector_name_map.get(cand.primary_sector, ""),
        "composite_score": round(composite, 2), "dimensions": dims,
    }


# ════════════════════════════════════════════════════════════
# 核心编排 — scan() Programmtic API
# ════════════════════════════════════════════════════════════

def scan(top_n: int = 5, candidates_n: int = 5, workers: int = 2,
         verbose: bool = True) -> dict:
    """龙头战法完整扫描 — Programmtic API

    返回结构化 dict:
      {
        "timestamp": str,
        "elapsed_s": float,
        "params": {"top_n", "candidates_n", "workers"},
        "sectors": {"up": [...], "down": [...]},
        "ranking": [  ← 仅 top_n 只，按综合分降序
          {
            "code", "name", "concepts", "board_count",
            "primary_sector", "primary_sector_name",
            "composite_score", "dimensions": {
              "drive": {"score", "weight", "details"},
              "anti_drop": {...},
              "leadership": {...},
              "absorption": {...},
            }
          }
        ],
        "api_stats": dict,
        "report_text": str,     ← top_n 只的自然语言报告
        "log_count": int,       ← 持久化日志条数
        "report_path": str,     ← 报告文本路径
      }
    """
    t_start = time.time()

    logger = ScanLogger()

    providers = create_providers(logger=logger)
    em = providers["eastmoney"]
    xq = providers["xueqiu"]
    tx = providers["tencent"]

    cache = DataCache()
    limiter = RateLimiter(max_workers=workers, logger=logger)

    # ────────────────────────────────────────────
    # Phase A: 板块排行
    # ────────────────────────────────────────────
    if verbose:
        print("📊 Phase A — 板块排行")

    top10_up = [s for s in em.get_sector_ranking(asc=False)
                if not any(s.name.startswith(p) for p in STATISTICAL_CONCEPT_PREFIXES)][:10]
    top10_down = [s for s in em.get_sector_ranking(asc=True)
                  if not any(s.name.startswith(p) for p in STATISTICAL_CONCEPT_PREFIXES)][:10]
    logger.phase("A", "板块排行", up=len(top10_up), down=len(top10_down))
    if verbose:
        print(f"   前10涨: {len(top10_up)} 个板块")
        print(f"   前10跌: {len(top10_down)} 个板块")

    if not top10_up:
        return {"error": "未获取到领涨板块", "ranking": [], "report_text": ""}

    # ────────────────────────────────────────────
    # Phase B: 候选股筛选
    # ────────────────────────────────────────────
    if verbose:
        print("📋 Phase B — 候选股筛选")

    all_candidates: dict[str, Candidate] = {}
    sector_components: dict[str, list[StockInfo]] = {}
    sector_filtered: dict[str, list[StockInfo]] = {}

    # 提交前10涨板块成分股请求（过 RateLimiter 防 burst 反爬）
    for s in top10_up:
        limiter.submit("eastmoney", "sector_components",
                       lambda sc=s.code: (
                           cache.set(f"sector:components:{sc}",
                                     em.get_sector_components(sc, page=1))))
    limiter.wait_all()

    for s in top10_up:
        components = cache.get(f"sector:components:{s.code}") or []
        sector_components[s.code] = components
        filtered = [c for c in components if _is_valid_candidate(c)]
        sector_filtered[s.code] = filtered

    # 收集需要拉K线的唯一股票（每板块前 pre_n 只）
    unique_codes: dict[str, StockInfo] = {}
    pre_n = max(candidates_n * 2, 10)
    for s in top10_up:
        for stock in sector_filtered[s.code][:pre_n]:
            if stock.code not in unique_codes:
                unique_codes[stock.code] = stock

    # 并发拉日K线
    if verbose:
        print(f"   拉取 {len(unique_codes)} 只个股日K线...")
    for code in unique_codes:
        limiter.submit("xueqiu", "kline",
                       lambda c=code: cache.set(f"kline:day:{c}", xq.get_kline(c, days=30)))
    limiter.wait_all()

    # 计算5日累计涨幅
    for code, stock in unique_codes.items():
        klines = cache.get(f"kline:day:{code}") or []
        stock.five_day_return = _compute_5day_return(klines)

    # 每个板块按5日累计涨幅重排，取前N
    for s in top10_up:
        pre_list = [st for st in sector_filtered[s.code] if st.code in unique_codes]
        pre_list.sort(key=lambda c: c.five_day_return, reverse=True)
        top_n_stocks = pre_list[:candidates_n]
        for stock in top_n_stocks:
            if stock.code in all_candidates:
                all_candidates[stock.code].concepts.append(s.name)
            else:
                all_candidates[stock.code] = Candidate(
                    code=stock.code, name=stock.name,
                    concepts=[s.name], primary_sector=s.code,
                )

    # 提交前10跌板块成分股请求（资金承接用，过 RateLimiter 防封）
    for s in top10_down:
        limiter.submit("eastmoney", "sector_components",
                       lambda sc=s.code: (
                           cache.set(f"sector:components:{sc}",
                                     em.get_sector_components(sc, page=1))))
    limiter.wait_all()

    for s in top10_down:
        components = cache.get(f"sector:components:{s.code}") or []
        sector_components[s.code] = components

    candidate_pool = list(all_candidates.values())
    logger.phase("B", f"候选股筛选", count=len(candidate_pool))
    if verbose:
        print(f"   候选池: {len(candidate_pool)} 只（去重）")
        for c in candidate_pool:
            print(f"     {c.name:6s} {c.code}  概念: {c.concepts}")

    if not candidate_pool:
        return {"error": "无候选股", "ranking": [], "report_text": ""}

    # 缓存板块成分股
    for s_code, comps in sector_components.items():
        cache.set(f"sector:components:{s_code}", comps)

    # ────────────────────────────────────────────
    # Phase C: 连板高度 + 排序
    # ────────────────────────────────────────────
    if verbose:
        print("📈 Phase C — 连板高度")

    # 日K线已在 Phase B 缓存，直接复用
    for c in candidate_pool:
        kline = cache.get(f"kline:day:{c.code}") or []
        c.board_count = _compute_consecutive_boards(kline)

    # 拉大盘日K线（上证指数 000001 的 K 线用于跳水日检测）
    market_code = "000001"
    market_kline = xq.get_kline(market_code, days=30)
    cache.set(f"kline:day:{market_code}", market_kline)

    # 排序 — 固定取 FULL_EVAL_COUNT 只做四维评分（连板优先）
    candidate_pool.sort(key=lambda c: (c.board_count, len(c.concepts)), reverse=True)
    ranking = candidate_pool[:FULL_EVAL_COUNT]
    logger.phase("C", f"评分候选池", total=len(ranking))

    if verbose:
        print(f"   评分候选池: {len(ranking)} 只")
        for r in ranking:
            print(f"     {r.code} {r.name:6s}  概念x{len(r.concepts)}  连板{r.board_count}")

    # ────────────────────────────────────────────
    # Phase D: 并发加载评分数据
    # ────────────────────────────────────────────
    if verbose:
        print("⏳ Phase D — 并发加载")

    # T1: 板块5分K（20个板块）
    all_sectors = top10_up + top10_down
    for s in all_sectors:
        limiter.submit("eastmoney", "sector_5min",
                       lambda sc=s.code: (
                           cache.set(f"kline:5min:sector:{sc}",
                                     em.get_sector_5min_kline(sc))))

    # T2: 候选股分时K线（只拉 top_n 只）
    for r in ranking:
        limiter.submit("xueqiu", "minute_kline",
                       lambda c=r.code: (
                           cache.set(f"kline:1min:{c}",
                                     xq.get_minute_kline(c))))

    # T3: 腾讯批量行情
    all_codes = set()
    for sc_list in sector_components.values():
        for s in sc_list:
            all_codes.add(s.code)
    all_codes_list = list(all_codes)[:200]
    limiter.submit("tencent", "quote",
                   lambda: cache.set("quotes:batch",
                                    tx.batch_get_quotes(all_codes_list)))

    limiter.wait_all()
    logger.phase("D", "并发数据加载完成")
    if verbose:
        print(f"   ✅ 全部加载完成")

    # ────────────────────────────────────────────
    # Phase E: 主进程打分 — 只对 top_n 只
    # ────────────────────────────────────────────
    if verbose:
        print("🔨 Phase E — 四维打分")

    # 注入元数据到缓存（供 scorer 读取）
    cache.set("__meta__:candidates", [
        {"code": c.code, "name": c.name, "concepts": c.concepts,
         "primary_sector": c.primary_sector, "board_count": c.board_count}
        for c in ranking
    ])
    cache.set("__meta__:sector_codes", [s.code for s in top10_down])

    # 板块名称映射
    sector_name_map = {s.code: s.name for s in all_sectors}
    cache.set("__meta__:sector_name_map", sector_name_map)

    results = []
    for cand in ranking:
        try:
            sr = _score_one(cand, cache, ranking, [s.code for s in top10_down],
                           sector_name_map, logger)
            results.append(sr)
            if verbose:
                dims = sr.get("dimensions", {})
                print(f"  {cand.code} {cand.name:6s}  "
                      f"综合={sr['composite_score']:5.1f}  "
                      f"带动={dims.get('drive',{}).get('score',0):5.1f}  "
                      f"抗跌={dims.get('anti_drop',{}).get('score',0):5.1f}  "
                      f"领涨={dims.get('leadership',{}).get('score',0):5.1f}  "
                      f"承接={dims.get('absorption',{}).get('score',0):5.1f}")
        except Exception as e:
            if verbose:
                print(f"  ⚠️ {cand.code} 打分失败: {e}", file=sys.stderr)

    # ────────────────────────────────────────────
    # Phase F: 输出与持久化
    # ────────────────────────────────────────────
    elapsed = time.time() - t_start
    logger.phase("F", f"扫描完成", elapsed_s=round(elapsed, 1))

    output = {
        "timestamp": time.strftime("%Y%m%d_%H%M%S"),
        "elapsed_s": round(elapsed, 1),
        "params": {"top_n": top_n, "candidates_n": candidates_n, "workers": workers},
        "sectors": {
            "up": [{"code": s.code, "name": s.name, "pct": round(s.pct, 4)} for s in top10_up],
            "down": [{"code": s.code, "name": s.name, "pct": round(s.pct, 4)} for s in top10_down],
        },
        "ranking": [],
        "api_stats": logger.api_stats(),
        "report_text": "",
        "log_count": 0,
        "report_path": "",
    }

    if results:
        results.sort(key=lambda r: r.get("composite_score", 0), reverse=True)
        output["ranking"] = results  # 返回全部评分结果

        # top_n 控制输出范围
        display_list = results[:top_n]

        # 生成报告
        reporter = ReportBuilder(logger)
        report_parts = []
        for r in display_list:
            report_parts.append(reporter.build_stock_report(
                r["code"], r.get("name", ""),
                r.get("board_count", 0), r.get("concepts", []),
                composite_score=r.get("composite_score", 0),
                dimensions=r.get("dimensions", {}),
                primary_sector_name=r.get("primary_sector_name", ""),
            ))
        output["report_text"] = "\n\n".join(report_parts)

        if verbose:
            print(f"\n{'═'*56}")
            print(f"🐉 龙头战法扫描完成 ({elapsed:.0f}s)")
            print(f"{'═'*56}")
            print(f"\n{'代码':8s} {'名称':8s} {'综合':>6s}  {'带动':>6s}  {'抗跌':>6s}  {'领涨':>6s}  {'承接':>6s}")
            print("-" * 56)
            for r in display_list:
                dims = r.get("dimensions", {})
                print(f"{r['code']:8s} {r.get('name', ''):8s} "
                      f"{r['composite_score']:6.1f}  "
                      f"{dims.get('drive', {}).get('score', 0):6.1f}  "
                      f"{dims.get('anti_drop', {}).get('score', 0):6.1f}  "
                      f"{dims.get('leadership', {}).get('score', 0):6.1f}  "
                      f"{dims.get('absorption', {}).get('score', 0):6.1f}")

            print(f"\n{'═'*56}")
            print(f"📋 完整详细报告")
            print(f"{'═'*56}")
            for i, r in enumerate(display_list):
                print(report_parts[i])
                print()

        # ── 持久化 ──
        timestamp = output["timestamp"]

        # 结构化日志 → SQLite
        try:
            import dragon_quant.storage.db as db
            db.save_scan_logs(timestamp, logger.to_dicts())
            log_count = db.count_scan_logs(timestamp)
            output["log_count"] = log_count
        except Exception as e:
            if verbose:
                print(f"  ⚠️ 日志持久化失败: {e}", file=sys.stderr)
            output["log_count"] = len(logger.to_dicts())

        # 报告文本
        report_path = RESULTS_DIR / f"scan_report_{timestamp}.txt"
        with open(report_path, "w") as f:
            f.write(reporter.build_summary_report(display_list))
            f.write("\n\n")
            f.write(output["report_text"])
        output["report_path"] = str(report_path)

        # SQLite 持久化
        scan_date = timestamp[:8]
        scan_date_fmt = f"{scan_date[:4]}-{scan_date[4:6]}-{scan_date[6:8]}"
        try:
            from dragon_quant.storage import db
            db.save_scan(
                scan_id=timestamp,
                scan_date=scan_date_fmt,
                elapsed_s=output["elapsed_s"],
                top_n=top_n,
                candidates_n=candidates_n,
                workers=workers,
                stocks=results,
            )
        except Exception as e:
            if verbose:
                print(f"  ⚠️ SQLite 持久化失败: {e}", file=sys.stderr)

        if verbose:
            print(f" 报告已保存: {report_path}")

    return output


def run_scan(top_n: int = 25, candidates_n: int = 5, workers: int = 2):
    """CLI 入口 — 同 scan() 但带 verbose 输出"""
    scan(top_n=top_n, candidates_n=candidates_n, workers=workers, verbose=True)
