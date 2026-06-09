"""
带动性 scorer（权重 35%）

核心问题：这只股票封板时，板块里其他股票跟不跟？
数据策略：在 Phase B 已获取的候选股范围内按板块分组比对封板时间。
"""

from datetime import datetime
from typing import Optional
from dragon_quant.models.types import ScoreResult, KBar, StockInfo, Candidate
from dragon_quant.cache.data_cache import DataCache


DRIVE_SAMPLE_LIMIT = 50


def score(code: str, cache: DataCache, candidate_pool: Optional[list[Candidate]] = None,
          primary_sector: str = "") -> ScoreResult:
    """
    Args:
        code: 股票代码
        cache: 共享数据缓存
        candidate_pool: 全部候选股列表（用于板块内封板比对）
        primary_sector: 候选股主板块代码
    Returns:
        ScoreResult(dim="drive", score, weight=0.35, details)
    """
    # 加载个股数据
    stock_klines: list[KBar] = cache.get(f"kline:day:{code}") or []
    stock_1min: list[KBar] = cache.get(f"kline:1min:{code}") or []
    components: list[StockInfo] = cache.get(f"sector:components:{primary_sector}") or []
    all_quotes = cache.get("quotes:batch") or []
    quote_map = {q.code: q for q in all_quotes} if all_quotes else {}

    if not stock_klines:
        return ScoreResult(
            dim="drive", score=30.0, weight=0.35,
            details={"fallback": True, "reason": "无日K线数据"}
        )

    # ─── Step 1: 找近 3 个涨停日 ───
    limit_up_dates = _find_limit_up_dates(stock_klines, stock_1min)

    if not limit_up_dates:
        return ScoreResult(
            dim="drive", score=30.0, weight=0.35,  # 兜底 30 分
            details={"limit_up_count": 0, "reason": "近30日无涨停"}
        )

    # ─── 构建同板块候选股池 ───
    peer_candidates = _build_peer_pool(code, primary_sector, candidate_pool, cache)

    # ─── Step 2: 每个涨停日独立打分 ───
    day_scores = []
    day_details = []
    for lu in limit_up_dates[:3]:  # 最多 3 天
        ds, dd = _score_limit_up_day(
            lu, primary_sector, components, quote_map, peer_candidates, cache
        )
        day_scores.append(ds)
        day_details.append(dd)

    # ─── Step 3: 取最佳天 ───
    if len(day_scores) == 1:
        best_idx = 0
    else:
        best_idx = max(range(len(day_scores)), key=lambda i: day_scores[i])

    drive_score = day_scores[best_idx]
    best_day_detail = day_details[best_idx]

    # 连板加分
    max_cons = max(lu["consecutive"] for lu in limit_up_dates)
    if max_cons >= 2:
        consecutive_bonus = min(max_cons * 5, 100 - drive_score)
        drive_score += consecutive_bonus
    else:
        consecutive_bonus = 0

    drive_score = min(drive_score, 100)

    return ScoreResult(
        dim="drive",
        score=round(drive_score, 2),
        weight=0.35,
        details={
            "limit_up_days": len(limit_up_dates[:3]),
            "best_day": limit_up_dates[best_idx]["date"],
            "best_day_detail": best_day_detail,
            "consecutive_bonus": consecutive_bonus,
            "max_consecutive": max_cons,
        }
    )


# ─── 涨停日识别 ───

def _find_limit_up_dates(day_klines: list[KBar], five_min_klines: list[KBar]) -> list[dict]:
    """从日K线倒推近 3 个涨停日，附封板时间和换手率"""
    limit_up_dates = []
    consecutive = 0

    for bar in reversed(day_klines):
        if bar.pct >= 9.9:
            consecutive += 1
            # 从 5 分 K 找封板时间
            board_time = _detect_board_time(five_min_klines, bar.timestamp)
            date_str = datetime.fromtimestamp(bar.timestamp / 1000).strftime("%Y-%m-%d")
            limit_up_dates.append({
                "date": date_str,
                "timestamp": bar.timestamp,
                "board_time": board_time,       # "09:45" or None
                "turnover": bar.turnover,
                "consecutive": consecutive,
                "pct": bar.pct,
            })
        else:
            consecutive = 0

    return limit_up_dates


def _detect_board_time(five_min: list[KBar], day_ts: int) -> Optional[str]:
    """从 5 分 K 线找封板时间（当天第一个接近涨停价的 bar）"""
    if not five_min:
        return None

    day_date = datetime.fromtimestamp(day_ts / 1000).date()
    day_bars = [b for b in five_min
                if datetime.fromtimestamp(b.timestamp / 1000).date() == day_date]

    if not day_bars:
        return None

    # 涨停价 ≈ 当天最高价（涨停日 high 就是涨停价）
    limit_up_price = max(b.close for b in day_bars)
    # 找到第一个 close 接近涨停价的 bar（0.1%误差容忍）
    for bar in day_bars:
        if limit_up_price > 0 and bar.close / limit_up_price >= 0.999:
            dt = datetime.fromtimestamp(bar.timestamp / 1000)
            return f"{dt.hour}:{dt.minute:02d}"

    # fallback: close >= 当天第一根 bar open * 1.099
    day_open = day_bars[0].open
    for bar in day_bars:
        if day_open > 0 and bar.close / day_open >= 1.099:
            dt = datetime.fromtimestamp(bar.timestamp / 1000)
            return f"{dt.hour}:{dt.minute:02d}"

    return None


# ─── 同板块候选股池 ───

def _build_peer_pool(code: str, primary_sector: str,
                     candidate_pool: Optional[list[Candidate]],
                     cache: DataCache) -> list[dict]:
    """构建同板块候选股封板信息列表（供比对封板时间）"""
    peers = []
    if not candidate_pool:
        # fallback: 从缓存推断
        return peers

    for cand in candidate_pool:
        if cand.code == code:
            continue
        if primary_sector not in cand.concepts and not _is_same_sector(cand, primary_sector, cache):
            continue

        peer_1min = cache.get(f"kline:1min:{cand.code}") or []
        peer_day = cache.get(f"kline:day:{cand.code}") or []
        peer_lu = _find_limit_up_dates(peer_day, peer_1min)

        # 取当天的封板时间
        latest_date = datetime.now().strftime("%Y-%m-%d")
        for lu in peer_lu:
            if lu["date"] == latest_date and lu["board_time"]:
                peers.append({
                    "code": cand.code,
                    "name": cand.name,
                    "board_time": lu["board_time"],
                    "board_timestamp": _time_to_minutes(lu["board_time"]),
                })
                break

    return peers


def _is_same_sector(cand: Candidate, sector_code: str, cache: DataCache) -> bool:
    """检查候选股是否属于指定板块"""
    components = cache.get(f"sector:components:{sector_code}") or []
    for comp in components:
        if comp.code == cand.code:
            return True
    return False


def _time_to_minutes(t: str) -> int:
    """"09:45" → 585 (分钟)"""
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return 9999  # 排序时放最后


# ─── 单涨停日打分 ───

def _score_limit_up_day(lu: dict, primary_sector: str,
                         components: list[StockInfo],
                         quote_map: dict,
                         peer_candidates: list[dict],
                         cache: DataCache) -> tuple[float, dict]:
    """
    Returns: (day_score, detail_dict)
    """
    # (a) 板块共鸣 Voice 30%
    voice_score, voice_raw = _voice_score(primary_sector, components, quote_map)

    # (b) 跟风力度 Follow 30%
    follow_score, follow_raw = _follow_score(primary_sector, components, quote_map)

    # (c) 封板决策力 Board Leadership 40%
    board_score, board_detail = _board_leadership_score(
        lu, peer_candidates, components, primary_sector
    )
    # 加入板块涨停总数
    board_detail["sector_limit_up_total"] = voice_raw.get("limit_up", 0)

    day_score = voice_score * 0.3 + follow_score * 0.3 + board_score * 0.4

    return day_score, {
        "voice": round(voice_score, 2),
        "voice_raw": voice_raw,
        "follow": round(follow_score, 2),
        "follow_raw": follow_raw,
        "board_leadership": round(board_score, 2),
        "board_detail": board_detail,
    }


# ─── 子因子 A: 板块共鸣 ───


def _resolve_component_pct(comp: StockInfo, quote_map: dict) -> float:
    pct_val = comp.pct
    if pct_val == 0.0 and comp.code in quote_map:
        pct_val = quote_map[comp.code].pct
    return pct_val


def _active_components(components: list[StockInfo], quote_map: dict,
                       limit: int = DRIVE_SAMPLE_LIMIT) -> list[StockInfo]:
    ranked = sorted(
        components,
        key=lambda comp: (_resolve_component_pct(comp, quote_map), comp.code),
        reverse=True,
    )
    return ranked[:limit]

def _voice_score(sector_code: str, components: list[StockInfo],
                 quote_map: dict) -> tuple[float, dict]:
    """同行业涨停家数占比 → (score, raw_counts)"""
    total = len(components)
    scoring_components = _active_components(components, quote_map)
    scoring_total = len(scoring_components)
    limit_up_codes = []
    for comp in scoring_components:
        pct_val = _resolve_component_pct(comp, quote_map)
        if pct_val >= 9.9:
            limit_up_codes.append(comp.code)

    limit_up_count = len(limit_up_codes)
    if scoring_total == 0:
        return 0.0, {"total": total, "scoring_total": 0, "sample_limit": DRIVE_SAMPLE_LIMIT, "limit_up": 0}

    ratio = limit_up_count / scoring_total
    score = min(ratio / 0.10, 1.0) * 100
    return score, {
        "total": total,
        "scoring_total": scoring_total,
        "sample_limit": DRIVE_SAMPLE_LIMIT,
        "limit_up": limit_up_count,
    }


# ─── 子因子 B: 跟风力度 ───

def _follow_score(sector_code: str, components: list[StockInfo],
                  quote_map: dict) -> tuple[float, dict]:
    """涨幅 >3% 但未涨停的占比 → (score, raw_counts)"""
    total = len(components)
    scoring_components = _active_components(components, quote_map)
    scoring_total = len(scoring_components)
    limit_up_codes = set()
    strong_count = 0
    down_count = 0

    for comp in scoring_components:
        pct_val = _resolve_component_pct(comp, quote_map)
        if pct_val >= 9.9:
            limit_up_codes.add(comp.code)
        elif pct_val > 3.0:
            strong_count += 1
        if pct_val < 0:
            down_count += 1

    non_limit_total = scoring_total - len(limit_up_codes)
    if non_limit_total == 0:
        score = 100.0 if scoring_total > 0 else 0.0
        return score, {
            "total": total,
            "scoring_total": scoring_total,
            "sample_limit": DRIVE_SAMPLE_LIMIT,
            "strong": strong_count,
            "down": down_count,
            "limit_up": len(limit_up_codes),
        }

    ratio = strong_count / non_limit_total
    score = min(ratio / 0.15, 1.0) * 100
    return score, {
        "total": total,
        "scoring_total": scoring_total,
        "sample_limit": DRIVE_SAMPLE_LIMIT,
        "strong": strong_count,
        "down": down_count,
        "limit_up": len(limit_up_codes),
    }


# ─── 子因子 C: 封板决策力 ───

def _board_leadership_score(lu: dict, peers: list[dict],
                            components: list[StockInfo],
                            sector_code: str) -> tuple[float, dict]:
    """
    四子因子: C1(排名25%) + C2(绝对时间25%) + C3(紧密度50%)
    × 一字板惩罚
    """
    # C1: 封板排名
    rank_score, rank = _seal_rank_score(lu, peers)

    # C2: 绝对时间
    early_score = _early_time_score(lu.get("board_time"))

    # C3: 小弟紧密度
    gap_score, gap_detail = _gap_score(lu, peers)

    board_score = rank_score * 0.25 + early_score * 0.25 + gap_score * 0.50

    # 一字板惩罚
    is_yizi = _is_yiziban(lu)
    penalty = 0.85 if is_yizi else 1.0
    board_score *= penalty

    return board_score, {
        "rank_score": round(rank_score, 2),
        "seal_rank": rank,
        "early_score": round(early_score, 2),
        "board_time": lu.get("board_time"),
        "gap_score": round(gap_score, 2),
        "gap_detail": gap_detail,
        "is_yiziban": is_yizi,
        "penalty": penalty,
    }


def _seal_rank_score(lu: dict, peers: list[dict]) -> tuple[float, int]:
    """C1: 在同板块涨停候选股中按封板时间排名"""
    if not lu.get("board_time"):
        return 50.0, len(peers) + 1  # 一字板或无封板信号，排最后

    my_minutes = _time_to_minutes(lu["board_time"])
    all_times = [(p["code"], p["board_timestamp"]) for p in peers]

    # 只有封板时间的参与排名
    ranked = sorted([t for _, t in all_times if t < 9999] + [my_minutes])

    total = len(ranked)
    if total == 0:
        rank = 1
    else:
        rank = ranked.index(my_minutes) + 1

    return (1 - (rank - 1) / total) * 100, rank


def _early_time_score(board_time: Optional[str]) -> float:
    """C2: 离散阶梯封板时间分"""
    if not board_time:
        return 1.0  # 一字板或无时间

    h = int(board_time.split(":")[0])
    m = int(board_time.split(":")[1])
    minutes = h * 60 + m

    if minutes <= 570:   # ≤9:30
        return 100.0
    elif minutes <= 630:  # ≤10:30
        return 70.0
    elif minutes <= 690:  # ≤11:30
        return 40.0
    else:
        return 10.0         # 午后


def _gap_score(lu: dict, peers: list[dict]) -> tuple[float, dict]:
    """C3: 小弟紧密度 — 其他涨停股与候选股封板时间差"""
    if not lu.get("board_time"):
        return 50.0, {"avg_gap_min": None, "within_5min_pct": 0, "peer_count": 0}

    my_minutes = _time_to_minutes(lu["board_time"])
    gaps = []

    for p in peers:
        if p["board_timestamp"] < 9999:  # 有封板时间的
            gaps.append(abs(p["board_timestamp"] - my_minutes))

    if not gaps:
        # 独苗，无小弟
        return 50.0, {"avg_gap_min": None, "within_5min_pct": 0, "peer_count": 0, "solo": True}

    avg_gap = sum(gaps) / len(gaps)
    within_5 = sum(1 for g in gaps if g <= 5) / len(gaps)

    if avg_gap <= 5 and within_5 > 0.5:
        score = 100.0
    else:
        score = max(0.0, 100 - avg_gap / 30 * 100)

    return score, {
        "avg_gap_min": round(avg_gap, 1),
        "within_5min_pct": round(within_5, 2),
        "peer_count": len(gaps),
    }


def _is_yiziban(lu: dict) -> bool:
    """一字板判定：无封板时间(board_time=None) 且 换手率 < 1%"""
    if lu.get("board_time") is not None:
        return False
    return lu.get("turnover", 0) < 1.0
