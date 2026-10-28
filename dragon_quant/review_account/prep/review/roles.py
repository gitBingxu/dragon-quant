"""五角色责任链、跨板块唯一主归属与未知处理（纯函数）。

输入只有涨停池行、主线板块行与配置；本模块**无 I/O**，不 import provider /
storage / service。所有阈值（中军流通市值、容量核心成交额/高度上限）来自
:class:`~.config.ReviewConfig`。

设计模式：

* **责任链（Chain of Responsibility）** —— 每只股票按
  ``leader → vanguard → mainstay → capacity → follower`` 依次判定，命中即停；
* **策略（Strategy）** —— 跨板块归属按「涨停原因 token 匹配数 → 主线分」排序，
  规则独立于角色判定。

未知不猜：成员缺失 → 角色 ``null``；首封时间/板型缺失 → 先锋不可判并继续下一条规则；
非主线标的显式走 ``diagnostic_non_mainline`` 权重 profile，绝不借用 follower。
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from .config import CONFIG, DIAGNOSTIC_PROFILE, ReviewConfig, weight_profile_for

_TOKEN_SPLIT_RE = re.compile(r"[^\w一-鿿]+", re.UNICODE)


# ---------------------------------------------------------------------------
# 标量工具
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None


def _time_minutes(value: Any) -> float | None:
    """``HH:MM`` / ``HH:MM:SS`` → 当日分钟数；非法返回 ``None``。"""

    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return hour * 60 + minute + second / 60.0


def _tokens(text: Any) -> list[str]:
    if not isinstance(text, str):
        return []
    return [token for token in _TOKEN_SPLIT_RE.split(text) if token]


def _token_match(row: Mapping[str, Any], board_name: str) -> int:
    """涨停原因 token 与板块名的匹配数（双向包含，大小写不敏感）。"""

    name = board_name.casefold()
    count = 0
    for token in _tokens(row.get("reason_type")):
        folded = token.casefold()
        if folded and (folded in name or name in folded):
            count += 1
    return count


def _is_one_word(row: Mapping[str, Any]) -> bool:
    limit_up_type = row.get("limit_up_type")
    return isinstance(limit_up_type, str) and "一字" in limit_up_type


def _is_mainstay(row: Mapping[str, Any], config: ReviewConfig) -> bool:
    float_cap = _num(row.get("float_market_cap"))
    return float_cap is not None and float_cap >= config.role_mainstay_min_float_cap


def _is_capacity(row: Mapping[str, Any], config: ReviewConfig) -> bool:
    amount = _num(row.get("amount"))
    height = _num(row.get("height"))
    return (
        amount is not None
        and amount >= config.role_capacity_min_amount
        and height is not None
        and height <= config.role_capacity_max_height
    )


# ---------------------------------------------------------------------------
# 板块元数据
# ---------------------------------------------------------------------------


def _board_name(board: Mapping[str, Any]) -> str | None:
    name = board.get("board_name") or board.get("name")
    return name if isinstance(name, str) and name else None


def _board_members(board: Mapping[str, Any]) -> list[str] | None:
    value = board.get("members")
    if value is None:
        value = board.get("stock_list")
    if not isinstance(value, list):
        return None
    codes: list[str] = []
    seen: set[str] = set()
    for item in value:
        code = item.get("code") if isinstance(item, dict) else item
        if isinstance(code, str) and code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _board_meta(board: Mapping[str, Any], fallback_rank: int) -> dict:
    rank_number = _num(board.get("rank"))
    rank = int(rank_number) if rank_number is not None and rank_number > 0 else fallback_rank
    score = _num(board.get("mainline_score"))
    state = board.get("state") if isinstance(board.get("state"), str) else None
    return {
        "name": _board_name(board),
        "rank": rank,
        "score": score,
        "state": state,
        "continuous": _num(board.get("continuous_plate_num")),
        "raw": board,
    }


# ---------------------------------------------------------------------------
# 单板块责任链
# ---------------------------------------------------------------------------


def _sort_key(row: Mapping[str, Any]) -> tuple:
    height = _num(row.get("height"))
    seal = _time_minutes(row.get("first_seal_time"))
    amount = _num(row.get("amount"))
    return (
        0 if height is not None else 1,
        -(height or 0.0),
        0 if seal is not None else 1,
        seal or 0.0,
        0 if amount is not None else 1,
        -(amount or 0.0),
        str(row.get("code") or ""),
    )


def _vanguard_eligible(row: Mapping[str, Any]) -> bool:
    if _time_minutes(row.get("first_seal_time")) is None:
        return False
    limit_up_type = row.get("limit_up_type")
    if not isinstance(limit_up_type, str) or not limit_up_type:
        return False
    return "一字" not in limit_up_type


def _vanguard_key(row: Mapping[str, Any]) -> tuple:
    return (
        _time_minutes(row.get("first_seal_time")) or 0.0,
        0 if _num(row.get("amount")) is not None else 1,
        -(_num(row.get("amount")) or 0.0),
        str(row.get("code")),
    )


def _assign_board(rows: list[dict], config: ReviewConfig) -> dict:
    """在一个主线板块内执行责任链，返回 code → 角色与排序位置。"""

    ordered = sorted(rows, key=_sort_key)
    roles: dict[str, dict[str, str]] = {}
    ranks = {row["code"]: index + 1 for index, row in enumerate(ordered)}
    leader_code: str | None = None
    vanguard_code: str | None = None

    if ordered:
        first = ordered[0]
        height = _num(first.get("height"))
        if height is not None:
            leader_code = first["code"]
            if int(height) == 1:
                roles[leader_code] = {
                    "role": "leader",
                    "role_confidence": "low",
                    "role_basis": "first_board_earliest",
                }
            else:
                roles[leader_code] = {
                    "role": "leader",
                    "role_confidence": "high",
                    "role_basis": f"板块内高度最高 {int(height)} 板",
                }

    remaining = [row for row in ordered if row["code"] not in roles]
    eligible_vanguards = [row for row in ordered if _vanguard_eligible(row)]
    vanguard_earliest = None
    if eligible_vanguards:
        vanguard_earliest = min(eligible_vanguards, key=_vanguard_key)["code"]

    candidates = [row for row in remaining if _vanguard_eligible(row)]
    if candidates:
        chosen = min(candidates, key=_vanguard_key)
        vanguard_code = chosen["code"]
        roles[vanguard_code] = {
            "role": "vanguard",
            "role_confidence": "high",
            "role_basis": f"最早非一字涨停（首封 {chosen.get('first_seal_time')}）",
        }

    for row in remaining:
        code = row["code"]
        if code in roles:
            continue
        if _is_mainstay(row, config):
            roles[code] = {
                "role": "mainstay",
                "role_confidence": "high",
                "role_basis": (
                    f"流通市值 {_num(row.get('float_market_cap')) / 1e8:.0f} 亿 "
                    f"≥ {config.role_mainstay_min_float_cap / 1e8:.0f} 亿"
                ),
            }
        elif _is_capacity(row, config):
            roles[code] = {
                "role": "capacity",
                "role_confidence": "high",
                "role_basis": (
                    f"成交额 {_num(row.get('amount')) / 1e8:.2f} 亿且高度 "
                    f"≤ {config.role_capacity_max_height}"
                ),
            }
        else:
            roles[code] = {
                "role": "follower",
                "role_confidence": "low",
                "role_basis": "板块内其余涨停股（兜底）",
            }

    return {
        "roles": roles,
        "ordered": ordered,
        "ranks": ranks,
        "leader": leader_code,
        "vanguard": vanguard_code,
        "vanguard_earliest": vanguard_earliest,
    }


# ---------------------------------------------------------------------------
# 证据与未知
# ---------------------------------------------------------------------------


def _evidence(row: Mapping[str, Any]) -> dict:
    return {
        "height": row.get("height"),
        "first_seal_time": row.get("first_seal_time"),
        "amount": row.get("amount"),
        "float_market_cap": row.get("float_market_cap"),
        "limit_up_type": row.get("limit_up_type"),
        "reason_type": row.get("reason_type"),
    }


def _unknown_record(row: Mapping[str, Any], reason: str, mainline_rank: int | None) -> dict:
    if reason == "not_in_mainline":
        basis = "非主线标的（未进入当日主线），不参与角色竞争"
    elif reason == "board_members_missing":
        basis = "主线成员缺失，角色不可判"
    else:
        basis = "当日主线不可用，角色不可判"
    return {
        "code": row["code"],
        "role": None,
        "role_confidence": "unknown",
        "role_basis": basis,
        "role_competitors": [],
        "mainline": None,
        "also_in_boards": [],
        "weight_profile": DIAGNOSTIC_PROFILE,
        "unknown_reason": reason,
        "intra_board_rank": None,
        "board_limit_up_num": None,
        "mainline_rank": mainline_rank,
        "mainline_days": None,
        "attr_match": 0 if reason == "not_in_mainline" else None,
        "evidence": _evidence(row),
    }


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def assign_roles(
    pool_rows: list, block_rows: list, config: ReviewConfig = CONFIG
) -> dict[str, dict]:
    """给每只涨停股分配唯一角色（纯函数）。

    ``block_rows`` 可以是 :func:`~.mainline.compute_mainlines` 的富化行，也可以是
    含 ``name`` / ``stock_list`` 的原始板块行；``is_mainline=False`` 的行被忽略。
    """

    rows: list[dict] = []
    seen_codes: set[str] = set()
    for row in pool_rows or []:
        if not isinstance(row, dict):
            continue
        code = row.get("code")
        if not isinstance(code, str) or not code or code in seen_codes:
            continue
        seen_codes.add(code)
        rows.append(row)
    row_by_code = {row["code"]: row for row in rows}

    boards: list[dict] = []
    seen_names: set[str] = set()
    for index, board in enumerate(block_rows or []):
        if not isinstance(board, dict) or board.get("is_mainline") is False:
            continue
        meta = _board_meta(board, index + 1)
        if meta["name"] is None or meta["name"] in seen_names:
            continue
        seen_names.add(meta["name"])
        boards.append(meta)

    members_by_board: dict[str, list[str] | None] = {}
    missing_members = False
    for meta in boards:
        members = _board_members(meta["raw"])
        if members is None or not members:
            missing_members = True
            members_by_board[meta["name"]] = None
        else:
            members_by_board[meta["name"]] = [
                code for code in members if code in row_by_code
            ]

    board_results: dict[str, dict] = {}
    for meta in boards:
        members = members_by_board[meta["name"]]
        if members is None:
            continue
        board_results[meta["name"]] = _assign_board(
            [row_by_code[code] for code in members], config
        )

    result: dict[str, dict] = {}
    for row in rows:
        code = row["code"]
        memberships: list[tuple[int, dict]] = []
        for meta in boards:
            members = members_by_board[meta["name"]]
            if members is None or code not in members:
                continue
            memberships.append((_token_match(row, meta["name"]), meta))

        if not memberships:
            if not boards:
                result[code] = _unknown_record(row, "no_mainline_available", None)
            elif missing_members:
                result[code] = _unknown_record(row, "board_members_missing", None)
            else:
                result[code] = _unknown_record(row, "not_in_mainline", 0)
            continue

        memberships.sort(
            key=lambda pair: (
                -pair[0],
                1 if pair[1]["score"] is None else 0,
                -float(pair[1]["score"] or 0.0),
                pair[1]["rank"],
                pair[1]["name"],
            )
        )
        primary = memberships[0][1]
        secondary = sorted(
            (meta for _, meta in memberships[1:]),
            key=lambda meta: (meta["rank"], meta["name"]),
        )
        primary_result = board_results.get(primary["name"])
        role_info = primary_result["roles"].get(code) if primary_result else None
        if role_info is None:
            result[code] = _unknown_record(row, "board_members_missing", None)
            continue

        competitors: list[str] = []
        if primary_result is not None:
            if code == primary_result["leader"]:
                competitors.append("leader")
            if code in (
                primary_result["vanguard"],
                primary_result.get("vanguard_earliest"),
            ):
                competitors.append("vanguard")
            if _is_mainstay(row, config):
                competitors.append("mainstay")
            if _is_capacity(row, config):
                competitors.append("capacity")
        competitors = [role for role in competitors if role != role_info["role"]]
        for meta in secondary:
            other_result = board_results.get(meta["name"])
            other_role = other_result["roles"].get(code) if other_result else None
            if other_role is not None:
                label = f"{other_role['role']}@{meta['name']}"
                if label not in competitors:
                    competitors.append(label)

        rank = primary_result["ranks"].get(code) if primary_result else None
        result[code] = {
            "code": code,
            "role": role_info["role"],
            "role_confidence": role_info["role_confidence"],
            "role_basis": role_info["role_basis"],
            "role_competitors": competitors,
            "mainline": {
                "board_name": primary["name"],
                "rank": primary["rank"],
                "mainline_score": primary["score"],
                "state": primary["state"],
            },
            "also_in_boards": [meta["name"] for meta in secondary],
            "weight_profile": weight_profile_for(role_info["role"]),
            "unknown_reason": None,
            "intra_board_rank": rank,
            "board_limit_up_num": len(primary_result["ordered"]) if primary_result else None,
            "mainline_rank": primary["rank"],
            "mainline_days": primary["continuous"],
            "attr_match": memberships[0][0],
            "evidence": _evidence(row),
        }

    return result


__all__ = ["assign_roles"]
