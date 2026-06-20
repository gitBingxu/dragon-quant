"""
腾讯 Provider — 个股实时行情、个股日K线(fallback)、个股5分K线(fallback 1分分时→合成)
无需 Cookie，零认证
"""

import json, sys, time
import urllib.request
from typing import Optional
from dragon_quant.models.types import Quote, KBar, StockInfo, SectorPerformance
from dragon_quant.providers.base import StockProvider

GTIMG = "https://qt.gtimg.cn"
MINUTE = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"

TENCENT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://finance.qq.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


def _gtimg_codes(codes: list[str]) -> str:
    """sh600519,sZ300750 -> q=sh600519,sz300750"""
    parts = []
    for c in codes:
        prefix = "sh" if c.startswith(("6", "9")) else "sz"
        parts.append(f"{prefix}{c}")
    return ",".join(parts)


def _fetch_gtimg(codes: list[str], logger=None, endpoint: str = "") -> Optional[str]:
    """腾讯 gtimg 批量获取，返回原始 gbk 文本"""
    q = _gtimg_codes(codes)
    url = f"{GTIMG}/q={q}"
    req = urllib.request.Request(url, headers=dict(TENCENT_HEADERS))
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        elapsed = (time.time() - t0) * 1000
        result = raw.decode("gbk")
        if logger:
            logger.api("tencent", endpoint, ok=True, elapsed_ms=elapsed)
        return result
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        if logger:
            logger.api("tencent", endpoint, ok=False,
                       elapsed_ms=elapsed, error=str(e))
        print(f"  ⚠️ 腾讯行情失败: {e}", file=sys.stderr)
        return None


def _parse_gtimg_quote(line: str) -> Optional[Quote]:
    """解析单只股票的 gtimg 行情"""
    fields = line.split("~")
    if len(fields) < 52:
        return None
    try:
        return Quote(
            code=fields[2], name=fields[1],
            price=float(fields[3]), prev_close=float(fields[4]),
            open_px=float(fields[5]),
            high=float(fields[33]), low=float(fields[34]),
            pct=float(fields[32]), chg=float(fields[31]),
            turnover_rate=float(fields[38]), amplitude=float(fields[43]),
            volume=float(fields[36]), amount=float(fields[37]),
            market_cap=float(fields[45]) * 1e8,  # 亿→元
            float_market_cap=float(fields[44]) * 1e8,
            volume_ratio=float(fields[49]),
            pe=float(fields[39]),
            limit_up=float(fields[47]), limit_down=float(fields[48]),
            avg_price=float(fields[51]),
            bid1_price=float(fields[9]), bid1_volume=float(fields[10]),
            ask1_volume=float(fields[20]),
        )
    except (ValueError, IndexError):
        return None


class TencentProvider(StockProvider):

    @property
    def name(self) -> str:
        return "tencent"

    # ─── 实时行情 ───

    def get_quote(self, code: str) -> Optional[Quote]:
        raw = _fetch_gtimg([code], logger=self._logger, endpoint="quote")
        if not raw:
            return None
        # gtimg 返回多行，找到对应股票的行
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # 格式: v_sh600519="..."
            q = line.split("=", 1)
            if len(q) < 2:
                continue
            val = q[1].strip(';"\' ')
            return _parse_gtimg_quote(val)
        return None

    def batch_get_quotes(self, codes: list[str]) -> list[Quote]:
        """批量获取行情"""
        raw = _fetch_gtimg(codes, logger=self._logger, endpoint="batch_quotes")
        if not raw:
            return []
        result = []
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            q = line.split("=", 1)
            if len(q) < 2:
                continue
            val = q[1].strip(';"\' ')
            quote = _parse_gtimg_quote(val)
            if quote:
                result.append(quote)
        return result

    # ─── 日 K 线 (fallback) — 通过 gtimg 合成 ───

    def get_kline(self, code: str, days: int = 20) -> list[KBar]:
        """腾讯 gtimg 不直接提供日K线，此方法留作 future work"""
        return []

    # ─── 5 分 K 线 (fallback) — 1 分分时合成 ───

    def get_5min_kline(self, code: str, bars: int = 96) -> list[KBar]:
        """从腾讯1分钟分时数据合成5分钟K线"""
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        url = f"{MINUTE}?code={prefix}{code}"
        try:
            headers = dict(TENCENT_HEADERS)
            headers["Referer"] = f"https://finance.qq.com/"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"  ⚠️ 腾讯分时失败: {e}", file=sys.stderr)
            return []

        bar_data = data.get("data", {}).get(prefix + code, {}).get("data", {}).get("data", [])
        # 格式: "0930 1371.66 186 25512876.20" → 时间 价格 成交量 成交额
        return self._synthesize_5min(bar_data, bars)

    @staticmethod
    def _synthesize_5min(minute_bars: list[str], bars: int) -> list[KBar]:
        """5根1分钟bar合成1根5分钟K线"""
        if not minute_bars:
            return []

        result = []
        group = []
        for bar_str in minute_bars:
            parts = bar_str.strip().split()
            if len(parts) < 4:
                continue
            try:
                time_str = parts[0]  # "0930"
                px = float(parts[1])
                vol = float(parts[2])
                amt = float(parts[3])
                hh, mm = int(time_str[:2]), int(time_str[2:4])
                group.append((hh * 60 + mm, px, vol, amt))
            except (ValueError, IndexError):
                continue

        # 每5分钟一组
        chunks = [group[i:i + 5] for i in range(0, len(group), 5)]
        for chunk in chunks:
            if not chunk:
                continue
            # 用分钟时间做合成
            start_min = chunk[0][0]
            end_min = chunk[-1][0]
            base_date = "2026-01-01"  # 占位，后面会覆盖
            ts = int(time.mktime(time.strptime(f"{base_date} {start_min // 60:02d}:{start_min % 60:02d}", "%Y-%m-%d %H:%M"))) * 1000

            opens = [c[1] for c in chunk]
            high = max(c[1] for c in chunk)
            low = min(c[1] for c in chunk)
            vol = sum(c[2] for c in chunk)
            amt = sum(c[3] for c in chunk)
            result.append(KBar(
                timestamp=ts,
                open=opens[0], close=opens[-1], high=high, low=low,
                volume=vol, amount=amt,
                chg=0, pct=0, turnover=0,
            ))

        return result[-bars:]

    # ─── 未实现 ───

    def get_sector_ranking(self, asc: bool = False) -> list[SectorPerformance]:
        raise NotImplementedError

    def get_sector_components(self, sector_code: str, page: int = 1,
                              all_pages: bool = False,
                              page_size: int = 50) -> list[StockInfo]:
        raise NotImplementedError

    def get_sector_5min_kline(self, sector_code: str, bars: int = 100) -> list[KBar]:
        raise NotImplementedError
