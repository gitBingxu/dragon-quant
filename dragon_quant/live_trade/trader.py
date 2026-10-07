from __future__ import annotations

from datetime import datetime

from dragon_quant.live_trade.signal_engine import SignalEngine
from dragon_quant.review_account.data import MarketData, historical_events
from dragon_quant.review_account.execution import fee
from dragon_quant.review_account.market import SHANGHAI, at, bar_times, calendar_start, collect_candidates
from dragon_quant.review_account.models import Position, StrategyConfig
from dragon_quant.storage import db


class LiveTrader:
    """buy/sell 命令的信号执行器：回放当日事件流，复用 SignalEngine，纯信号记账。"""

    def __init__(self, cfg: StrategyConfig, source: str = "v2", data=None):
        self.cfg = StrategyConfig.from_dict(cfg.to_json_dict())
        self.source = source
        self.data = data or MarketData()

    def buy(self, trade_date: str, as_of: str | None = None) -> dict:
        return self._run(trade_date, as_of, "buy")

    def sell(self, trade_date: str, as_of: str | None = None) -> dict:
        return self._run(trade_date, as_of, "sell")

    def _run(self, day, clock, command):
        datetime.strptime(day, "%Y-%m-%d")
        if clock:
            datetime.strptime(clock, "%H:%M")
        now = datetime.now(SHANGHAI)
        today = now.strftime("%Y-%m-%d")
        calendar = self.data.calendar(calendar_start(day, self.cfg.candidate_lookback_days), day, include_today=True)
        if day not in calendar:
            raise ValueError(f"{day} 不是已确认的交易日")

        if day < today or (day == today and clock is not None):
            # 历史（或今日回放）：必须指定 --at，只取对应时点及之前的行情。
            if not clock:
                raise ValueError("历史日期必须提供 --at HH:MM，且仅使用对应历史行情")
            target = at(day, clock)
            until = None if day < today else target
        else:
            # 实时今日：回放截至当前已完成的5分钟K。
            time = now.strftime("%H:%M:%S")
            if not ("09:30:00" <= time < "11:30:01" or "13:00:00" <= time < "15:06:00"):
                if time < "09:30:00":
                    raise ValueError("当前尚未开盘（09:30 开始），请开盘后执行")
                if "11:30:01" <= time < "13:00:00":
                    raise ValueError("当前午间休市（11:30–13:00），请 13:00 后执行")
                raise ValueError("当前已收盘，请在交易时段执行，或用 --date/--at 回放历史")
            target = int(now.timestamp() * 1000)
            completed = [t for t in bar_times(day) if t <= target]
            if not completed:
                raise ValueError("尚无完整5分钟K（09:35 首根），请稍后执行")
            until = completed[-1]

        if command == "buy":
            return self._run_buy(day, target, until, calendar)
        return self._run_sell(day, target, until)

    def _run_buy(self, day, target, until, calendar):
        held_codes = {s["code"] for s in db.list_open_signals(source=self.source)}
        candidates = collect_candidates(calendar, day, self.cfg, db.get_dragons_by_date)
        codes = {c["code"] for c in candidates}
        engine = SignalEngine(self.cfg, held_codes=held_codes)
        buys, details = [], []
        for event in historical_events(day, candidates, codes, self.data, until=until):
            if event.timestamp > target:
                break
            event.allow_buy = True
            result = engine.step(event)
            buys.extend(result["buys"])
            details.extend(result["details"])
        for b in buys:
            db.insert_signal(b["code"], b["name"], b["entry_date"], b["entry_price"],
                             b["reason_code"], b["reason_text"], b["signal"], self.source)
        for p in engine.positions:
            db.update_signal_peaks(p.code, p.highest_return, p.highest_price)
        return {"action": "buy" if buys else "idle", "buys": buys, "details": details,
                "reason_text": f"买入 {len(buys)} 只" if buys else "候选池暂无触发买点的标的"}

    def _run_sell(self, day, target, until):
        open_signals = db.list_open_signals(before_date=day, source=self.source)
        positions = [self._notional_position(s) for s in open_signals]
        codes = {s["code"] for s in open_signals}
        engine = SignalEngine(self.cfg, positions=positions)
        sells = []
        for event in historical_events(day, [], codes, self.data, until=until):
            if event.timestamp > target:
                break
            event.allow_buy = False
            result = engine.step(event)
            sells.extend(result["sells"])
        for s in sells:
            db.close_signal(s["code"], s["exit_date"], s["exit_price"],
                            s["reason_code"], s["reason_text"], s["signal"], s["hold_days"])
        for p in engine.positions:
            db.update_signal_peaks(p.code, p.highest_return, p.highest_price)
        held = [{"code": p.code, "name": p.name, "highest_return": p.highest_return,
                 "highest_price": p.highest_price} for p in engine.positions]
        return {"action": "sell" if sells else "idle", "sells": sells, "held": held,
                "reason_text": f"卖出 {len(sells)} 只" if sells else "持仓暂无卖出信号"}

    def _notional_position(self, s: dict) -> Position:
        """用 DB 信号记录构造 notional Position（qty=100），供 evaluate_sell/break_even 精确对齐。"""
        cost = s["entry_price"] * 100 + fee(s["entry_price"] * 100, "BUY", self.cfg)
        return Position(s["code"], s["name"], 100, s["entry_date"], s["entry_price"], cost,
                        s["entry_reason_code"], s["entry_reason_text"], s["entry_signal"],
                        highest_return=s["highest_return"],
                        highest_price=max(s["highest_price"], s["entry_price"]),
                        took_profit_half=bool(s["took_profit_half"]))
