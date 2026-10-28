"""详细池代码解析（**唯一实现**，纯函数叶子）。

采集段（``collectors._collect_stock_daily`` / 资金、公告超集）与计算段
（``orchestrator.compute_review``）共用本模块，禁止在采集器里另写一份池规则——
两处漂移会让「采到的代码」与「评分的代码」不一致，产生静默缺维（技术方案 §7）。

代码来源（优先级即截断顺序）：

1. ``dragons`` 窗口真龙并集；
2. ``ths_limit_up_pool`` 涨停池；
3. ``ths_block_top`` 前 ``mainline_top_n`` 个主线的成分股；
4. 正锚（市场最高板）与负锚（昨日涨停今日跌幅前三 / 高标炸板）；
5. ``holdings`` 本地持仓代码。

再排除 ST / 上市不足 20 日 / 北交所 / 停牌，超过 ``review_max_codes`` 时按上述
优先级截断并标 ``truncated``。

本模块无 I/O：不读文件 / 数据库 / 环境变量 / 时钟，不发请求；输入只有普通 dict。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .board_rank import rank_boards
from .config import CONFIG, ReviewConfig

_LIANBAN_RE = re.compile(r"(\d+)天(\d+)板")

# 上市不足该天数的股票不进入详细池（次新波动结构不可比）。
_MIN_LISTING_DAYS = 20
# 负锚取跌幅最深的前 N 只（昨日涨停今日回落）。
_NEGATIVE_ANCHOR_LIMIT = 3


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


def _dedupe_codes(values: Iterable[Any]) -> list[str]:
    """按首次出现去重，保持确定性顺序。"""

    seen: set[str] = set()
    codes: list[str] = []
    for value in values:
        code = value.get("code") if isinstance(value, dict) else value
        if isinstance(code, str) and code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _rows(assets: Mapping[str, Any], asset_type: str) -> list[dict]:
    return _list_of_dicts(_as_dict(assets.get(asset_type)).get("rows"))


def _row_height(row: Mapping[str, Any]) -> int | None:
    high_num = _num(row.get("high_num"))
    if high_num is not None and high_num > 0:
        return int(high_num)
    for key in ("high_days", "high"):
        match = _LIANBAN_RE.search(str(row.get(key) or ""))
        if match:
            return int(match.group(2))
    return None


# ---------------------------------------------------------------------------
# 排除规则
# ---------------------------------------------------------------------------


def _normalized_code(code: str) -> str:
    value = code.strip().upper()
    if value.startswith(("SH", "SZ", "BJ")):
        value = value[2:]
    return value


def _is_bse(code: str) -> bool:
    raw = code.strip().upper()
    if raw.startswith("BJ"):
        return True
    value = _normalized_code(raw)
    if not value.isdigit() or len(value) != 6:
        return False
    return value.startswith(("4", "8", "92"))


def _is_st(row: Mapping[str, Any]) -> bool:
    for key in ("is_st", "st", "is_stock_st"):
        if row.get(key) is True:
            return True
    name = row.get("name")
    return isinstance(name, str) and "ST" in name.upper()


def _listing_days(row: Mapping[str, Any]) -> int | None:
    for key in ("listing_days", "listed_days", "list_days", "new_stock_days"):
        value = _num(row.get(key))
        if value is not None:
            return int(value)
    return None


def _is_new(row: Mapping[str, Any]) -> bool:
    days = _listing_days(row)
    return days is not None and days < _MIN_LISTING_DAYS


def _is_suspended(row: Mapping[str, Any]) -> bool:
    if row.get("is_suspended") is True or row.get("suspended") is True:
        return True
    return row.get("status") == "suspended"


# ---------------------------------------------------------------------------
# 锚（正 / 负）
# ---------------------------------------------------------------------------


def _fg_rows(assets: Mapping[str, Any], board_name: str) -> list[dict]:
    boards = _as_dict(_as_dict(assets.get("fg_boards")).get("boards"))
    board = boards.get(board_name)
    if not isinstance(board, dict) or board.get("status") == "missing":
        return []
    return _list_of_dicts(board.get("rows"))


def _negative_rows(assets: Mapping[str, Any]) -> list[tuple[str, float]]:
    """昨日涨停（缺失时昨日连板）今日跌幅最深的前 N 只。"""

    for board_name in ("昨日涨停", "昨日连板"):
        scored: list[tuple[str, float]] = []
        for row in _fg_rows(assets, board_name):
            code = row.get("code")
            close = _num(row.get("close"))
            pre_close = _num(row.get("pre_close"))
            if not isinstance(code, str) or not code or close is None or pre_close is None:
                continue
            if pre_close <= 0 or close <= 0:
                continue
            pct = (close / pre_close - 1.0) * 100.0
            if pct < 0:
                scored.append((code, pct))
        if scored:
            scored.sort(key=lambda pair: (pair[1], pair[0]))
            return scored[:_NEGATIVE_ANCHOR_LIMIT]
    return []


def derive_anchors(assets: Mapping[str, Any]) -> dict[str, list[dict]]:
    """正 / 负锚（纯函数，采集与计算共用）。

    * 正锚 = 涨停池中高度最高的股票（``height >= 2``；首板不构成锚）；
    * 负锚 = 昨日涨停今日跌幅前三（缺失回退昨日连板），与正锚去重。
    """

    pool_rows = _rows(assets, "ths_limit_up_pool")
    heights = [
        (height, row.get("code"))
        for row in pool_rows
        for height in (_row_height(row),)
        if height is not None and height >= 2 and isinstance(row.get("code"), str)
    ]
    positive: list[dict] = []
    positive_codes: set[str] = set()
    if heights:
        max_height = max(height for height, _ in heights)
        for height, code in sorted(heights, key=lambda pair: str(pair[1])):
            if height != max_height or code in positive_codes:
                continue
            positive_codes.add(code)
            positive.append({"code": code, "reason": f"市场最高 {max_height} 板"})

    negative: list[dict] = []
    for code, pct in _negative_rows(assets):
        if code in positive_codes:
            continue
        negative.append({"code": code, "reason": f"昨日涨停今日 {pct:+.1f}%"})
    return {"positive": positive, "negative": negative}


# ---------------------------------------------------------------------------
# 详细池
# ---------------------------------------------------------------------------


def _mainline_codes(assets: Mapping[str, Any], config: ReviewConfig) -> list[str]:
    """与 ``compute_mainlines`` **同一实现**排序：前 N 个主线的成分股。"""

    codes: list[str] = []
    for row in rank_boards(assets, config)[: max(int(config.mainline_top_n), 0)]:
        codes.extend(_dedupe_codes(row.get("members") or []))
    return _dedupe_codes(codes)


def _holdings_codes(assets: Mapping[str, Any]) -> list[str]:
    codes = _as_dict(assets.get("holdings")).get("codes")
    return _dedupe_codes(codes if isinstance(codes, list) else [])


def _meta_by_code(assets: Mapping[str, Any]) -> dict[str, dict]:
    """代码 → 合并后的行元数据（dragons < block 成员 < 涨停池，后者覆盖前者）。"""

    meta: dict[str, dict] = {}
    for row in _rows(assets, "dragons"):
        code = row.get("code")
        if isinstance(code, str) and code:
            meta[code] = dict(row)
    for board in _rows(assets, "ths_block_top"):
        members = board.get("stock_list")
        if members is None:
            members = board.get("members")
        for item in _list_of_dicts(members):
            code = item.get("code")
            if isinstance(code, str) and code:
                meta.setdefault(code, {}).update(item)
    for row in _rows(assets, "ths_limit_up_pool"):
        code = row.get("code")
        if isinstance(code, str) and code:
            meta.setdefault(code, {}).update(row)
    return meta


def derive_pool_codes(
    assets: Mapping[str, Any], config: ReviewConfig = CONFIG
) -> dict[str, Any]:
    """详细池代码解析（纯函数，确定性）。

    返回 ``{"codes": [...], "sources": {name: [...]}, "truncated": bool,
    "excluded": {"st": n, "new": n, "bse": n, "suspended": n}}``。
    ``sources`` 保留每个来源贡献的代码（未过滤前），便于审计「这只从哪来」。
    """

    holdings = _holdings_codes(assets)
    anchors = derive_anchors(assets)
    sources: dict[str, list[str]] = {
        "dragons": _dedupe_codes(_rows(assets, "dragons")),
        "limit_up_pool": _dedupe_codes(_rows(assets, "ths_limit_up_pool")),
        "mainlines": _mainline_codes(assets, config),
        "positive_anchor": _dedupe_codes(anchors["positive"]),
        "negative_anchor": _dedupe_codes(anchors["negative"]),
        "holdings": holdings,
    }

    ordered: list[str] = []
    seen: set[str] = set()
    for codes in sources.values():
        for code in codes:
            if code not in seen:
                seen.add(code)
                ordered.append(code)

    meta = _meta_by_code(assets)
    excluded = {"st": 0, "new": 0, "bse": 0, "suspended": 0}
    kept: list[str] = []
    for code in ordered:
        row = meta.get(code, {})
        if _is_st(row):
            excluded["st"] += 1
            continue
        if _is_new(row):
            excluded["new"] += 1
            continue
        if _is_bse(code):
            excluded["bse"] += 1
            continue
        if _is_suspended(row):
            excluded["suspended"] += 1
            continue
        kept.append(code)

    max_codes = int(config.review_max_codes)
    truncated = max_codes >= 0 and len(kept) > max_codes
    codes = kept[:max_codes] if max_codes >= 0 else kept
    return {
        "codes": codes,
        "sources": sources,
        "truncated": truncated,
        "excluded": excluded,
    }


__all__ = ["derive_anchors", "derive_pool_codes"]
