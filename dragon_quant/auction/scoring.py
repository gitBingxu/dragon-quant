"""集合竞价共享评分 — 采集与重算共用同一份 evaluate → rank → serialize。

唯一实现：``score_auction_assets`` 只做单股异常隔离与排名/序列化顺序控制，
不触碰存储、行情或 CLI。``collect`` 与 ``recompute`` 都必须调用本模块，
禁止各自复制一份评分循环。
"""

from __future__ import annotations


def _snapshot_summary(verdict) -> dict:
    return {
        "code": verdict.code,
        "is_weak": verdict.is_weak,
        "strength": verdict.strength,
        "direction": verdict.direction,
        "level": verdict.level,
        "unverified": verdict.unverified,
        "rank": verdict.rank,
        "reject_reason": verdict.reject_reason,
    }


def _score_auction_assets(assets: list[dict]) -> tuple[dict, list, dict]:
    """evaluate → rank_auctions → verdict_to_payload（顺序固定）。

    assets: [{"code", "items", "quote"}]；返回 (payloads, failures, summaries)。
    单股异常隔离，不中断整批。
    """
    from dragon_quant.auction import evaluate, rank_auctions
    from dragon_quant.auction.serialize import verdict_to_payload

    verdicts, failures, summaries = [], [], {}
    for asset in assets:
        code = asset["code"]
        try:
            verdicts.append(evaluate(code, asset["items"], asset.get("quote")))
        except Exception as exc:
            failures.append({"code": code, "stage": "score",
                             "error": f"{type(exc).__name__}: {exc}"})

    rank_auctions(verdicts)          # 必须先 rank 再序列化，否则 rank 永远丢失

    payloads = {}
    for verdict in verdicts:
        try:
            payloads[verdict.code] = verdict_to_payload(verdict)
            summaries[verdict.code] = _snapshot_summary(verdict)
        except Exception as exc:
            failures.append({"code": verdict.code, "stage": "serialize",
                             "error": f"{type(exc).__name__}: {exc}"})
    return payloads, failures, summaries
