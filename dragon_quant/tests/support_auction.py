"""Shared synthetic fixtures for auction tests (no I/O, no real database).

The tapes encode the real-sample facts documented in the research report
(``archived_md/auction/2026-10-29-auction-strength-research.md`` F2/F3/F5): ``matched`` may fall
before 09:20 and never falls after, the last point is often 09:24:57–09:24:59,
and the official ``open_px`` differs from the last indicative price.
"""

from __future__ import annotations

from dragon_quant.infra.models import Quote


def item(clock: str, price: float, matched: float, unmatched: float) -> dict:
    return {"time": clock, "price": price, "matched": matched,
            "unmatched": unmatched}


def quote(code: str = "600001", *, prev_close: float = 10.0,
          open_px: float = 10.2, price: float = 10.3,
          float_cap: float = 8.0e8, limit_down: float = 9.0,
          limit_up: float = 11.0, name: str = "示例") -> Quote:
    pct = ((price / prev_close - 1) * 100) if prev_close else 0.0
    return Quote(
        code=code, name=name, price=price, prev_close=prev_close,
        open_px=open_px, high=max(price, open_px), low=min(price, open_px),
        pct=pct, chg=price - prev_close,
        turnover_rate=1.0, amplitude=2.0, volume=1e6, amount=1e8,
        market_cap=float_cap * 1.25, float_market_cap=float_cap,
        volume_ratio=1.0, pe=20.0, limit_up=limit_up, limit_down=limit_down,
        avg_price=price,
    )


def ascending(core_bars: int = 6, *, start_price: float = 10.2,
              step: float = 0.02, base_matched: float = 1000.0,
              matched_step: float = 2000.0, unmatched: float = 300.0) -> list:
    """09:20 起单调走强、匹配量单调增加的合成 tape（沪深同构）。"""
    rows = [
        item("09:15:00", start_price - 0.1, 0.0, unmatched),
        item("09:18:00", start_price - 0.05, base_matched * 0.5, unmatched),
    ]
    seconds = [9 * 3600 + 20 * 60 + i for i in range(core_bars)]
    for i, second in enumerate(seconds):
        clock = f"{second // 3600:02d}:{(second % 3600) // 60:02d}:{second % 60:02d}"
        rows.append(item(clock, start_price + step * i,
                         base_matched + matched_step * i, unmatched))
    return rows


def real_sample_shape() -> list:
    """还原真实样本形状：09:20 前 matched 下降、末点 09:24:59、官方开盘不同。"""
    return [
        item("09:15:00", 10.00, 0.0, 200.0),
        item("09:16:00", 10.10, 3000.0, 150.0),
        item("09:17:00", 10.20, 1000.0, 100.0),      # 09:20 前下降
        item("09:18:00", 10.25, 2200.0, 800.0),
        item("09:19:00", 10.30, 1800.0, 600.0),      # 09:20 前再次下降
        item("09:20:00", 10.30, 2500.0, 500.0),
        item("09:21:00", 10.32, 4000.0, 450.0),
        item("09:22:00", 10.34, 6000.0, 400.0),
        item("09:23:00", 10.36, 8000.0, 350.0),
        item("09:24:00", 10.38, 10000.0, 300.0),
        item("09:24:30", 10.40, 12000.0, 250.0),
        item("09:24:45", 10.42, 13500.0, 200.0),
        item("09:24:50", 10.43, 14250.0, 175.0),
        item("09:24:59", 10.44, 15000.0, 150.0),
    ]


def weak_to_strong() -> list:
    """未匹配由卖方向转买方，幅度达标（转换事件夹具）。"""
    return [
        item("09:15:00", 10.00, 0.0, -100.0),
        item("09:18:00", 9.95, 500.0, -150.0),
        item("09:20:00", 9.90, 1000.0, -900.0),
        item("09:21:00", 9.95, 2000.0, -800.0),
        item("09:22:00", 10.00, 3000.0, -600.0),
        item("09:23:00", 10.05, 5000.0, -100.0),
        item("09:24:00", 10.10, 7000.0, 200.0),
        item("09:24:30", 10.12, 9000.0, 400.0),
        item("09:24:45", 10.14, 11000.0, 600.0),
        item("09:24:50", 10.145, 12000.0, 700.0),
        item("09:25:00", 10.15, 13000.0, 800.0),
    ]


def strong_to_weak() -> list:
    """未匹配由买方向转卖方且幅度达标（强转弱 veto 夹具）。"""
    return [
        item("09:15:00", 10.0, 0.0, 100.0),
        item("09:18:00", 10.1, 500.0, 200.0),
        item("09:20:00", 10.2, 1000.0, 1500.0),
        item("09:21:00", 10.2, 2000.0, 1400.0),
        item("09:22:00", 10.2, 3000.0, 1200.0),
        item("09:23:00", 10.1, 5000.0, -500.0),
        item("09:24:00", 10.0, 7000.0, -1200.0),
        item("09:24:30", 9.95, 9000.0, -1600.0),
        item("09:24:45", 9.9, 11000.0, -1800.0),
        item("09:25:00", 9.9, 13000.0, -2000.0),
    ]
