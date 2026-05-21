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
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from dragon_quant.models.types import Candidate, StockInfo
from dragon_quant.cache.data_cache import DataCache
from dragon_quant.rate_limit import RateLimiter
from dragon_quant.providers import create_providers
from dragon_quant.logging.logger import ScanLogger
from dragon_quant.logging.reporter import ReportBuilder
from dragon_quant.storage.paths import DATA_DIR, SHARED_DIR, RESULTS_DIR
from dragon_quant._version import __version__

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


def _print_cached_output(output_data: dict, top_n: int):
    """打印来自 raw_output 的缓存结果"""
    print(f"\n{'═'*56}")
    print(f"🐉 龙头战法扫描完成 (缓存读取) - {output_data.get('timestamp', '?')}")
    print(f"{'═'*56}")
    print(f"\n{'代码':8s} {'名称':8s} {'综合':>6s}  {'带动':>6s}  {'抗跌':>6s}  {'领涨':>6s}  {'承接':>6s}")
    print("-" * 56)
    display_list = output_data.get("ranking", [])[:top_n]
    for r in display_list:
        print(f"{r['code']:8s} {r.get('name', ''):8s} "
              f"{r.get('composite_score', 0):6.1f}  "
              f"{r.get('dimensions', {}).get('drive', {}).get('score', 0):6.1f}  "
              f"{r.get('dimensions', {}).get('anti_drop', {}).get('score', 0):6.1f}  "
              f"{r.get('dimensions', {}).get('leadership', {}).get('score', 0):6.1f}  "
              f"{r.get('dimensions', {}).get('absorption', {}).get('score', 0):6.1f}")
    print(f"\n{'═'*56}")
    print(f"📋 完整详细报告 (来自缓存)")
    print(f"{'═'*56}")
    print(output_data.get("report_text", ""))
    print()



def _get_trade_date() -> Optional[str]:
    """通过雪球API确定最近交易日（A股）。

    先尝试分时K线，失败回退日K线。返回 "YYYY-MM-DD" 或 None。
    """
    from dragon_quant.providers import create_providers
    providers = create_providers()
    xq = providers.get("xueqiu")
    if not xq:
        return None
    bj_tz = timezone(timedelta(hours=8))
    # 优先分时K线（能精确反映当日是否有交易）
    try:
        bars = xq.get_minute_kline("600519")
        if bars:
            ts = bars[-1].timestamp / 1000
            return datetime.fromtimestamp(ts, tz=bj_tz).strftime("%Y-%m-%d")
    except Exception:
        pass
    # 回退日K线（latest bar 即最近交易日）
    try:
        klines = xq.get_kline("600519", days=5)
        if klines:
            ts = klines[-1].timestamp / 1000
            return datetime.fromtimestamp(ts, tz=bj_tz).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


# ════════════════════════════════════════════════════════════
# 核心编排 — scan() Programmtic API
# ════════════════════════════════════════════════════════════

def scan(top_n: int = 5, candidates_n: int = 5, workers: int = 2,
         verbose: bool = True, force: bool = False) -> dict:
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
    bj_tz = timezone(timedelta(hours=8))
    now = datetime.now(bj_tz)
    
    # 1. 交易时间段拦截 (工作日的 9:00 - 15:00)
    is_trading_day = now.weekday() < 5
    if not force and is_trading_day and 9 <= now.hour < 15:
        msg = (
            "⚠️ 拦截: 当前处于 A 股交易时段 (尚未收盘)！\n"
            "【原因】龙头战法的四维打分强依赖于【日K线最终收盘价】、【最终连板高度】和【全天资金承接情况】。\n"
            "在 15:00 收盘前，行情数据处于剧烈波动中，提前执行会导致评分严重失真、排名不准，极易选出伪龙头。\n"
            "💡 强烈推荐在 15:00 收盘后再执行 scan 命令获取准确结果。\n"
            "（若为盘中测试需要，请使用 --force 参数强制执行）"
        )
        if verbose:
            print(msg)
        return {"error": "未收盘", "message": msg, "ranking": [], "report_text": ""}

    # 2. 三步缓存检查
    scan_date_fmt = now.strftime("%Y-%m-%d")
    cache_note = None
    cached_scan = None
    if not force:
        try:
            from dragon_quant.storage import db
            # Step 1: 精确匹配当天日期
            cached_scan = db.get_latest_scan_by_date(scan_date_fmt, top_n)
            # Step 2: 当天无记录 → 用雪球分时K确定最近交易日，查历史记录
            if not cached_scan:
                trade_date = _get_trade_date()
                if trade_date and trade_date != scan_date_fmt:
                    cached_scan = db.get_latest_scan_by_date(trade_date, top_n)
                    if cached_scan:
                        cache_note = f"（非交易日，使用最近交易日 {trade_date} 的数据）"
            # 命中缓存 → 输出结果（仅 raw_output 非空时）
            if cached_scan and cached_scan.get("raw_output"):
                if verbose:
                    if cache_note:
                        print(f"💡 {cache_note}")
                    else:
                        print(f"💡 发现今日 ({scan_date_fmt}) 已存在 top_n={top_n} 的扫描记录")
                    print(f"   直接从数据库读取缓存 (ID: {cached_scan['id']})...")
                output_data = json.loads(cached_scan["raw_output"])
                output_data["cached"] = True
                if verbose:
                    _print_cached_output(output_data, top_n)
                return output_data
        except Exception as e:
            if verbose:
                print(f"  ⚠️ 检查缓存失败，将执行完整扫描: {e}", file=sys.stderr)

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
        limiter.submit("eastmoney", "em",
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
        limiter.submit("eastmoney", "em",
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
        limiter.submit("eastmoney", "em",
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
        
        # 提前初始化 Reporter，并为所有结果生成报告，存入 r["report_text"] 以便持久化
        reporter = ReportBuilder(logger)
        for r in results:
            r["report_text"] = reporter.build_stock_report(
                r["code"], r.get("name", ""),
                r.get("board_count", 0), r.get("concepts", []),
                composite_score=r.get("composite_score", 0),
                dimensions=r.get("dimensions", {}),
                primary_sector_name=r.get("primary_sector_name", ""),
            )

        output["ranking"] = results  # 返回全部评分结果

        # top_n 控制输出范围
        display_list = results[:top_n]

        # 提取自然语言报告并拼接
        report_parts = [r["report_text"] for r in display_list]
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
        scan_id = f"{timestamp[:8]}_{top_n}"  # 20260519_25，同日期同 top_n 自动覆盖

        # 结构化日志 → SQLite
        try:
            from dragon_quant.storage import db
            db.save_scan_logs(scan_id, logger.to_dicts())
            log_count = db.count_scan_logs(scan_id)
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
        scan_date = scan_id[:8]
        scan_date_fmt = f"{scan_date[:4]}-{scan_date[4:6]}-{scan_date[6:8]}"
        try:
            from dragon_quant.storage import db
            db.save_scan(
                scan_id=scan_id,
                scan_date=scan_date_fmt,
                elapsed_s=output["elapsed_s"],
                top_n=top_n,
                candidates_n=candidates_n,
                workers=workers,
                stocks=results[:top_n],
                raw_output=json.dumps(output, ensure_ascii=False)
            )
            
            # 保存 top_n 详细信息到 dragons 表
            # 从缓存的 quotes:batch 提取个股行情快照
            all_quotes = cache.get("quotes:batch") or []
            quote_map = {q.code: q for q in all_quotes} if all_quotes else {}
            
            # 构建交易日历（5 日去重用）
            from dragon_quant.utils.trading import build_trade_calendar, trade_days_between
            cal_start = (datetime.now(bj_tz) - timedelta(days=30)).strftime("%Y-%m-%d")
            cal_end = now.strftime("%Y-%m-%d")
            calendar = build_trade_calendar(cal_start, cal_end)

            dragons_to_save = []
            skipped_count = 0
            for r in display_list:
                code = r["code"]

                # 5 日去重：该 code 上次入选距今 < 5 个交易日则跳过
                last_entry = db.get_last_entry(code)
                if last_entry and trade_days_between(last_entry, scan_date_fmt, calendar) < 5:
                    skipped_count += 1
                    continue

                quote = quote_map.get(code)
                dragon_data = r.copy()  # 复制基础评分数据
                if quote:
                    dragon_data.update({
                        "open_px": quote.open_px,
                        "close_px": quote.price, # quote.price 是现价/收盘价
                        "high_px": quote.high,
                        "low_px": quote.low,
                        "pct": quote.pct,
                        "turnover_rate": quote.turnover_rate,
                        "amount": quote.amount,
                        "market_cap": quote.market_cap,
                    })
                dragons_to_save.append(dragon_data)

            if verbose and skipped_count > 0:
                print(f"  🚫 5 日内已入选，跳过 {skipped_count} 只")
                
            db.save_dragons(scan_date_fmt, scan_id, dragons_to_save, version=__version__)
            
        except Exception as e:
            if verbose:
                print(f"  ⚠️ SQLite 持久化失败: {e}", file=sys.stderr)

        if verbose:
            print(f" 报告已保存: {report_path}")

    return output


def run_scan(top_n: int = 25, candidates_n: int = 5, workers: int = 2, force: bool = False):
    """CLI 入口 — 同 scan() 但带 verbose 输出"""
    scan(top_n=top_n, candidates_n=candidates_n, workers=workers, verbose=True, force=force)
