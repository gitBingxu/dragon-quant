from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from dragon_quant.live_trade.row_builder import build_live_row
from dragon_quant.providers.tencent import TencentProvider
from dragon_quant.review_account.data import MarketData, historical_events
from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.market import SHANGHAI, DataCoverageError, at, bar_times, calendar_start, collect_candidates
from dragon_quant.review_account.models import AccountState, MarketEvent, StrategyConfig
from dragon_quant.storage import db


class LiveTrader:
    def __init__(self, account: dict, cfg: StrategyConfig, quote_provider=None, kline_provider=None, data=None):
        self.account, self.cfg = account, StrategyConfig.from_dict(cfg.to_json_dict())
        self.quotes = quote_provider or TencentProvider()
        self.data = data or MarketData(provider=kline_provider)

    def process_event(self, event: MarketEvent, command="buy") -> dict:
        raw, revision = db.load_live_engine_state(self.account)
        state = AccountState.from_dict(raw)
        result = TradingEngine(self.cfg, state).step(event)
        if result["reason_code"] != "already_processed":
            db.save_live_engine_step(self.account["id"], revision, state.to_dict(), result, command)
        self.account["cash"] = state.cash
        return result

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
        raw, _ = db.load_live_engine_state(self.account)
        if raw["trade_date"] and day < raw["trade_date"]:
            raise ValueError("不能向已有账户回放更早日期，请使用新的 --account")
        if day < today and clock and raw["last_event"] > at(day, clock):
            raise ValueError("不能回放到已处理时点之前")
        candidates = collect_candidates(calendar, day, self.cfg, db.get_dragons_by_date) if command == "buy" else []
        codes = {c["code"] for c in candidates} | {p["code"] for p in raw["positions"]}
        if day < today:
            if not clock:
                raise ValueError("历史日期必须提供 --at HH:MM，且仅使用对应历史行情")
            target = at(day, clock)
            events = [e for e in historical_events(day, candidates, codes, self.data)
                      if e.timestamp <= target and e.timestamp > raw["last_event"]]
            trades, positions, details = [], [], []
            for event in events:
                event.allow_buy = command == "buy"
                result = self.process_event(event, command)
                trades.extend(result["trades"])
                positions.extend(result["positions"])
                details.extend(result["details"])
            result = {"trades": trades, "positions": positions, "details": details,
                      "reason_code": "historical_replay", "pending": db.load_live_engine_state(self.account)[0]["pending"]}
        else:
            if day != today or clock:
                raise ValueError("实时执行只允许今日且不指定 --at；历史 --at 仅用于回放")
            ts = int(now.timestamp() * 1000)
            time = now.strftime("%H:%M:%S")
            if not ("09:30:00" <= time < "11:30:01" or "13:00:00" <= time < "15:06:00"):
                if time < "09:30:00":
                    raise ValueError("当前尚未开盘（09:30 开始），请开盘后执行")
                if "11:30:01" <= time < "13:00:00":
                    raise ValueError("当前午间休市（11:30–13:00），请 13:00 后执行")
                raise ValueError("当前已收盘，请在交易时段执行，或用 --date/--at 回放历史")
            completed = [t for t in bar_times(day) if t <= ts]
            phase = ("close" if time >= "15:00:00" else "open" if time < "09:31:00"
                     else "late" if time >= "14:55:00" else "bar")
            if not completed and phase != "open":
                phase = "fill"
            rows = {}
            for code in sorted(codes):
                q = self.quotes.get_quote(code)
                fetched_at = int(datetime.now(SHANGHAI).timestamp() * 1000)
                age_limit = 360_000 if phase == "close" else self.cfg.quote_max_age_seconds * 1000
                if not q or not q.timestamp or not 0 <= fetched_at - q.timestamp <= age_limit:
                    raise DataCoverageError(f"{code} 实时行情缺失或时间戳过期，不执行本次账户变更")
                if completed and q.timestamp < completed[-1]:
                    raise DataCoverageError(f"{code} 报价早于最新已完成K线，等待刷新")
                bars = self.data.intraday(code, day, completed[-1]) if completed else []
                rows[code] = build_live_row(self.data.daily(code, day), q, day, bars, ts, phase)
                if phase == "close":
                    rows[code]["execution_price"] = None
            completed_at = int(datetime.now(SHANGHAI).timestamp() * 1000)
            if any(not 0 <= completed_at - r["execution_timestamp"] <= age_limit for r in rows.values()):
                raise DataCoverageError("取数耗时导致报价过期，请重新执行")
            if phase != "close" and (completed_at >= at(day, "15:00")
                    or at(day, "11:30") < completed_at < at(day, "13:00")):
                raise DataCoverageError("取数结束时已休市，不执行过期委托")
            for r in rows.values():
                r["observed_at"] = completed_at
            event = MarketEvent(completed_at, phase, rows, candidates, command == "buy")
            result = self.process_event(event, command)
        trades = result["trades"]
        return {**result, "trades": [asdict(t) for t in trades],
                "action": "buy" if any(t.side == "BUY" for t in trades) else "sell" if trades else "idle",
                "reason_text": "已执行纸上交易" if trades else "等待新信号或后续可执行报价",
                "results": [{"code": t.code, "name": t.name, "action": t.side.lower(),
                             "reason_code": t.reason_code, "reason_text": t.reason_text, "trade": asdict(t)} for t in trades]}
