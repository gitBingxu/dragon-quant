"""
通达信 TDX Provider — 首选数据链路（tmdx / easy_tdx，TCP 协议，无 Cookie 无反爬）

设计要点（见 providers/TDX_PROVIDER_技术方案.md）：
  - 惰性依赖：easy_tdx（tmdx 包）是默认依赖，随 `pip install dragon-quant`
    自动安装，导入放在首次调用
    （避免 import 开销，并在缺失时优雅降级返回空/None，由 orchestrator 回退到
    老链路兜底）。缺失时的控制台提示见 `warn_if_unavailable()`。
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

import importlib.util
import math
import sys
import threading
import time
from typing import Any, Optional

from dragon_quant.models.types import KBar, Quote, SectorPerformance, StockInfo
from dragon_quant.providers.base import StockProvider

# ─── 惰性依赖 ──────────────────────────────────────────────────────────────

_easytdx: Any = None  # easy_tdx 模块；False=已尝试但不可用


def _import_easytdx() -> Any:
    """惰性导入 easy_tdx（tmdx 默认依赖）。缺失时返回 None 触发兜底回退。"""
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


def _easy_tdx_installed() -> bool:
    """easy_tdx 是否已安装（find_spec 仅探测模块存在性，零 import 开销、无副作用）。

    注意：探测的是「模块可被找到」而非「可成功导入」——tmdx 已装但其依赖（如
    pandas）损坏时这里仍返回 True，主链路真实可用性由 `is_available()` 的完整
    import 兜底判断。此取舍用零开销换取「每次命令都探测」的可行性。
    """
    try:
        return importlib.util.find_spec("easy_tdx") is not None
    except (ImportError, AttributeError, ValueError):
        return False


def warn_if_unavailable() -> None:
    """easy_tdx（tmdx）缺失时向 stderr 打印提示，供 cli.main() 对所有命令调用。

    每次 CLI 命令都会调用，故用 find_spec 探测而非 is_available() 的完整 import，
    避免命令入口背上 easy_tdx（及 pandas）的 import 开销。
    """
    if _easy_tdx_installed():
        return
    advice = "请执行 `pip install tmdx`（或 `pip install dragon-quant`）补装。"
    print(
        "⚠️ 未安装 tmdx（通达信 TDX 首选数据链路），tdx 主链路不可用时将回退到同花顺/雪球/腾讯老链路。\n"
        f"【建议】{advice}\n"
        "【注意】后续 review-account / buy / sell 等命令将依赖 tmdx，建议提前安装。",
        file=sys.stderr,
    )


# 仓库指数别名 → TDX 代码（上证指数 SH000001 → 999999；000001 是平安银行不映射）
_INDEX_ALIAS = {"SH000001": "999999"}

# 板块维度 → easy_tdx.BoardType 属性名（运行时从 et.BoardType 取，避免导入期依赖）
_BOARD_TYPE_BY_CATEGORY = {"hy": "HY", "gn": "GN", "dq": "DQ"}

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
        # 存在性检查：字段缺失保持 None（未知），不被 _f 误置为 0.0（无流入）
        main_net_amount=_f(r["main_net_amount"]) if "main_net_amount" in r else None,
    )


def _to_sector(r: dict, category: str = "", source: str = "tdx") -> SectorPerformance:
    """board_list 行 → SectorPerformance（涨跌幅由 price/pre_close 现算）。"""
    price = _f(r.get("price"))
    pre = _f(r.get("pre_close"))
    pct = ((price / pre - 1) * 100.0) if pre else 0.0
    return SectorPerformance(
        code=str(r.get("code", "")),
        name=str(r.get("name", "")),
        pct=pct, amplitude=0.0, turnover_rate=0.0,
        category=category, source=source,
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


def _to_auction_items(records: list[dict]) -> list[dict]:
    """MAC get_auction DataFrame(records) → list[{'time','price','matched','unmatched'}]。

    records 键：time(datetime.time) / price / matched / unmatched。time 统一序列化为
    "HH:MM:SS" 字符串，便于 JSON 落库。
    """
    items: list[dict] = []
    for r in records:
        t = r.get("time")
        ts = t.strftime("%H:%M:%S") if hasattr(t, "strftime") else str(t or "")
        items.append({
            "time": ts,
            "price": _f(r.get("price")),
            "matched": int(_f(r.get("matched"))),
            "unmatched": int(_f(r.get("unmatched"))),
        })
    return items


def _normalize_value(v):
    """DataFrame 标量 → native Python 类型；NaN/inf → None（numpy 标量经 item() 还原）。"""
    if v is None:
        return None
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (int, str, bool)):
        return v
    item = getattr(v, "item", None)
    if callable(item):  # numpy 标量
        try:
            return _normalize_value(item())
        except Exception:
            return None
    return v


# ─── 宏观日 K 转换（纯函数，可单测）──────────────────────────────────────────


def _ex_enums():
    """惰性导入扩展行情枚举（测试可 patch 此函数，避免依赖真实 easy_tdx）。"""
    from easy_tdx.mac.enums import ExMarket, Period
    return ExMarket, Period


def _macro_record_from_kline(spec, df, trade_date: str,
                             market_name: str, code: str):
    """扩展市场日 K DataFrame → 归一宏观记录。

    口径由 ``spec.same_day_close`` 决定：True 排除 ``day > trade_date``（同日收盘，
    夜盘归下一交易日 bar）；False 排除 ``day >= trade_date``（上一已完成 bar）。
    """
    from dragon_quant.providers.macro import build_macro_record

    same_day = spec.same_day_close
    records = df.to_dict("records") if df is not None and not df.empty else []
    rows: list[tuple[str, float]] = []
    for r in records:
        dt = r.get("datetime")
        if hasattr(dt, "strftime"):
            day = dt.strftime("%Y-%m-%d")
        else:
            day = str(dt or "")[:10]
        if len(day) != 10:
            continue
        if (day > trade_date) if same_day else (day >= trade_date):
            continue
        close = _f(r.get("close"))
        if close <= 0:
            continue
        rows.append((day, close))
    if not rows:
        return None
    rows.sort(key=lambda item: item[0])
    session_date, price = rows[-1]
    prev_close = rows[-2][1] if len(rows) >= 2 else None

    label = price_unit = None
    if market_name == "SH_GOLD":       # 沪金口径（元/克），与 COMEX 美元金区分
        label, price_unit = "沪金", "cny_per_g"
    return build_macro_record(
        spec, price=price, prev_close=prev_close, source="tdx_ex",
        code=code, market=market_name, session_date=session_date,
        chg_bp_approx=True if spec.kind == "yield" else None,
        precision_note=("TDX 两位小数日频快照，bp 为近似值"
                        if spec.kind == "yield" else None),
        label=label, price_unit=price_unit,
    )


# 市场宽度行保留字段（datetime 类字段不入资产，避免序列化歧义）
_BREADTH_KEYS = ("code", "name", "close", "pre_close", "open", "high", "low",
                 "vol", "amount", "buy_price_limit", "sell_price_limit")

# FG 板块成员字段映射（值=源字段名；market_cap 取 total_market_cap_ab）。
# 保持与存量 get_fg_board_members 完全一致的 13 个键，向后兼容。
_FG_MEMBER_FIELDS = {
    "code": "code", "name": "name", "close": "close", "pre_close": "pre_close",
    "open": "open", "high": "high", "low": "low", "turnover": "turnover",
    "amount": "amount", "market_cap": "total_market_cap_ab",
    "main_net_amount": "main_net_amount",
    "buy_price_limit": "buy_price_limit", "sell_price_limit": "sell_price_limit",
}


def _fg_member_row(r: dict) -> dict:
    return {k: _normalize_value(r.get(src)) for k, src in _FG_MEMBER_FIELDS.items()}


def _finance_record(row: dict) -> dict:
    """财务 DataFrame 行 → 纯 dict（丢弃 ``_`` 内部字段、枚举转 int、NaN→None）。"""
    out: dict = {}
    for key, value in row.items():
        if str(key).startswith("_"):
            continue
        normalized = _normalize_value(value)
        if hasattr(normalized, "value"):      # Market 等 Enum
            normalized = normalized.value
        out[key] = normalized
    return out

# ─── Provider ──────────────────────────────────────────────────────────────


class TdxProvider(StockProvider):

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._client: Any = None  # MacClient；False=永久不可用
        self._macro_client: Any = None  # MacExClient（扩展行情）；False=永久不可用
        self._macro_error: Optional[str] = None  # 首次失败根因（供 RuntimeError 诊断）
        self._fin_client: Any = None  # TdxClient（标准行情，财务命令专属）；False=不可用
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

    def _get_macro_client(self):
        """惰性创建 MacExClient（扩展行情，端口 7727）。不可用 → None。

        失败时抛出的异常由 ``get_macro_quotes`` 统一转 RuntimeError（provider 契约：
        transport/解析/写盘失败必须抛出，由 providers.macro 门面逐源回退）。
        """
        if self._macro_client is None:
            et = _import_easytdx()
            if et is None:
                self._macro_error = "easy_tdx 不可用"
                self._macro_client = False
                return None
            try:
                from easy_tdx.ex.mac_client import MacExClient
                client = MacExClient.from_best_host(timeout=8, ping_timeout=2)
                client.connect()
                self._macro_client = client
            except Exception as exc:
                self._macro_error = f"{type(exc).__name__}: {exc}"
                self._macro_client = False
                return None
        return self._macro_client or None

    def _quote_fields(self):
        """完整 Quote 字段集：COMMON（换手/量比/市值/PE/涨跌停）+ QUOTE（买一卖一）。"""
        return self._preset.COMMON + self._preset.QUOTE

    # ─── 财务（P0.5，独立第二连接）──────────────────────────────────────────

    def _get_fin_client(self):
        """惰性创建标准行情 ``TdxClient``（财务命令所在客户端）。

        与 ``MacClient`` 分开：财务命令不在 MAC 扩展客户端上，且独立连接避免把
        财务的单票串行调用塞进行情链路的锁。失败返回 None（调用方法抛异常）。
        """
        if self._fin_client is None:
            et = _import_easytdx()
            if et is None:
                self._fin_client = False
                return None
            try:
                client = et.TdxClient.from_best_host(timeout=8, ping_timeout=2)
                client.ensure_connected()
                self._fin_client = client
            except Exception:
                self._fin_client = False
                return None
        return self._fin_client or None

    def _require_fin_client(self):
        client = self._get_fin_client()
        if client is None:
            raise RuntimeError("tdx 财务客户端不可用（easy_tdx 缺失或连接失败）")
        return client

    def get_finance_info(self, code: str) -> dict:
        """最新单期命名财务快照（``FinanceInfo``）→ 纯 dict（含 ``updated_date``）。

        transport/解析失败抛异常（新增采集方法契约，不返回空 dict）。
        """
        et = _import_easytdx()
        if et is None:
            raise RuntimeError("easy_tdx 不可用")
        client = self._require_fin_client()
        market, c = _resolve(code)
        t0 = time.time()
        try:
            df = client.get_finance_info(market=et.Market(market), code=c)
            records = df.to_dict("records") if df is not None and not df.empty else []
            if not records:
                raise RuntimeError(f"get_finance_info({code}) 返回空结果")
            row = _finance_record(records[0])
            row.setdefault("code", c)
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "finance_info", ok=True, elapsed_ms=elapsed,
                                 note=f"{code} updated={row.get('updated_date')}")
            return row
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "finance_info", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            raise

    def get_financial_files(self, host: Optional[str] = None) -> list[dict]:
        """``gpcw.txt`` 专业财报文件清单 → ``[{filename, md5, size}]``。

        tmdx 的 DataFrame 列名是 ``hash`` / ``filesize``；此处显式改名，
        否则 ``fin_report_files.md5`` 会写空。
        """
        client = self._require_fin_client()
        t0 = time.time()
        try:
            df = client.get_financial_file_list(host=host)
            records = df.to_dict("records") if df is not None and not df.empty else []
            out = []
            for row in records:
                filename = row.get("filename")
                md5 = row.get("hash") or row.get("md5")
                if not filename or not md5:
                    continue
                size = row.get("filesize", row.get("size"))
                if hasattr(size, "item"):
                    size = size.item()
                out.append({
                    "filename": str(filename),
                    "md5": str(md5).lower(),
                    "size": int(size) if size is not None else None,
                })
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "financial_files", ok=True,
                                 elapsed_ms=elapsed, note=f"n={len(out)}")
            return out
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "financial_files", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            raise

    def get_financial_records(self, filename: str, host: Optional[str] = None) -> list[dict]:
        """单期财报 → ``[{code, market, report_date, fields}]``。

        注意：该调用内部会**再下载一次 zip**，调用方不得先调 ``get_financial_file``。
        """
        client = self._require_fin_client()
        t0 = time.time()
        try:
            df = client.get_financial_records(filename, host=host)
            records = df.to_dict("records") if df is not None and not df.empty else []
            out = [_finance_record(row) for row in records]
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "financial_records", ok=True,
                                 elapsed_ms=elapsed,
                                 note=f"{filename} n={len(out)}")
            return out
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "financial_records", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            raise

    def get_financial_file(self, filename: str, host: Optional[str] = None) -> bytes:
        """下载财报 zip 原始字节（审计/校准备用；常规解析走 ``get_financial_records``）。"""
        client = self._require_fin_client()
        return client.get_financial_file(filename, host=host)

    def get_xdxr_info(self, code: str) -> list[dict]:
        """除权除息记录（本期仅落库备用，不参与因子）。"""
        et = _import_easytdx()
        if et is None:
            raise RuntimeError("easy_tdx 不可用")
        client = self._require_fin_client()
        market, c = _resolve(code)
        df = client.get_xdxr_info(market=et.Market(market), code=c)
        records = df.to_dict("records") if df is not None and not df.empty else []
        return [_finance_record(row) for row in records]

    # ─── 板块 ───

    def get_sector_ranking(self, asc: bool = False,
                           category: str = "hy") -> list[SectorPerformance]:
        """板块涨跌幅排行（通达信 board_list）。

        category: hy 行业 / gn 概念 / dq 地区。未知维度抛 ValueError（编程错误，
        不伪装成 api 失败），由 orchestrator 按维度降级（不中断扫描）。
        """
        board_name = _BOARD_TYPE_BY_CATEGORY.get(category)
        if board_name is None:
            raise ValueError(f"未知板块维度: {category!r}")
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
                    board_type=getattr(et.BoardType, board_name),
                    count=10000,
                    sort_column=et.BoardSortColumn.CHANGE_PCT,
                )
            rows = ([_to_sector(r, category, "tdx") for r in df.to_dict("records")]
                    if not df.empty else [])
            rows.sort(key=lambda s: s.pct, reverse=not asc)
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_ranking", ok=bool(rows),
                                 elapsed_ms=elapsed, note=f"{category} n={len(rows)}",
                                 dimension=category)
            return rows
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_ranking", ok=False,
                                 elapsed_ms=elapsed, error=str(e),
                                 dimension=category)
            return []

    def get_sector_components(self, sector_code: str, page: int = 1,
                              all_pages: bool = False,
                              page_size: int = 50,
                              category: str = "hy") -> list[StockInfo]:
        """板块成分股（通达信 board_members，按涨跌幅降序）。

        TDX 以 code 路由，category 仅用于日志与接口一致性；code 冲突由
        orchestrator 在 Phase A 提前剔除。
        """
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
                                 elapsed_ms=elapsed,
                                 note=f"{category} n={len(rows)}", dimension=category)
            return rows
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_components", ok=False,
                                 elapsed_ms=elapsed, error=str(e), dimension=category)
            return []

    def get_fg_boards(self, names: list[str]) -> dict[str, dict]:
        """FG(风格)板块批量成员：一次板块列表解析 + 逐板块成员。

        返回 {name: {'rows': [...], 'status': 'ok'|'missing'}}。
        错误契约（prep 采集）：transport/连接失败抛异常；板块名不存在记 missing。
        """
        t0 = time.time()
        et = _import_easytdx()
        if et is None:
            raise RuntimeError("easy_tdx 不可用")
        client = self._get_client()
        if client is None:
            raise RuntimeError("tdx 连接不可用")
        with self._lock:
            boards = client.get_board_list(
                board_type=et.BoardType.FG, count=10000,
                sort_column=et.BoardSortColumn.CHANGE_PCT,
            )
            code_by_name = {}
            for r in boards.to_dict("records"):
                code_by_name[str(r.get("name", "")).strip()] = str(r.get("code", "")).strip()
            out: dict[str, dict] = {}
            for name in names:
                target = name.strip()
                code = code_by_name.get(target)
                if not code:
                    out[name] = {"rows": [], "status": "missing"}
                    continue
                df = client.get_board_members(
                    board_symbol=code,
                    sort_type=et.SortType.CHANGE_PCT,
                    sort_order=et.SortOrder.DESC,
                )
                rows = [_fg_member_row(r)
                        for r in (df.to_dict("records") if not df.empty else [])]
                out[name] = {"rows": rows, "status": "ok"}
        elapsed = (time.time() - t0) * 1000
        if self._logger:
            self._logger.api("tdx", "fg_boards", ok=True, elapsed_ms=elapsed,
                             note=f"names={len(names)}")
        return out

    def get_fg_board_members(self, board_name: str) -> list[dict]:
        """取通达信 FG(风格)统计板块成分（如「昨日涨停」「昨日跌停」「最近多板」）。

        返回原始成员 dict（含完整行情字段），供盘后复盘的情绪温度/主线识别使用。
        board_name 需与通达信板块名精确匹配；未找到或失败返回空列表（存量契约）。
        """
        t0 = time.time()
        try:
            return self.get_fg_boards([board_name]).get(board_name, {}).get("rows", [])
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "fg_board_members", ok=False,
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

    def get_sector_5min_kline(self, sector_code: str, bars: int = 100,
                              category: str = "hy") -> list[KBar]:
        t0 = time.time()
        try:
            kbars = self._index_kline(sector_code, _import_easytdx().Period.MIN_5, bars)
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_5min_kline", ok=bool(kbars),
                                 elapsed_ms=elapsed, note=f"{category} n={len(kbars)}", dimension=category)
            return kbars[-bars:] if bars else kbars
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_5min_kline", ok=False,
                                 elapsed_ms=elapsed, error=str(e), dimension=category)
            return []

    def get_sector_1min_kline(self, sector_code: str, bars: int = 240,
                              category: str = "hy") -> list[KBar]:
        t0 = time.time()
        try:
            kbars = self._index_kline(sector_code, _import_easytdx().Period.MIN_1, bars)
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_1min_kline", ok=bool(kbars),
                                 elapsed_ms=elapsed, note=f"{category} n={len(kbars)}", dimension=category)
            return kbars[-bars:] if bars else kbars
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_1min_kline", ok=False,
                                 elapsed_ms=elapsed, error=str(e), dimension=category)
            return []

    def get_sector_5min_kline_history(self, sector_code: str,
                                      days: int = 10,
                                      category: str = "hy") -> list[KBar]:
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
                                 elapsed_ms=elapsed, note=f"{category} n={len(kbars)}", dimension=category)
            return kbars
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "sector_5min_history", ok=False,
                                 elapsed_ms=elapsed, error=str(e), dimension=category)
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

    # ─── prep 盘后复盘采集（错误契约：transport 失败抛异常，由采集器捕获记 error）───

    def get_market_breadth_rows(self, count: int = 6000) -> list[dict]:
        """全 A 实时行情行（市场宽度五合一原始素材）。

        get_stock_quotes_list 内部自动分页（多次串行请求，复用 _lock）；
        **必须显式传 count≈6000**（默认只回 80 条）。返回行已归一化
        （numpy 标量 → native，NaN → None），仅保留 _BREADTH_KEYS 字段。
        """
        t0 = time.time()
        et = _import_easytdx()
        if et is None:
            raise RuntimeError("easy_tdx 不可用")
        client = self._get_client()
        if client is None:
            raise RuntimeError("tdx 连接不可用")
        with self._lock:
            df = client.get_stock_quotes_list(
                category=et.Category.A, start=0, count=count,
                fields=self._preset.COMMON,
            )
        rows = [{k: _normalize_value(r.get(k)) for k in _BREADTH_KEYS}
                for r in (df.to_dict("records") if df is not None and not df.empty else [])]
        elapsed = (time.time() - t0) * 1000
        if self._logger:
            self._logger.api("tdx", "market_breadth_rows", ok=bool(rows),
                             elapsed_ms=elapsed, note=f"n={len(rows)}")
        # 空结果不上抛：由 prep 采集器按 sample_n 判定 empty_suspicious（契约：
        # provider 只在 transport 失败时抛异常，不替上层判定数据充分性）
        return rows

    def get_announcements(self, code: str, count: int = 30, page: int = 1) -> list[dict]:
        """cninfo 个股公告单页（title/type/date/url），最新在前。

        独立于 MacClient 的 HTTP 客户端（巨潮，无反爬）。失败抛异常（采集契约）。
        date 统一序列化为 "YYYY-MM-DD" 字符串，便于 JSON 落库与窗口过滤。
        """
        _import_easytdx()  # 确保 easy_tdx 可用（CninfoClient 随包分发）
        from easy_tdx.cninfo import CninfoClient
        c = code.upper()
        if c.startswith(("SH", "SZ", "BJ")):
            c = c[2:]
        t0 = time.time()
        try:
            df = CninfoClient().get_announcements(c, count=count, page=page)
        except Exception as e:
            raise RuntimeError(f"cninfo 公告拉取失败 {c} page={page}: {e}") from e
        out: list[dict] = []
        for r in (df.to_dict("records") if df is not None and not df.empty else []):
            d = r.get("date")
            out.append({
                "title": str(r.get("title", "")),
                "type": str(r.get("type", "") or ""),
                "date": d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d or ""),
                "url": str(r.get("url", "") or r.get("pdf_url", "") or ""),
            })
        elapsed = (time.time() - t0) * 1000
        if self._logger:
            self._logger.api("tdx", "announcements", ok=True,
                             elapsed_ms=elapsed, note=f"{c} p{page} n={len(out)}")
        return out

    # ─── 宏观扰动（扩展行情日 K；错误契约：失败抛异常）────────────────────────

    def get_macro_quotes(self, specs: list, trade_date: str) -> dict[str, dict]:
        """扩展行情日 K → 宏观记录（日经/A50 期指、美债、COMEX 金/沪金）。

        口径由 spec 声明：``same_day_close=True`` 取 ``<= trade_date`` 的最后一根
        （同日收盘，夜盘归下一交易日 bar）；否则取 ``< trade_date`` 的最后一根。
        与运行时刻解耦，`--recompute` 可重放；`GC00W` 无数据时自动尝试
        `SH_GOLD/Au99.99`。transport/解析失败抛 RuntimeError（由门面回退）。
        """
        t0 = time.time()
        client = self._get_macro_client()
        if client is None:
            detail = f": {self._macro_error}" if self._macro_error else ""
            raise RuntimeError(f"tdx 扩展行情不可用{detail}")
        ExMarket, Period = _ex_enums()
        wanted: list[tuple] = []
        for spec in specs or []:
            for market_name, code in getattr(spec, "tdx", ()) or ():
                try:
                    market = int(getattr(ExMarket, market_name))
                except (AttributeError, TypeError, ValueError):
                    continue
                wanted.append((spec, market_name, market, code))
        if not wanted:
            return {}

        out: dict[str, dict] = {}
        errors: list[str] = []
        with self._lock:
            for spec, market_name, market, code in wanted:
                if spec.key in out:          # 前序候选已命中（COMEX 优先于沪金）
                    continue
                try:
                    df = client.goods_kline(market, code, Period.DAILY, 0, 10)
                except Exception as exc:
                    errors.append(f"{market_name}/{code}: {type(exc).__name__}: {exc}")
                    continue
                rec = _macro_record_from_kline(
                    spec, df, trade_date, market_name, code)
                if rec:
                    out[spec.key] = rec
        elapsed = (time.time() - t0) * 1000
        if self._logger:
            self._logger.api("tdx", "macro_quotes", ok=bool(out), elapsed_ms=elapsed,
                             note=f"n={len(out)} errs={len(errors)}")
        if not out and errors:
            raise RuntimeError("tdx 宏观日 K 全部失败：" + "；".join(errors))
        return out

    # ─── 集合竞价 ───

    def get_auction(self, code: str) -> list[dict]:
        """个股当日集合竞价盘口（仅当日 9:25 后可用，盘后仍可拉取，无历史回看）。

        返回 [{'time','price','matched','unmatched'}, ...]；非交易时段/失败返回 []。
        """
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
                df = client.get_auction(market=market, code=c)
            items = _to_auction_items(df.to_dict("records")) if not df.empty else []
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "auction", ok=bool(items),
                                 elapsed_ms=elapsed, note=f"{code} n={len(items)}")
            return items
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if self._logger:
                self._logger.api("tdx", "auction", ok=False,
                                 elapsed_ms=elapsed, error=str(e))
            return []
