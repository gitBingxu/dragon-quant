"""
Orchestrator — 编排层主流程

阶段：
  A. 板块排行 → 前10涨/前10跌
  B. 候选股筛选 → 每板块前5（过滤ST/双创） + 多概念跟踪
  C. 连板高度 + 排序 → top 25
  D. 并发加载评分数据 → 全部写共享缓存
  E. subprocess 并行打分
  F. 输出报告
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
from dragon_quant.providers.cookie import _data_dir as get_data_dir

DATA_DIR = get_data_dir()
SHARED_DIR = DATA_DIR / "shared"
LOG_DIR = DATA_DIR / "logs"


def _is_valid_candidate(stock: StockInfo) -> bool:
    """过滤：非ST、非双创(30/68开头)"""
    name = stock.name or ""
    code = stock.code or ""
    if not code:
        return False
    # ST
    if "ST" in name.upper():
        return False
    # 双创
    if code.startswith(("30", "68")):
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
        if pct and pct >= 9.9:
            count += 1
        else:
            break
    return count


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
        "composite_score": round(composite, 2), "dimensions": dims,
    }


# ════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════

def run_scan(top_n: int = 25, candidates_n: int = 5, workers: int = 2):
    """完整扫描流程"""
    t_start = time.time()
    providers = create_providers()
    em = providers["eastmoney"]
    xq = providers["xueqiu"]
    tx = providers["tencent"]

    cache = DataCache()
    limiter = RateLimiter()

    from dragon_quant.logging.logger import ScanLogger
    logger = ScanLogger()

    # ────────────────────────────────────────────
    # Phase A: 板块排行
    # ────────────────────────────────────────────
    print("📊 Phase A — 板块排行")

    top10_up = em.get_sector_ranking(asc=False)[:10]
    top10_down = em.get_sector_ranking(asc=True)[:10]
    logger.phase("A", "板块排行", up=len(top10_up), down=len(top10_down))
    print(f"   前10涨: {len(top10_up)} 个板块")
    print(f"   前10跌: {len(top10_down)} 个板块")

    if not top10_up:
        print("❌ 未获取到领涨板块", file=sys.stderr)
        return

    # ────────────────────────────────────────────
    # Phase B: 候选股筛选
    # ────────────────────────────────────────────
    print("📋 Phase B — 候选股筛选")

    all_candidates: dict[str, Candidate] = {}   # code → Candidate
    sector_components: dict[str, list[StockInfo]] = {}  # 所有20个板块成分股缓存

    # 拉前10涨板块成分股 → 筛选候选
    for s in top10_up:
        components = em.get_sector_components(s.code, page=1)
        sector_components[s.code] = components

        filtered = [c for c in components if _is_valid_candidate(c)]
        top5 = filtered[:candidates_n]

        for stock in top5:
            if stock.code in all_candidates:
                all_candidates[stock.code].concepts.append(s.name)
            else:
                all_candidates[stock.code] = Candidate(
                    code=stock.code, name=stock.name,
                    concepts=[s.name], primary_sector=s.code,
                )

    # 拉前10跌板块成分股（资金承接用）
    for s in top10_down:
        components = em.get_sector_components(s.code, page=1)
        sector_components[s.code] = components

    candidate_pool = list(all_candidates.values())
    logger.phase("B", f"候选股筛选", count=len(candidate_pool))
    print(f"   候选池: {len(candidate_pool)} 只（去重）")
    for c in candidate_pool:
        print(f"     {c.name:6s} {c.code}  概念: {c.concepts}")

    if not candidate_pool:
        print("❌ 无候选股", file=sys.stderr)
        return

    # 缓存板块成分股
    for s_code, comps in sector_components.items():
        cache.set(f"sector:components:{s_code}", comps)

    # ────────────────────────────────────────────
    # Phase C: 连板高度 + 排序
    # ────────────────────────────────────────────
    print("📈 Phase C — 连板高度")

    # 拉候选股日K线
    for c in candidate_pool:
        kline = xq.get_kline(c.code, days=30)
        c.board_count = _compute_consecutive_boards(kline)
        cache.set(f"kline:day:{c.code}", kline)

    # 拉大盘日K线（上证指数 000001 的 K 线用于跳水日检测）
    market_code = "000001"
    market_kline = xq.get_kline(market_code, days=30)
    cache.set(f"kline:day:{market_code}", market_kline)

    # 排序
    candidate_pool.sort(key=lambda c: (len(c.concepts), c.board_count), reverse=True)
    ranking = candidate_pool[:top_n]
    logger.phase("C", f"连板高度排序", top=len(ranking))

    print(f"   排序取前 {len(ranking)} 只:")
    for r in ranking:
        print(f"     {r.code} {r.name:6s}  概念x{len(r.concepts)}  连板{r.board_count}")

    # ────────────────────────────────────────────
    # Phase D: 并发加载评分数据
    # ────────────────────────────────────────────
    print("⏳ Phase D — 并发加载")

    # T1: 板块5分K（20个板块）
    all_sectors = top10_up + top10_down
    for s in all_sectors:
        limiter.submit("eastmoney", "sector_5min",
                       lambda sc=s.code: (
                           cache.set(f"kline:5min:sector:{sc}",
                                     em.get_sector_5min_kline(sc))))

    # T2: 候选股5分K（25只）
    for r in ranking:
        limiter.submit("xueqiu", "5min_kline",
                       lambda c=r.code: (
                           cache.set(f"kline:5min:{c}",
                                     xq.get_5min_kline(c))))

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
    print(f"   ✅ 全部加载完成")

    # ────────────────────────────────────────────
    # Phase E: 主进程直接打分（跳过子进程序列化）
    # ────────────────────────────────────────────
    print("🔨 Phase E — 四维打分")

    # 注入元数据到缓存（供 scorer 读取）
    cache.set("__meta__:candidates", [
        {"code": c.code, "name": c.name, "concepts": c.concepts,
         "primary_sector": c.primary_sector, "board_count": c.board_count}
        for c in ranking
    ])
    cache.set("__meta__:sector_codes", [s.code for s in all_sectors])

    # 重建候选股对象（用于 drive 的 peer_pool）
    candidate_pool = ranking  # 直接用 sorted ranking

    results = []
    for cand in candidate_pool:
        try:
            sr = _score_one(cand, cache, candidate_pool, [s.code for s in all_sectors],
                           logger)
            results.append(sr)
            dims = sr.get("dimensions", {})
            print(f"  {cand.code} {cand.name:6s}  "
                  f"综合={sr['composite_score']:5.1f}  "
                  f"带动={dims.get('drive',{}).get('score',0):5.1f}  "
                  f"抗跌={dims.get('anti_drop',{}).get('score',0):5.1f}  "
                  f"领涨={dims.get('leadership',{}).get('score',0):5.1f}  "
                  f"承接={dims.get('absorption',{}).get('score',0):5.1f}")
        except Exception as e:
            print(f"  ⚠️ {cand.code} 打分失败: {e}", file=sys.stderr)

    # ────────────────────────────────────────────
    # Phase F: 输出
    # ────────────────────────────────────────────
    elapsed = time.time() - t_start
    logger.phase("F", f"扫描完成", elapsed_s=round(elapsed, 1))
    print(f"\n{'═'*56}")
    print(f"🐉 龙头战法扫描完成 ({elapsed:.0f}s)")
    print(f"{'═'*56}")

    if results:
        results.sort(key=lambda r: r.get("composite_score", 0), reverse=True)
        print(f"\n{'代码':8s} {'名称':8s} {'综合':>6s}  {'带动':>6s}  {'抗跌':>6s}  {'领涨':>6s}  {'承接':>6s}")
        print("-" * 56)
        for r in results:
            dims = r.get("dimensions", {})
            print(f"{r['code']:8s} {r.get('name', ''):8s} "
                  f"{r['composite_score']:6.1f}  "
                  f"{dims.get('drive', {}).get('score', 0):6.1f}  "
                  f"{dims.get('anti_drop', {}).get('score', 0):6.1f}  "
                  f"{dims.get('leadership', {}).get('score', 0):6.1f}  "
                  f"{dims.get('absorption', {}).get('score', 0):6.1f}")

        # ── 自然语言报告 ──
        from dragon_quant.logging.reporter import ReportBuilder
        reporter = ReportBuilder(logger)
        print(f"\n{'═'*56}")
        print(f"📋 TOP 3 详细报告")
        print(f"{'═'*56}")
        for r in results[:3]:
            print(reporter.build_stock_report(
                r["code"], r.get("name", ""),
                r.get("board_count", 0), r.get("concepts", [])
            ))
            print()

        # ── 持久化 ──
        from dragon_quant.providers.cookie import _data_dir as get_data_dir
        log_path = get_data_dir() / "logs" / f"scan_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
        logger.dump_jsonl(log_path)
        print(f"📝 日志已保存: {log_path}")
