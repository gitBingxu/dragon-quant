"""市场状态纯计算：七子分、四态 + unknown。

输入只有资产 ``dict`` 与上一快照 ``prev``；本模块**无 I/O**：不读文件 / 环境变量 /
时钟，不发请求，不 import provider / storage / service。所有阈值、权重与锚点都来自
:class:`~.config.ReviewConfig`，模块内不保存第二套数字。

设计模式：

* **策略（Strategy）** —— 七个子分各是一条"输入 → 0–100"的插值策略，配置只换锚点；
* **模板方法（Template Method）** —— 统一走 ``_finalize``（子分 → 缺失归一 → 状态），
  子分之间只替换"输入如何取数"。
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Tuple

from .config import CONFIG, ReviewConfig

_LIANBAN_RE = re.compile(r"(\d+)天(\d+)板")

_SUB_REASONS = {
    "LU": "limit_up_cnt_missing",
    "LD": "limit_down_cnt_missing",
}


# ---------------------------------------------------------------------------
# 标量工具
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    """只接受有限数值 / 可转数值的字符串；``bool`` 一律视为缺失。"""

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


def _piecewise_score(value: float, anchors: Tuple[Tuple[float, float], ...]) -> float:
    """锚点分段线性插值；两端 clamp 到首/末锚点分值。"""

    points = sorted((float(x), float(y)) for x, y in anchors)
    if not points:
        return 0.0
    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            if x1 == x0:  # 配置退化时避免 ZeroDivisionError
                return y1
            return y0 + (value - x0) / (x1 - x0) * (y1 - y0)
    return points[-1][1]


def market_sub_score(key: str, value: Any, config: ReviewConfig = CONFIG) -> float | None:
    """单个市场子分：缺失返回 ``None``，未知 key fail loud。"""

    if key not in config.weights_market:
        raise ValueError(f"unknown market sub score: {key!r}")
    number = _num(value)
    if number is None:
        return None
    return round(_piecewise_score(number, config.market_anchors[key]), 1)


def market_state_for_score(score: float | None, config: ReviewConfig = CONFIG) -> str:
    """按 70/50/30 阈值返回 strong / neutral / weak / ice；分数缺失 → unknown。"""

    if score is None:
        return "unknown"
    value = float(score)
    if not math.isfinite(value):
        return "unknown"
    strong, neutral, weak = config.market_state_thresholds
    if value >= strong:
        return "strong"
    if value >= neutral:
        return "neutral"
    if value >= weak:
        return "weak"
    return "ice"


# ---------------------------------------------------------------------------
# 资产取数（只读 dict）
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(container: Any) -> list[dict]:
    rows = _as_dict(container).get("rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _pool(assets: Mapping[str, Any]) -> dict:
    return _as_dict(assets.get("ths_limit_up_pool"))


def _limit_up_cnt(pool: Mapping[str, Any]) -> int | None:
    total = _num(pool.get("total"))
    if total is not None and total >= 0:
        return int(total)
    rows = pool.get("rows")
    # 池被分页截断时，行数只是下界，不能冒充全量涨停家数（缺失 ≠ 低估）。
    if isinstance(rows, list) and pool.get("truncated") is not True:
        return len(rows)
    return None


def _limit_down_cnt(breadth: Mapping[str, Any]) -> int | None:
    count = _num(breadth.get("limit_down_cnt"))
    if count is not None and count >= 0:
        return int(count)
    rows = breadth.get("limit_down")
    return len(rows) if isinstance(rows, list) else None


def _broken_cnt(
    breadth: Mapping[str, Any], pool_rows: list[dict], pool_truncated: bool
) -> int | None:
    rows = breadth.get("broken")
    if isinstance(rows, list):
        return len(rows)
    if pool_rows and not pool_truncated:
        return sum(1 for row in pool_rows if (_num(row.get("open_num")) or 0) > 0)
    return None


def _fg_rows(assets: Mapping[str, Any], board_name: str) -> list[dict] | None:
    boards = _as_dict(_as_dict(assets.get("fg_boards")).get("boards"))
    board = boards.get(board_name)
    if not isinstance(board, dict) or board.get("status") == "missing":
        return None
    rows = board.get("rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else None


def _premium_pct(assets: Mapping[str, Any]) -> float | None:
    rows = _fg_rows(assets, "昨日涨停")
    if not rows:
        return None
    returns = [
        (float(row["close"]) / float(row["pre_close"]) - 1.0) * 100.0
        for row in rows
        if (_num(row.get("pre_close")) or 0) > 0 and (_num(row.get("close")) or 0) > 0
    ]
    return round(sum(returns) / len(returns), 2) if returns else None


def _lianban_count(row: Mapping[str, Any]) -> int:
    match = _LIANBAN_RE.search(str(row.get("high_days") or ""))
    if not match:
        return 0
    days, boards = int(match.group(1)), int(match.group(2))
    return boards if days == boards and boards >= 2 else 0


def _promotion_rate(
    assets: Mapping[str, Any], pool_rows: list[dict], pool_truncated: bool
) -> float | None:
    yesterday = _fg_rows(assets, "昨日涨停")
    if not pool_rows or not yesterday or pool_truncated:
        return None
    lianban = sum(1 for row in pool_rows if _lianban_count(row) >= 2)
    return round(lianban / len(yesterday), 3)


def _row_height(row: Mapping[str, Any]) -> int | None:
    high_num = _num(row.get("high_num"))
    if high_num is not None and high_num > 0:
        return int(high_num)
    for key in ("high", "high_days"):
        match = _LIANBAN_RE.search(str(row.get(key) or ""))
        if match:
            return int(match.group(2))
    return None


def _max_board_height(assets: Mapping[str, Any], pool_rows: list[dict]) -> int | None:
    heights = [
        height
        for height in (_row_height(row) for row in _rows(assets.get("ths_block_top")))
        if height is not None
    ]
    heights.extend(
        height
        for height in (_row_height(row) for row in pool_rows)
        if height is not None
    )
    return max(heights) if heights else None


def _prev_market(prev: Any) -> dict:
    if not isinstance(prev, dict):
        return {}
    review = prev.get("review")
    if isinstance(review, dict) and isinstance(review.get("market"), dict):
        return review["market"]
    market = prev.get("market")
    if isinstance(market, dict):
        return market
    return {}


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def compute_market_state(
    assets: dict, prev: dict | None = None, config: ReviewConfig = CONFIG
) -> dict:
    """市场七子分 + 四态（纯函数）。

    ``LU`` / ``LD`` 是判定基座：任一缺失 → ``score=None``、``state="unknown"``，
    绝不用其余分项拼凑或回落 neutral。其余子分缺失时在**当前可用权重**内归一化，
    并标 ``partial=True`` + ``missing_inputs``（缺失 ≠ 中性）。
    """

    pool = _pool(assets)
    pool_rows = _rows(pool)
    pool_truncated = pool.get("truncated") is True
    breadth = _as_dict(assets.get("market_breadth"))

    limit_up_cnt = _limit_up_cnt(pool)
    limit_down_cnt = _limit_down_cnt(breadth)
    broken_cnt = _broken_cnt(breadth, pool_rows, pool_truncated)
    premium_pct = _premium_pct(assets)
    promotion_rate = _promotion_rate(assets, pool_rows, pool_truncated)
    max_height = _max_board_height(assets, pool_rows)
    total_amount = _num(breadth.get("total_amount"))
    broken_rate = (
        broken_cnt / (broken_cnt + limit_up_cnt)
        if broken_cnt is not None and limit_up_cnt is not None and broken_cnt + limit_up_cnt > 0
        else None
    )
    total_amount_yi = total_amount / 1e8 if total_amount is not None and total_amount >= 0 else None

    raw_inputs = {
        "LU": limit_up_cnt,
        "LD": limit_down_cnt,
        "PRE": premium_pct,
        "PRO": promotion_rate,
        "HGT": max_height,
        "BRK": broken_rate,
        "LIQ": total_amount_yi,
    }
    sub_scores = {
        key: market_sub_score(key, raw_inputs[key], config) for key in config.weights_market
    }
    missing = [key for key in config.weights_market if sub_scores[key] is None]
    basis_missing = [key for key in config.market_basis if sub_scores.get(key) is None]

    if basis_missing:
        score: float | None = None
    else:
        available_weight = sum(
            weight for key, weight in config.weights_market.items() if sub_scores[key] is not None
        )
        score = (
            round(
                sum(
                    weight * float(sub_scores[key])
                    for key, weight in config.weights_market.items()
                    if sub_scores[key] is not None
                )
                / available_weight,
                1,
            )
            if available_weight > 0
            else None
        )

    state = market_state_for_score(score, config)
    unknown_reason = ";".join(_SUB_REASONS[key] for key in basis_missing) or None

    prev_market = _prev_market(prev)
    prev_score = _num(prev_market.get("score"))
    prev_state = prev_market.get("state") if isinstance(prev_market.get("state"), str) else None
    score_change = (
        round(score - prev_score, 1)
        if score is not None and prev_score is not None
        else None
    )

    evidence: list[str] = []
    if limit_up_cnt is not None and limit_down_cnt is not None:
        evidence.append(f"涨停 {limit_up_cnt} 家 / 跌停 {limit_down_cnt} 家")
    elif limit_up_cnt is not None:
        evidence.append(f"涨停 {limit_up_cnt} 家")
    elif limit_down_cnt is not None:
        evidence.append(f"跌停 {limit_down_cnt} 家")
    if broken_rate is not None:
        evidence.append(f"炸板率 {broken_rate * 100:.1f}%")
    if max_height is not None:
        evidence.append(f"最高 {max_height} 板")
    if premium_pct is not None:
        evidence.append(f"昨日涨停溢价 {premium_pct:+.1f}%")
    if promotion_rate is not None:
        evidence.append(f"晋级率 {promotion_rate * 100:.1f}%")
    if total_amount_yi is not None:
        evidence.append(f"成交额 {total_amount_yi:.0f} 亿")
    if score_change is not None:
        evidence.append(f"分数环比 {score_change:+.1f}")

    return {
        "state": state,
        "score": score,
        "coeff": float(config.market_state_coef[state]),
        "partial": bool(missing),
        "sub_scores": sub_scores,
        "inputs": {
            "limit_up_cnt": limit_up_cnt,
            "limit_down_cnt": limit_down_cnt,
            "broken_rate": None if broken_rate is None else round(broken_rate, 4),
            "yesterday_premium_pct": premium_pct,
            "promotion_rate": promotion_rate,
            "max_board_height": max_height,
            "total_amount": total_amount,
        },
        "missing_inputs": missing,
        "evidence": evidence,
        "unknown_reason": unknown_reason,
        "prev_score": prev_score,
        "prev_state": prev_state,
        "score_change": score_change,
    }


__all__ = ["compute_market_state", "market_state_for_score", "market_sub_score"]
