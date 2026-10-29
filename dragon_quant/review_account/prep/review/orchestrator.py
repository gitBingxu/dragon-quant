"""复盘编排：把资产组合成 ``review`` / ``review_candidates``（纯函数）。

* 不触网、不读写库、不读环境变量 / 时钟；时间一律由入参 ``trade_date`` 传入；
* 内部逐候选 ``try/except``：单只字段异常只让该只 ``review_score=null``，
  绝不清空整个数组（技术方案 §9.3）；
* 组合顺序固定：市场 → 主线 → 角色 → 详细池 → 逐只评分 → 排序。

设计模式：

* **组合根（Composition Root）** —— 只有本模块可以组合市场 / 主线 / 角色 / 评分
  四个纯函数模块，其它模块之间禁止互相反向依赖；
* **策略（Strategy）** —— 角色权重由配置 profile 提供，评分算法与角色解耦。

M3 切片只产出评分与角色证据；闸门 / 仓位 / ``strategy_context`` 由 M4 接入。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .config import (
    CONFIG, CONFIG_VERSION, DIAGNOSTIC_PROFILE, ReviewConfig, config_hash,
)
from .context import build_context, prep_frozen_at
from .gates import ELIGIBILITY_UNKNOWN, evaluate_gates
from .mainline import compute_mainlines
from .market import compute_market_state
from .pool import derive_anchors, derive_pool_codes
from .roles import assign_roles
from .scoring import score_candidate
from .sizing import portfolio_summary, size_position

from dragon_quant.review_account.verdicts import find_risk_announcement

# 复盘分快照版本（config 之外的算法契约版本，便于回放时区分实现代次）。
REVIEW_VERSION = "review_v1"

_CST = timezone(timedelta(hours=8))
_LIANBAN_RE = re.compile(r"(\d+)天(\d+)板")

# 日 K 派生 D5/D6 所需的最少行数；不足即 unknown（不用短样本硬算）。
_MIN_DAILY_BARS = 20
_LIMIT_UP_PCT = 9.5


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    return None


def _row_height(row: Mapping[str, Any]) -> int | None:
    high_num = _num(row.get("high_num"))
    if high_num is not None and high_num > 0:
        return int(high_num)
    for key in ("high_days", "high"):
        match = _LIANBAN_RE.search(str(row.get(key) or ""))
        if match:
            return int(match.group(2))
    return None


def _hhmmss(timestamp: Any) -> str | None:
    """epoch 秒 → ``HH:MM:SS``（固定 +08:00，中国无夏令时）；非法返回 None。"""

    value = _num(timestamp)
    if value is None or value <= 0:
        return None
    try:
        moment = datetime.fromtimestamp(value, _CST)
    except (OverflowError, OSError, ValueError):
        return None
    if not (9 <= moment.hour <= 15):
        return None
    return moment.strftime("%H:%M:%S")


def _dedupe(values: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values if isinstance(values, list) else []:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


# ---------------------------------------------------------------------------
# 日 K → D5 / D6 指标
# ---------------------------------------------------------------------------


def _weighted_percentile(pairs: list[tuple[float, float]], q: float) -> float | None:
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        return None
    threshold = total * q
    cumulative = 0.0
    for price, weight in pairs:
        cumulative += weight
        if cumulative >= threshold:
            return price
    return pairs[-1][0] if pairs else None


def daily_metrics(rows: Any) -> dict[str, float | int | None]:
    """日 K 行 → D5/D6 指标（纯函数）。

    * D5（位置/筹码/成本）与前 20 日涨停次数需要至少 20 根日 K，否则 unknown；
    * D6 次日上涨率使用**至多 21 根**（最多 20 个「当日 → 次日」收益）；
      不足 20 个时按实际样本数，`next_day_up_sample` 始终保留真实值，
      样本 < 3 时由评分层把 S2 置 null（本函数不伪造成 20）。
    """

    bars: list[tuple[str, float, float, float, float]] = []
    for row in _list_of_dicts(rows):
        close = _num(row.get("close"))
        if close is None or close <= 0:
            continue
        high = _num(row.get("high"))
        low = _num(row.get("low"))
        volume = _num(row.get("volume"))
        bars.append((
            str(row.get("date") or ""),
            close,
            high if high is not None else close,
            low if low is not None else close,
            volume if volume is not None and volume >= 0 else 0.0,
        ))

    # D6：最多 21 根 → 最多 20 个相邻收益（严格 20 日口径）。
    window = bars[-21:]
    pairs = min(max(len(window) - 1, 0), 20)
    up_cnt = sum(
        1 for index in range(pairs) if window[index + 1][1] > window[index][1]
    )
    next_day_rate = round(up_cnt / pairs, 4) if pairs >= 1 else None

    if len(bars) < _MIN_DAILY_BARS:
        return {
            "distance_high60": None,
            "chip_concentration": None,
            "profit_ratio": None,
            "limit_up_cnt_20d": None,
            "next_day_up_rate_20d": next_day_rate,
            "next_day_up_sample": pairs,
        }

    closes = [bar[1] for bar in bars]
    last_close = closes[-1]
    high = max(bar[2] for bar in bars)
    average = sum(closes) / len(closes)

    triples = [((bar[2] + bar[3] + bar[1]) / 3.0, bar[4]) for bar in bars]
    if all(weight <= 0 for _, weight in triples):
        triples = [(price, 1.0) for price, _ in triples]
    triples.sort(key=lambda pair: pair[0])
    p10 = _weighted_percentile(triples, 0.10)
    p50 = _weighted_percentile(triples, 0.50)
    p90 = _weighted_percentile(triples, 0.90)

    limit_up_cnt = sum(
        1 for index in range(pairs)
        if (window[index][1] > 0
            and (window[index + 1][1] / window[index][1] - 1.0) * 100.0
            >= _LIMIT_UP_PCT)
    )

    return {
        "distance_high60": round((high - last_close) / high * 100.0, 2) if high > 0 else None,
        "chip_concentration": (
            round((p90 - p10) / p50, 4)
            if p10 is not None and p50 is not None and p90 is not None and p50 > 0
            else None
        ),
        "profit_ratio": round(last_close / average - 1.0, 4) if average > 0 else None,
        "limit_up_cnt_20d": limit_up_cnt,
        "next_day_up_rate_20d": next_day_rate,
        "next_day_up_sample": pairs,
    }


# ---------------------------------------------------------------------------
# 角色 / 候选输入
# ---------------------------------------------------------------------------


def _role_blocks(
    assets: Mapping[str, Any], mainlines: list[dict], config: ReviewConfig
) -> list[dict]:
    """把富化主线行还原成 ``assign_roles`` 可消费的板块行（保留成员名单）。"""

    raw_by_name: dict[str, dict] = {}
    for row in _list_of_dicts(_as_dict(assets.get("ths_block_top")).get("rows")):
        name = row.get("name") or row.get("board_name")
        if isinstance(name, str) and name:
            raw_by_name.setdefault(name, row)

    blocks: list[dict] = []
    for item in mainlines[: max(int(config.mainline_top_n), 0)]:
        name = item.get("board_name")
        if not isinstance(name, str) or not name:
            continue
        block = dict(raw_by_name.get(name, {}))
        block.update({
            "name": name,
            "board_name": name,
            "rank": item.get("rank"),
            "mainline_score": item.get("mainline_score"),
            "state": item.get("state"),
            "continuous_plate_num": item.get("continuous_plate_num"),
            "is_mainline": True,
        })
        blocks.append(block)
    return blocks


def _role_source_rows(
    pool_rows: list[dict], quotes: Mapping[str, Any]
) -> list[dict]:
    """涨停池原始行 → ``assign_roles`` 期望的归一化行（高度/首封/成交额/市值）。"""

    rows: list[dict] = []
    for row in pool_rows:
        code = row.get("code")
        if not isinstance(code, str) or not code:
            continue
        quote = _as_dict(quotes.get(code))
        normalized = dict(row)
        normalized["height"] = _row_height(row)
        normalized["first_seal_time"] = _hhmmss(row.get("first_limit_up_time"))
        normalized["amount"] = _num(quote.get("amount"))
        normalized["float_market_cap"] = _num(quote.get("float_market_cap"))
        rows.append(normalized)
    return rows


def _unknown_role(mainlines_available: bool) -> dict:
    """非涨停池标的（持仓 / 主线核心未涨停）的诊断角色记录。"""

    return {
        "code": None,
        "role": None,
        "role_confidence": "unknown",
        "role_basis": "非涨停池标的，不参与角色竞争",
        "role_competitors": [],
        "mainline": None,
        "also_in_boards": [],
        "weight_profile": DIAGNOSTIC_PROFILE,
        "unknown_reason": None if mainlines_available else "no_mainline_available",
        "intra_board_rank": None,
        "board_limit_up_num": None,
        "mainline_rank": 0 if mainlines_available else None,
        "mainline_days": None,
        "attr_match": 0 if mainlines_available else None,
        "evidence": {},
    }


def _seal_inputs(row: Mapping[str, Any], quote: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """封单额 / 封单占成交额比率。

    ``order_volume`` 的单位（手/股）尚未实测校准（技术方案 §10.5），按「未知不猜」
    原则本切片返回 ``None``：D3.S/R 保持 ``null``，绝不用可能差 100 倍的值参与评分。
    校准固化后再按 ``order_volume × limit_up_price × 100`` 接入。
    """

    return None, None


def _candidate_inputs(
    row: Mapping[str, Any],
    quote: Mapping[str, Any],
    role: Mapping[str, Any],
    metrics: Mapping[str, Any],
    mainlines_available: bool,
) -> dict:
    seal_amount, seal_ratio = _seal_inputs(row, quote)
    return {
        "height": _row_height(row),
        "intra_board_rank": role.get("intra_board_rank"),
        "board_limit_up_num": role.get("board_limit_up_num"),
        "mainline_rank": role.get("mainline_rank", 0 if mainlines_available else None),
        "attr_match": role.get("attr_match", 0 if mainlines_available else None),
        "mainline_days": role.get("mainline_days"),
        "first_seal_time": _hhmmss(row.get("first_limit_up_time")),
        "open_num": _num(row.get("open_num")),
        "seal_amount": seal_amount,
        "seal_ratio": seal_ratio,
        "float_market_cap": _num(quote.get("float_market_cap")),
        "main_net_amount": _num(quote.get("main_net_amount")),
        "amount": _num(quote.get("amount")),
        "turnover_rate": _num(quote.get("turnover_rate")),
        "distance_high60": metrics.get("distance_high60"),
        "chip_concentration": metrics.get("chip_concentration"),
        "profit_ratio": metrics.get("profit_ratio"),
        "limit_up_cnt_20d": metrics.get("limit_up_cnt_20d"),
        "next_day_up_rate_20d": metrics.get("next_day_up_rate_20d"),
        "next_day_up_sample": metrics.get("next_day_up_sample"),
    }


# ---------------------------------------------------------------------------
# 数据健康
# ---------------------------------------------------------------------------


def _coverage_health(
    payload: Mapping[str, Any], key: str, codes: list[str]
) -> dict:
    """payload 覆盖 ``codes`` 的健康状态（review 作用域；缺失即降级）。"""

    container = _as_dict(payload.get(key))
    missing = [code for code in codes if code not in container]
    if missing:
        return {"status": "degraded", "note": "missing_codes", "missing": missing}
    return {"status": "ok", "note": None, "missing": []}


def _review_degraded(
    assets: Mapping[str, Any], review_codes: list[str]
) -> list[str]:
    marks: list[str] = []
    specs = (
        ("fund_flow", "quotes"),
        ("cninfo_announcements", "items"),
        ("stock_daily", "rows"),
    )
    for asset_type, key in specs:
        payload = assets.get(asset_type)
        if not isinstance(payload, dict):
            marks.append(f"{asset_type}_missing")
            continue
        health = payload.get("review_health")
        if not isinstance(health, dict):
            health = _coverage_health(payload, key, review_codes)
        if health.get("status") not in (None, "ok"):
            marks.append(f"{asset_type}_review_degraded")
    return marks


def _candidate_legacy(assets: Mapping[str, Any]) -> dict[str, dict]:
    """只读证据：旧 candidates 的 code → verdict / composite_score（不参与评分）。"""

    mapping: dict[str, dict] = {}
    for item in _list_of_dicts(assets.get("candidates")):
        code = item.get("code")
        if isinstance(code, str) and code:
            mapping[code] = item
    return mapping


# ---------------------------------------------------------------------------
# 闸门 / 仓位输入
# ---------------------------------------------------------------------------


def _risk_payload(
    items: Mapping[str, Any], code: str, trade_date: str
) -> dict:
    """公告风险等级；公告缺失 = unknown（绝不当作无风险，E16）。"""

    item = items.get(code)
    available = isinstance(item, dict)
    announcements = item.get("announcements") if available else None
    if not available or not isinstance(announcements, list):
        # 资产缺失或 payload 畸形 → 风险未知；绝不当作「无风险」（E16）。
        return {"level": "unknown"}
    found = find_risk_announcement(announcements, trade_date, True)
    if not found:
        return {"level": "none"}
    return {
        "level": found.get("level"),
        "title": found.get("title"),
        "date": found.get("date"),
    }


def _is_suspended(row: Mapping[str, Any]) -> bool:
    if row.get("is_suspended") is True or row.get("suspended") is True:
        return True
    return row.get("status") == "suspended"


def _mainline_hit(entry: Mapping[str, Any]) -> bool:
    role = entry.get("role")
    return (
        isinstance(role, str)
        and bool(role)
        and isinstance(entry.get("mainline"), dict)
    )


def _apply_gates_and_sizing(
    entry: dict,
    *,
    pool_available: bool,
    amount: float | None,
    t_pct: float | None,
    suspended: bool,
    limit_up_type: Any,
    risk: dict,
    market: dict,
    trade_date: str,
    config: ReviewConfig,
) -> None:
    """就地写入闸门/仓位结果；硬风险与缺失语义由纯函数决定。"""

    gate = evaluate_gates(
        {
            "code": entry.get("code"),
            "role": entry.get("role"),
            "mainline_hit": _mainline_hit(entry),
            "review_score": entry.get("review_score"),
            "review_grade": entry.get("review_grade"),
            "confidence": entry.get("confidence"),
            "coverage": entry.get("coverage"),
            "pool_available": pool_available,
            "t_pct": t_pct,
            "amount": amount,
            "suspended": suspended,
            "limit_up_type": limit_up_type,
            "risk": risk,
        },
        {"trade_date": trade_date, "market": market},
        config,
    )
    sized = size_position({**entry, **gate}, market, config)
    entry["risk_level"] = gate["risk_level"]
    entry["eligibility"] = sized["eligibility"]
    entry["position_pct"] = sized["position_pct"]
    entry["position_basis"] = sized["position_basis"]
    entry["gate_trace"] = gate["gate_trace"]
    entry["reason_code"] = sized["reason_code"]
    entry["warnings"] = _dedupe(
        list(entry.get("warnings") or [])
        + list(gate.get("warnings") or [])
        + list(sized.get("warnings") or [])
    )


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def _prev_audit(prev: dict | None, prev_meta: Mapping[str, Any] | None) -> dict:
    """上一快照审计块（只增键）：状态 + 来源 + 降级原因。"""

    meta = prev_meta if isinstance(prev_meta, Mapping) else {}
    snapshot_date = meta.get("trade_date")
    snapshot_version = prev.get("version") if isinstance(prev, Mapping) else None
    if meta.get("error"):
        status = "corrupt"
    elif not isinstance(prev, Mapping):
        status = "snapshot_missing"
    else:
        review = prev.get("review")
        mainlines = review.get("mainlines") if isinstance(review, Mapping) else None
        status = "ok" if isinstance(mainlines, list) and mainlines else "missing"
    degraded: list[str] = []
    if status != "ok":
        degraded.append({
            "corrupt": "prev_snapshot_corrupt",
            "snapshot_missing": "prev_snapshot_missing",
            "missing": "prev_mainlines_missing",
        }.get(status, f"prev_{status}"))
    return {
        "snapshot_date": snapshot_date if isinstance(snapshot_date, str) else None,
        "snapshot_version": (snapshot_version
                             if isinstance(snapshot_version, str) else None),
        "mainlines": status,
        "degraded": degraded,
    }


def compute_review(
    assets: dict,
    trade_date: str,
    *,
    config: ReviewConfig = CONFIG,
    prev: dict | None = None,
    prev_meta: Mapping[str, Any] | None = None,
) -> dict:
    """资产 → ``review`` / ``review_candidates``（纯函数）。

    ``prev`` 为上一快照（可选）；缺失时市场环比与主线状态按 unknown 处理，
    绝不用当日数据自比。``compute_mainlines`` 返回空列表时必须映射为
    ``degraded=["mainline_unavailable"]``（M2 已知约束），不得解释为「真实无主线」。
    """

    source = assets if isinstance(assets, dict) else {}
    market = compute_market_state(source, prev, config)
    mainlines = compute_mainlines(source, prev, config, prev_meta=prev_meta)
    mainlines_available = bool(mainlines)

    pool = derive_pool_codes(source, config)
    pool_codes: list[str] = list(pool["codes"])
    anchors = derive_anchors(source)

    pool_rows = _list_of_dicts(_as_dict(source.get("ths_limit_up_pool")).get("rows"))
    pool_by_code = {
        row["code"]: row for row in pool_rows if isinstance(row.get("code"), str)
    }
    quotes = _as_dict(_as_dict(source.get("fund_flow")).get("quotes"))
    announcement_items = _as_dict(
        _as_dict(source.get("cninfo_announcements")).get("items"))
    daily_rows = _as_dict(_as_dict(source.get("stock_daily")).get("rows"))

    roles = assign_roles(
        _role_source_rows(pool_rows, quotes),
        _role_blocks(source, mainlines, config),
        config,
    )
    legacy_by_code = _candidate_legacy(source)

    degraded: list[str] = []
    if not mainlines_available:
        degraded.append("mainline_unavailable")
    if market.get("state") == "unknown":
        degraded.append("market_unknown")
    degraded.extend(_review_degraded(source, pool_codes))

    review_candidates: list[dict] = []
    for code in pool_codes:
        row = pool_by_code.get(code, {})
        quote = _as_dict(quotes.get(code))
        role = roles.get(code) or _unknown_role(mainlines_available)
        metrics = daily_metrics(daily_rows.get(code))
        legacy = legacy_by_code.get(code)
        name = row.get("name") or quote.get("name")

        entry: dict = {
            "code": code,
            "name": name,
            "trade_date": trade_date,
            "role": role.get("role"),
            "role_confidence": role.get("role_confidence"),
            "role_basis": role.get("role_basis"),
            "role_competitors": list(role.get("role_competitors") or []),
            "mainline": (
                {
                    "board_name": role["mainline"].get("board_name"),
                    "rank": role["mainline"].get("rank"),
                    "state": role["mainline"].get("state"),
                }
                if isinstance(role.get("mainline"), dict)
                else None
            ),
            "weight_profile": role.get("weight_profile") or DIAGNOSTIC_PROFILE,
            # T 日冻结的 T+1 预期基准（产品 §11.1）；T+1 只读，不重算。
            "expectations": (
                dict(config.expectations[role["role"]])
                if isinstance(role.get("role"), str)
                and role["role"] in config.expectations else None
            ),
            "warnings": [],
            "legacy": {
                "in_candidates": legacy is not None,
                "verdict": legacy.get("verdict") if legacy else None,
                "composite_score": legacy.get("composite_score") if legacy else None,
            },
        }
        if not quote:
            entry["warnings"].append("quote_missing")
        if not daily_rows.get(code):
            entry["warnings"].append("daily_missing")
        if not isinstance(announcement_items.get(code), dict):
            entry["warnings"].append("announcements_missing")

        try:
            inputs = _candidate_inputs(row, quote, role, metrics, mainlines_available)
            scored = score_candidate(
                inputs,
                {
                    "role": role.get("role"),
                    "limit_up_pool_available": bool(pool_rows),
                    "degraded": degraded,
                },
                config,
            )
            entry.update({
                "review_score": scored["review_score"],
                "review_grade": scored["review_grade"],
                "confidence": scored["confidence"],
                "coverage": scored["coverage"],
                "dimensions": scored["dimensions"],
                "contributions": scored["contributions"],
                "top_drivers": scored["top_drivers"],
                "top_drags": scored["top_drags"],
                "missing_dims": scored["missing_dims"],
                "reason_code": (
                    None if scored["review_score"] is not None else "data_insufficient"
                ),
            })
            t_pct = _num(quote.get("pct"))
            if t_pct is None:
                t_pct = _num(row.get("change_rate"))
            _apply_gates_and_sizing(
                entry,
                pool_available=bool(pool_rows),
                amount=_num(quote.get("amount")),
                t_pct=t_pct,
                suspended=_is_suspended(row),
                limit_up_type=row.get("limit_up_type"),
                risk=_risk_payload(announcement_items, code, trade_date),
                market=market,
                trade_date=trade_date,
                config=config,
            )
        except Exception as e:  # 单只异常隔离：不影响其余候选，也不中断 prep
            entry.update({
                "review_score": None,
                "review_grade": None,
                "confidence": 0.0,
                "coverage": 0.0,
                "dimensions": {},
                "contributions": [],
                "top_drivers": [],
                "top_drags": [],
                "missing_dims": [],
                "reason_code": "compute_error",
                "risk_level": "unknown",
                "eligibility": ELIGIBILITY_UNKNOWN,
                "position_pct": None,
                "position_basis": [],
                "gate_trace": [],
                "warnings": entry["warnings"] + [f"{type(e).__name__}: {e}"],
            })
        review_candidates.append(entry)

    review_candidates.sort(
        key=lambda item: (
            1 if item.get("review_score") is None else 0,
            -(item.get("review_score") or 0.0),
            str(item.get("code") or ""),
        )
    )
    portfolio = portfolio_summary(review_candidates, config)

    risk = {
        "limit_down_cnt": market.get("inputs", {}).get("limit_down_cnt"),
        "broken_rate": market.get("inputs", {}).get("broken_rate"),
        # T-2/T-3 高度需要历史快照，M3 尚无入参 → unknown（不猜）。
        "height_d3_change": None,
    }
    review_payload = {
        "enabled": True,
        "review_version": REVIEW_VERSION,
        "config_version": CONFIG_VERSION,
        "config_hash": config_hash(config),
        "market": market,
        "mainlines": mainlines,
        "anchors": {
            "positive": anchors["positive"],
            "negative": anchors["negative"],
            "risk": risk,
        },
        "pool": {
            "count": len(pool_codes),
            "truncated": pool["truncated"],
            "excluded": pool["excluded"],
        },
        "degraded": sorted(set(degraded)),
        "portfolio": portfolio,
        "review_candidates": review_candidates,
        # 上一快照审计（只增键）：无快照 / 损坏 / 无主线时给出稳定原因，
        # 避免 Agent 面对无说明的「主线未知」。
        "prev": _prev_audit(prev, prev_meta),
    }
    review_payload["strategy_context"] = build_context(
        {
            **review_payload,
            "trade_date": trade_date,
            "frozen_at": prep_frozen_at(trade_date),
        },
        config,
    )
    return review_payload


__all__ = ["REVIEW_VERSION", "compute_review", "daily_metrics"]
