from __future__ import annotations

from datetime import datetime

from dragon_quant.live_trade.shadow import build_shadow_state
from dragon_quant.live_trade.signal_engine import SignalEngine
from dragon_quant.review_account.data import MarketData, historical_events
from dragon_quant.review_account.market import SHANGHAI, at, bar_times, calendar_start, collect_candidates, load_market_sentiment
from dragon_quant.storage import db


class LiveTrader:
    """buy/sell 命令的信号执行器：回放当日事件流，复用 SignalEngine，纯信号记账。"""

    def __init__(self, cfg, source: str = "v2", data=None):
        from dragon_quant.review_account.models import StrategyConfig
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
        return self._run_sell(day, target, until, calendar)

    # ── 数据分级：候选缺日K是买入前提缺失（抛错），持仓缺失则降级剔除 ──

    def _usable(self, day: str, codes: set[str], degraded: list[str], label: str) -> set[str]:
        usable = set()
        for code in sorted(codes):
            try:
                self.data.daily(code, day)
            except Exception:
                degraded.append(f"{label} {code} 缺少日K，已从当日回放剔除")
                continue
            usable.add(code)
        return usable

    @staticmethod
    def _quality(events_rows: dict, current: str) -> str:
        if not events_rows:
            return current
        return next(iter(events_rows.values())).get("data_quality", current)

    def _run_buy(self, day, target, until, calendar):
        signals = db.list_all_signals(source=self.source)
        shadow = build_shadow_state(signals, self.cfg, day, as_of=target)
        sentiment = load_market_sentiment(calendar, day, self.cfg)
        shadow.sentiment_pct = sentiment
        positions = shadow.positions
        held_codes = {p.code for p in positions}
        candidates = collect_candidates(calendar, day, self.cfg, db.get_dragons_by_date)
        candidate_codes = {c["code"] for c in candidates}
        degraded: list[str] = []
        usable_positions = self._usable(day, held_codes - candidate_codes, degraded, "持仓")
        codes = candidate_codes | usable_positions
        engine = SignalEngine(self.cfg, positions=positions, held_codes=held_codes,
                              sentiment_pct=sentiment, account=shadow, evaluate_sells=False)
        buys, details = [], []
        quality = "strict_5min"
        for event in historical_events(day, candidates, codes, self.data, until=until):
            if event.timestamp > target:
                break
            event.allow_buy = True
            quality = self._quality(event.rows, quality)
            result = engine.step(event)
            buys.extend(result["buys"])
            details.extend(result["details"])
        for b in buys:
            _, created = db.insert_signal(b["code"], b["name"], b["entry_date"], b["entry_price"],
                                          b["reason_code"], b["reason_text"], b["signal"],
                                          self.source, qty=b["qty"])
            b["persisted"] = created
        # 未落库的买入（命中同 code 的另一条 open 行）不回写峰值，避免写到无关信号上。
        skipped_peaks = {b["code"] for b in buys if not b["persisted"]}
        for p in engine.positions:
            if p.code not in skipped_peaks:
                db.update_signal_peaks(p.code, p.highest_return, p.highest_price)
        return {"action": "buy" if buys else "idle", "buys": buys, "details": details,
                "sentiment_pct": sentiment, "data_quality": quality,
                "degraded": degraded, "fired_gate": engine.fired_gate,
                "reason_text": f"买入 {len(buys)} 只" if buys else "候选池暂无触发买点的标的"}

    def _run_sell(self, day, target, until, calendar):
        signals = db.list_all_signals(source=self.source)
        shadow = build_shadow_state(signals, self.cfg, day, as_of=target)
        positions = shadow.positions
        degraded: list[str] = []
        try:
            candidates = collect_candidates(calendar, day, self.cfg, db.get_dragons_by_date)
        except Exception as exc:  # 卖出是风控路径，不因候选池问题整体失败
            candidates = []
            degraded.append(f"候选池读取失败（{exc}），仅按持仓判定")
        position_codes = {p.code for p in positions}
        candidate_codes = self._usable(day, {c["code"] for c in candidates} - position_codes,
                                       degraded, "候选")
        codes = position_codes | candidate_codes
        engine = SignalEngine(self.cfg, positions=positions)
        sells = []
        quality = "strict_5min"
        for event in historical_events(day, candidates, codes, self.data, until=until):
            if event.timestamp > target:
                break
            event.allow_buy = False
            quality = self._quality(event.rows, quality)
            result = engine.step(event)
            sells.extend(result["sells"])
        for s in sells:
            if s["partial"]:
                s["persisted"] = db.half_close_signal(
                    s["code"], s["exit_date"], s["exit_price"], s["qty"],
                    s["reason_code"], s["reason_text"], s["signal"])
            else:
                s["persisted"] = db.close_signal(
                    s["code"], s["exit_date"], s["exit_price"],
                    s["reason_code"], s["reason_text"], s["signal"], s["hold_days"])
        for p in engine.positions:
            db.update_signal_peaks(p.code, p.highest_return, p.highest_price)
        held = [{"code": p.code, "name": p.name, "highest_return": p.highest_return,
                 "highest_price": p.highest_price, "qty": p.qty,
                 "took_profit_half": p.took_profit_half} for p in engine.positions]
        return {"action": "sell" if sells else "idle", "sells": sells, "held": held,
                "data_quality": quality, "degraded": degraded,
                "reason_text": f"卖出 {len(sells)} 只" if sells else "持仓暂无卖出信号"}
