"""
Orchestrator — 编排层主流程

阶段：
  A. 板块排行 → 前5涨/前20跌
  B. 候选股筛选 → 每个领涨行业取当日全部涨停股（过滤ST/双创/北交所）
  C. 连板高度 + 排序（连板优先） → 候选池全部评分
  D. 并发加载五维评分数据 → 全部写共享缓存
  E. 主进程五维打分
  F. 输出报告（表格 + 自然语言 + 持久化）

对外接口：
  scan(top_n, candidates_n, workers) → dict   Programmtic API
  run_scan(top_n, candidates_n, workers)       CLI 入口（带 print 输出）
"""

import json
import sys
import time
import functools
from datetime import datetime, timezone, timedelta
from typing import Optional

from dragon_quant.models.types import Candidate, StockInfo
from dragon_quant.cache.data_cache import DataCache
from dragon_quant.rate_limit import RateLimiter
from dragon_quant.providers import create_providers
from dragon_quant.providers.tdx import is_available as _tdx_available
from dragon_quant.providers.ths import drain_fetch_failures
from dragon_quant.logging.logger import ScanLogger
from dragon_quant.logging.reporter import ReportBuilder
from dragon_quant.storage.paths import RESULTS_DIR
from dragon_quant._version import __version__
from dragon_quant.scorers import registry as R, rank_verdicts
from dragon_quant.scorers.base import DragonVerdict
from dragon_quant.sectors import (
    CATEGORY_CN, SECTOR_CATEGORIES, SECTOR_TOP_N, category_priority,
    components_key, sector_1min_key, sector_5min_key, sector_key,
)

STATISTICAL_CONCEPT_PREFIXES = (
    "昨日涨停", "昨日连板", "昨日首板", "昨日打二板",
    "昨日涨停_含一字", "昨日连板_含一字",
    "昨日炸板", "昨日跌停", "昨日触板",
    "最近多板", "东方财富热股",
)

RANK_DOWN_COUNT = 20  # 领跌板块取前 20（资金承接 + 5分K，固定行业口径）

# 数据源 / 接口中文名（仅用于控制台失败提示）
PROVIDER_CN = {"tdx": "通达信", "ths": "同花顺", "xueqiu": "雪球", "tencent": "腾讯"}
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
    """打印自上次游标以来新增的 api 失败，按 provider+endpoint+维度 聚合。返回新游标。

    数据源：logger.query(category="api", level="error")，每条 category 形如
    api:{provider}:{endpoint}，data["error"] 为失败原因，code 为板块/个股代码，
    data["dimension"] 为板块维度（hy/gn/dq，非板块接口为空）。
    """
    fails = logger.query(category="api", level="error")
    new = fails[seen_count:]
    # 已成功回退的 (endpoint, dimension)（主链路失败但老链路兜底成功）
    rescued = {(e.category.split(":")[-1], e.data.get("dimension"))
               for e in logger.query(category="fallback")}
    if verbose and new:
        agg: dict = {}
        for e in new:
            parts = e.category.split(":")  # api:provider:endpoint
            if len(parts) < 3:
                continue
            provider, endpoint = parts[1], parts[2]
            dimension = e.data.get("dimension") or ""
            g = agg.setdefault((provider, endpoint, dimension),
                               {"count": 0, "codes": [], "error": ""})
            g["count"] += 1
            if e.code:
                g["codes"].append(e.code)
            g["error"] = e.data.get("error", "") or g["error"]
        for (provider, endpoint, dimension), g in agg.items():
            pcn = PROVIDER_CN.get(provider, provider)
            ecn = ENDPOINT_CN.get(endpoint, endpoint)
            is_rescued = provider == "tdx" and (endpoint, dimension or None) in rescued
            codes = ""
            if g["codes"]:
                shown = ", ".join(g["codes"][:5])
                more = "…" if len(g["codes"]) > 5 else ""
                codes = f"（{shown}{more}）"
            reason = f"：{g['error']}" if g["error"] else ""
            icon = "⚠️" if is_rescued else "❌"
            suffix = "（已回退老链路）" if is_rescued else ""
            dim = f"[{dimension}]" if dimension else ""
            print(f"   {icon} {pcn}·{ecn}{dim} 失败 {g['count']} 次{suffix}{codes}{reason}",
                  file=sys.stderr)
    return len(fails)


def _cache_worth_writing(data) -> bool:
    """非 None 且（非 list 或 list 非空）才值得写盘，避免缓存 403 空结果。"""
    if data is None:
        return False
    if isinstance(data, list):
        return len(data) > 0
    return True


def _try_primary(primary_fn, fallback_fn, endpoint, fallback_name="", logger=None):
    """首选主链路(tdx)，失败/空结果回退老链路。回退成功记 fallback 事件供失败归因。"""
    try:
        data = primary_fn()
        if data:
            return data
    except Exception:
        pass
    data = fallback_fn()
    if logger and data and endpoint:
        logger.warn(f"fallback:{endpoint}",
                    f"{endpoint} 主链路(tdx)失败，已回退 {fallback_name}".strip())
    return data


def _cached_fetch(limiter, cache, provider, key, fetch_fn,
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
    limiter.submit(provider, task)


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


def _merge_sector_rankings(rankings: dict[str, list], *, top_n: int, keep) -> list:
    """三维板块榜合并 → 过滤 → 去重 → 按涨幅降序截断。

    rankings: {category: [SectorPerformance, ...]}
    去重键为规范化板块键（source:category:code），排序键为 (-pct, code) 保证确定性。
    """
    seen: set[str] = set()
    merged = []
    for rows in rankings.values():
        for s in rows:
            if not keep(s):
                continue
            key = sector_key(s.source, s.category, s.code)
            if key in seen:
                continue
            seen.add(key)
            merged.append(s)
    merged.sort(key=lambda s: (-s.pct, s.code))
    return merged[:top_n]


def _drop_code_collisions(rankings: dict[str, list], logger=None):
    """逐板块剔除跨维度同 code 的冲突项。

    协议层 get_board_members(board_symbol) / get_stock_kline(market, code) 不携带
    board_type，同 code 会被服务端解析为同一板块。按 hy > gn > dq 优先级保留首个
    占用者，只丢弃后出现维度中该 code 的板块（不是整维剔除）。

    返回 (过滤后的 rankings, [(category, code), ...])。
    """
    owner: dict[str, str] = {}
    out: dict[str, list] = {cat: [] for cat in rankings}
    dropped: list[tuple[str, str]] = []
    for cat in sorted(rankings, key=category_priority):
        for s in rankings[cat]:
            if s.code in owner:
                dropped.append((cat, s.code))
                continue
            owner[s.code] = cat
            out[cat].append(s)
    if dropped and logger:
        logger.error("degraded:sector_code_collision",
                     f"跨维度 code 冲突 {len(dropped)} 个，已逐板块剔除",
                     collisions=[f"{c}:{code}" for c, code in dropped])
    return {c: v for c, v in out.items() if v}, dropped


def _build_candidates(top_sectors: list, filtered_by_key: dict[str, list],
                      limit_up_pct: float) -> dict[str, Candidate]:
    """按总榜顺序把各板块涨停股合并为候选池。

    top_sectors 已按涨幅降序；同一 code 首次出现即归属最高排名板块，
    后续板块只追加 concepts 标签（去重 + primary_sector 归属一次完成）。
    filtered_by_key 已在 Phase B 过 _is_valid_candidate（ST/双创/北交所）。
    """
    out: dict[str, Candidate] = {}
    for s in top_sectors:
        key = sector_key(s.source, s.category, s.code)
        for st in filtered_by_key.get(key, []) or []:
            if st.pct < limit_up_pct:
                continue
            cand = out.get(st.code)
            if cand is None:
                out[st.code] = Candidate(
                    code=st.code, name=st.name, concepts=[s.name],
                    primary_sector=s.code,
                    primary_sector_key=key,
                    primary_sector_category=s.category,
                    primary_sector_source=s.source,
                )
            elif s.name not in cand.concepts:
                cand.concepts.append(s.name)
    return out


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
    """递归转换 dataclass / bytes / 容器为 JSON 友好结构。"""
    if hasattr(obj, '__dataclass_fields__'):
        return {
            f.name: _to_serializable(getattr(obj, f.name))
            for f in obj.__dataclass_fields__.values()
        }
    if isinstance(obj, list):
        return [_to_serializable(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')
    return obj


# ════════════════════════════════════════════════════════════
# 单只打分
# ════════════════════════════════════════════════════════════

def _score_one(cand: Candidate, cache: DataCache,
               candidate_pool: list[Candidate],
               all_sector_keys: list[str],
               sector_name_map: dict[str, str],
               logger) -> dict:
    """五维评分 + 门槛/加权聚合。"""
    from dragon_quant.scorers import evaluate

    verdict = evaluate(
        cand.code, cache,
        candidate_pool=candidate_pool,
        primary_sector=cand.primary_sector,
        primary_sector_key=cand.primary_sector_key,
        all_sector_keys=all_sector_keys,
        sector_name_map=sector_name_map,
    )
    dims = {}
    for dim_name, sr in verdict.dims.items():
        dims[dim_name] = {"score": sr.score, "weight": sr.weight, "details": sr.details}
        logger.scorer(dim_name, cand.code, score=sr.score, weight=sr.weight,
                      **sr.details)

    sector_id = cand.primary_sector_key or cand.primary_sector
    return {
        "code": cand.code, "name": cand.name,
        "concepts": cand.concepts, "board_count": cand.board_count,
        "primary_sector": cand.primary_sector,
        "primary_sector_key": cand.primary_sector_key,
        "primary_sector_category": cand.primary_sector_category,
        "primary_sector_source": cand.primary_sector_source,
        "primary_sector_name": sector_name_map.get(sector_id, ""),
        "composite_score": verdict.composite, "dimensions": dims,
        "is_true_dragon": verdict.is_true_dragon,
        "reject_reason": verdict.reject_reason,
    }


def _print_cached_output(output_data: dict, top_n: int):
    """打印来自 raw_output 的缓存结果"""
    print(f"\n{'═'*56}")
    print(f"🐉 龙头战法扫描完成 (缓存读取) - {output_data.get('timestamp', '?')}")
    print(f"{'═'*56}")
    print(f"\n{'代码':8s} {'名称':8s} {'综合':>6s}  {'带动':>6s}  "
          f"{'领涨':>6s}  {'抗跌':>6s}  {'流动':>6s}  {'承接':>6s}  真龙")
    print("-" * 64)
    display_list = [r for r in output_data.get("ranking", [])
                    if r.get("is_true_dragon")][:top_n]
    if not display_list:
        print("本轮无符合条件的真龙")
    for r in display_list:
        dims = r.get("dimensions", {})
        mark = "🐉" if r.get("is_true_dragon") else "✗"
        print(f"{r['code']:8s} {r.get('name', ''):8s} "
              f"{r.get('composite_score', 0):6.1f}  "
              f"{dims.get('drive', {}).get('score', 0):6.1f}  "
              f"{dims.get('leadership', {}).get('score', 0):6.1f}  "
              f"{dims.get('anti_drop', {}).get('score', 0):6.1f}  "
              f"{dims.get('liquidity', {}).get('score', 0):6.1f}  "
              f"{dims.get('absorption', {}).get('score', 0):6.1f}   {mark}")
    print(f"\n{'═'*56}")
    print("📋 完整详细报告 (来自缓存)")
    print(f"{'═'*56}")
    print("\n\n".join(r.get("report_text", "") for r in display_list))
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
         scorers: str = "v2", refresh_provider_cache: bool = False) -> dict:
    """龙头战法完整扫描 — Programmtic API

    返回结构化 dict:
      {
        "timestamp": str,
        "elapsed_s": float,
        "params": {"top_n", "candidates_n", "workers"},
        "sectors": {"up": [...], "down": [...]},
        "ranking": [  ← 全部评分结果，按综合分降序
          {
            "code", "name", "concepts", "board_count",
            "primary_sector", "primary_sector_name",
            "composite_score", "dimensions": {
              "drive": {"score", "weight", "details"},
              "leadership": {...},
              "anti_drop": {...},
              "liquidity": {...},
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
    source = "v2"

    # 1. 交易时间段拦截 (工作日的 9:00 - 15:00)
    is_trading_day = now.weekday() < 5
    if not force and is_trading_day and 9 <= now.hour < 15:
        msg = (
            "⚠️ 拦截: 当前处于 A 股交易时段 (尚未收盘)！\n"
            "【原因】龙头战法的五维打分强依赖于【日K线最终收盘价】、【最终连板高度】和【全天资金承接情况】。\n"
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
    tdx = providers["tdx"]
    ths = providers["ths"]
    xq = providers["xueqiu"]
    tx = providers["tencent"]

    cache = DataCache()
    # 同花顺无强反爬：低延迟即可
    limiter = RateLimiter(max_workers=workers, logger=logger,
                          provider_delays={"ths": (0.3, 0.6)})

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
    # 仅当 easy_tdx 可用时才记录回退事件，避免未安装时每个 fetch 都刷一条日志
    _try = functools.partial(_try_primary,
                             logger=logger if _tdx_available() else None)

    # 失败接口提示游标（每个 phase 后增量打印新出现的 api 失败）
    fail_seen = 0

    # ────────────────────────────────────────────
    # Phase A: 板块排行
    # ────────────────────────────────────────────
    if verbose:
        print("📊 Phase A — 板块排行")

    # 板块黑名单（DB 可配置，叠加统计型概念前缀过滤）
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

    # 三维融合：行业(HY) + 概念(GN) + 地区(DQ)，逐维度 TDX → THS 回退。
    # 缓存键含 source+category，避免跨源/跨维度串号（§7.2）。
    rankings: dict[str, list] = {}
    sector_sources: dict[str, str] = {}
    degraded: list[str] = []

    def _fetch_category_ranking(cat: str):
        """单维度排行：tdx → ths → 空。两段调用都兜异常，单维度失败不中断扫描。"""
        rows = []
        try:
            rows = _cf_sync(f"sector:ranking:tdx:{cat}",
                            lambda c=cat: tdx.get_sector_ranking(asc=False,
                                                                 category=c)) or []
        except ValueError:
            raise
        except Exception as e:
            if logger:
                logger.warn("fallback:sector_ranking",
                            f"sector_ranking[{cat}] 主链路(tdx)异常，尝试回退同花顺: {e}",
                            dimension=cat)
            rows = []
        if rows:
            return rows, "tdx"
        try:
            rows = _cf_sync(f"sector:ranking:ths:{cat}",
                            lambda c=cat: ths.get_sector_ranking(asc=False,
                                                                 category=c)) or []
        except ValueError:
            raise
        except Exception as e:
            if logger:
                logger.warn("degraded:sector_ranking",
                            f"sector_ranking[{cat}] 回退同花顺异常: {e}",
                            dimension=cat)
            rows = []
        if rows:
            if logger and _tdx_available():
                logger.warn("fallback:sector_ranking",
                            f"sector_ranking[{cat}] 主链路(tdx)失败，已回退 同花顺",
                            dimension=cat)
            return rows, "ths"
        return [], ""

    for cat in SECTOR_CATEGORIES:
        rows, src = _fetch_category_ranking(cat)
        if rows:
            rankings[cat] = rows
            sector_sources[cat] = src
        else:
            degraded.append(cat)
            if logger:
                logger.warn("degraded:sector_ranking",
                            f"sector_ranking[{cat}] 不可用，已从融合总榜剔除",
                            dimension=cat)

    # 防御：进入融合榜的板块必须带维度与来源，否则会生成空 namespace 缓存键
    for cat in list(rankings):
        valid = [s for s in rankings[cat] if s.source and s.category]
        dropped_rows = len(rankings[cat]) - len(valid)
        if dropped_rows and logger:
            logger.error("degraded:sector_identity",
                         f"sector_ranking[{cat}] 有 {dropped_rows} 个板块缺少 "
                         f"source/category，已剔除",
                         dimension=cat)
        rankings[cat] = valid
        if not rankings[cat]:
            rankings.pop(cat, None)
            sector_sources.pop(cat, None)
            if cat not in degraded:
                degraded.append(cat)

    # 协议层按 code 请求、不带 board_type：逐板块剔除跨维度冲突 code（hy > gn > dq）
    rankings, collisions = _drop_code_collisions(rankings, logger)
    # 按最终可用维度反推状态：被碰撞清空/取数失败的维度统一 unavailable + degraded
    degraded = [c for c in SECTOR_CATEGORIES if c not in rankings]

    top_up = _merge_sector_rankings(rankings, top_n=SECTOR_TOP_N, keep=_sector_ok)
    # 领跌固定行业口径（资金承接语义不变）
    top_down = sorted([s for s in rankings.get("hy", []) if _sector_ok(s)],
                      key=lambda s: s.pct)[:RANK_DOWN_COUNT]
    sector_providers = {cat: (tdx if sector_sources[cat] == "tdx" else ths)
                        for cat in rankings}
    sources_out = {cat: (sector_sources.get(cat) if cat in rankings else "unavailable")
                   for cat in SECTOR_CATEGORIES}

    logger.phase("A", "板块排行", up=len(top_up), down=len(top_down),
                 degraded=degraded)
    if verbose:
        print(f"   领涨 {len(top_up)} 个板块（行业+概念+地区融合 Top{SECTOR_TOP_N}）:")
        for s in top_up:
            print(f"     ↑ {s.name}({s.code}, {CATEGORY_CN.get(s.category, s.category)})"
                  f"  {s.pct:+.2f}%")
        print(f"   领跌 {len(top_down)} 个板块（行业）:")
        for s in top_down:
            print(f"     ↓ {s.name}({s.code})  {s.pct:+.2f}%")
        if degraded:
            names = "、".join(CATEGORY_CN.get(c, c) for c in degraded)
            print(f"   ⚠️ 维度不可用: {names}（已从融合榜剔除）", file=sys.stderr)

    fail_seen = _report_api_failures(logger, fail_seen, verbose)

    if not top_up:
        return {"error": "未获取到领涨板块", "ranking": [], "report_text": ""}

    # ────────────────────────────────────────────
    # Phase B: 候选股筛选
    # ────────────────────────────────────────────
    if verbose:
        print("📋 Phase B — 候选股筛选")

    sector_components: dict[str, list[StockInfo]] = {}
    sector_filtered: dict[str, list[StockInfo]] = {}

    # 提交入选板块成分股请求（过 RateLimiter 防 burst 反爬）
    for s in top_up:
        key = sector_key(s.source, s.category, s.code)
        _cf(s.source, components_key(key),
            (lambda s=s: sector_providers[s.category].get_sector_components(
                s.code, page=1, all_pages=True, category=s.category)))
    limiter.wait_all()
    fail_seen = _report_api_failures(logger, fail_seen, verbose)

    for s in top_up:
        key = sector_key(s.source, s.category, s.code)
        components = cache.get(components_key(key)) or []
        sector_components[key] = components
        sector_filtered[key] = [c for c in components if _is_valid_candidate(c)]

    # 收集需要拉K线的唯一股票：每板块当日所有涨停个股（pct ≥ LIMIT_UP_PCT）。
    unique_codes: dict[str, StockInfo] = {}
    LIMIT_UP_PCT = 9.9
    for s in top_up:
        key = sector_key(s.source, s.category, s.code)
        picks = [st for st in sector_filtered[key] if st.pct >= LIMIT_UP_PCT]
        for stock in picks:
            if stock.code not in unique_codes:
                unique_codes[stock.code] = stock

    # 并发拉日K线
    if verbose:
        print(f"   拉取 {len(unique_codes)} 只个股日K线...")
    for code in unique_codes:
        _cf("xueqiu", f"kline:day:{code}",
            lambda c=code: _try(lambda: tdx.get_kline(c, days=30),
                                lambda: xq.get_kline(c, days=30),
                                "kline", "雪球"))
    limiter.wait_all()
    fail_seen = _report_api_failures(logger, fail_seen, verbose)

    # 计算5日累计涨幅
    for code, stock in unique_codes.items():
        klines = cache.get(f"kline:day:{code}") or []
        stock.five_day_return = _compute_5day_return(klines)

    # 候选构建：按总榜降序合并各板块涨停股（去重 + primary_sector 归属）。
    all_candidates = _build_candidates(top_up, sector_filtered, LIMIT_UP_PCT)
    candidate_pool = list(all_candidates.values())
    logger.phase("B", "候选股筛选", count=len(candidate_pool))
    if verbose:
        print(f"   候选池: {len(candidate_pool)} 只（去重），按板块明细:")
        for s in top_up:
            key = sector_key(s.source, s.category, s.code)
            picks = [c for c in sector_filtered[key]
                     if c.code in all_candidates]
            print(f"     ▎{s.name}({s.code}, {CATEGORY_CN.get(s.category, s.category)})"
                  f"  {len(picks)} 只")
            for c in picks:
                print(f"        {c.code} {c.name}  涨幅{c.pct:+.2f}%")

    if not candidate_pool:
        return {"error": "无候选股", "ranking": [], "report_text": ""}

    # ────────────────────────────────────────────
    # Phase C: 连板高度 + 排序
    # ────────────────────────────────────────────
    if verbose:
        print("📈 Phase C — 连板高度")

    # 日K线已在 Phase B 缓存，直接复用
    for c in candidate_pool:
        kline = cache.get(f"kline:day:{c.code}") or []
        c.board_count = _compute_consecutive_boards(kline)
        # 5日总涨幅写入候选（leadership 涨幅分位用），复用日K。
        c.fived_pct = _compute_5day_return(kline)

    # 排序（连板优先）后对候选池全部个股评分，不再截断
    candidate_pool.sort(key=lambda c: (c.board_count, len(c.concepts)), reverse=True)
    ranking = candidate_pool
    logger.phase("C", "评分候选池", total=len(ranking))

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

    # T1: 板块历史5分K（虹吸回看）+ 入选板块当日1分K（带动/抗跌基准）
    all_sectors = top_up + top_down
    for s in all_sectors:
        key = sector_key(s.source, s.category, s.code)
        _cf(s.source, sector_5min_key(key),
            (lambda s=s: sector_providers[s.category].get_sector_5min_kline_history(
                s.code, days=10, category=s.category)))
    for s in top_up:
        key = sector_key(s.source, s.category, s.code)
        _cf(s.source, sector_1min_key(key),
            (lambda s=s: sector_providers[s.category].get_sector_1min_kline(
                s.code, category=s.category)))

    # T2: 全部候选股分时K线（含 drive 封板池对比所需的同板块涨停股）
    for r in candidate_pool:
        _cf("xueqiu", f"kline:1min:{r.code}",
            lambda c=r.code: _try(lambda: tdx.get_minute_kline(c),
                                  lambda: xq.get_minute_kline(c),
                                  "minute_kline", "雪球"))
    _cf("xueqiu", f"kline:1min:{R.MARKET_SYMBOL}",
        lambda: _try(lambda: tdx.get_minute_kline(R.MARKET_SYMBOL),
                     lambda: xq.get_minute_kline(R.MARKET_SYMBOL),
                     "minute_kline", "雪球"))

    # T3: 腾讯批量行情（含收盘盘口 bid1/ask1，liquidity 封单用）
    all_codes = set()
    for sc_list in sector_components.values():
        for s in sc_list:
            all_codes.add(s.code)
    all_codes_list = sorted(all_codes)

    def fetch_quotes():
        def _tx_quotes():
            quotes = {}
            for start in range(0, len(all_codes_list), 200):
                for quote in tx.batch_get_quotes(all_codes_list[start:start + 200]):
                    quotes[quote.code] = quote
            return [quotes[code] for code in sorted(quotes)]
        # tdx 内部按 80 只/次分片
        return _try(lambda: tdx.batch_get_quotes(all_codes_list),
                    _tx_quotes, "batch_quotes", "腾讯")

    _cf("tencent", "quotes:batch", fetch_quotes)

    limiter.wait_all()
    logger.phase("D", "并发数据加载完成")
    prev_seen = fail_seen
    fail_seen = _report_api_failures(logger, fail_seen, verbose)

    ths_fails = drain_fetch_failures()
    if verbose and ths_fails:
        total = sum(ths_fails.values())
        detail = "、".join(f"{k}×{v}" for k, v in sorted(ths_fails.items()))
        print(f"   ⚠️ 同花顺取数失败 {total} 次（{detail}），相关维度已降级中性分",
              file=sys.stderr)

    if verbose:
        if fail_seen == prev_seen:
            print("   ✅ 全部加载完成")
        else:
            print("   ⚠️ 数据加载完成（部分接口失败，详见上方）")

    # ────────────────────────────────────────────
    # Phase E: 主进程打分 — 对候选池全部个股
    # ────────────────────────────────────────────
    if verbose:
        print("🔨 Phase E — 五维打分")

    # 注入元数据到缓存（供 scorer 读取）
    cache.set("__meta__:candidates", [
        {"code": c.code, "name": c.name, "concepts": c.concepts,
         "primary_sector": c.primary_sector,
         "primary_sector_key": c.primary_sector_key,
         "board_count": c.board_count}
        for c in ranking
    ])
    down_keys = [sector_key(s.source, s.category, s.code) for s in top_down]
    cache.set("__meta__:sector_codes", down_keys)

    # 板块名称映射（键为规范化 source:category:code）
    sector_name_map = {sector_key(s.source, s.category, s.code): s.name
                       for s in all_sectors}
    cache.set("__meta__:sector_name_map", sector_name_map)

    # 缓存键存在性自检：全部候选全部板块键都未命中 → 系统性键名失配（防静默降级）
    def _has_sector_data(c: Candidate) -> bool:
        key = c.primary_sector_key or c.primary_sector
        return bool(cache.get(components_key(key))
                    or cache.get(sector_1min_key(key))
                    or cache.get(sector_5min_key(key)))

    expected_keys = {k for k, comps in sector_components.items() if comps}
    sector_cache_miss = bool(expected_keys) and all(
        not _has_sector_data(c) for c in candidate_pool
        if c.primary_sector_key in expected_keys)
    if sector_cache_miss:
        logger.error("degraded:sector_cache",
                     "所有候选的板块缓存键均未命中，疑似键名失配（评分将整体降级）")

    # 用全候选池做板块内对比（连板/涨幅分位、封板池）。
    peer_pool = candidate_pool
    results = []
    for cand in ranking:
        try:
            sr = _score_one(cand, cache, peer_pool,
                            down_keys,
                            sector_name_map, logger)
            results.append(sr)
            if verbose:
                dims = sr.get("dimensions", {})
                print(f"  {cand.code} {cand.name:6s}  "
                      f"综合={sr['composite_score']:5.1f}  "
                      f"{'✓真龙' if sr.get('is_true_dragon') else '✗'}  "
                      f"带动={dims.get('drive',{}).get('score',0):5.1f}  "
                      f"领涨={dims.get('leadership',{}).get('score',0):5.1f}  "
                      f"抗跌={dims.get('anti_drop',{}).get('score',0):5.1f}  "
                      f"流动={dims.get('liquidity',{}).get('score',0):5.1f}  "
                      f"承接={dims.get('absorption',{}).get('score',0):5.1f}")
        except Exception as e:
            if verbose:
                print(f"  ⚠️ {cand.code} 打分失败: {e}", file=sys.stderr)

    # ────────────────────────────────────────────
    # Phase F: 输出与持久化
    # ────────────────────────────────────────────
    elapsed = time.time() - t_start
    logger.phase("F", "扫描完成", elapsed_s=round(elapsed, 1))

    output = {
        "timestamp": time.strftime("%Y%m%d_%H%M%S"),
        "source": source,
        "elapsed_s": round(elapsed, 1),
        "params": {"top_n": top_n, "candidates_n": candidates_n, "workers": workers, "scorers": source},
        "sectors": {
            "up": [{"code": s.code, "name": s.name, "pct": round(s.pct, 4),
                    "category": s.category, "source": s.source} for s in top_up],
            "down": [{"code": s.code, "name": s.name, "pct": round(s.pct, 4),
                      "category": s.category, "source": s.source} for s in top_down],
            "degraded": list(degraded),
            "sources": dict(sources_out),
            "collisions": [f"{c}:{code}" for c, code in collisions],
        },
        "sector_cache_miss": sector_cache_miss,
        "ranking": [],
        "api_stats": logger.api_stats(),
        "report_text": "",
        "log_count": 0,
        "report_path": "",
    }

    if results:
        results.sort(key=lambda r: (-r["composite_score"], r["code"]))
        verdicts = rank_verdicts([
            DragonVerdict(code=r["code"], is_true_dragon=r["is_true_dragon"],
                          composite=r["composite_score"])
            for r in results
        ])
        for result, verdict in zip(results, verdicts):
            result["rank"] = verdict.rank
        
        # 提前初始化 Reporter，并为所有结果生成报告，存入 r["report_text"] 以便持久化
        reporter = ReportBuilder(logger)
        for r in results:
            r["report_text"] = reporter.build_stock_report(
                r["code"], r.get("name", ""),
                r.get("board_count", 0), r.get("concepts", []),
                composite_score=r.get("composite_score", 0),
                dimensions=r.get("dimensions", {}),
                primary_sector_name=r.get("primary_sector_name", ""),
                is_true_dragon=r.get("is_true_dragon", False),
                reject_reason=r.get("reject_reason"),
            )

        output["ranking"] = results  # 返回全部评分结果

        # top_n 控制输出范围
        display_list = [r for r in results if r["is_true_dragon"]][:top_n]
        if verbose and not display_list:
            print("本轮无符合条件的真龙")

        report_parts = [r["report_text"] for r in display_list]
        output["report_text"] = "\n\n".join(report_parts) or "本轮无符合条件的真龙"

        if verbose:
            print(f"\n{'═'*56}")
            print(f"🐉 龙头战法扫描完成 ({elapsed:.0f}s)")
            print(f"{'═'*56}")
            print(f"\n{'代码':8s} {'名称':8s} {'综合':>6s}  {'带动':>6s}  "
                  f"{'领涨':>6s}  {'抗跌':>6s}  {'流动':>6s}  {'承接':>6s}  真龙")
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

            print(f"\n{'═'*56}")
            print("📋 完整详细报告")
            print(f"{'═'*56}")
            for i, r in enumerate(display_list):
                print(report_parts[i])
                print()

        # ── 持久化 ──
        timestamp = output["timestamp"]
        scan_id = f"{source}_{timestamp[:8]}_{top_n}"  # v2_20260519_25

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
        with open(report_path, "w", encoding="utf-8") as f:
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
                stocks=results,
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
            for r in display_list:
                code = r["code"]
                new_rank = r["rank"]

                # 5 日去重：该 code 上次入选距今 < 5 个交易日
                #   仅对「跨日」(last_date 严格早于当天) 重复入选去重；
                #   当前主流程固定使用 dragons_v2，兼容历史 v2 数据。
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
             force: bool = False, scorers: str = "v2",
             refresh_provider_cache: bool = False):
    """CLI 入口 — 同 scan() 但带 verbose 输出"""
    scan(top_n=top_n, candidates_n=candidates_n, workers=workers,
         verbose=True, force=force, scorers=scorers,
         refresh_provider_cache=refresh_provider_cache)
