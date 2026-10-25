"""Pure CLI helpers shared by the command modules.

Nothing here carries domain logic: these are the strategy-config loader, the
date normalisation, trade-date resolution and dataclass/KBar → JSON-dict
serializers that ``cli.py`` historically owned.  They live here as the single
implementation; ``cli`` and the command modules re-export the objects, never
copy them.
"""

from __future__ import annotations

import json
from datetime import timedelta, timezone
from typing import Any, Optional

from dragon_quant.runtime import clock

_SHANGHAI = timezone(timedelta(hours=8))


def load_strategy_params(path: Optional[str]) -> Optional[dict]:
    """读取并校验 ``--config`` 策略 JSON（唯一实现，历史行为不变）。

    ``None``/空路径返回 ``None``；非 JSON 对象抛 ``ValueError``；字段合法性由
    ``StrategyConfig.from_dict`` 校验，返回**原始 dict** 供调用方合并。
    """
    if not path:
        return None
    from dragon_quant.review_account.models import StrategyConfig

    with open(path, encoding="utf-8") as handle:
        values = json.load(handle)
    if not isinstance(values, dict):
        raise ValueError("策略配置必须是 JSON 对象")
    StrategyConfig.from_dict(values)
    return values


def normalize_cli_date(d: str) -> str:
    """CLI 日期支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD，统一转为 YYYY-MM-DD。"""
    d = d.strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    if len(d) == 10 and d[4] == "/" and d[7] == "/":
        return f"{d[:4]}-{d[5:7]}-{d[8:10]}"
    return d


def resolve_trade_date(raw: Optional[str]) -> str:
    """解析交易日：显式 --date 优先，否则取 Asia/Shanghai 当前日期。"""
    if raw:
        return normalize_cli_date(raw)
    return clock.now(_SHANGHAI).strftime("%Y-%m-%d")


def kbar_to_dict(kbar) -> dict:
    """KBar → dict (float 字段保留 4 位小数，与历史 JSON 输出一致)。"""
    d = {}
    for f in ("time", "open", "close", "high", "low", "volume", "pct"):
        v = getattr(kbar, f, None)
        if isinstance(v, float):
            v = round(v, 4)
        d[f] = v
    return d


def to_dict(obj) -> Any:
    """dataclass → dict (递归处理 list/dict)。"""
    if hasattr(obj, '__dataclass_fields__'):
        return {f.name: to_dict(getattr(obj, f.name))
                for f in obj.__dataclass_fields__.values()}
    if isinstance(obj, list):
        return [to_dict(i) for i in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj
