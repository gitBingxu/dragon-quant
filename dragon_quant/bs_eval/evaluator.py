"""单只 dragon 的假想回放器：只做批量驱动与窗口度量。

买卖判定与成交语义唯一实现在 ``review_account``（``evaluate_buy`` /
``evaluate_sell`` / ``SignalEngine``）；本模块不复制任何买点、卖点或成交规则。
"""

from __future__ import annotations

from typing import Optional

from dragon_quant.bs_eval import constants
from dragon_quant.bs_eval.candidates import eval_candidate
from dragon_quant.review_account.data import historical_events
from dragon_quant.review_account.live_trade.signal_engine import SignalEngine
from dragon_quant.review_account.market import (
    DataCoverageError,
    daily_bar,
    event_date,
)
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review.strict_metrics import window_metrics


def _quality_of(event_row: Optional[dict]) -> Optional[str]:
    """单个事件行的数据质量：日 K 兜底标记，否则视为严格 5 分钟。"""

    if event_row is None:
        return None
    return "daily_fallback" if event_row.get("data_quality") == "daily_fallback" else "strict_5min"


def _aggregate_quality(day_qualities: dict[str, str],
                       replay_days: int) -> Optional[str]:
    """行级 data_quality：全 strict / 全 fallback / 混合 / 未回放。"""

    if not day_qualities:
        return None
    fallback = sum(1 for value in day_qualities.values() if value == "daily_fallback")
    strict = len(day_qualities) - fallback
    if fallback == 0:
        return "strict_5min"
    if strict == 0:
        return "daily_fallback"
    return "mixed"


def _klines(history: list) -> list[dict]:
    return [
        {
            "date": event_date(bar.timestamp),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
        }
        for bar in history
    ]


def _metrics_payload(metrics: Optional[dict]) -> dict:
    if metrics is None:
        return {
            "max_return_5d": None,
            "max_drawdown_5d": None,
            "max_return_hold_days": None,
            "partial": True,
        }
    return metrics


class BsEvaluator:
    """逐 dragon 回放：一个 ``(trade_date, code)`` 一个引擎实例。"""

    def __init__(self, cfg: StrategyConfig, data, calendar: list[str],
                 replay_end: str):
        self.cfg = cfg
        self.data = data
        self.calendar = list(calendar)
        self.calendar_set = set(calendar)
        self.replay_end = replay_end

    # ─── 对外入口 ────────────────────────────────────────────────────────

    def evaluate(self, row: dict) -> dict:
        """回放一只龙头，返回 ``bs_signals`` 业务字段（不落库）。"""

        code = row["code"]
        trade_date = row["trade_date"]
        name = row.get("name") or ""

        future_days = [day for day in self.calendar if day > trade_date]
        buy_window = future_days[: constants.BUY_WINDOW_DAYS]
        if len(buy_window) < constants.BUY_WINDOW_DAYS:
            return self._payload(
                row, eval_status=constants.STATUS_PENDING,
                eval_note=constants.NOTE_BUY_WINDOW_NOT_READY, is_terminal=0,
            )

        try:
            history = self.data.daily(code, trade_date)
        except DataCoverageError as exc:
            return self.cannot_evaluate(row, constants.NOTE_DAILY_MISSING, str(exc))

        prior = [bar for bar in history if event_date(bar.timestamp) < trade_date]
        if len(prior) < 20:
            return self.cannot_evaluate(
                row, constants.NOTE_WARMUP_INSUFFICIENT,
                f"预热日 K 不足 20 日（{len(prior)}）")

        klines = _klines(history)
        engine = SignalEngine(
            self.cfg, positions=None, held_codes=None, sentiment_pct=None,
            account=None, evaluate_sells=True,
        )
        candidate = eval_candidate(row)

        bought: Optional[dict] = None
        half_leg: Optional[dict] = None
        final_sell: Optional[dict] = None
        day_qualities: dict[str, str] = {}
        fallback_days = 0
        suspended_days = 0
        replay_days = 0
        last_replayed_day: Optional[str] = None

        buy_window_end = buy_window[-1]
        for day in future_days:
            if bought is None and day > buy_window_end:
                break
            if bought is not None and final_sell is not None:
                break
            if bought is not None:
                days_after = sum(
                    1 for item in self.calendar
                    if bought["entry_date"] < item <= day)
                if days_after > constants.MAX_REPLAY_TRADING_DAYS:
                    break
            replay_days += 1
            last_replayed_day = day
            active = [candidate] if (bought is None and day in buy_window) else []
            try:
                events = list(historical_events(
                    day, active, {code}, self.data, daily_start=trade_date))
            except DataCoverageError:
                suspended_days += 1
                continue

            day_row_seen = False
            day_quality: Optional[str] = None
            for event in events:
                event.candidates = list(active) if bought is None else []
                event.allow_buy = bool(event.candidates)
                result = engine.step(event)
                event_row = event.rows.get(code)
                if event_row is not None:
                    day_row_seen = True
                    day_quality = _quality_of(event_row)
                for fill in result.get("buys", []):
                    if bought is None:
                        bought = fill
                for fill in result.get("sells", []):
                    if fill.get("partial"):
                        half_leg = fill
                    else:
                        final_sell = fill
            if day_row_seen and day_quality is not None:
                day_qualities[day] = day_quality
                if day_quality == "daily_fallback":
                    fallback_days += 1
            elif not day_row_seen:
                # 停牌/不可用日：无 rows，不伪造价格与质量。
                suspended_days += 1

        if bought is None:
            return self._payload(
                row, eval_status=constants.STATUS_NO_SIGNAL,
                is_terminal=1, replay_days=replay_days,
                fallback_days=fallback_days, suspended_days=suspended_days,
                data_quality=_aggregate_quality(day_qualities, replay_days),
            )

        payload = self._buy_payload(row, bought, history, klines, day_qualities)
        payload.update({
            "replay_days": replay_days,
            "fallback_days": fallback_days,
            "suspended_days": suspended_days,
            "data_quality": _aggregate_quality(day_qualities, replay_days),
            "buy_data_quality": day_qualities.get(bought["entry_date"]),
        })
        if half_leg is not None:
            payload.update({
                "sell_half_date": half_leg.get("exit_date"),
                "sell_half_price": half_leg.get("exit_price"),
                "sell_half_qty": half_leg.get("qty"),
                "sell_half_rule_code": half_leg.get("reason_code"),
            })

        if final_sell is None:
            days_after_buy = self._days_after(bought["entry_date"], last_replayed_day)
            capped = days_after_buy >= constants.MAX_REPLAY_TRADING_DAYS
            payload.update({
                "eval_status": constants.STATUS_OPEN,
                "eval_note": (constants.NOTE_HOLD_CAP_REACHED if capped else None),
                "is_terminal": 1 if capped else 0,
            })
            return payload

        return self._closed_payload(payload, final_sell, klines, day_qualities)

    # ─── 买入字段 ────────────────────────────────────────────────────────

    def _buy_payload(self, row: dict, bought: dict, history: list,
                     klines: list[dict],
                     day_qualities: dict[str, str]) -> dict:
        buy_date = bought["entry_date"]
        strict_price = bought.get("entry_price")
        bar = daily_bar(history, buy_date)
        loose_price = bar.low if bar is not None else None

        strict_metrics = (
            window_metrics(klines, self.calendar_set, buy_date, strict_price)
            if strict_price else None
        )
        loose_metrics = (
            window_metrics(klines, self.calendar_set, buy_date, loose_price)
            if loose_price else None
        )
        strict_value = _metrics_payload(strict_metrics)
        loose_value = _metrics_payload(loose_metrics)
        partial = (
            strict_metrics is None or strict_value["partial"]
            or (loose_price is not None
                and (loose_metrics is None or loose_value["partial"]))
        )
        hold_days = strict_value["max_return_hold_days"]
        if hold_days is None:
            hold_days = loose_value["max_return_hold_days"]
        return self._payload(
            row,
            eval_status=constants.STATUS_OPEN,
            is_terminal=0,
            buy_date=buy_date,
            buy_rule_code=bought.get("reason_code"),
            buy_rule_text=bought.get("reason_text"),
            buy_loose_price=loose_price,
            buy_strict_price=strict_price,
            buy_loose_max_return_5d=loose_value["max_return_5d"],
            buy_loose_max_drawdown_5d=loose_value["max_drawdown_5d"],
            buy_strict_max_return_5d=strict_value["max_return_5d"],
            buy_strict_max_drawdown_5d=strict_value["max_drawdown_5d"],
            buy_max_return_hold_days=hold_days,
            buy_window_partial=1 if partial else 0,
        )

    # ─── 卖出字段 ────────────────────────────────────────────────────────

    def _closed_payload(self, payload: dict, sell: dict, klines: list[dict],
                        day_qualities: dict[str, str]) -> dict:
        sell_date = sell.get("exit_date")
        sell_price = sell.get("exit_price")
        metrics = (
            window_metrics(klines, self.calendar_set, sell_date, sell_price)
            if sell_date and sell_price else None
        )
        value = _metrics_payload(metrics)
        partial = metrics is None or value["partial"]
        payload.update({
            "eval_status": constants.STATUS_CLOSED,
            "is_terminal": 0 if partial else 1,
            "sell_date": sell_date,
            "sell_rule_code": sell.get("reason_code"),
            "sell_rule_text": sell.get("reason_text"),
            "sell_price": sell_price,
            "sell_hold_days": sell.get("hold_days"),
            "sell_data_quality": day_qualities.get(sell_date),
            "sell_max_return_5d": value["max_return_5d"],
            "sell_max_drawdown_5d": value["max_drawdown_5d"],
            "sell_max_return_hold_days": value["max_return_hold_days"],
            "sell_window_partial": 1 if partial else 0,
        })
        return payload

    def cannot_evaluate(self, row: dict, note: str,
                        detail: Optional[str] = None) -> dict:
        """行级数据问题/异常的统一 payload（原因只记稳定码 + 短消息）。"""

        return self._payload(
            row, eval_status=constants.STATUS_CANNOT_EVALUATE,
            eval_note=note, is_terminal=0,
            data_quality=None,
        )

    # ─── 通用 payload ────────────────────────────────────────────────────

    def _payload(self, row: dict, *, eval_status: str, eval_note: Optional[str] = None,
                 is_terminal: int = 0, data_quality: Optional[str] = None,
                 replay_days: int = 0, fallback_days: int = 0,
                 suspended_days: int = 0, **fields) -> dict:
        payload = {
            "trade_date": row["trade_date"],
            "code": row["code"],
            "name": row.get("name") or "",
            "source": "v2",
            "eval_status": eval_status,
            "eval_note": eval_note,
            "is_terminal": is_terminal,
            "data_quality": data_quality,
            "replay_days": replay_days,
            "fallback_days": fallback_days,
            "suspended_days": suspended_days,
            "buy_date": None,
            "buy_rule_code": None,
            "buy_rule_text": None,
            "buy_loose_price": None,
            "buy_strict_price": None,
            "buy_data_quality": None,
            "buy_loose_max_return_5d": None,
            "buy_loose_max_drawdown_5d": None,
            "buy_strict_max_return_5d": None,
            "buy_strict_max_drawdown_5d": None,
            "buy_max_return_hold_days": None,
            "buy_window_partial": 0,
            "sell_date": None,
            "sell_rule_code": None,
            "sell_rule_text": None,
            "sell_price": None,
            "sell_hold_days": None,
            "sell_data_quality": None,
            "sell_half_date": None,
            "sell_half_price": None,
            "sell_half_qty": None,
            "sell_half_rule_code": None,
            "sell_max_return_5d": None,
            "sell_max_drawdown_5d": None,
            "sell_max_return_hold_days": None,
            "sell_window_partial": 0,
        }
        payload.update(fields)
        return payload

    def _days_after(self, entry_date: str, last_day: Optional[str]) -> int:
        if last_day is None:
            return 0
        return sum(1 for day in self.calendar if entry_date < day <= last_day)


__all__ = ["BsEvaluator"]
