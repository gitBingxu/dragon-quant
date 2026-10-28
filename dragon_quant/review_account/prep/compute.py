"""prep compute 段：情绪 / 主线 / 候选池 / 宏观的纯计算（只读 assets，无 I/O）。

快照 = 资产的纯函数：``service._compute_all`` 先按 collection_report / as_of 过滤
不可用资产，再逐因子隔离调用本模块。任何单个资产畸形只让对应因子为 null，
绝不中断整个复盘（``factor_errors`` / ``degraded`` 留痕）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from dragon_quant.infra.provider.macro import (
    CALIBER_PREVIOUS, CALIBER_SAME_DAY, MACRO_INDEX_KEYS, MACRO_KEYS,
    SPEC_BY_KEY,
)
from dragon_quant.review_account.verdicts import find_risk_announcement

# 复盘分编排是纯函数子包；compute 只做单向 re-export（service → compute → review，
# 显式别名表示「故意导出」，供 service / main 消费）。
from .review import (  # noqa: F401
    REVIEW_VERSION as REVIEW_VERSION,
    compute_feedback as compute_feedback,
    compute_plan_review as compute_plan_review,
    compute_review as compute_review,
)

# 快照/窗口/交易日统一时区（service / rebuild 也复用本常量，避免多处定义漂移）
SHANGHAI = ZoneInfo("Asia/Shanghai")


# ─── v0.1 情绪 → 建议仓位启发式（阈值不变，输入口径升级为 T 日权威值）─────────

def position_advice(limit_up_cnt: int, limit_down_cnt: int) -> tuple:
    """情绪 → 建议仓位启发式（阈值集中于此，后续回测调参）。"""
    if limit_up_cnt >= 80 and limit_down_cnt <= 5:
        return "重仓", 0.8
    if limit_down_cnt >= 30:
        return "空仓", 0.0
    if limit_down_cnt >= 10 or limit_up_cnt <= 15:
        return "轻仓", 0.3
    return "正常", 0.5


# ─── 计算层（纯函数，只读 assets，无 I/O）─────────────────────────────────────

_LIANBAN_RE = re.compile(r"(\d+)天(\d+)板")


def _lianban_count(high_days) -> int:
    """high_days「N天M板」→ 连板数（N==M 且 M≥2 视为连板；否则 0）。

    字段语义待交易日实测核对；若 pool 无数值连板字段，compute 退化为
    again_limit_rate（见 compute_sentiment 注释）。
    """
    if not high_days:
        return 0
    m = _LIANBAN_RE.search(str(high_days))
    if not m:
        return 0
    days, boards = int(m.group(1)), int(m.group(2))
    return boards if days == boards and boards >= 2 else 0


def _board_height(row: dict) -> Optional[int]:
    """block_top 行 → 最高板高度：优先数值 high_num，缺失解析 high「N天M板」取 M。"""
    hn = row.get("high_num")
    try:
        if hn is not None and int(hn) > 0:
            return int(hn)
    except (TypeError, ValueError):
        pass
    m = _LIANBAN_RE.search(str(row.get("high") or ""))
    return int(m.group(2)) if m else None


def _hhmmss(ts) -> Optional[str]:
    """epoch 秒 → "HH:MM:SS"（Asia/Shanghai）；异常返回 None。"""
    try:
        return datetime.fromtimestamp(int(ts), SHANGHAI).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return None


def _list_of_dicts(value) -> list:
    """类型收敛：仅保留 list 中的 dict 元素（畸形 payload 不抛异常）。"""
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _num_or_none(value) -> Optional[float]:
    """float 归一；NaN/inf/异常 → None。"""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def _fg_board_rows(fg: dict, name: str) -> tuple[list, bool]:
    """取 FG 单板块成员；返回 (rows, 是否可用)。

    板块名不存在 / status=="missing" 时 rows=[]，但**不可用**——调用方必须把
    该维度当作未知（None），不能当成「0 家」。否则在 THS 缺失 + 板块改名/列表
    不全的双重故障下会产出错误的仓位建议。
    """
    board = fg.get(name)
    if not isinstance(board, dict) or board.get("status") == "missing":
        return [], False
    return _list_of_dicts(board.get("rows")), True


def compute_sentiment(assets: dict) -> dict:
    """情绪温度 v0.2：THS T 日权威 + TDX 宽度 + FG T-1 交叉，双口径并列留痕。

    每个来源分组独立容错：单个资产畸形只让对应因子为 null 并记入 factor_errors，
    不影响其余因子（与 compute 段逐因子隔离共同保证「单点失败不中断」）。
    调用方应已用 ``_prepare_assets`` 过滤不可用资产。
    """
    local_errors: dict[str, str] = {}

    def _safe(name: str, fn, default=None):
        try:
            return fn()
        except Exception as e:
            local_errors[name] = f"{type(e).__name__}: {e}"
            return default

    def _dict(name: str) -> dict:
        v = assets.get(name)
        return v if isinstance(v, dict) else {}

    pool = _dict("ths_limit_up_pool")
    breadth = _dict("market_breadth")
    fg = _dict("fg_boards").get("boards")
    fg = fg if isinstance(fg, dict) else {}
    block_top = _list_of_dicts(_dict("ths_block_top").get("rows"))
    pool_rows = _list_of_dicts(pool.get("rows"))

    degraded: list[str] = []
    factor_sources: dict[str, str] = {}

    # T 日涨停家数（THS 权威）；缺失 → null（不拿 FG T-1 冒充 T）
    limit_up_ths = pool.get("total") if pool else None
    if limit_up_ths is None:
        degraded.append("ths_limit_up_pool")
    else:
        factor_sources["limit_up_cnt_ths"] = "ths_limit_up_pool"

    # FG T-1 交叉口径（不进公式，除非 THS 缺失且显式标记）
    fg_up_rows, fg_up_ok = _fg_board_rows(fg, "昨日涨停")
    fg_down_rows, fg_down_ok = _fg_board_rows(fg, "昨日跌停")
    lhb_rows, lhb_ok = _fg_board_rows(fg, "昨日上榜")
    limit_up_t1 = len(fg_up_rows) if fg_up_ok else None
    limit_down_t1 = len(fg_down_rows) if fg_down_ok else None
    if fg and not (fg_up_ok and fg_down_ok):
        degraded.append("fg_boards_partial")   # 单板块缺失：对应维度未知而非 0

    # 市场宽度（up/down/成交额/跌停/炸板）
    up_cnt = breadth.get("up_cnt")
    down_cnt = breadth.get("down_cnt")
    total_amount = breadth.get("total_amount")
    # 资产缺失（宽度未采集）≠ 字段畸形：只在键存在时取值，不制造 factor_errors
    limit_down_cnt = (len(breadth["limit_down"])
                      if isinstance(breadth.get("limit_down"), list) else None)
    broken_cnt = (len(breadth["broken"])
                  if isinstance(breadth.get("broken"), list) else None)
    if not breadth:
        degraded.append("market_breadth")
    if broken_cnt is None:
        def _broken_from_pool():
            if not pool_rows:            # 无涨停池数据时不伪造 0，保持未知
                return None
            return sum(1 for r in pool_rows if (r.get("open_num") or 0) > 0)

        broken_cnt = _safe("broken_from_open_num", _broken_from_pool)
        if broken_cnt is not None:
            factor_sources["broken_cnt"] = "ths_limit_up_pool(open_num>0)"

    # 昨日涨停溢价：FG 昨日涨停成员当日 (close/pre_close-1) 均值 %
    def _premium():
        rets = [(r["close"] / r["pre_close"] - 1) * 100 for r in fg_up_rows
                if (r.get("pre_close") or 0) > 0 and (r.get("close") or 0) > 0]
        return round(sum(rets) / len(rets), 2) if rets else None

    premium = _safe("yesterday_premium", _premium)

    # 晋级率：今日连板家数(high_days 判定) / 昨日涨停家数(FG T-1 即「昨日」)
    def _promotion():
        if not pool_rows or not limit_up_t1:
            return None
        lianban = sum(1 for r in pool_rows if _lianban_count(r.get("high_days")) >= 2)
        return round(lianban / limit_up_t1, 3)

    promotion = _safe("promotion_rate", _promotion)
    if promotion is not None:
        factor_sources["promotion_rate"] = "ths_limit_up_pool(high_days) / fg(T-1)"

    # 最高板：block_top high_num（缺失解析 high 文本）
    max_height = _safe("max_board_height",
                       lambda: max((h for h in (_board_height(r) for r in block_top) if h),
                                   default=None))

    # 龙虎榜名单数：FG 昨日上榜
    lhb_cnt = len(lhb_rows) if lhb_ok else None

    # 仓位公式输入：THS(T) 优先；缺失显式回退 FG(T-1) 并标记 degraded
    pos_up, pos_up_src = ((limit_up_ths, "ths_t") if limit_up_ths is not None
                          else (limit_up_t1, "fg_t1"))
    pos_down, pos_down_src = ((limit_down_cnt, "breadth_t") if limit_down_cnt is not None
                              else (limit_down_t1, "fg_t1"))
    if (pos_up is not None and pos_up_src == "fg_t1") or \
       (pos_down is not None and pos_down_src == "fg_t1"):
        degraded.append("position_input_fg_t1")

    if pos_up is not None and pos_down is not None:
        advice, pct = position_advice(pos_up, pos_down)
        total = pos_up + pos_down
        score = round(pos_up / total * 100, 1) if total else None
    else:
        advice, pct, score = None, None, None

    limit_up_cnt = limit_up_ths  # 对外主口径：T 日精确（缺失为 null）
    out = {
        "up_cnt": up_cnt,
        "down_cnt": down_cnt,
        "limit_up_cnt": limit_up_cnt,
        "limit_down_cnt": limit_down_cnt,
        "total_amount": total_amount,
        "temperature_score": score,
        "position_advice": advice,
        "position_pct": pct,
        "limit_up_cnt_ths": limit_up_ths,
        "limit_down_cnt_ths": None,  # THS pool 不含跌停（占位）
        "limit_up_cnt_t1_fg": limit_up_t1,
        "limit_down_cnt_t1_fg": limit_down_t1,
        "position_input": {"limit_up_cnt": pos_up_src if pos_up is not None else None,
                           "limit_down_cnt": pos_down_src if pos_down is not None else None},
        "broken_cnt": broken_cnt,
        "broken_rate": (round(broken_cnt / (broken_cnt + limit_up_cnt), 3)
                        if broken_cnt is not None and limit_up_cnt else None),
        "yesterday_premium_pct": premium,
        "promotion_rate": promotion,
        "max_board_height": max_height,
        "lhb_cnt": lhb_cnt,
        "factor_sources": factor_sources,
        "degraded": sorted(set(degraded)),
    }
    if local_errors:
        out["factor_errors"] = local_errors
    return out


def compute_main_boards(assets: dict) -> list:
    """题材主线：block_top 主路径（题材+涨停家数+最高板+真龙命中数）；
    缺失回退 v0.1 真龙 concepts 计数（元素仅 board_name/dragon_count，消费方须 .get()）。"""
    bt_payload = assets.get("ths_block_top")
    bt_payload = bt_payload if isinstance(bt_payload, dict) else {}
    bt_raw = bt_payload.get("rows")
    bt_rows = [r for r in bt_raw if isinstance(r, dict)] if isinstance(bt_raw, list) else []
    dragon_payload = assets.get("dragons")
    dragon_payload = dragon_payload if isinstance(dragon_payload, dict) else {}
    dragon_raw = dragon_payload.get("rows")
    dragon_rows = [d for d in dragon_raw if isinstance(d, dict)] if isinstance(dragon_raw, list) else []
    dragon_codes = {d.get("code") for d in dragon_rows}
    if bt_rows:
        boards = [{
            "board_name": r.get("name"),
            "limit_up_num": r.get("limit_up_num"),
            "high": r.get("high"),
            "dragon_count": sum(1 for s in (r.get("stock_list") or [])
                                if isinstance(s, dict) and s.get("code") in dragon_codes),
        } for r in bt_rows]
        boards.sort(key=lambda b: (-(b.get("limit_up_num") or 0),
                                   str(b.get("board_name") or "")))
        return boards[:5]
    # 并集口径：同一 code 跨日重复入选只计一次（保持首次出现顺序）
    counter: dict[str, int] = {}
    seen_codes: set[str] = set()
    for d in dragon_rows:
        code = d.get("code")
        if code:
            if code in seen_codes:
                continue
            seen_codes.add(code)
        concepts = d.get("concepts")
        for c in (concepts if isinstance(concepts, list) else []):
            counter[c] = counter.get(c, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"board_name": name, "dragon_count": cnt} for name, cnt in ranked[:3]]


def _candidate_preference(candidate: dict) -> tuple:
    """去重偏好键：与 ``market.candidate_key`` 对齐（-综合分, rank）。"""
    score = candidate.get("composite_score")
    rank = candidate.get("rank")
    return (-(score if isinstance(score, (int, float)) else 0),
            rank if isinstance(rank, int) else 999999)


def _prefer_candidate(new: dict, old: dict) -> bool:
    """新候选是否更优：偏好键更小；完全相同则入选日更新者胜。"""
    key_new, key_old = _candidate_preference(new), _candidate_preference(old)
    if key_new != key_old:
        return key_new < key_old
    return str(new.get("trade_date") or "") > str(old.get("trade_date") or "")


def compute_candidates(assets: dict, trade_date: str) -> list:
    """候选池：窗口内真龙并集（同 code 保留更优）+ 同日 THS 归因 + T 日资金/公告。

    - 每只候选保留**行内真实入选日**（旧资产无行内日期时回退 ``trade_date``）；
    - 归因字段仅在候选入选日与 THS 池日期一致时填充，跨日候选为 None（未知不伪造）；
    - ``main_net_amount`` / ``announcements`` 是 T 日时点信息，对所有候选附加。

    调用方应已用 ``_prepare_assets`` 过滤不可用资产——例如 fund_flow 处于
    empty_suspicious 时该资产被剔除，main_net_amount 自然为 None（而非误导性 0）。
    """
    dragon_payload = assets.get("dragons")
    dragon_payload = dragon_payload if isinstance(dragon_payload, dict) else {}
    dragon_raw = dragon_payload.get("rows")
    dragon_rows = [d for d in dragon_raw if isinstance(d, dict)] if isinstance(dragon_raw, list) else []

    pool_payload = assets.get("ths_limit_up_pool")
    pool_payload = pool_payload if isinstance(pool_payload, dict) else {}
    pool_raw = pool_payload.get("rows")
    pool_rows = [r for r in pool_raw if isinstance(r, dict)] if isinstance(pool_raw, list) else []
    pool_by_code = {r.get("code"): r for r in pool_rows}
    pool_day = pool_payload.get("trade_date")

    flow = (assets.get("fund_flow") or {}).get("quotes")
    flow = flow if isinstance(flow, dict) else {}
    anns = (assets.get("cninfo_announcements") or {}).get("items")
    anns = anns if isinstance(anns, dict) else {}

    merged: dict[str, dict] = {}
    for d in dragon_rows:
        code = d.get("code")
        if not code:
            continue
        cand = dict(d)
        cand["trade_date"] = d.get("trade_date") or dragon_payload.get("trade_date") or trade_date
        previous = merged.get(code)
        if previous is None or _prefer_candidate(cand, previous):
            merged[code] = cand

    pool_available = bool(pool_rows)
    out = []
    for cand in merged.values():
        code = cand.get("code")
        p = pool_by_code.get(code) if pool_available else None
        # 入选日归因（既有契约）：仅候选入选日 == 今日池日期时填充，跨日为 null
        same_day = pool_available and pool_day is not None and cand.get("trade_date") == pool_day
        p_same_day = p if same_day else None
        cand["reason_type"] = p_same_day.get("reason_type") if p_same_day else None
        cand["limit_up_type"] = p_same_day.get("limit_up_type") if p_same_day else None
        cand["open_num"] = p_same_day.get("open_num") if p_same_day else None
        cand["is_again_limit"] = bool(p_same_day.get("is_again_limit")) if p_same_day else None
        cand["first_limit_up_time"] = (_hhmmss(p_same_day.get("first_limit_up_time"))
                                       if p_same_day else None)

        q = flow.get(code)
        q = q if isinstance(q, dict) else None
        item = anns.get(code)
        raw_anns = item.get("announcements") if isinstance(item, dict) else None
        announcements = raw_anns if isinstance(raw_anns, list) else []
        cand["main_net_amount"] = q.get("main_net_amount") if q else None
        cand["announcements"] = announcements

        # facts：今日事实一次算清（in_pool_today 与入选日解耦）
        t_pct = _num_or_none(p.get("change_rate")) if p else None
        if t_pct is None and q is not None:
            t_pct = _num_or_none(q.get("pct"))
        t_turnover = _num_or_none(p.get("turnover_rate")) if p else None
        if t_turnover is None and q is not None:
            t_turnover = _num_or_none(q.get("turnover_rate"))
        announcements_available = isinstance(item, dict)
        risk = find_risk_announcement(announcements, trade_date,
                                      announcements_available)
        cand["facts"] = {
            "pool_available": pool_available,
            "in_pool_today": p is not None,
            "t_pct": t_pct,
            "t_turnover": t_turnover,
            "t_amount": _num_or_none(q.get("amount")) if q else None,
            "open_num": p.get("open_num") if p else None,
            "high_days": p.get("high_days") if p else None,
            "reason_type_today": p.get("reason_type") if p else None,
            "limit_up_type": p.get("limit_up_type") if p else None,
            "first_limit_up_time": (_hhmmss(p.get("first_limit_up_time"))
                                    if p else None),
            "limit_up_suc_rate": (_num_or_none(p.get("limit_up_suc_rate"))
                                  if p else None),
            "main_net_amount": _num_or_none(q.get("main_net_amount")) if q else None,
            "flow_available": q is not None,
            "announcements_available": announcements_available,
            "announcement_count": len(announcements),
            "risk_announcement": risk,
        }
        out.append(cand)
    return out


def _dragon_source(assets: dict) -> dict:
    """真龙来源血缘：多日窗口输出逐日 dates；旧资产补 window_days=1 以保持口径可判定。"""
    d = assets.get("dragons")
    d = d if isinstance(d, dict) else {}
    if not d:
        # dragons 资产整体不可用：与「legacy 单日资产」区分（window_days=0）
        return {"trade_date": None, "scan_id": None, "version": None, "count": None,
                "window_days": 0, "dates": [], "version_mixed": False}
    days = _list_of_dicts(d.get("days"))
    if days:
        dates = [{"trade_date": day.get("trade_date"), "scan_id": day.get("scan_id"),
                  "version": day.get("version"), "count": day.get("count"),
                  "status": day.get("status")} for day in days]
        window_days = d.get("window_days")
        window_days = window_days if isinstance(window_days, int) else len(days)
    else:
        count = d.get("count") or 0
        dates = [{"trade_date": d.get("trade_date"), "scan_id": d.get("scan_id"),
                  "version": d.get("version"), "count": count,
                  "status": "ok" if count else "empty"}]
        window_days = 1
    versions = {item.get("version") for item in dates if item.get("version")}
    return {
        "trade_date": d.get("trade_date"),
        "scan_id": d.get("scan_id"),
        "version": d.get("version"),
        "count": d.get("count"),
        "window_days": window_days,
        "dates": dates,
        "version_mixed": len(versions) > 1,
    }


def _empty_macro() -> dict:
    return {
        "available": False,
        "as_of": None,
        "session": None,
        "instruments": {},
        "degraded": ["macro_global"],
        "sources": {},
    }


def _macro_stale(session_date, trade_date: str) -> bool:
    try:
        lag = (datetime.fromisoformat(trade_date).date()
               - datetime.fromisoformat(str(session_date)[:10]).date()).days
        return lag > 4
    except (TypeError, ValueError):
        return False


def _macro_session_kind(session_date, trade_date: str) -> Optional[str]:
    """逐品种收盘口径：同日 / 上一交易日 / 不可用。"""
    if not isinstance(session_date, str) or len(session_date) < 10:
        return None
    day = session_date[:10]
    if day == trade_date:
        return "same_day"
    if day < trade_date:
        return "previous_session"
    return None


def _macro_session(instruments: dict) -> Optional[str]:
    """外围指数组（日经/A50）的收盘口径聚合：same_day / previous_session / mixed。"""
    kinds = []
    for key in MACRO_INDEX_KEYS:
        rec = instruments.get(key)
        if not isinstance(rec, dict):
            continue
        kind = rec.get("session_kind")
        if isinstance(kind, str) and kind:
            kinds.append(kind)
    if not kinds:
        return None
    if len(set(kinds)) > 1:
        return "mixed"
    return kinds[0]


def _normalize_macro_record(rec: object, key: str, trade_date: str) -> Optional[dict]:
    spec = SPEC_BY_KEY.get(key)
    if spec is None or not isinstance(rec, dict):
        return None
    price = _num_or_none(rec.get("price"))
    if price is None or price <= 0:
        return None
    session_date = rec.get("session_date")
    session_date = session_date[:10] if isinstance(session_date, str) else None
    # 缺失/未来日期视为不可用，不伪装成「上一交易日」
    session_kind = _macro_session_kind(session_date, trade_date)
    if session_kind is None:
        return None
    return {
        "key": key,
        "label": str(rec.get("label") or spec.label),
        "kind": str(rec.get("kind") or spec.kind),
        "price_unit": str(rec.get("price_unit") or spec.price_unit),
        "chg_unit": str(rec.get("chg_unit") or spec.chg_unit),
        "caliber": (CALIBER_SAME_DAY if spec.same_day_close
                    else CALIBER_PREVIOUS),
        "price": price,
        "prev_close": _num_or_none(rec.get("prev_close")),
        "chg": _num_or_none(rec.get("chg")),
        "pct": _num_or_none(rec.get("pct")),
        "chg_bp": _num_or_none(rec.get("chg_bp")),
        "chg_bp_approx": (rec.get("chg_bp_approx")
                          if isinstance(rec.get("chg_bp_approx"), bool) else None),
        "precision_note": (str(rec.get("precision_note"))
                           if rec.get("precision_note") else None),
        "source": str(rec.get("source") or ""),
        "code": str(rec.get("code") or ""),
        "market": str(rec.get("market") or ""),
        "session_date": session_date,
        "session_kind": session_kind,
        "raw_date": rec.get("raw_date"),
        "raw_time": rec.get("raw_time"),
        "stale": _macro_stale(session_date, trade_date),
    }


def compute_macro(assets: dict, trade_date: str) -> dict:
    """宏观资产 → 快照归一结果（纯函数）；缺失/畸形 → available=False。"""
    payload = assets.get("macro_global")
    if not isinstance(payload, dict):
        return _empty_macro()
    raw = payload.get("instruments")
    if not isinstance(raw, dict):
        return _empty_macro()
    instruments = {}
    for key in MACRO_KEYS:
        rec = _normalize_macro_record(raw.get(key), key, trade_date)
        if rec is not None:
            instruments[key] = rec
    if not instruments:
        return _empty_macro()

    raw_degraded = payload.get("degraded")
    # 旧资产可能残留 us_* 等已下线 key：只保留当前 MACRO_KEYS，避免旧键泄漏到 CLI/JSON
    degraded = {str(item) for item in raw_degraded
                if isinstance(item, str) and item in MACRO_KEYS} \
        if isinstance(raw_degraded, list) else set()
    degraded |= {key for key in MACRO_KEYS if key not in instruments}
    raw_sources = payload.get("sources")
    sources = ({key: value for key, value in raw_sources.items()
                if key in MACRO_KEYS}
               if isinstance(raw_sources, dict) else {})
    return {
        "available": True,
        "as_of": payload.get("as_of"),
        "session": _macro_session(instruments),
        "instruments": instruments,
        "degraded": sorted(degraded),
        "sources": sources,
    }
