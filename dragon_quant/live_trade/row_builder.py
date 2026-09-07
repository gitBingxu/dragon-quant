"""用实时行情快照 + 历史日 K 拼出策略函数消费的 row，实现 buy/sell 命令对
review_account 策略的完全复用。

- buy（9:25）：只知道今日开盘价，用历史日 K（截至上一交易日）算指标，
  再手工拼当日开盘决策 row，喂给 evaluate_buy 的开盘买点路径。
- sell（14:55）：现价≈收盘、日内高低已知，用实时快照合成一根今日 KBar 追加到
  历史后统一 enrich，取末行为 row，天然得到含今日的 ma5 / 涨停收盘 / 放量等指标。
"""

from datetime import datetime
from typing import Optional

from dragon_quant.models.types import KBar, Quote
from dragon_quant.review_account.indicators import enrich_daily_klines, kbar_date


def _ts_ms(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d").timestamp() * 1000)


def build_history_rows(klines: list[KBar], before_date: str) -> list[dict]:
    """把历史日 K 转为带指标的行，仅保留 before_date 之前（不含）的记录。"""
    rows = enrich_daily_klines(klines or [])
    return [r for r in rows if r["date"] < before_date]


def build_buy_row(klines: list[KBar], quote: Quote, trade_date: str) -> Optional[dict]:
    """构造 9:25 开盘买入决策所需的 (row, prev_row, hist_rows)。

    row 只包含开盘即可确认、不含未来函数的字段：开盘价、开盘缺口、昨收、涨停价、
    是否一字板（开盘≈涨停且未打开），量能门槛由候选池昨日值兜底，这里带上实时快照
    的 amount/turnover 仅作参考。返回 None 表示历史数据不足。
    """
    hist_rows = build_history_rows(klines, trade_date)
    if not hist_rows:
        return None
    prev_row = hist_rows[-1]
    prev_close = quote.prev_close or prev_row.get("close")
    open_px = quote.open_px or 0.0
    if open_px <= 0 or not prev_close or prev_close <= 0:
        return None
    open_gap_pct = (open_px / prev_close - 1) * 100
    limit_up = quote.limit_up or round(prev_close * 1.1, 2)
    # 9:25 集合竞价结束，若开盘即封死一字板（开盘≈涨停且现价未打开），视为一字板
    is_one_word_board = bool(
        limit_up and open_px >= limit_up * 0.999
        and quote.low and quote.low >= limit_up * 0.999
    )
    row = {
        "date": trade_date,
        "open": open_px,
        "prev_close": prev_close,
        "open_gap_pct": open_gap_pct,
        "prev_high": prev_row.get("high"),
        "limit_up": limit_up,
        "is_one_word_board": is_one_word_board,
        "amount": quote.amount or 0.0,
        "turnover": quote.turnover_rate or 0.0,
    }
    return {"row": row, "prev_row": prev_row, "hist_rows": hist_rows}


def build_sell_row(klines: list[KBar], quote: Quote, trade_date: str) -> Optional[dict]:
    """构造 14:55 卖出决策所需的 row：用实时快照合成今日 KBar 追加历史后统一 enrich。

    返回 {"row": ..., "hist_rows": ...}；row 为含今日的末行（含 ma5/prev_close/
    prev_volume/volume_change/is_limit_up_close 等）。历史数据不足返回 None。
    """
    hist = [k for k in (klines or []) if kbar_date(k) < trade_date]
    if not hist:
        return None
    prev_close = quote.prev_close or hist[-1].close
    close = quote.price or quote.open_px or prev_close
    chg = close - prev_close if prev_close else 0.0
    pct = (close / prev_close - 1) * 100 if prev_close else 0.0
    today = KBar(
        timestamp=_ts_ms(trade_date),
        volume=quote.volume or 0.0,
        open=quote.open_px or close,
        high=quote.high or close,
        low=quote.low or close,
        close=close,
        chg=chg,
        pct=pct,
        turnover=quote.turnover_rate or 0.0,
        amount=quote.amount or 0.0,
    )
    rows = enrich_daily_klines(hist + [today])
    row = rows[-1]
    if quote.limit_up:
        row["limit_up"] = quote.limit_up
    return {"row": row, "hist_rows": rows[:-1]}
