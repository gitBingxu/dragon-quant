"""
通达信 TDX Provider — 首选数据链路（tmdx / easy_tdx，TCP 协议，无 Cookie 无反爬）

设计要点（见 providers/TDX_PROVIDER_技术方案.md）：
  - 惰性依赖：easy_tdx（tmdx 包）是必装依赖，但导入放在首次调用（避免 import 开销，
    并在缺失时优雅降级返回空/None，由 orchestrator 回退到老链路兜底）。
  - 连接模型：单个共享 ``MacClient``（MAC 拓展行情，含板块/复权K线/换手市值PE），
    内部 ``threading.Lock`` 串行化所有调用。注意 ``MacClient._reconnect()`` 会整体
    替换 ``self._conn``，因此「同一时刻仅一个线程调用」是正确性要求——本 provider
    用锁保证，不依赖外部 RateLimiter 是否串行。
  - 数据转换：DataFrame(dict records) → KBar/Quote/StockInfo/SectorPerformance，
    集中在模块级纯函数，便于不装 easy_tdx 也能单测。
  - 指数代码映射：``SH000001``(上证指数) → TDX ``999999``；``000001`` 仍是平安银行。
  - 板块口径：用通达信自有的行业板块(BoardType.HY, 881xxx)，与同花顺非同一分类
    （口径差异由主/备链路 fallback 承担，本方案不做统一）。

字段单位（实现期需用已知样本核对的点，见 §7.8）：
  - vol/amount/bid_volume/ask_volume：手 / 元（与腾讯 gtimg 同源同单位）
  - total_shares/float_shares：万股（据 bitmap 注释）；市值据此 ×1e4×price 推算
  - 涨跌幅 pct 无直接字段，由 close/pre_close 现算
"""

import math
import threading
import time
from typing import Any, Optional

from dragon_quant.models.types import KBar, Quote, SectorPerformance, StockInfo
from dragon_quant.providers.base import StockProvider

# ─── 惰性依赖 ──────────────────────────────────────────────────────────────

_easytdx: Any = None  # easy_tdx 模块；False=已尝试但不可用


def _import_easytdx() -> Any:
    """惰性导入 easy_tdx（tmdx 必装依赖）。缺失时返回 None 触发兜底回退。"""
    global _easytdx
    if _easytdx is None:
        try:
            import easy_tdx
            _easytdx = easy_tdx
        except ImportError:
            _easytdx = False
    return _easytdx or None


def is_available() -> bool:
    """easy_tdx 是否可用（缺失时主链路静默降级、不记回退事件）。"""
    return _import_easytdx() is not None


# 仓库指数别名 → TDX 代码（上证指数 SH000001 → 999999；000001 是平安银行不映射）
_INDEX_ALIAS = {"SH000001": "999999"}

# ─── 代码/市场解析 ──────────────────────────────────────────────────────────


def _market_of(code: str) -> int:
    """6 位代码 → 市场 int（对齐 easy_tdx.Market：SH=1 / SZ=0 / BJ=2）。"""
    c = code.upper()
    if c.startswith(("SH", "SZ", "BJ")):
        c = c[2:]
    if len(c) != 6:
        return 1  # 默认 SH
    if c.startswith(("881", "880", "999")):
        return 1  # SH：行业/概念板块指数、上证指数系列
    if c.startswith("399"):
        return 0  # SZ：深证指数系列
    if c.startswith(("920", "8")):
        return 2  # BJ
    if c.startswith("6"):
        return 1  # SH
    if c.startswith(("0", "3")):
        return 0  # SZ
    if c.startswith("900"):
        return 1  # SH B 股
    if c.startswith("200"):
        return 0  # SZ B 股
    return 1


def _resolve(code: str) -> tuple[int, str]:
    """(market_int, tdx_6位code)。处理指数别名。"""
    c = code.upper()
    if c in _INDEX_ALIAS:
        return _market_of(_INDEX_ALIAS[c]), _INDEX_ALIAS[c]
    if c.startswith(("SH", "SZ", "BJ")):
        c = c[2:]
    return _market_of(c), c

# ─── 安全数值 / 时间戳 ─────────────────────────────────────────────────────


def _f(v, default: float = 0.0) -> float:
    """float 转换，NaN/None/异常 → default。"""
    try:
        x = float(v)
        if math.isnan(x):
            return default
        return x
    except (TypeError, ValueError):
        return default


def _ts_ms(dt) -> int:
    """datetime / pd.Timestamp / iso 字符串 → 本地 epoch 毫秒。"""
    if dt is None:
        return 0
    try:
        import datetime as _dt
        if isinstance(dt, str):
            dt = _dt.datetime.fromisoformat(dt)
        return int(time.mktime((dt.year, dt.month, dt.day,
                                dt.hour, dt.minute, 0, 0, 0, -1))) * 1000
    except Exception:
        return 0

# ─── 数据转换（纯函数，可单测）──────────────────────────────────────────────


def _to_kbars(records: list[dict]) -> list[KBar]:
    """MAC kline DataFrame(records) → list[KBar]。

    records 键：datetime/open/high/low/close/vol/amount（+float_shares 忽略）。
    pct/chg 用前一根 close 推算（复权下等比缩放，涨幅百分比不变量，见 §7.9）。
    """
    bars: list[KBar] = []
    prev = 0.0
    for r in records:
        c = _f(r.get("close"))
        o = _f(r.get("open"))
        h = _f(r.get("high"))
        lo = _f(r.get("low"))
        v = _f(r.get("vol"))
        amt = _f(r.get("amount"))
        chg = (c - prev) if prev else 0.0
        pct = (chg / prev * 100.0) if prev else 0.0
        bars.append(KBar(
            timestamp=_ts_ms(r.get("datetime")),
            volume=v, open=o, high=h, low=lo, close=c,
            chg=chg, pct=pct, turnover=0.0, amount=amt,
        ))
        prev = c
    return bars


def _to_quote(r: dict) -> Optional[Quote]:
    """MAC 行情 DataFrame 行 → Quote。close<=0 视为无效返回 None。

    r 键（COMMON + QUOTE 字段集，小写 snake_case）：close/pre_close/open/high/low/
    vol/vol_ratio/amount/turnover/total_shares/float_shares/pe_dynamic/pe_ttm/
    buy_price_limit/sell_price_limit/bid_price/bid_volume/ask_volume 等。
    """
    close = _f(r.get("close"))
    if close <= 0:
        return None
    pre = _f(r.get("pre_close"))
    high = _f(r.get("high"))
    low = _f(r.get("low"))
    vol = _f(r.get("vol"))
    amt = _f(r.get("amount"))
    # 市值：total_shares/float_shares 据 bitmap 注释为「万股」，×1e4×price → 元。
    # 单位需用已知市值样本核对（§7.8），暂按万股口径推算。
    total_shares = _f(r.get("total_shares"))
    float_shares = _f(r.get("float_shares"))
    return Quote(
        code=str(r.get("code", "")),
        name=str(r.get("name", "")),
        price=close, prev_close=pre,
        open_px=_f(r.get("open")), high=high, low=low,
        pct=((close / pre - 1) * 100.0) if pre else 0.0,
        chg=(close - pre) if pre else 0.0,
        turnover_rate=_f(r.get("turnover")),
        amplitude=((high - low) / pre * 100.0) if pre else 0.0,
        volume=vol, amount=amt,
        market_cap=total_shares * 1e4 * close,
        float_market_cap=float_shares * 1e4 * close,
        volume_ratio=_f(r.get("vol_ratio")),
        pe=_f(r.get("pe_dynamic")) or _f(r.get("pe_ttm")),
        limit_up=_f(r.get("buy_price_limit")),
        limit_down=_f(r.get("sell_price_limit")),
        avg_price=(amt / (vol * 100.0)) if vol > 0 else 0.0,
        bid1_price=_f(r.get("bid_price")),
        bid1_volume=_f(r.get("bid_volume")),
        ask1_volume=_f(r.get("ask_volume")),
        timestamp=0,
    )


def _to_sector(r: dict) -> SectorPerformance:
    """board_list 行 → SectorPerformance（涨跌幅由 price/pre_close 现算）。"""
    price = _f(r.get("price"))
    pre = _f(r.get("pre_close"))
    pct = ((price / pre - 1) * 100.0) if pre else 0.0
    return SectorPerformance(
        code=str(r.get("code", "")),
        name=str(r.get("name", "")),
        pct=pct, amplitude=0.0, turnover_rate=0.0,
    )


def _to_stockinfo(r: dict, sector_code: str) -> StockInfo:
    """board_members 行 → StockInfo（close=现价，pct 由 close/pre_close 现算）。"""
    close = _f(r.get("close"))
    pre = _f(r.get("pre_close"))
    pct = ((close / pre - 1) * 100.0) if pre else 0.0
    return StockInfo(
        code=str(r.get("code", "")),
        name=str(r.get("name", "")),
        sector_code=sector_code,
        pct=pct, price=close,
    )

# ─── Provider ──────────────────────────────────────────────────────────────


class TdxProvider(StockProvider):

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._client: Any = None  # MacClient；False=永久不可用
        self._preset: Any = None  # easy_tdx PresetField

    @property
    def name(self) -> str:
        return "tdx"

    # ─── 连接 ───

    def _get_client(self):
        """惰性创建共享 MacClient（线程安全）。easy_tdx 缺失/连接失败 → None。"""
        if self._client is None:
            et = _import_easytdx()
            if et is None:
                self._client = False
                return None
            try:
                from easy_tdx.codec.bitmap import PresetField
                self._preset = PresetField
                client = et.MacClient.from_best_host()
                client.connect()
                self._client = client
            except Exception:
                self._client = False
                return None
        return self._client or None

    def _quote_fields(self):
        """完整 Quote 字段集：COMMON（换手/量比/市值/PE/涨跌停）+ QUOTE（买一卖一）。"""
        return self._preset.COMMON + self._preset.QUOTE

    # ─── 板块 ───

    def get_sector_ranking(self, asc: bool = False) -> list[SectorPerformance]:
        """行业板块涨跌幅排行（通达信 BoardType.HY，按涨跌幅排序）。"""
        t0 = time.time()
        try:
            et = _import_easytdx()
            if et is None:
                return []
            client = self._get_client()
            if client is None:
                return []
            with self._lock:
                df = client.get_board_list(
                    board_type=et.BoardType.HY,
                    count=10000,
                    sort_column=et.BoardSortColumn.CHANGE_PCT,
                )
            rows = ([_to_sector(r) for r in df.to_dict("records")]
                    if not df.empty else [])
            rows.sort(key=lambda s: s.pct, reverse=not asc)
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_ranking", ok=bool(rows),
                                 elapsed_ms=elapsed, note=f"n={len(rows)}")
            return rows
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_ranking", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            return []

    def get_sector_components(self, sector_code: str, page: int = 1,
                              all_pages: bool = False,
                              page_size: int = 50) -> list[StockInfo]:
        """板块成分股（通达信 board_members，按涨跌幅降序）。"""
        t0 = time.time()
        try:
            et = _import_easytdx()
            if et is None:
                return []
            client = self._get_client()
            if client is None:
                return []
            with self._lock:
                df = client.get_board_members(
                    board_symbol=sector_code,
                    sort_type=et.SortType.CHANGE_PCT,
                    sort_order=et.SortOrder.DESC,
                )
            rows = [_to_stockinfo(r, sector_code) for r in df.to_dict("records")] \
                if not df.empty else []
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_components", ok=bool(rows),
                                 elapsed_ms=elapsed, note=f"n={len(rows)}")
            return rows
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_components", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            return []

    def _index_kline(self, sector_code: str, period, count: int) -> list[KBar]:
        """板块指数 K 线（无复权；分钟线 vol 为 NaN→0，不影响价格曲线类评分）。"""
        et = _import_easytdx()
        if et is None:
            return []
        client = self._get_client()
        if client is None:
            return []
        market, code = _resolve(sector_code)
        with self._lock:
            df = client.get_stock_kline(
                market=market, code=code, period=period,
                count=count, adjust=et.Adjust.NONE,
            )
        if df.empty:
            return []
        return _to_kbars(df.to_dict("records"))

    def get_sector_5min_kline(self, sector_code: str, bars: int = 100) -> list[KBar]:
        t0 = time.time()
        try:
            kbars = self._index_kline(sector_code, _import_easytdx().Period.MIN_5, bars)
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_5min_kline", ok=bool(kbars),
                                 elapsed_ms=elapsed, note=f"n={len(kbars)}")
            return kbars[-bars:] if bars else kbars
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_5min_kline", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            return []

    def get_sector_1min_kline(self, sector_code: str, bars: int = 240) -> list[KBar]:
        t0 = time.time()
        try:
            kbars = self._index_kline(sector_code, _import_easytdx().Period.MIN_1, bars)
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_1min_kline", ok=bool(kbars),
                                 elapsed_ms=elapsed, note=f"n={len(kbars)}")
            return kbars[-bars:] if bars else kbars
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_1min_kline", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            return []

    def get_sector_5min_kline_history(self, sector_code: str,
                                      days: int = 10) -> list[KBar]:
        """板块近 days 个交易日的 5 分钟历史 K 线。"""
        t0 = time.time()
        try:
            kbars = self._index_kline(sector_code, _import_easytdx().Period.MIN_5,
                                      days * 48)
            if kbars and days:
                by_day: dict[str, list[KBar]] = {}
                for kb in kbars:
                    d = time.strftime("%Y%m%d", time.localtime(kb.timestamp / 1000))
                    by_day.setdefault(d, []).append(kb)
                keep = list(by_day.keys())[-days:]
                kbars = [kb for d in keep for kb in by_day[d]]
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_5min_history", ok=bool(kbars),
                                 elapsed_ms=elapsed, note=f"n={len(kbars)}")
            return kbars
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_5min_history", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            return []

    # ─── 个股 ───

    def get_kline(self, code: str, days: int = 20) -> list[KBar]:
        """个股日 K（前复权 QFQ）。"""
        t0 = time.time()
        try:
            et = _import_easytdx()
            if et is None:
                return []
            client = self._get_client()
            if client is None:
                return []
            market, c = _resolve(code)
            with self._lock:
                df = client.get_stock_kline(
                    market=market, code=c, period=et.Period.DAILY,
                    count=max(days, 1), adjust=et.Adjust.QFQ,
                )
            kbars = _to_kbars(df.to_dict("records")) if not df.empty else []
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "kline", ok=bool(kbars),
                                 elapsed_ms=elapsed, note=f"n={len(kbars)}")
            return kbars[-days:] if days else kbars
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "kline", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            return []

    def get_5min_kline(self, code: str, bars: int = 96) -> list[KBar]:
        t0 = time.time()
        try:
            et = _import_easytdx()
            if et is None:
                return []
            client = self._get_client()
            if client is None:
                return []
            market, c = _resolve(code)
            with self._lock:
                df = client.get_stock_kline(
                    market=market, code=c, period=et.Period.MIN_5,
                    count=bars, adjust=et.Adjust.QFQ,
                )
            kbars = _to_kbars(df.to_dict("records")) if not df.empty else []
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "5min_kline", ok=bool(kbars),
                                 elapsed_ms=elapsed, note=f"n={len(kbars)}")
            return kbars[-bars:] if bars else kbars
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "5min_kline", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            return []

    def get_minute_kline(self, code: str) -> list[KBar]:
        """当日 1 分钟 K（MIN_1，真实 OHLC；非分时接口）。"""
        t0 = time.time()
        try:
            et = _import_easytdx()
            if et is None:
                return []
            client = self._get_client()
            if client is None:
                return []
            market, c = _resolve(code)
            with self._lock:
                df = client.get_stock_kline(
                    market=market, code=c, period=et.Period.MIN_1,
                    count=240, adjust=et.Adjust.NONE,
                )
            kbars = _to_kbars(df.to_dict("records")) if not df.empty else []
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "minute_kline", ok=bool(kbars),
                                 elapsed_ms=elapsed, note=f"n={len(kbars)}")
            return kbars
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "minute_kline", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            return []

    # ─── 行情 ───

    def get_quote(self, code: str) -> Optional[Quote]:
        quotes = self.batch_get_quotes([code])
        return quotes[0] if quotes else None

    def batch_get_quotes(self, codes: list[str]) -> list[Quote]:
        """批量行情（MAC 自定义字段集，含五档封单）。单次最多 80 只，自动分片。"""
        t0 = time.time()
        try:
            et = _import_easytdx()
            if et is None:
                return []
            client = self._get_client()
            if client is None:
                return []
            fields = self._quote_fields()
            result: list[Quote] = []
            for i in range(0, len(codes), 80):
                chunk = codes[i:i + 80]
                stocks = [_resolve(c) for c in chunk]
                with self._lock:
                    df = client.get_stock_quotes(stocks, fields)
                if df is not None and not df.empty:
                    for r in df.to_dict("records"):
                        q = _to_quote(r)
                        if q is not None:
                            result.append(q)
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "batch_quotes", ok=bool(result),
                                 elapsed_ms=elapsed, note=f"n={len(result)}")
            return result
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "batch_quotes", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            return []
