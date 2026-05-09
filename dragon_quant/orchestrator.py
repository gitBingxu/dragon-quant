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
import subprocess
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


def _save_shared(cache: DataCache):
    """导出缓存快照到共享文件，子进程读取"""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    path = SHARED_DIR / f"dq_shared_{timestamp}.json"
    snapshot = cache.snapshot()
    with open(path, "w") as f:
        json.dump(snapshot, f, ensure_ascii=False)
    return str(path)


def _load_shared(path: str, cache: DataCache):
    """从共享文件恢复缓存"""
    with open(path) as f:
        data = json.load(f)
    cache.load_snapshot(data)


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

    # ────────────────────────────────────────────
    # Phase A: 板块排行
    # ────────────────────────────────────────────
    print("📊 Phase A — 板块排行")

    top10_up = em.get_sector_ranking(asc=False)[:10]
    top10_down = em.get_sector_ranking(asc=True)[:10]
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
    print(f"   ✅ 全部加载完成")

    # ────────────────────────────────────────────
    # Phase E: 聚合
    # ────────────────────────────────────────────
    shared_path = _save_shared(cache)
    print(f"💾 Phase E — 共享缓存: {shared_path}")

    # ────────────────────────────────────────────
    # Phase F: subprocess 并行打分
    # ────────────────────────────────────────────
    print("🔨 Phase F — 并行打分")

    results = []
    with subprocess.Popen(
        [sys.executable, "-m", "dragon_quant.analyze", "stub", "--shared-cache", shared_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as proc:
        out, err = proc.communicate(timeout=60)
        if out:
            print(out.strip())

    # ────────────────────────────────────────────
    # Phase G: 输出
    # ────────────────────────────────────────────
    elapsed = time.time() - t_start
    print(f"\n══════════════════════════════════════════")
    print(f"🐉 龙头战法扫描完成 ({elapsed:.0f}s)")
    print(f"══════════════════════════════════════════")

    return ranking, cache.stats()
