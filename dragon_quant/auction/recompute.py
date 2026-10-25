"""集合竞价离线重算 — 只用已存原始资产重算快照（零 provider、零网络）。"""

from __future__ import annotations

from dragon_quant.auction.dates import _parse_auction_date, _today
from dragon_quant.auction.scoring import _score_auction_assets
from dragon_quant.auction.types import AuctionOutcome


def run_auction_recompute(raw_date: str, *, services=None,
                          today: str | None = None) -> AuctionOutcome:
    """用已存原始资产重算某日竞价快照（不触网、不构造 provider）。

    ``services`` 仅为签名对称保留，本函数从不读取它；``today`` 供命令层沿用
    既有 ``_resolve_trade_date`` patch 点，缺省时使用 Asia/Shanghai 当前日期。
    """
    from dragon_quant.storage import assets_db
    from dragon_quant.auction.serialize import quote_from_payload

    try:
        trade_date = _parse_auction_date(raw_date)
    except ValueError as exc:
        return AuctionOutcome.error({"ok": False, "mode": "recompute",
                                     "trade_date": raw_date, "error": str(exc)})

    current = today or _today()
    if trade_date > current:
        return AuctionOutcome.error(
            {"ok": False, "mode": "recompute", "trade_date": trade_date,
             "error": f"未来日期不可重算: {trade_date} > {current}"})

    try:
        assets = assets_db.list_auction_assets(trade_date)
    except Exception as exc:
        return AuctionOutcome.error(
            {"ok": False, "mode": "recompute", "trade_date": trade_date,
             "error": f"读取竞价原始资产失败: {type(exc).__name__}: {exc}"})
    if not assets:
        return AuctionOutcome.error(
            {"ok": False, "mode": "recompute", "trade_date": trade_date,
             "error": f"{trade_date} 无竞价原始资产，无法重算"})

    usable, skipped_empty, failed, quote_missing = [], [], [], []
    for asset in assets:
        code = asset["code"]
        if asset.get("items_error"):
            failed.append({"code": code, "stage": "asset",
                           "error": asset["items_error"]})
            continue
        if asset.get("quote_error"):
            failed.append({"code": code, "stage": "quote",
                           "error": asset["quote_error"]})
            continue
        if not asset.get("items"):
            skipped_empty.append(code)
            continue
        raw_quote = asset.get("quote")
        try:
            quote = quote_from_payload(raw_quote)
        except Exception as exc:          # 有 quote_json 但损坏 → 单独计失败
            failed.append({"code": code, "stage": "quote",
                           "error": f"{type(exc).__name__}: {exc}"})
            continue
        if raw_quote is None:
            quote_missing.append(code)
        usable.append({"code": code, "items": asset["items"], "quote": quote})

    if not usable:
        return AuctionOutcome.error(
            {"ok": False, "mode": "recompute", "trade_date": trade_date,
             "asset_count": len(assets), "skipped_empty": skipped_empty,
             "failed": failed,
             "error": "无可计算资产（items 全为空或行情 payload 全部损坏）"})

    payloads, score_failures, summaries = _score_auction_assets(usable)
    failed.extend(score_failures)
    if not payloads:
        return AuctionOutcome.error(
            {"ok": False, "mode": "recompute", "trade_date": trade_date,
             "asset_count": len(assets), "usable_count": len(usable),
             "skipped_empty": skipped_empty, "quote_missing": quote_missing,
             "failed": failed, "error": "全部评分/序列化失败，快照未写入"})

    try:
        saved = assets_db.replace_auction_snapshots(trade_date, payloads, source="v2")
    except Exception as exc:
        return AuctionOutcome.error(
            {"ok": False, "mode": "recompute", "trade_date": trade_date,
             "error": f"快照写入失败: {type(exc).__name__}: {exc}"})

    payload = {
        "ok": not failed,
        "mode": "recompute",
        "trade_date": trade_date,
        "source": "v2",
        "asset_count": len(assets),
        "usable_count": len(usable),
        "skipped_empty": skipped_empty,
        "quote_missing": quote_missing,
        "scored_count": len(payloads),
        "snapshot_saved_count": saved,
        "snapshot_ok": True,
        "snapshot_skipped": None,
        "snapshot_error": None,
        "failed": failed,
        "snapshots": [summaries[c] for c in sorted(summaries)],
    }
    names = {asset["code"]: (asset.get("name") or "") for asset in assets}
    return AuctionOutcome(payload=payload, exit_code=0, render_names=names,
                          mode="recompute")
