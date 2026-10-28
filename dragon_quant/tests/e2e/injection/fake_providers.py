"""Fake provider classes used by every E2E child process.

The real provider modules are replaced wholesale by an import finder keyed on
the module/class names, so *no* real transport code can be reached.  Every
method mirrors the production signature and either:

* returns a fixture-driven response from the active scenario JSON, converted
  into the real dataclass (``KBar`` / ``Quote`` / ``StockInfo`` /
  ``SectorPerformance``), or
* raises ``NotImplementedError`` exactly where the production provider does,
  or
* raises ``FakeScenarioMissing`` when the scenario has no entry.

The last case is the fail-closed guarantee: a missing fixture is a loud error,
never a silent fallback to a real provider, Yahoo or the network.

Only stdlib is imported at module level; application models are imported
lazily inside the conversion helpers.
"""

from __future__ import annotations

import base64
import functools
import json
import os
from pathlib import Path
from typing import Optional

_SCENARIO_CACHE: dict[str, dict] = {}


class FakeScenarioMissing(RuntimeError):
    """The scenario has no response for this provider/method pair."""


class FakeProviderError(RuntimeError):
    """The scenario explicitly configures an error for this call."""


# ─── call log ───────────────────────────────────────────────────────────────


def _summary(value: object):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        keys = [getattr(item, "key", None) for item in value]
        if any(key is not None for key in keys):
            return {"len": len(value), "keys": keys}
        return {"len": len(value)}
    if isinstance(value, dict):
        return {"keys": sorted(str(key) for key in value)}
    return {"type": type(value).__name__}


def _log(provider: str, method: str, **fields: object) -> None:
    path = os.environ.get("DQ_E2E_CALL_LOG")
    if not path:
        return
    record = {"provider": provider, "method": method, "pid": os.getpid()}
    record.update(fields)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


# ─── scenario loading ───────────────────────────────────────────────────────


def _default_scenario_path() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures" / "scenario_minimal.json"


def _scenario() -> dict:
    path = os.environ.get("DQ_E2E_SCENARIO") or str(_default_scenario_path())
    if path in _SCENARIO_CACHE:
        return _SCENARIO_CACHE[path]
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FakeScenarioMissing(f"E2E scenario 不可读: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FakeScenarioMissing(f"E2E scenario 顶层必须是对象: {path}")
    _SCENARIO_CACHE[path] = payload
    return payload


#: Scenario dispatch keys.  A method payload may be a plain response (M1A
#: behavior, unchanged) or an object with one of these keys, whose value maps
#: the named call argument to a nested response.  This lets one fixture serve
#: several codes/dates/categories without weakening the fail-closed contract:
#: a token missing from the table still raises ``FakeScenarioMissing``.
_DISPATCH_ARGS: dict[str, tuple[str, ...]] = {
    "by_code": ("code", "sector_code"),
    "by_date": ("date",),
    "by_category": ("category",),
    # ``str(bool)`` is the token, so the table keys are "True" / "False".
    "by_asc": ("asc",),
}


def _dispatch(spec: object, provider: str, method: str, args: dict) -> object:
    if not isinstance(spec, dict):
        return spec
    for key, names in _DISPATCH_ARGS.items():
        if key not in spec:
            continue
        table = spec[key]
        if not isinstance(table, dict):
            raise FakeProviderError(
                f"providers.{provider}.{method}.{key} 必须是对象")
        token = next(
            (args[name] for name in names if args.get(name) is not None), None)
        if token is None:
            raise FakeScenarioMissing(
                f"providers.{provider}.{method} 需要 {names[0]} 才能分派")
        token = str(token)
        if token not in table:
            raise FakeScenarioMissing(
                f"scenario 缺少 providers.{provider}.{method}.{key}[{token}]")
        return table[token]
    return spec


def _respond(provider: str, method: str, **args: object):
    _log(provider, method, args={key: _summary(value) for key, value in args.items()})
    providers = _scenario().get("providers")
    if not isinstance(providers, dict):
        raise FakeScenarioMissing(f"scenario 缺少 providers.{provider}.{method}")
    section = providers.get(provider)
    if not isinstance(section, dict) or method not in section:
        raise FakeScenarioMissing(f"scenario 缺少 providers.{provider}.{method}")
    spec = _dispatch(section[method], provider, method, args)
    if isinstance(spec, dict) and set(spec) == {"error"}:
        raise FakeProviderError(str(spec["error"]))
    if isinstance(spec, dict) and set(spec) == {"value"}:
        return spec["value"]
    return spec


# ─── model conversion (lazy application import) ────────────────────────────


def _model(name: str):
    from dragon_quant.infra import models as model_types

    return getattr(model_types, name)


_KBAR_DEFAULTS = {
    "timestamp": 0,
    "volume": 0.0,
    "open": 0.0,
    "high": 0.0,
    "low": 0.0,
    "close": 0.0,
    "chg": 0.0,
    "pct": 0.0,
    "turnover": 0.0,
    "amount": 0.0,
}

_QUOTE_DEFAULTS = {
    "code": "",
    "name": "",
    "price": 0.0,
    "prev_close": 0.0,
    "open_px": 0.0,
    "high": 0.0,
    "low": 0.0,
    "pct": 0.0,
    "chg": 0.0,
    "turnover_rate": 0.0,
    "amplitude": 0.0,
    "volume": 0.0,
    "amount": 0.0,
    "market_cap": 0.0,
    "float_market_cap": 0.0,
    "volume_ratio": 0.0,
    "pe": 0.0,
    "limit_up": 0.0,
    "limit_down": 0.0,
    "avg_price": 0.0,
    "timestamp": 0,
    "main_net_amount": None,
}

_STOCK_DEFAULTS = {
    "code": "",
    "name": "",
    "exchange": "",
    "sector_code": "",
    "sector_name": "",
    "pct": 0.0,
    "price": 0.0,
    "five_day_return": 0.0,
}

_SECTOR_DEFAULTS = {
    "code": "",
    "name": "",
    "pct": 0.0,
    "amplitude": 0.0,
    "turnover_rate": 0.0,
    "category": "",
    "source": "",
}


def _build(model_name: str, item: object, defaults: dict):
    if not isinstance(item, dict):
        raise FakeProviderError(f"{model_name} 场景项必须是对象: {item!r}")
    cls = _model(model_name)
    fields = cls.__dataclass_fields__
    unknown = sorted(set(item) - set(fields))
    if unknown:
        raise FakeProviderError(f"{model_name} 场景含未知字段: {unknown!r}")
    data = dict(defaults)
    data.update(item)
    return cls(**data)


def _require_list(payload: object, label: str) -> list:
    if not isinstance(payload, list):
        raise FakeProviderError(f"{label} 场景必须是数组: {payload!r}")
    return payload


def _require_dict(payload: object, label: str) -> dict:
    if not isinstance(payload, dict):
        raise FakeProviderError(f"{label} 场景必须是对象: {payload!r}")
    return payload


def _dict_rows(payload: object, label: str) -> list[dict]:
    rows = _require_list(payload, label)
    for row in rows:
        if not isinstance(row, dict):
            raise FakeProviderError(f"{label} 场景数组项必须是对象: {row!r}")
    return rows


def _logged_method(fn):  # type: ignore[no-untyped-def]
    """Mirror production ``logger.api`` accounting for fixture responses.

    Real providers report every transport call to the ``ScanLogger``; without
    this the scan baseline would always see ``api_stats.total == 0`` and the
    call contract would go unverified.  ``NotImplementedError`` is not a
    transport call and is left unlogged, exactly like production.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        method = fn.__name__
        try:
            result = fn(self, *args, **kwargs)
        except NotImplementedError:
            raise
        except (FakeProviderError, FakeScenarioMissing) as exc:
            if self._logger is not None:
                self._logger.api(self.provider, method, ok=False,
                                 elapsed_ms=0.0, error=str(exc))
            raise
        if self._logger is not None:
            self._logger.api(self.provider, method, ok=True, elapsed_ms=0.0)
        return result

    return wrapper


def _instrument_class(cls) -> None:  # type: ignore[no-untyped-def]
    for name, value in list(vars(cls).items()):
        if name.startswith("get_") or name == "batch_get_quotes":
            if callable(value):
                setattr(cls, name, _logged_method(value))


class _FakeProvider:
    provider = "base"

    def __init_subclass__(cls, **kwargs):  # type: ignore[no-untyped-def]
        super().__init_subclass__(**kwargs)
        _instrument_class(cls)

    def __init__(self) -> None:
        self._logger = None
        _log(self.provider, "__init__")

    def set_logger(self, logger) -> None:  # type: ignore[no-untyped-def]
        self._logger = logger

    @property
    def name(self) -> str:
        raise NotImplementedError(f"{self.provider} 未声明 name")

    # ─── required StockProvider surface (fixture-driven) ──────────────────

    def get_sector_ranking(self, asc: bool = False,
                           category: str = "hy"):  # type: ignore[no-untyped-def]
        payload = _respond(self.provider, "get_sector_ranking",
                           asc=asc, category=category)
        return [
            _build("SectorPerformance", item, _SECTOR_DEFAULTS)
            for item in _require_list(payload, "get_sector_ranking")
        ]

    def get_sector_components(self, sector_code: str, page: int = 1,
                              all_pages: bool = False,
                              page_size: int = 50,
                              category: str = "hy"):  # type: ignore[no-untyped-def]
        payload = _respond(
            self.provider, "get_sector_components",
            sector_code=sector_code, page=page, all_pages=all_pages,
            page_size=page_size, category=category,
        )
        return [
            _build("StockInfo", item, _STOCK_DEFAULTS)
            for item in _require_list(payload, "get_sector_components")
        ]

    def get_sector_5min_kline(self, sector_code: str, bars: int = 100,
                              category: str = "hy"):  # type: ignore[no-untyped-def]
        payload = _respond(
            self.provider, "get_sector_5min_kline",
            sector_code=sector_code, bars=bars, category=category,
        )
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_sector_5min_kline")
        ]

    def get_kline(self, code: str, days: int = 20):  # type: ignore[no-untyped-def]
        payload = _respond(self.provider, "get_kline", code=code, days=days)
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_kline")
        ]

    def get_5min_kline(self, code: str, bars: int = 96):  # type: ignore[no-untyped-def]
        payload = _respond(self.provider, "get_5min_kline", code=code, bars=bars)
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_5min_kline")
        ]

    def get_quote(self, code: str) -> Optional[object]:
        payload = _respond(self.provider, "get_quote", code=code)
        if payload is None:
            return None
        return _build("Quote", payload, _QUOTE_DEFAULTS)

    # ─── optional StockProvider surface (production default: not implemented)

    def get_sector_1min_kline(self, sector_code: str, bars: int = 240,
                              category: str = "hy"):  # type: ignore[no-untyped-def]
        raise NotImplementedError(f"{self.provider} 不提供板块当日 1 分 K")

    def get_sector_5min_kline_history(self, sector_code: str, days: int = 10,
                                      category: str = "hy"):  # type: ignore[no-untyped-def]
        raise NotImplementedError(f"{self.provider} 不提供板块历史 5 分 K")

    def get_minute_kline(self, code: str):  # type: ignore[no-untyped-def]
        raise NotImplementedError(f"{self.provider} 不提供个股 1 分 K")

    def get_5min_kline_for(self, code: str, target_ts: int,
                           bars_before: int = 48, bars_after: int = 96,
                           fq_type: str = "after"):  # type: ignore[no-untyped-def]
        raise NotImplementedError(f"{self.provider} 不提供指定日 5 分 K")

    def batch_get_quotes(self, codes: list[str]):  # type: ignore[no-untyped-def]
        raise NotImplementedError(f"{self.provider} 不提供批量行情")

    def batch_get_daily_klines(self, codes: list[str], days: int = 120,
                               end_date: str | None = None):
        raise NotImplementedError(f"{self.provider} 不提供批量日 K")

    def get_index_daily(self, code: str, days: int = 120):
        raise NotImplementedError(f"{self.provider} 不提供指数日 K")

    def get_market_breadth_rows(self, count: int = 6000) -> list[dict]:
        raise NotImplementedError(f"{self.provider} 不提供全 A 市场宽度")

    def get_fg_boards(self, names: list[str]) -> dict[str, dict]:
        raise NotImplementedError(f"{self.provider} 不提供 FG 板块批量成员")

    def get_limit_up_pool(self, date: str) -> dict:
        raise NotImplementedError(f"{self.provider} 不提供涨停池")

    def get_block_top(self, date: str) -> dict:
        raise NotImplementedError(f"{self.provider} 不提供题材主线榜")

    def get_announcements(self, code: str, count: int = 30, page: int = 1) -> list[dict]:
        raise NotImplementedError(f"{self.provider} 不提供公告")

    def get_macro_quotes(self, specs: list, trade_date: str) -> dict[str, dict]:
        raise NotImplementedError(f"{self.provider} 不提供宏观报价")

    def get_auction_snapshot(self, code: str) -> Optional[dict]:
        raise NotImplementedError(f"{self.provider} 不提供竞价终态")

    def get_5min_kline_for_date(self, code: str, date: str):
        raise NotImplementedError(f"{self.provider} 不提供指定日 5 分钟 K")


# Base-class methods must be instrumented once; subclasses override a few and
# are instrumented by ``__init_subclass__`` when they are defined.
_instrument_class(_FakeProvider)


class TdxProvider(_FakeProvider):
    provider = "tdx"

    @property
    def name(self) -> str:
        return "tdx"

    def get_sector_1min_kline(self, sector_code: str, bars: int = 240,
                              category: str = "hy"):  # type: ignore[no-untyped-def]
        payload = _respond(
            self.provider, "get_sector_1min_kline",
            sector_code=sector_code, bars=bars, category=category,
        )
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_sector_1min_kline")
        ]

    def get_sector_5min_kline_history(self, sector_code: str, days: int = 10,
                                      category: str = "hy"):  # type: ignore[no-untyped-def]
        payload = _respond(
            self.provider, "get_sector_5min_kline_history",
            sector_code=sector_code, days=days, category=category,
        )
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_sector_5min_kline_history")
        ]

    def get_minute_kline(self, code: str):  # type: ignore[no-untyped-def]
        payload = _respond(self.provider, "get_minute_kline", code=code)
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_minute_kline")
        ]

    def batch_get_quotes(self, codes: list[str]):  # type: ignore[no-untyped-def]
        payload = _respond(self.provider, "batch_get_quotes", codes=codes)
        return [
            _build("Quote", item, _QUOTE_DEFAULTS)
            for item in _require_list(payload, "batch_get_quotes")
        ]

    def batch_get_daily_klines(self, codes: list[str], days: int = 120,
                               end_date: str | None = None):  # type: ignore[no-untyped-def]
        payload = _respond(self.provider, "batch_get_daily_klines",
                           codes=codes, days=days, end_date=end_date)
        data = _require_dict(payload, "batch_get_daily_klines")
        wanted = set(codes)
        return {
            code: [_build("KBar", item, _KBAR_DEFAULTS) for item in rows]
            for code, rows in data.items()
            if code in wanted
        }

    def get_market_breadth_rows(self, count: int = 6000) -> list[dict]:
        payload = _respond(self.provider, "get_market_breadth_rows", count=count)
        return _dict_rows(payload, "get_market_breadth_rows")

    def get_fg_boards(self, names: list[str]) -> dict[str, dict]:
        payload = _respond(self.provider, "get_fg_boards", names=names)
        return _require_dict(payload, "get_fg_boards")

    def get_announcements(self, code: str, count: int = 30, page: int = 1) -> list[dict]:
        payload = _respond(
            self.provider, "get_announcements", code=code, count=count, page=page
        )
        return _dict_rows(payload, "get_announcements")

    def get_macro_quotes(self, specs: list, trade_date: str) -> dict[str, dict]:
        payload = _respond(
            self.provider, "get_macro_quotes", specs=specs, trade_date=trade_date
        )
        return _require_dict(payload, "get_macro_quotes")

    def get_auction(self, code: str) -> list[dict]:
        payload = _respond(self.provider, "get_auction", code=code)
        return _dict_rows(payload, "get_auction")

    def get_auction_snapshot(self, code: str) -> Optional[dict]:
        payload = _respond(self.provider, "get_auction_snapshot", code=code)
        if payload is None:
            return None
        return _require_dict(payload, "get_auction_snapshot")

    def get_5min_kline_for_date(self, code: str, date: str):  # type: ignore[no-untyped-def]
        payload = _respond(
            self.provider, "get_5min_kline_for_date", code=code, date=date
        )
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_5min_kline_for_date")
        ]

    def get_finance_info(self, code: str) -> dict:
        payload = _respond(self.provider, "get_finance_info", code=code)
        return _require_dict(payload, "get_finance_info")

    def get_financial_files(self, host=None) -> list[dict]:  # type: ignore[no-untyped-def]
        payload = _respond(self.provider, "get_financial_files", host=host)
        return _dict_rows(payload, "get_financial_files")

    def get_financial_records(self, filename: str, host=None) -> list[dict]:  # type: ignore[no-untyped-def]
        payload = _respond(
            self.provider, "get_financial_records", filename=filename, host=host
        )
        return _dict_rows(payload, "get_financial_records")

    def get_financial_file(self, filename: str, host=None) -> bytes:  # type: ignore[no-untyped-def]
        payload = _respond(
            self.provider, "get_financial_file", filename=filename, host=host
        )
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, str):
            return payload.encode("utf-8")
        if isinstance(payload, dict) and "text" in payload:
            return str(payload["text"]).encode("utf-8")
        if isinstance(payload, dict) and "base64" in payload:
            return base64.b64decode(str(payload["base64"]))
        raise FakeProviderError(f"get_financial_file 场景无法解码: {payload!r}")

    def get_xdxr_info(self, code: str) -> list[dict]:
        payload = _respond(self.provider, "get_xdxr_info", code=code)
        return _dict_rows(payload, "get_xdxr_info")

    def get_fg_board_members(self, board_name: str) -> list[dict]:
        payload = _respond(
            self.provider, "get_fg_board_members", board_name=board_name
        )
        return _dict_rows(payload, "get_fg_board_members")


class THSProvider(_FakeProvider):
    provider = "ths"

    @property
    def name(self) -> str:
        return "ths"

    def get_kline(self, code: str, days: int = 20):  # type: ignore[no-untyped-def]
        raise NotImplementedError("同花顺 provider 不提供个股 K 线")

    def get_5min_kline(self, code: str, bars: int = 96):  # type: ignore[no-untyped-def]
        raise NotImplementedError("同花顺 provider 不提供个股 5 分 K 线")

    def get_quote(self, code: str) -> Optional[object]:
        raise NotImplementedError("同花顺 provider 不提供个股行情")

    def get_sector_1min_kline(self, sector_code: str, bars: int = 240,
                              category: str = "hy"):  # type: ignore[no-untyped-def]
        payload = _respond(
            self.provider, "get_sector_1min_kline",
            sector_code=sector_code, bars=bars, category=category,
        )
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_sector_1min_kline")
        ]

    def get_sector_5min_kline_history(self, sector_code: str, days: int = 10,
                                      category: str = "hy"):  # type: ignore[no-untyped-def]
        payload = _respond(
            self.provider, "get_sector_5min_kline_history",
            sector_code=sector_code, days=days, category=category,
        )
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_sector_5min_kline_history")
        ]

    def get_limit_up_pool(self, date: str) -> dict:
        payload = _respond(self.provider, "get_limit_up_pool", date=date)
        return _require_dict(payload, "get_limit_up_pool")

    def get_block_top(self, date: str) -> dict:
        payload = _respond(self.provider, "get_block_top", date=date)
        return _require_dict(payload, "get_block_top")


class XueqiuProvider(_FakeProvider):
    provider = "xueqiu"

    @property
    def name(self) -> str:
        return "xueqiu"

    def get_kline(self, code: str, days: int = 20,
                  fq_type: str = "after"):  # type: ignore[no-untyped-def]
        payload = _respond(
            self.provider, "get_kline", code=code, days=days, fq_type=fq_type
        )
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_kline")
        ]

    def get_5min_kline_for(self, code: str, target_ts: int,
                           bars_before: int = 48, bars_after: int = 96,
                           fq_type: str = "after"):  # type: ignore[no-untyped-def]
        payload = _respond(
            self.provider, "get_5min_kline_for",
            code=code, target_ts=target_ts, bars_before=bars_before,
            bars_after=bars_after, fq_type=fq_type,
        )
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_5min_kline_for")
        ]

    def get_minute_kline(self, code: str):  # type: ignore[no-untyped-def]
        payload = _respond(self.provider, "get_minute_kline", code=code)
        return [
            _build("KBar", item, _KBAR_DEFAULTS)
            for item in _require_list(payload, "get_minute_kline")
        ]

    def get_macro_quotes(self, specs: list, trade_date: str) -> dict[str, dict]:
        payload = _respond(
            self.provider, "get_macro_quotes", specs=specs, trade_date=trade_date
        )
        return _require_dict(payload, "get_macro_quotes")

    def get_sector_ranking(self, asc: bool = False,
                           category: str = "hy"):  # type: ignore[no-untyped-def]
        raise NotImplementedError("雪球不提供板块排行")

    def get_sector_components(self, sector_code: str, page: int = 1,
                              all_pages: bool = False,
                              page_size: int = 50,
                              category: str = "hy"):  # type: ignore[no-untyped-def]
        raise NotImplementedError("雪球不提供板块成分股")

    def get_sector_5min_kline(self, sector_code: str, bars: int = 100,
                              category: str = "hy"):  # type: ignore[no-untyped-def]
        raise NotImplementedError("雪球不提供板块 K 线")


class TencentProvider(_FakeProvider):
    provider = "tencent"

    @property
    def name(self) -> str:
        return "tencent"

    def get_kline(self, code: str, days: int = 20):  # type: ignore[no-untyped-def]
        """Mirror production: gtimg has no daily K line, so this returns ``[]``."""

        _log(self.provider, "get_kline", args={"code": _summary(code),
                                               "days": _summary(days)})
        return []

    def batch_get_quotes(self, codes: list[str]):  # type: ignore[no-untyped-def]
        payload = _respond(self.provider, "batch_get_quotes", codes=codes)
        return [
            _build("Quote", item, _QUOTE_DEFAULTS)
            for item in _require_list(payload, "batch_get_quotes")
        ]

    def get_macro_quotes(self, specs: list, trade_date: str) -> dict[str, dict]:
        payload = _respond(
            self.provider, "get_macro_quotes", specs=specs, trade_date=trade_date
        )
        return _require_dict(payload, "get_macro_quotes")

    def get_sector_ranking(self, asc: bool = False,
                           category: str = "hy"):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def get_sector_components(self, sector_code: str, page: int = 1,
                              all_pages: bool = False,
                              page_size: int = 50,
                              category: str = "hy"):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def get_sector_5min_kline(self, sector_code: str, bars: int = 100,
                              category: str = "hy"):  # type: ignore[no-untyped-def]
        raise NotImplementedError


PROVIDER_CLASSES = {
    "tdx": TdxProvider,
    "ths": THSProvider,
    "xueqiu": XueqiuProvider,
    "tencent": TencentProvider,
}


# ─── import-time symbols consumed by production modules ────────────────────


def is_available() -> bool:
    _log("tdx", "is_available")
    return True


def warn_if_unavailable() -> bool:
    _log("tdx", "warn_if_unavailable")
    return False


def drain_fetch_failures() -> list:
    _log("ths", "drain_fetch_failures")
    return []
