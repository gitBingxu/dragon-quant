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
import functools
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

RANK_UP_COUNT = 8     # 领涨板块取前 8（候选筛选 + 5分K）
RANK_UP_COUNT_V2 = 5  # scorers_v2：领涨板块取前 5（候选为板块内当日涨停股）
RANK_DOWN_COUNT = 20  # 领跌板块取前 20（资金承接 + 5分K）

# 数据源 / 接口中文名（仅用于控制台失败提示）
PROVIDER_CN = {"ths": "同花顺", "xueqiu": "雪球", "tencent": "腾讯", "eastmoney": "东财"}
ENDPOINT_CN = {
    "sector_ranking": "获取板块排行",
    "sector_components": "获取板块内个股",
    "sector_5min_kline": "获取板块5分K线",
    "sector_5min_history": "获取板块历史5分K线",
    "sector_1min_kline": "获取板块1分K线",
    "kline": "获取个股日K线",
    "minute_kline": "获取个股分时K线",
    "batch_quotes": "获取批量行情",
}


def _report_api_failures(logger, seen_count: int, verbose: bool) -> int:
    """打印自上次游标以来新增的 api 失败，按 provider+endpoint 聚合。返回新游标。

    数据源：logger.query(category="api", level="error")，每条 category 形如
    api:{provider}:{endpoint}，data["error"] 为失败原因，code 为板块/个股代码。
    """
    fails = logger.query(category="api", level="error")
    new = fails[seen_count:]
    if verbose and new:
        agg: dict = {}
        for e in new:
            parts = e.category.split(":")  # api:provider:endpoint
            if len(parts) < 3:
                continue
            provider, endpoint = parts[1], parts[2]
            g = agg.setdefault((provider, endpoint),
                               {"count": 0, "codes": [], "error": ""})
            g["count"] += 1
            if e.code:
                g["codes"].append(e.code)
            g["error"] = e.data.get("error", "") or g["error"]
        for (provider, endpoint), g in agg.items():
            pcn = PROVIDER_CN.get(provider, provider)
            ecn = ENDPOINT_CN.get(endpoint, endpoint)
            codes = ""
            if g["codes"]:
                shown = ", ".join(g["codes"][:5])
                more = "…" if len(g["codes"]) > 5 else ""
                codes = f"（{shown}{more}）"
            reason = f"：{g['error']}" if g["error"] else ""
            print(f"   ❌ {pcn}·{ecn} 失败 {g['count']} 次{codes}{reason}",
                  file=sys.stderr)
    return len(fails)


def _cache_worth_writing(data) -> bool:
    """非 None 且（非 list 或 list 非空）才值得写盘，避免缓存 403 空结果。"""
    if data is None:
        return False
    if isinstance(data, list):
        return len(data) > 0
    return True


def _cached_fetch(limiter, cache, provider, endpoint, key, fetch_fn,
                  trade_date, *, refresh, volatile, namespace=""):
    """带交易日磁盘缓存的并发取数：命中则跳过 limiter，未命中提交任务并按需落盘。"""
    if not refresh and not volatile:
        if cache.load_for_trade_date(key, trade_date, namespace) is not None:
            return  # 命中：不进 limiter 队列

    def task():
        data = fetch_fn()
        if _cache_worth_writing(data) and not volatile:
            cache.set_for_trade_date(key, data, trade_date, namespace)
        else:
            cache.set(key, data)  # 仅写内存供本轮使用
    limiter.submit(provider, endpoint, task)


def _cached_fetch_sync(cache, key, fetch_fn, trade_date, *,
                       refresh, volatile, namespace=""):
    """同步版（不经 limiter）：命中返回缓存，未命中 fetch 并按需落盘。"""
    if not refresh and not volatile:
        cached = cache.load_for_trade_date(key, trade_date, namespace)
        if cached is not None:
            return cached
    data = fetch_fn()
    if _cache_worth_writing(data) and not volatile:
        cache.set_for_trade_date(key, data, trade_date, namespace)
    else:
        cache.set(key, data)
    return data


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


def _score_one_v2(cand: Candidate, cache: DataCache,
                  candidate_pool: list[Candidate],
                  all_sector_codes: list[str],
                  sector_name_map: dict[str, str],
                  logger) -> dict:
    """scorers_v2 五维评分 + 门槛/加权聚合（输出兼容 _score_one 结构）。"""
    from dragon_quant.scorers_v2 import evaluate

    verdict = evaluate(
        cand.code, cache,
        candidate_pool=candidate_pool,
        primary_sector=cand.primary_sector,
        all_sector_codes=all_sector_codes,
        sector_name_map=sector_name_map,
    )
    dims = {}
    for dim_name, sr in verdict.dims.items():
        dims[dim_name] = {"score": sr.score, "weight": sr.weight, "details": sr.details}
        logger.scorer(dim_name, cand.code, score=sr.score, weight=sr.weight,
                      **sr.details)

    return {
        "code": cand.code, "name": cand.name,
        "concepts": cand.concepts, "board_count": cand.board_count,
        "primary_sector": cand.primary_sector,
        "primary_sector_name": sector_name_map.get(cand.primary_sector, ""),
        "composite_score": verdict.composite, "dimensions": dims,
        "is_true_dragon": verdict.is_true_dragon,
        "reject_reason": verdict.reject_reason,
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
         verbose: bool = True, force: bool = False,
         scorers: str = "v1", refresh_provider_cache: bool = False) -> dict:
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
    use_v2 = (scorers == "v2")
    source = "v2" if use_v2 else "v1"
    rank_up_count = RANK_UP_COUNT_V2 if use_v2 else RANK_UP_COUNT
    
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
            cached_scan = db.get_latest_scan_by_date(scan_date_fmt, top_n, source=source)
            # Step 2: 当天无记录 → 用雪球分时K确定最近交易日，查历史记录
            if not cached_scan:
                trade_date = _get_trade_date()
                if trade_date and trade_date != scan_date_fmt:
                    cached_scan = db.get_latest_scan_by_date(trade_date, top_n, source=source)
                    if cached_scan:
                        cache_note = f"（非交易日，使用最近交易日 {trade_date} 的数据）"
            # 命中缓存 → 输出结果（仅 raw_output 非空时）
            if cached_scan and cached_scan.get("raw_output"):
                if verbose:
                    if cache_note:
                        print(f"💡 {cache_note}")
                    else:
                        print(f"💡 发现今日 ({scan_date_fmt}) 已存在 top_n={top_n} 的扫描记录")
                    print(f"   直接从数据库读取 {source} 缓存 (ID: {cached_scan['id']})...")
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
    ths = providers["ths"]
    xq = providers["xueqiu"]
    tx = providers["tencent"]

    cache = DataCache()
    # 东财接口强反爬：push2/push2his 串行 + 每次调用间隔 1.5~2.5s 随机延迟降低封禁风险
    # 同花顺无强反爬：低延迟即可
    limiter = RateLimiter(max_workers=workers, logger=logger,
                          provider_delays={"eastmoney": (1.5, 2.5),
                                           "ths": (0.3, 0.6)})

    # provider 磁盘缓存命名空间：按交易日复用，避免每次命令重打满同花顺。
    #   trade_date 优先用雪球分时K确定的最近交易日，失败回退自然日。
    #   volatile：--force 闯入交易时段时，provider 缓存只用内存不落盘，
    #             防止盘中波动数据冻结进当日交易日目录污染收盘后复用。
    trade_date = _get_trade_date() or now.strftime("%Y-%m-%d")
    volatile = is_trading_day and 9 <= now.hour < 15
    _cf = functools.partial(_cached_fetch, limiter, cache,
                            trade_date=trade_date,
                            refresh=refresh_provider_cache, volatile=volatile)
    _cf_sync = functools.partial(_cached_fetch_sync, cache,
                                 trade_date=trade_date,
                                 refresh=refresh_provider_cache, volatile=volatile)

    # 失败接口提示游标（每个 phase 后增量打印新出现的 api 失败）
    fail_seen = 0

    # ────────────────────────────────────────────
    # Phase A: 板块排行
    # ────────────────────────────────────────────
    if verbose:
        print("📊 Phase A — 板块排行")

    # 概念板块黑名单（DB 可配置，叠加统计型概念前缀过滤）
    try:
        from dragon_quant.storage import db as _db
        blacklist = _db.get_sector_blacklist()
    except Exception as e:
        if verbose:
            print(f"  ⚠️ 读取板块黑名单失败，忽略: {e}", file=sys.stderr)
        blacklist = []

    def _sector_ok(s) -> bool:
        if any(s.name.startswith(p) for p in STATISTICAL_CONCEPT_PREFIXES):
            return False
        if any(bw and bw in s.name for bw in blacklist):
            return False
        return True

    # 一次抓取全部概念排行（provider 内已多页+本地排序），本地切涨幅/跌幅榜，
    # 避免重复抓 8 页（desc/asc 数据相同）。缓存原始榜，黑名单过滤仍在内存做，
    # 保证黑名单改动即时生效。
    ranking_raw = _cf_sync("sector:ranking",
                           lambda: ths.get_sector_ranking(asc=False)) or []
    ranking_all = [s for s in ranking_raw if _sector_ok(s)]
    top10_up = ranking_all[:rank_up_count]
    top10_down = sorted(ranking_all, key=lambda s: s.pct)[:RANK_DOWN_COUNT]
    logger.phase("A", "板块排行", up=len(top10_up), down=len(top10_down))
    if verbose:
        print(f"   领涨 {len(top10_up)} 个板块:")
        for s in top10_up:
            print(f"     ↑ {s.name}({s.code})  {s.pct:+.2f}%")
        print(f"   领跌 {len(top10_down)} 个板块:")
        for s in top10_down:
            print(f"     ↓ {s.name}({s.code})  {s.pct:+.2f}%")

    fail_seen = _report_api_failures(logger, fail_seen, verbose)

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
    #   v2: all_pages=True 翻页到 ≈50 只，供 leadership 涨幅分位用更大样本
    for s in top10_up:
        _cf("ths", "ths", f"sector:components:{s.code}",
            (lambda sc=s.code: ths.get_sector_components(sc, page=1,
                                                         all_pages=use_v2)),
            namespace=source)
    limiter.wait_all()
    fail_seen = _report_api_failures(logger, fail_seen, verbose)

    for s in top10_up:
        components = cache.get(f"sector:components:{s.code}") or []
        sector_components[s.code] = components
        filtered = [c for c in components if _is_valid_candidate(c)]
        sector_filtered[s.code] = filtered

    # 收集需要拉K线的唯一股票
    #   v1: 每板块前 pre_n 只（按成分股原序）
    #   v2: 每板块当日所有涨停个股（pct ≥ LIMIT_UP_PCT）
    unique_codes: dict[str, StockInfo] = {}
    pre_n = max(candidates_n * 2, 10)
    LIMIT_UP_PCT = 9.9
    for s in top10_up:
        if use_v2:
            picks = [st for st in sector_filtered[s.code] if st.pct >= LIMIT_UP_PCT]
        else:
            picks = sector_filtered[s.code][:pre_n]
        for stock in picks:
            if stock.code not in unique_codes:
                unique_codes[stock.code] = stock

    # 并发拉日K线
    if verbose:
        print(f"   拉取 {len(unique_codes)} 只个股日K线...")
    for code in unique_codes:
        _cf("xueqiu", "kline", f"kline:day:{code}",
            lambda c=code: xq.get_kline(c, days=30))
    limiter.wait_all()
    fail_seen = _report_api_failures(logger, fail_seen, verbose)

    # 计算5日累计涨幅
    for code, stock in unique_codes.items():
        klines = cache.get(f"kline:day:{code}") or []
        stock.five_day_return = _compute_5day_return(klines)

    # 候选构建：
    #   v1: 每板块按5日累计涨幅重排取前 candidates_n
    #   v2: 取每板块当日所有涨停个股（不截断）
    for s in top10_up:
        pre_list = [st for st in sector_filtered[s.code] if st.code in unique_codes]
        if use_v2:
            top_n_stocks = [st for st in pre_list if st.pct >= LIMIT_UP_PCT]
            if verbose:
                print(f"   [{s.name}] 涨停候选 {len(top_n_stocks)} 只"
                      f"（成分股{len(sector_filtered[s.code])}只）")
        else:
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

    candidate_pool = list(all_candidates.values())
    logger.phase("B", f"候选股筛选", count=len(candidate_pool))
    if verbose:
        print(f"   候选池: {len(candidate_pool)} 只（去重），按板块明细:")
        for s in top10_up:
            picks = [c for c in sector_filtered[s.code]
                     if c.code in all_candidates]
            print(f"     ▎{s.name}({s.code})  {len(picks)} 只")
            for c in picks:
                print(f"        {c.code} {c.name}  涨幅{c.pct:+.2f}%")

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
        # v2: 5日总涨幅写入候选（leadership 涨幅分位用），复用日K
        if use_v2:
            c.fived_pct = _compute_5day_return(kline)

    # 拉大盘日K线（上证指数 000001 的 K 线用于跳水日检测）
    market_code = "000001"
    market_kline = _cf_sync(f"kline:day:{market_code}",
                            lambda: xq.get_kline(market_code, days=30)) or []

    # 排序（连板优先）后对候选池全部个股评分，不再截断
    candidate_pool.sort(key=lambda c: (c.board_count, len(c.concepts)), reverse=True)
    ranking = candidate_pool
    logger.phase("C", f"评分候选池", total=len(ranking))

    if verbose:
        print(f"   评分候选池: {len(ranking)} 只")
        for r in ranking:
            print(f"     {r.code} {r.name:6s}  概念x{len(r.concepts)}  连板{r.board_count}")

    fail_seen = _report_api_failures(logger, fail_seen, verbose)

    # ────────────────────────────────────────────
    # Phase D: 并发加载评分数据
    # ────────────────────────────────────────────
    if verbose:
        print("⏳ Phase D — 并发加载")

    # T1: 板块5分K
    #   v1: 当日5分K（provider 内聚合）
    #   v2: 近10交易日历史5分K（虹吸回看），并额外拉主板块当日1分K（带动/抗跌基准）
    #   口径不同 → 按 source 隔离缓存文件，避免 v1/v2 串味
    all_sectors = top10_up + top10_down
    for s in all_sectors:
        if use_v2:
            _cf("ths", "ths", f"kline:5min:sector:{s.code}",
                (lambda sc=s.code: ths.get_sector_5min_kline_history(sc, days=10)),
                namespace=source)
        else:
            _cf("ths", "ths", f"kline:5min:sector:{s.code}",
                (lambda sc=s.code: ths.get_sector_5min_kline(sc)),
                namespace=source)
    if use_v2:
        # 主板块当日1分K（领涨板块即可，带动/板块抗跌基准）
        for s in top10_up:
            _cf("ths", "ths", f"kline:1min:sector:{s.code}",
                (lambda sc=s.code: ths.get_sector_1min_kline(sc)),
                namespace=source)

    # T2: 候选股分时K线
    #   v1: 只拉 ranking（top_n 评分池）
    #   v2: 拉全部候选（均为涨停股，含 drive 封板池对比所需的同板块涨停股）
    minute_targets = candidate_pool if use_v2 else ranking
    for r in minute_targets:
        _cf("xueqiu", "minute_kline", f"kline:1min:{r.code}",
            lambda c=r.code: xq.get_minute_kline(c))
    if use_v2:
        # 大盘当日1分K（抗跌性大盘基准）
        _cf("xueqiu", "minute_kline", "kline:1min:000001",
            lambda: xq.get_minute_kline("000001"))

    # T3: 腾讯批量行情（含收盘盘口 bid1/ask1，liquidity 封单用）
    all_codes = set()
    for sc_list in sector_components.values():
        for s in sc_list:
            all_codes.add(s.code)
    all_codes_list = list(all_codes)[:200]
    _cf("tencent", "quote", "quotes:batch",
        lambda: tx.batch_get_quotes(all_codes_list))

    limiter.wait_all()
    logger.phase("D", "并发数据加载完成")
    prev_seen = fail_seen
    fail_seen = _report_api_failures(logger, fail_seen, verbose)
    if verbose:
        if fail_seen == prev_seen:
            print(f"   ✅ 全部加载完成")
        else:
            print(f"   ⚠️ 数据加载完成（部分接口失败，详见上方）")

    # ────────────────────────────────────────────
    # Phase E: 主进程打分 — 只对 top_n 只
    # ────────────────────────────────────────────
    if verbose:
        print("🔨 Phase E — 五维打分" if use_v2 else "🔨 Phase E — 四维打分")

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

    # v2 用全候选池做板块内对比（连板/涨幅分位、封板池）；v1 用 ranking
    peer_pool = candidate_pool if use_v2 else ranking
    results = []
    for cand in ranking:
        try:
            if use_v2:
                sr = _score_one_v2(cand, cache, peer_pool,
                                   [s.code for s in top10_down],
                                   sector_name_map, logger)
            else:
                sr = _score_one(cand, cache, ranking, [s.code for s in top10_down],
                               sector_name_map, logger)
            results.append(sr)
            if verbose:
                dims = sr.get("dimensions", {})
                if use_v2:
                    print(f"  {cand.code} {cand.name:6s}  "
                          f"综合={sr['composite_score']:5.1f}  "
                          f"{'✓真龙' if sr.get('is_true_dragon') else '✗'}  "
                          f"带动={dims.get('drive',{}).get('score',0):5.1f}  "
                          f"领涨={dims.get('leadership',{}).get('score',0):5.1f}  "
                          f"抗跌={dims.get('anti_drop',{}).get('score',0):5.1f}  "
                          f"流动={dims.get('liquidity',{}).get('score',0):5.1f}  "
                          f"承接={dims.get('absorption',{}).get('score',0):5.1f}")
                else:
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
        "source": source,
        "elapsed_s": round(elapsed, 1),
        "params": {"top_n": top_n, "candidates_n": candidates_n, "workers": workers, "scorers": source},
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
            if use_v2:
                r["report_text"] = reporter.build_stock_report_v2(
                    r["code"], r.get("name", ""),
                    r.get("board_count", 0), r.get("concepts", []),
                    composite_score=r.get("composite_score", 0),
                    dimensions=r.get("dimensions", {}),
                    primary_sector_name=r.get("primary_sector_name", ""),
                    is_true_dragon=r.get("is_true_dragon", False),
                    reject_reason=r.get("reject_reason"),
                )
            else:
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
            if use_v2:
                print(f"\n{'代码':8s} {'名称':8s} {'综合':>6s}  {'带动':>6s}  {'领涨':>6s}  {'抗跌':>6s}  {'流动':>6s}  {'承接':>6s}  真龙")
                print("-" * 64)
                for r in display_list:
                    dims = r.get("dimensions", {})
                    mark = "🐉" if r.get("is_true_dragon") else "✗"
                    print(f"{r['code']:8s} {r.get('name', ''):8s} "
                          f"{r['composite_score']:6.1f}  "
                          f"{dims.get('drive', {}).get('score', 0):6.1f}  "
                          f"{dims.get('leadership', {}).get('score', 0):6.1f}  "
                          f"{dims.get('anti_drop', {}).get('score', 0):6.1f}  "
                          f"{dims.get('liquidity', {}).get('score', 0):6.1f}  "
                          f"{dims.get('absorption', {}).get('score', 0):6.1f}   {mark}")
            else:
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
        scan_id = f"{source}_{timestamp[:8]}_{top_n}"  # v1_20260519_25 / v2_20260519_25

        # 结构化日志 → SQLite
        try:
            from dragon_quant.storage import db
            db.save_scan_logs(scan_id, logger.to_dicts(), source=source)
            log_count = db.count_scan_logs(scan_id, source=source)
            output["log_count"] = log_count
        except Exception as e:
            if verbose:
                print(f"  ⚠️ 日志持久化失败: {e}", file=sys.stderr)
            output["log_count"] = len(logger.to_dicts())

        # 报告文本
        report_path = RESULTS_DIR / f"scan_report_{source}_{timestamp}.txt"
        with open(report_path, "w") as f:
            if use_v2:
                f.write(reporter.build_summary_report_v2(display_list))
            else:
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
                scan_id=scan_id,
                scan_date=scan_date_fmt,
                elapsed_s=output["elapsed_s"],
                top_n=top_n,
                candidates_n=candidates_n,
                workers=workers,
                stocks=results[:top_n],
                raw_output=json.dumps(output, ensure_ascii=False),
                source=source,
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
            updated_count = 0
            for i, r in enumerate(display_list):
                code = r["code"]
                new_rank = i + 1  # 与 save_dragons 中 rank = i + 1 一致

                # 5 日去重：该 code 上次入选距今 < 5 个交易日
                #   仅对「跨日」(last_date 严格早于当天) 重复入选去重；
                #   v1/v2 使用独立 dragons 表，因此同日另一评分器版本不会影响当前体系。
                last_info = db.get_last_entry_with_rank(code, source=source)
                if last_info:
                    last_date, old_rank = last_info
                    if last_date < scan_date_fmt and \
                            trade_days_between(last_date, scan_date_fmt, calendar) < 5:
                        # 新 rank 更好（数字更小）→ 覆写所有字段；否则跳过
                        if old_rank is not None and new_rank < old_rank:
                            updated_count += 1
                            # 继续处理，让 save_dragons 的 UPSERT 更新记录
                        else:
                            skipped_count += 1
                            continue

                quote = quote_map.get(code)
                dragon_data = r.copy()  # 复制基础评分数据
                dragon_data["scan_id"] = scan_id
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

            if verbose and (skipped_count > 0 or updated_count > 0):
                parts = []
                if skipped_count > 0:
                    parts.append(f"跳过 {skipped_count} 只")
                if updated_count > 0:
                    parts.append(f"更新 {updated_count} 只(rank 提升)")
                print(f"  🚫 5 日内去重: {', '.join(parts)}")
                
            db.save_dragons(scan_date_fmt, dragons_to_save,
                            version=__version__, source=source)
            
        except Exception as e:
            if verbose:
                print(f"  ⚠️ SQLite 持久化失败: {e}", file=sys.stderr)

        if verbose:
            print(f" 报告已保存: {report_path}")

    return output


def run_scan(top_n: int = 25, candidates_n: int = 5, workers: int = 2,
             force: bool = False, scorers: str = "v1",
             refresh_provider_cache: bool = False):
    """CLI 入口 — 同 scan() 但带 verbose 输出"""
    scan(top_n=top_n, candidates_n=candidates_n, workers=workers,
         verbose=True, force=force, scorers=scorers,
         refresh_provider_cache=refresh_provider_cache)
