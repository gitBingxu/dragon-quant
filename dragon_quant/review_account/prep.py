"""盘后复盘：识别主线 + 情绪温度 + 候选池，产出可扩展 JSON 快照。

供 `prep` CLI 命令调用；review-account / buy-sell 经 `collect_candidates` 消费快照。
"""

from __future__ import annotations

from dragon_quant.providers.tdx import TdxProvider
from dragon_quant.storage import db


def position_advice(limit_up_cnt: int, limit_down_cnt: int) -> tuple:
    """v0.1 情绪 → 建议仓位启发式（阈值集中于此，后续回测调参）。"""
    if limit_up_cnt >= 80 and limit_down_cnt <= 5:
        return "重仓", 0.8
    if limit_down_cnt >= 30:
        return "空仓", 0.0
    if limit_down_cnt >= 10 or limit_up_cnt <= 15:
        return "轻仓", 0.3
    return "正常", 0.5


def compute_sentiment(tdx: TdxProvider) -> dict:
    """情绪温度：涨跌停家数（tmdx FG 板块）。全市场涨跌家数/量能留 Phase 2。

    注意：tmdx FG 板块是「当前/最近交易日」时点数据，仅当 prep 在复盘日盘后当天
    运行时才精确；补跑历史时 sentiment 反映补跑日而非复盘日，仅作建议仓位参考。
    """
    limit_up_cnt = len(tdx.get_fg_board_members("昨日涨停"))
    limit_down_cnt = len(tdx.get_fg_board_members("昨日跌停"))
    total = limit_up_cnt + limit_down_cnt
    score = round(limit_up_cnt / total * 100, 1) if total else None
    advice, pct = position_advice(limit_up_cnt, limit_down_cnt)
    return {
        "up_cnt": None,
        "down_cnt": None,
        "limit_up_cnt": limit_up_cnt,
        "limit_down_cnt": limit_down_cnt,
        "total_amount": None,
        "temperature_score": score,
        "position_advice": advice,
        "position_pct": pct,
    }


def compute_main_boards(dragons: list) -> list:
    """从 R 日真龙的领涨板块(concepts)聚合主线（真龙最多的板块）。"""
    counter = {}
    for d in dragons:
        for c in (d.get("concepts") or []):
            counter[c] = counter.get(c, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: -kv[1])
    return [{"board_name": name, "dragon_count": cnt} for name, cnt in ranked[:3]]


def compute_candidates(dragons: list, trade_date: str) -> list:
    """R 日真龙 = 主线板块龙头；补齐 trade_date 与 get_dragons_by_date 结构一致。"""
    out = []
    for d in dragons:
        cand = dict(d)
        cand["trade_date"] = trade_date
        out.append(cand)
    return out


def run_prep(trade_date: str, source: str = "v2") -> dict:
    """执行盘后复盘并落库，返回完整复盘结果 dict。"""
    tdx = TdxProvider()
    dragons = db.get_dragons(trade_date, source=source)
    result = {
        "trade_date": trade_date,
        "version": "0.1",
        "sentiment": compute_sentiment(tdx),
        "main_boards": compute_main_boards(dragons),
        "candidates": compute_candidates(dragons, trade_date),
    }
    db.save_prep_snapshot(trade_date, result, source=source)
    return result
