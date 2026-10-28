"""prep 采集段：8 个 collector、注册表、分类/健康判定与 collection_report 合并。

架构（M5B 最终边界）：
  - collectors 拥有「怎么采、采到什么算健康、report 怎么合并」；service 只负责
    日期门禁、增量计划与 collection→compute 编排。依赖方向单向：
    ``service → collectors → rebuild → compute``，collectors **永不**导入 service
    或包门面（避免 import 期循环）。
  - 注册表顺序有意义：dragons 先采，fund_flow / cninfo 消费其候选 codes。
  - same_day_only 资产（FG/实时行情类）仅 ``trade_date == today`` 可采；历史日不
    采集、不写 report，由 compute 按缺失降级，防止时点污染。
  - collection_report 语义 =「该日期资产当前最佳状态」：只合并本次实际执行的条目，
    补采失败不覆盖既有健康状态（记 ``last_attempt``），零采集时完全不动。

旧 patch 目标（provider 类、``ASSET_COLLECTORS``、``dragons_mirror``、
``collect_macro``、``_read_dragon_day`` / ``_stale_dates``）一律经
:func:`rebuild.legacy` 在调用期从 prep 包门面解析——静态重命名会静默让 patch 失效。

数据源：tmdx（MacClient）优先，THS dataapi 次之；排除东财。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Optional

from dragon_quant.infra import clock
from dragon_quant.infra.assets.dragons_mirror import BACKFILL_HINT, DragonsMirrorStale

from .compute import SHANGHAI, _list_of_dicts
from .rebuild import (
    MIRROR_RETRY_DELAY, WINDOW_TRADING_DAYS, _is_mirror_stale_day,
    build_dragons_payload, legacy,
)
from .review.pool import derive_pool_codes

if TYPE_CHECKING:  # 仅注解用：运行期一律经 legacy 从包门面解析（旧 patch 目标）
    from dragon_quant.infra.provider.tdx import TdxProvider
    from dragon_quant.infra.provider.ths import THSProvider


# ─── 常量（集中调参）────────────────────────────────────────────────────────

FG_BOARD_NAMES = ["昨日涨停", "昨日跌停", "昨日连板", "昨日首板", "昨日断板", "昨日上榜"]

CNINFO_WINDOW_DAYS = 7   # 公告窗口：保留 [T-7, T] 内公告
CNINFO_MAX_PAGES = 5     # 翻页上限保护；触顶且未到窗口 → truncated
STOCK_DAILY_DAYS = 120   # 详细池日 K 回看根数（D5 60 日高点 + D6 20 日股性）

_LIMIT_TOL_UP = 0.999    # 涨停/炸板判定容差（涨停价的 0.1%）
_LIMIT_TOL_DOWN = 1.001

# 全 A 扫描有效样本下限：低于此值视为 data source 未填充（非真实市场极值）
SAMPLE_N_MIN = 3000

# collection_report 中「不可用」的状态：compute 按缺失降级，绝不使用其值
_BAD_STATUS = {"error", "unavailable", "empty_suspicious"}
# 正常交易日不可能为空的资产：status=empty 也按缺失处理
_MUST_NONEMPTY = {"market_breadth", "fg_boards", "macro_global", "stock_daily"}
# 需要校验 as_of == trade_date 的资产：仅 same_day_only（实时/时点快照）类。
# 注意 cninfo_announcements 的 as_of 是采集时间而非数据时点（公告可历史回溯），
# 其窗口正确性由采集阶段 [T-window, T] 过滤保证，不做 as_of 相等校验。
_AS_OF_CHECKED = {"market_breadth", "fg_boards", "fund_flow", "t1_intraday"}

# 依赖 dragons 候选 codes 的资产：dragons 重采且 code 集合变化时级联补采
_DRAGON_DEPENDENTS = ("fund_flow", "cninfo_announcements", "stock_daily")
# code 覆盖校验：payload 键里应覆盖当前龙头并集 codes
_CODE_COVERED_KEYS = {
    "fund_flow": "quotes",
    "cninfo_announcements": "items",
    "stock_daily": "rows",
}
# review 独立修复涉及的资产（与 legacy 采集计划完全分离）
_REVIEW_ASSET_TYPES = ("fund_flow", "cninfo_announcements", "stock_daily")
_REVIEW_CONTAINER_KEYS = {"fund_flow": "quotes",
                          "cninfo_announcements": "items",
                          "stock_daily": "rows"}

# M5 反馈资产：缺失只影响 ``feedback`` / ``plan_review`` 自身，绝不写进旧
# ``sentiment.degraded``（否则历史日会污染旧 CLI 前缀与 advice 文案）。
_FEEDBACK_ASSETS = ("prev_plan", "auction_feedback", "t1_intraday")

# review 资产开关关闭时整体跳过的采集器（行为等价于本期未上线）。
_REVIEW_ONLY_ASSETS = ("holdings", "stock_daily", "prev_plan",
                       "auction_feedback", "t1_intraday")

# T+1 反馈补采上限（技术方案 §10.3：≤ review_max_codes；此处不 import review.config，
# collectors 只允许依赖 review.pool 纯函数叶子）。
REVIEW_BACKFILL_LIMIT = 60
# 冻结 playbook 的对照刻度（不表示系统做过实时判定）。
INTRADAY_CHECKPOINTS = ("09:25", "10:00")


def review_assets_enabled() -> bool:
    """新资产采集开关（默认开启；``DQ_REVIEW_ASSETS_ENABLED=0/false`` 整体回退）。"""

    raw = os.environ.get("DQ_REVIEW_ASSETS_ENABLED", "")
    return raw.strip().lower() not in ("0", "false", "no", "off")


# ─── 采集器与上下文 ────────────────────────────────────────────────────────

def _providers_from_services(services):
    """可选 M2 装配注入：取出 ``ProviderBundle``；无注入返回 None。

    None 表示沿用既有 ``TdxProvider()`` / ``THSProvider()`` /
    ``TencentProvider()`` / ``XueqiuProvider()`` 构造路径，旧调用与
    monkeypatch 目标保持不变。
    """
    if services is None:
        return None
    return getattr(services, "providers", None)


@dataclass
class PrepContext:
    """采集器共享上下文（dragons 先采，fund_flow/cninfo 消费其候选 codes）。

    ``window_trade_dates`` 由采集段前置解析（交易日历），采集器不得自行触网取日历；
    ``window_days`` 是**请求**的窗口长度（日历不足时实际日期可能更少）。
    """
    trade_date: str
    today: str
    tdx: TdxProvider
    ths: THSProvider
    dragons_rows: list = field(default_factory=list)
    # dragons 候选 codes 是否已就绪（成功采集或存量可用）：未就绪时不执行依赖采集器，
    # 避免以空 codes 产出空 payload 覆盖健康存量。
    dragons_ready: bool = False
    window_trade_dates: list = field(default_factory=list)
    window_days: int = WINDOW_TRADING_DAYS
    # 本次采集可见的资产视图（存量 + 本次已采 payload）；供 stock_daily / 资金 /
    # 公告采集器读取池依赖，绝不回写 storage。
    collected: dict = field(default_factory=dict)
    # review 独立修复模式：只请求 ``review_codes`` 并与存量 payload 按键合并，
    # 不写 collection_report、不改 legacy_codes、不改 legacy 采集选择。
    review_only: bool = False
    review_codes: list = field(default_factory=list)
    tencent: Optional[object] = None
    xueqiu: Optional[object] = None


@dataclass
class CollectorSpec:
    collect: Callable[[PrepContext], dict]
    same_day_only: bool
    # 空结果可疑检查：返回 note 字符串则记 empty_suspicious（区分「数据源未填充」与「真空」）
    suspicious_check: Optional[Callable[[dict], Optional[str]]] = None


def _collectors() -> dict:
    """采集器注册表（经包门面读取）。

    旧 ``patch.object(prep, "ASSET_COLLECTORS", ...)`` 是**重绑定**门面属性，
    ``patch.dict(prep.ASSET_COLLECTORS, ...)`` 是**原地改**同名字典；两种写法都必须
    影响计划与采集，因此注册表一律运行期解析，不做模块级静态引用。
    """
    return legacy("ASSET_COLLECTORS")


# ─── 双作用域 / 独立修复共用工具 ────────────────────────────────────────────

def _ctx_assets(ctx: PrepContext) -> dict:
    """采集器可见资产视图：存量 + 本次已采 payload（不含 collection_report）。"""

    return {k: v for k, v in (ctx.collected or {}).items() if k != "collection_report"}


def _dedupe_codes(values) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for value in values if isinstance(values, (list, tuple)) else []:
        code = value.get("code") if isinstance(value, dict) else value
        if isinstance(code, str) and code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _scoped_container(payload, key: str) -> dict:
    """按 ``legacy_codes`` 收窄容器；``legacy_codes`` 缺失时退化为全键（旧 payload）。

    这是「旧链路健康只看 dragons 子集」的唯一实现：详细池补充代码不得反向改变
    旧资产的 ``ok`` / ``empty`` / ``empty_suspicious``，否则旧 ``candidates`` 的
    ``flow_available`` 与 ``verdict`` 会静默变化（技术方案 §7 强制回归）。
    """

    if not isinstance(payload, dict):
        return {}
    container = payload.get(key)
    container = container if isinstance(container, dict) else {}
    legacy = payload.get("legacy_codes")
    if isinstance(legacy, list):
        return {code: container.get(code) for code in legacy if code in container}
    return container


def _review_health(payload, key: str, value_key: str | None = None) -> dict:
    """review 作用域健康：只对 ``review_codes`` 判缺失 / 全 None。"""

    if not isinstance(payload, dict):
        return {"status": "degraded", "note": "payload_missing", "missing": []}
    codes = _dedupe_codes(payload.get("review_codes"))
    if not codes:
        return {"status": "ok", "note": None, "missing": []}
    container = payload.get(key)
    container = container if isinstance(container, dict) else {}
    missing = [code for code in codes if code not in container]
    if missing:
        return {"status": "degraded", "note": "missing_codes", "missing": missing}
    if value_key:
        values = [
            container[code].get(value_key) if isinstance(container[code], dict) else None
            for code in codes
        ]
        if values and all(value is None or value == 0 for value in values):
            return {"status": "degraded", "note": f"{value_key}_all_none",
                    "missing": []}
    return {"status": "ok", "note": None, "missing": []}


def _mark_review_failure(payload: dict, entry: dict, missing: list[str]) -> dict:
    """修复失败：只把 ``review_health=error`` 写回 payload，不触碰旧字段。"""

    updated = dict(payload)
    updated["review_health"] = {
        "status": "error",
        "note": "review_repair_failed",
        "error": entry.get("error"),
        "missing": list(missing),
    }
    return updated


@dataclass(frozen=True)
class ReviewRepairPlan:
    """review 独立修复计划（与 legacy ``PrepPlan.collect`` 完全分离）。"""

    collect: list[str] = field(default_factory=list)
    missing: dict[str, list[str]] = field(default_factory=dict)
    pool_codes: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    enabled: bool = True

    def is_empty(self) -> bool:
        return not self.collect


def _collect_dragons(ctx: PrepContext) -> dict:
    """窗口内逐日冻结真龙（compute 的唯一真龙来源；含逐日 scan_id/version 血缘）。

    读源 = ``assets.db.dragons`` 镜像（AC5）；镜像写入是锁外 best-effort，
    因此必须**逐日对账**：源库有行而镜像缺行的日期改写为 error，绝不伪装成
    「该日没有真龙」。目标日 stale 先短重试一次（覆盖 scan 主写已提交、镜像
    尚未写完的并发窗口），仍 stale 则抛 :class:`DragonsMirrorStale`；
    历史日 stale 只降级（``dragons_mirror_missing_day``）。
    """
    read_day = legacy("_read_dragon_day")
    stale_dates = legacy("_stale_dates")
    window = list(ctx.window_trade_dates or [ctx.trade_date])
    day_rows: dict[str, list] = {}
    day_errors: dict[str, str] = {}
    for day in window:
        try:
            day_rows[day] = read_day(day)
        except Exception as e:  # 单日失败不中断：记 error 继续下一个交易日
            day_errors[day] = f"{type(e).__name__}: {e}"

    stale = stale_dates(window)
    if ctx.trade_date in stale:
        time.sleep(MIRROR_RETRY_DELAY)
        retry = stale_dates([ctx.trade_date])
        if ctx.trade_date in retry:
            raise DragonsMirrorStale(
                f"{ctx.trade_date} 源库有龙头记录而镜像缺行（assets.db.dragons）；"
                f"运行 {BACKFILL_HINT} 修复")
        stale.pop(ctx.trade_date, None)
        # 竞态恢复后必须**回读真实行**：首读发生在镜像写入之前，其空列表不可复用，
        # 否则会把「已恢复的数据」静默记成当日无真龙（error 伪装只是换成了 empty）。
        day_rows[ctx.trade_date] = read_day(ctx.trade_date)
        day_errors.pop(ctx.trade_date, None)

    payload = build_dragons_payload(
        window, ctx.window_days, trade_date=ctx.trade_date,
        window_source="calendar", day_rows=day_rows, day_errors=day_errors,
        stale=stale)

    errors = [f"{d['trade_date']}: {d['error']}"
              for d in payload["days"] if d.get("error")]
    if not payload["rows"] and errors:
        raise RuntimeError(f"{len(window)}日龙头读取失败：" + "；".join(errors))

    ctx.dragons_rows = payload["rows"]
    ctx.dragons_ready = True          # 含真空（窗口内无真龙）：codes 为空是确定事实
    return payload


def _dragon_codes(rows: list) -> list[str]:
    """并按集 codes：按首次出现去重（保持窗口/日内的确定性顺序）。"""
    seen: set[str] = set()
    codes: list[str] = []
    for row in rows:
        code = row.get("code") if isinstance(row, dict) else None
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _collect_ths_limit_up_pool(ctx: PrepContext) -> dict:
    api_date = ctx.trade_date.replace("-", "")
    data = ctx.ths.get_limit_up_pool(api_date)
    return {"trade_date": ctx.trade_date, "api_date": api_date,
            "total": data["total"], "rows": data["rows"],
            "truncated": bool(data.get("truncated"))}


def _pool_suspicious(payload: dict) -> Optional[str]:
    if payload.get("truncated"):
        return "涨停池翻页触顶（rows < total），数据不完整"
    return None


def _collect_ths_block_top(ctx: PrepContext) -> dict:
    api_date = ctx.trade_date.replace("-", "")
    data = ctx.ths.get_block_top(api_date)
    return {"trade_date": ctx.trade_date, "api_date": api_date, "rows": data["rows"]}


def _collect_market_breadth(ctx: PrepContext) -> dict:
    """全 A 扫描五合一：红盘率/涨停/炸板/跌停/成交额（一次扫描，纯计算）。"""
    rows = ctx.tdx.get_market_breadth_rows()
    valid = [r for r in rows
             if (r.get("close") or 0) > 0 and (r.get("pre_close") or 0) > 0]
    # 停牌行剔除：有价格但无量额（close==pre_close>0 的停牌股靠此判据排除）
    active = [r for r in valid if (r.get("vol") or 0) > 0 or (r.get("amount") or 0) > 0]
    up = sum(1 for r in active if r["close"] > r["pre_close"])
    down = sum(1 for r in active if r["close"] < r["pre_close"])
    limit_up, broken, limit_down = [], [], []
    for r in active:
        close, high = r["close"], r.get("high") or 0
        lu, ld = r.get("buy_price_limit") or 0, r.get("sell_price_limit") or 0
        if lu > 0:
            if close >= lu * _LIMIT_TOL_UP:
                limit_up.append(r["code"])
            elif high >= lu * _LIMIT_TOL_UP:
                broken.append(r["code"])
        if ld > 0 and close <= ld * _LIMIT_TOL_DOWN:
            limit_down.append(r["code"])
    return {
        "as_of": ctx.today,
        "up_cnt": up, "down_cnt": down, "flat_cnt": len(active) - up - down,
        "limit_up": sorted(limit_up), "broken": sorted(broken),
        "limit_down": sorted(limit_down),
        "total_amount": sum((r.get("amount") or 0) for r in valid),
        "sample_n": len(active), "raw_n": len(rows),
        "caliber": "active: close>0 & pre_close>0 & (vol>0 | amount>0); "
                   "limit tolerance 0.1%; total_amount over valid rows",
    }


def _breadth_suspicious(payload: dict) -> Optional[str]:
    n = payload.get("sample_n", 0)
    if n < SAMPLE_N_MIN:
        return f"sample_n={n} 低于有效样本下限 {SAMPLE_N_MIN}，疑数据源未填充"
    return None


def _collect_fg_boards(ctx: PrepContext) -> dict:
    boards = ctx.tdx.get_fg_boards(FG_BOARD_NAMES)
    return {"as_of": ctx.today, "boards": boards,
            "note": "FG 板块为 T-1 语义（昨日X=上一交易日集合）"}


def _fg_suspicious(payload: dict) -> Optional[str]:
    boards = payload.get("boards") or {}
    if not any((b.get("rows") or []) for b in boards.values()):
        return "全部 FG 板块成员为空，疑数据源未填充"
    return None


def _quote_record(quote) -> dict:
    """Quote → fund_flow 行（legacy 四字段 + review 评分所需价格/市值）。"""

    return {
        "main_net_amount": quote.main_net_amount,
        "pct": quote.pct,
        "turnover_rate": quote.turnover_rate,
        "amount": quote.amount,
        "price": quote.price,
        "float_market_cap": quote.float_market_cap,
        "name": quote.name,
    }


def _collect_fund_flow(ctx: PrepContext) -> dict:
    """资金流：dragons 并集 ∪ 详细池超集，同资产双健康作用域。

    * ``legacy_codes`` = 当前 dragons 并集（旧链路健康只看它）；
    * ``review_codes`` = 详细池 − legacy（只由 ``review.degraded`` 消费）；
    * review 修复模式只请求缺失代码并与存量 ``quotes`` 按键合并。
    """

    assets = _ctx_assets(ctx)
    existing = assets.get("fund_flow")
    existing = existing if isinstance(existing, dict) else {}
    legacy_codes = _dedupe_codes(ctx.dragons_rows)
    quotes = dict(existing.get("quotes") or {}) if ctx.review_only else {}
    if ctx.review_only:
        stored_legacy = existing.get("legacy_codes")
        if isinstance(stored_legacy, list):
            legacy_codes = _dedupe_codes(stored_legacy)
        review_codes = _dedupe_codes(
            list(existing.get("review_codes") or []) + list(ctx.review_codes or []))
        to_fetch = _dedupe_codes(ctx.review_codes)
    else:
        pool = derive_pool_codes(assets)
        legacy_set = set(legacy_codes)
        review_codes = [code for code in pool["codes"] if code not in legacy_set]
        to_fetch = _dedupe_codes(list(legacy_codes) + review_codes)

    fetched = ctx.tdx.batch_get_quotes(to_fetch) if to_fetch else []
    for quote in fetched:
        code = getattr(quote, "code", None)
        if isinstance(code, str) and code:
            quotes[code] = _quote_record(quote)

    payload = {
        "as_of": (existing.get("as_of") if ctx.review_only else None) or ctx.today,
        "quotes": quotes,
        "legacy_codes": legacy_codes,
        "review_codes": review_codes,
    }
    payload["review_health"] = _review_health(payload, "quotes", "main_net_amount")
    return payload


def _fund_flow_suspicious(payload: dict) -> Optional[str]:
    quotes = _scoped_container(payload, "quotes")
    if not quotes:
        return None  # 无候选导致的空由 empty 状态表达
    values = [q.get("main_net_amount") if isinstance(q, dict) else None
              for q in quotes.values()]
    if all(v is None for v in values) or all((v or 0) == 0 for v in values):
        return "main_net_amount 全为 None/0，疑数据源未填充（周六/非交易时段常见）"
    return None


def _collect_cninfo(ctx: PrepContext) -> dict:
    """公告采集：逐页翻至 date <= 窗口起点，过滤未来公告（look-ahead 防护）。

    超集口径与资金流一致：``items`` 覆盖 dragons ∪ 详细池，``legacy_codes``
    保持 dragons 并集；review 修复只补缺失代码并按键合并。
    """

    assets = _ctx_assets(ctx)
    existing = assets.get("cninfo_announcements")
    existing = existing if isinstance(existing, dict) else {}
    legacy_codes = _dedupe_codes(ctx.dragons_rows)
    items = dict(existing.get("items") or {}) if ctx.review_only else {}
    if ctx.review_only:
        stored_legacy = existing.get("legacy_codes")
        if isinstance(stored_legacy, list):
            legacy_codes = _dedupe_codes(stored_legacy)
        review_codes = _dedupe_codes(
            list(existing.get("review_codes") or []) + list(ctx.review_codes or []))
        to_fetch = _dedupe_codes(ctx.review_codes)
    else:
        pool = derive_pool_codes(assets)
        legacy_set = set(legacy_codes)
        review_codes = [code for code in pool["codes"] if code not in legacy_set]
        to_fetch = _dedupe_codes(list(legacy_codes) + review_codes)

    window_start = (datetime.fromisoformat(ctx.trade_date)
                    - timedelta(days=CNINFO_WINDOW_DAYS)).strftime("%Y-%m-%d")
    truncated_codes = set(existing.get("truncated_codes") or []) if ctx.review_only else set()
    for code in to_fetch:
        anns: list[dict] = []
        truncated = True  # 默认触顶；翻到窗口起点或官方无更多则解除
        for page in range(1, CNINFO_MAX_PAGES + 1):
            batch = ctx.tdx.get_announcements(code, count=30, page=page)
            if not batch:
                truncated = False
                break
            anns.extend(batch)
            oldest = batch[-1].get("date") or ""
            if oldest and oldest <= window_start:
                truncated = False
                break
        if truncated and anns:
            truncated_codes.add(code)
        items[code] = {
            "announcements": [a for a in anns
                              if window_start <= (a.get("date") or "") <= ctx.trade_date],
            "max_announcement_date": max((a.get("date") or "" for a in anns), default=None),
            "truncated": truncated and bool(anns),
        }
    payload = {
        "as_of": (existing.get("as_of") if ctx.review_only else None) or ctx.today,
        "window_days": CNINFO_WINDOW_DAYS,
        "window_start": window_start,
        "items": items,
        "truncated_codes": sorted(truncated_codes),
        "legacy_codes": legacy_codes,
        "review_codes": review_codes,
    }
    payload["review_health"] = _review_health(payload, "items")
    return payload


def _collect_holdings(ctx: PrepContext) -> dict:
    """本地持仓代码（只存 code 集合与来源，**不存**数量/成本/账户金额）。"""

    codes: list[str] = []
    sources: list[str] = []
    degraded: list[str] = []
    try:
        from dragon_quant.review_account.live_trade.storage import list_open_signals
        rows = list_open_signals(before_date=ctx.trade_date)
        codes = _dedupe_codes(rows)
        sources.append("buy_sell_signals")
    except Exception:
        degraded.append("holdings_unavailable")
    return {
        "as_of": ctx.today,
        "codes": codes,
        "sources": sources,
        "degraded": degraded,
    }


def _daily_row(bar) -> dict | None:
    """KBar → 日 K 行（date/open/high/low/close/volume/amount/turnover）。"""

    timestamp = getattr(bar, "timestamp", 0) or 0
    if not timestamp:
        return None
    try:
        day = datetime.fromtimestamp(timestamp / 1000.0, SHANGHAI).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None
    return {
        "date": day,
        "open": getattr(bar, "open", None),
        "high": getattr(bar, "high", None),
        "low": getattr(bar, "low", None),
        "close": getattr(bar, "close", None),
        "volume": getattr(bar, "volume", None),
        "amount": getattr(bar, "amount", None),
        "turnover": getattr(bar, "turnover", None),
    }


def _collect_stock_daily(ctx: PrepContext) -> dict:
    """详细池日 K（D5 位置/筹码 + D6 股性）；单 code 失败记 missing，不写假 K。

    不允许以空池覆盖健康存量：dragons 由 ``_DRAGON_DEPENDENTS`` 拦截，涨停池
    不可用直接抛错；review 修复模式只补 ``ctx.review_codes`` 并与存量按 code 合并。
    """

    assets = _ctx_assets(ctx)
    pool_payload = assets.get("ths_limit_up_pool")
    if not isinstance(pool_payload, dict):
        raise RuntimeError("涨停池不可用，无法确定详细池日 K 代码")
    if not pool_payload.get("rows") and not pool_payload.get("total"):
        raise RuntimeError("涨停池为空，无法确定详细池日 K 代码")

    pool = derive_pool_codes(assets)
    existing = assets.get("stock_daily")
    existing = existing if isinstance(existing, dict) else {}
    rows = dict(existing.get("rows") or {}) if ctx.review_only else {}
    if ctx.review_only:
        requested_codes = list(pool["codes"])       # 换血后始终更新为最新池
        to_fetch = _dedupe_codes(ctx.review_codes)
    else:
        requested_codes = list(pool["codes"])
        to_fetch = list(requested_codes)

    fetched = (ctx.tdx.batch_get_daily_klines(to_fetch, days=STOCK_DAILY_DAYS,
                                              end_date=ctx.trade_date)
               if to_fetch else {})
    for code, bars in fetched.items():
        converted = [row for row in (_daily_row(bar) for bar in bars or []) if row]
        converted = [row for row in converted if row["date"] <= ctx.trade_date]
        converted.sort(key=lambda row: row["date"])
        if converted:
            rows[code] = converted[-STOCK_DAILY_DAYS:]

    requested_set = set(requested_codes)
    cap = max(len(requested_codes) * 2, 1)
    if len(rows) > cap:
        ordered = [code for code in requested_codes if code in rows]
        ordered += [code for code in rows if code not in requested_set]
        rows = {code: rows[code] for code in ordered[:cap]}
    missing = [code for code in requested_codes if code not in rows]
    requested_n = len(requested_codes)
    returned_n = requested_n - len(missing)
    payload = {
        "as_of": (existing.get("as_of") if ctx.review_only else None) or ctx.today,
        "trade_date": ctx.trade_date,
        "requested_codes": requested_codes,
        "returned_codes": [code for code in requested_codes if code in rows],
        "missing_codes": missing,
        "rows": rows,
        "coverage": {
            "requested": requested_n,
            "returned": returned_n,
            "missing": missing,
            "ratio": round(returned_n / requested_n, 4) if requested_n else None,
        },
        "truncated": bool(pool.get("truncated")),
        "review_codes": _dedupe_codes(
            list(existing.get("review_codes") or []) + list(ctx.review_codes or []))
        if ctx.review_only else [code for code in requested_codes
                                 if code not in set(_dragon_codes(ctx.dragons_rows))],
    }
    payload["review_health"] = _review_health(payload, "rows")
    return payload


def _stock_daily_suspicious(payload: dict) -> Optional[str]:
    requested = payload.get("requested_codes") or []
    if not requested:
        return None
    coverage = payload.get("coverage") or {}
    ratio = coverage.get("ratio")
    if isinstance(ratio, (int, float)) and ratio < 0.8:
        return f"日 K 覆盖 {ratio:.0%} < 80%，疑数据源未填充"
    return None


# ─── M5：T-1 计划 / T+1 竞价反馈 / 盘中刻度 ─────────────────────────────────

_PLAN_POSITION_KEYS = (
    "code", "name", "role", "review_score", "review_grade", "position_pct",
    "eligibility", "reason_code", "mainline", "confidence",
)
_PLAN_FEEDBACK_KEYS = ("code", "role", "position_pct", "expected",
                       "allowed_actions", "unknown_reason")


def _previous_trade_date(ctx: PrepContext) -> Optional[str]:
    """窗口内 ``< trade_date`` 的最近交易日（T-1）；无日历 → None。"""

    dates = sorted(day for day in (ctx.window_trade_dates or [])
                   if day and day < ctx.trade_date)
    return dates[-1] if dates else None


def _read_prep_snapshot(trade_date: str):
    """读取某日快照（供 prev_plan 复制 T-1 计划）；无记录返回 None。"""

    from dragon_quant.infra.assets import db as assets_db

    return assets_db.get_prep_snapshot(trade_date)


def _context_hash(context: dict) -> Optional[str]:
    """``strategy_context`` canonical JSON → sha256 前 12 位（血缘，非业务键）。"""

    try:
        payload = json.dumps(context, sort_keys=True, ensure_ascii=False,
                             default=str)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _collect_prev_plan(ctx: PrepContext) -> dict:
    """T-1 计划物化：只读 ``prep_snapshots`` 的 ``strategy_context``。

    - 位置/冻结预期逐条复制，供 T+1 反馈使用；T+1 **不重算**预期。
    - 无 T-1 快照 → 合法 empty（首日无计划），不写 error。
    """

    source_date = _previous_trade_date(ctx)
    payload = {
        "as_of": ctx.today,
        "source_trade_date": source_date,
        "context_version": None,
        "context_hash": None,
        "market_state": None,
        "positions": [],
        "feedback_plans": [],
        "degraded": [],
    }
    if source_date is None:
        payload["degraded"] = ["no_previous_trade_date"]
        return payload
    snapshot = _read_prep_snapshot(source_date)
    context = snapshot.get("strategy_context") if isinstance(snapshot, dict) else None
    if not isinstance(context, dict):
        payload["degraded"] = ["no_prev_plan"]
        return payload

    positions = [
        {key: row.get(key) for key in _PLAN_POSITION_KEYS}
        for row in _list_of_dicts(context.get("positions"))
        if isinstance(row.get("code"), str) and row.get("code")
    ]
    plans = [
        {key: row.get(key) for key in _PLAN_FEEDBACK_KEYS}
        for row in _list_of_dicts(context.get("feedback_plans"))
        if isinstance(row.get("code"), str) and row.get("code")
    ]
    market = context.get("market") if isinstance(context.get("market"), dict) else {}
    payload.update({
        "context_version": context.get("context_version"),
        "context_hash": _context_hash(context),
        "market_state": market.get("state"),
        "positions": positions,
        "feedback_plans": plans,
    })
    return payload


def _auction_item_summary(items) -> Optional[dict]:
    """竞价明细 → 09:25 终态（最后一根）；price<=0 或明细损坏 → None。"""

    rows = _list_of_dicts(items)
    if not rows:
        return None
    last = max(rows, key=lambda item: str(item.get("time") or ""))
    price = last.get("price")
    price = float(price) if isinstance(price, (int, float)) and price > 0 else None
    matched = last.get("matched")
    matched = int(matched) if isinstance(matched, (int, float)) and matched >= 0 else 0
    if price is None:
        return None
    return {
        "price": price,
        "amount": price * matched,
        "matched": matched,
        "unmatched": int(last.get("unmatched") or 0),
    }


def _local_auction_rows(trade_date: str) -> dict[str, dict]:
    """同日 ``auction_assets`` → {code: 09:25 终态}（JSON 损坏的行跳过）。"""

    from dragon_quant.infra.assets import db as assets_db

    rows: dict[str, dict] = {}
    for asset in assets_db.list_auction_assets(trade_date):
        if not isinstance(asset, dict) or asset.get("items_error"):
            continue
        code = asset.get("code")
        summary = _auction_item_summary(asset.get("items"))
        if isinstance(code, str) and code and summary:
            rows[code] = summary
    return rows


def _prior_auction_amounts(codes: list[str], ctx: PrepContext) -> dict[str, list]:
    """T-1..T-5 的竞价成交额（新→旧**定长**列表，缺失日为 ``None``）。

    定长保留日序，纯函数才能区分「昨日缺失 → 用历史均值」与「昨日有效」；
    全部缺失时列表全为 ``None``，竞价比保持 ``None``（不伪造 1.0）。
    """

    dates = sorted((day for day in (ctx.window_trade_dates or [])
                    if day and day < ctx.trade_date), reverse=True)[:5]
    history: dict[str, list] = {code: [] for code in codes}
    for day in dates:
        local = _local_auction_rows(day)
        for code in codes:
            row = local.get(code)
            history[code].append(
                row["amount"] if row and row.get("amount") is not None else None)
    return history


def _collect_auction_feedback(ctx: PrepContext) -> dict:
    """T+1 竞价反馈：先读同日本地资产，缺口再走同日 TDX 补采。

    * 代码集合 = ``prev_plan.positions[].code``（不是当日池，否则错配）；
    * 只补「真实缺口」，不改变 ``auction`` 命令、其池或 JSON；
    * 只有真实补采失败/未验证才 ``unknown + auction_backfill_failed``。
    """

    assets = _ctx_assets(ctx)
    plan = assets.get("prev_plan") if isinstance(assets.get("prev_plan"), dict) else {}
    codes = _dedupe_codes(plan.get("positions"))[:REVIEW_BACKFILL_LIMIT]
    payload = {
        "as_of": ctx.today,
        "rows_by_code": {},
        "sources": [],
        "backfill": {"attempted": 0, "ok": 0, "failed": []},
        "unverified_count": 0,
        "degraded": [],
    }
    if not codes:
        return payload

    local = _local_auction_rows(ctx.trade_date)
    history = _prior_auction_amounts(codes, ctx)
    rows: dict[str, dict] = {}
    sources: set[str] = set()
    for code in codes:
        summary = local.get(code)
        if summary:
            rows[code] = {
                **summary,
                "source": "auction_assets",
                "verified": True,
                "prev_amounts": history.get(code, []),
                "unknown_reason": None,
            }
            sources.add("auction_assets")
            continue
        payload["backfill"]["attempted"] += 1
        try:
            snapshot = ctx.tdx.get_auction_snapshot(code)
        except Exception as e:  # 单只失败不中断：该只 unknown，其余照常
            snapshot = None
            error = f"{type(e).__name__}: {e}"
        else:
            error = None
        if isinstance(snapshot, dict) and (snapshot.get("price") or 0) > 0:
            rows[code] = {
                "price": snapshot.get("price"),
                "amount": snapshot.get("amount"),
                "matched": snapshot.get("matched"),
                "unmatched": snapshot.get("unmatched"),
                "source": "tdx_same_day",
                "verified": True,
                "prev_amounts": history.get(code, []),
                "unknown_reason": None,
            }
            sources.add("tdx_same_day")
            payload["backfill"]["ok"] += 1
            continue
        rows[code] = {
            "price": None, "amount": None, "matched": None, "unmatched": None,
            "source": "missing", "verified": False,
            "prev_amounts": history.get(code, []),
            "unknown_reason": "auction_backfill_failed",
        }
        sources.add("missing")
        payload["backfill"]["failed"].append(
            {"code": code, "error": error or "empty_snapshot"})

    payload["rows_by_code"] = rows
    payload["sources"] = sorted(sources)
    payload["unverified_count"] = sum(
        1 for row in rows.values() if not row.get("verified"))
    if payload["backfill"]["failed"]:
        payload["degraded"] = ["auction_backfill_failed"]
    return payload


def _intraday_time(bar) -> Optional[str]:
    timestamp = getattr(bar, "timestamp", 0) or 0
    if not timestamp:
        return None
    try:
        moment = datetime.fromtimestamp(timestamp / 1000.0, SHANGHAI)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%H:%M")


def _collect_t1_intraday(ctx: PrepContext) -> dict:
    """T+1 盘中 5 分钟刻度（仅 ``prev_plan.positions`` 代码；缺失降级 close_only）。

    只作冻结 playbook 的对照证据（09:25 / 10:00），不用于实时判定；单只失败
    记 ``missing``，不伪造 K 线。
    """

    assets = _ctx_assets(ctx)
    plan = assets.get("prev_plan") if isinstance(assets.get("prev_plan"), dict) else {}
    codes = _dedupe_codes(plan.get("positions"))[:REVIEW_BACKFILL_LIMIT]
    rows: dict[str, list] = {}
    missing: list[str] = []
    if codes:
        for code in codes:
            try:
                bars = ctx.tdx.get_5min_kline_for_date(code, ctx.trade_date)
            except Exception:  # 单只失败 → 降级，不让整项 error
                bars = []
            converted: list[dict] = []
            for bar in bars or []:
                moment = _intraday_time(bar)
                close = getattr(bar, "close", None)
                if moment is None or not isinstance(close, (int, float)) or close <= 0:
                    continue
                converted.append({
                    "time": moment,
                    "close": float(close),
                    "volume": getattr(bar, "volume", None),
                })
            if converted:
                rows[code] = sorted(converted, key=lambda row: row["time"])
            else:
                missing.append(code)
    return {
        "as_of": ctx.today,
        "trade_date": ctx.trade_date,
        "rows": rows,
        "missing_codes": missing,
        "coverage": {
            "requested": len(codes),
            "returned": len(rows),
            "missing": missing,
            "ratio": round(len(rows) / len(codes), 4) if codes else None,
        },
        "checkpoint": list(INTRADAY_CHECKPOINTS),
        "degraded": [] if rows else (["t1_intraday_missing"] if codes else []),
    }



def _collect_macro(ctx: PrepContext) -> dict:
    """宏观扰动：日经/A50 期指（同日）、美债 10Y、黄金（T-1）。

    源对象：tdx（扩展行情日 K，主链路）、yahoo（仅美债/黄金回退）。
    日经/A50 的 priority 只有 tdx，无回退；全部失败由 collect_macro 抛 RuntimeError。
    腾讯/雪球 provider 的宏观方法保留历史契约，但当前 MACRO_SPECS 无该源路由。
    """
    sources = {
        "tdx": ctx.tdx,
        "yahoo": legacy("YahooMacroSource")(),
    }
    return legacy("collect_macro")(sources, ctx.trade_date, as_of=ctx.trade_date)


def _macro_suspicious(payload: dict) -> Optional[str]:
    if not (payload.get("instruments") or {}):
        return "宏观品种全部缺失"
    return None


ASSET_COLLECTORS: dict[str, CollectorSpec] = {
    # 顺序有意义：dragons 先采（fund_flow/cninfo/stock_daily 依赖候选 codes）
    "dragons": CollectorSpec(_collect_dragons, same_day_only=False),
    "ths_limit_up_pool": CollectorSpec(_collect_ths_limit_up_pool, same_day_only=False,
                                       suspicious_check=_pool_suspicious),
    "ths_block_top": CollectorSpec(_collect_ths_block_top, same_day_only=False),
    "market_breadth": CollectorSpec(_collect_market_breadth, same_day_only=True,
                                    suspicious_check=_breadth_suspicious),
    "fg_boards": CollectorSpec(_collect_fg_boards, same_day_only=True,
                               suspicious_check=_fg_suspicious),
    # holdings 在 fund_flow 之前：详细池需要持仓代码参与超集采集
    "holdings": CollectorSpec(_collect_holdings, same_day_only=False),
    "fund_flow": CollectorSpec(_collect_fund_flow, same_day_only=True,
                               suspicious_check=_fund_flow_suspicious),
    "cninfo_announcements": CollectorSpec(_collect_cninfo, same_day_only=False),
    # 宏观日 K canonical：对历史日可确定性重建，不设 same_day_only
    "macro_global": CollectorSpec(_collect_macro, same_day_only=False,
                                  suspicious_check=_macro_suspicious),
    # stock_daily 最后：依赖 dragons + 涨停池 + 主线 + holdings 的完整池
    "stock_daily": CollectorSpec(_collect_stock_daily, same_day_only=False,
                                 suspicious_check=_stock_daily_suspicious),
    # M5：prev_plan 必须先于 auction_feedback / t1_intraday（后者只消费其 codes）。
    # 技术 §7 的注册顺序表把 auction_feedback 写在 prev_plan 之前，与其自身
    # 「代码集合 = prev_plan.positions」的依赖声明矛盾；此处按依赖正确顺序注册。
    "prev_plan": CollectorSpec(_collect_prev_plan, same_day_only=False),
    "auction_feedback": CollectorSpec(_collect_auction_feedback,
                                      same_day_only=True),
    "t1_intraday": CollectorSpec(_collect_t1_intraday, same_day_only=True),
}


# ─── 分类 / 健康判定 / report 合并 ──────────────────────────────────────────

_EMPTY_KEYS = {
    "dragons": lambda p: p.get("count", 0) == 0,
    "ths_limit_up_pool": lambda p: not (p.get("rows") or []) and not p.get("total"),
    "ths_block_top": lambda p: not (p.get("rows") or []),
    "market_breadth": lambda p: p.get("sample_n", 0) == 0,
    "fg_boards": lambda p: not any((b.get("rows") or [])
                                   for b in (p.get("boards") or {}).values()),
    # fund_flow / cninfo 的空判据只看 legacy 子集（详细池补充代码不得改旧健康）
    "fund_flow": lambda p: not _scoped_container(p, "quotes"),
    "cninfo_announcements": lambda p: not _scoped_container(p, "items"),
    "macro_global": lambda p: not (p.get("instruments") or {}),
    "holdings": lambda p: not (p.get("codes") or []),
    "stock_daily": lambda p: not (p.get("rows") or {}),
    "prev_plan": lambda p: not (p.get("positions") or []),
    "auction_feedback": lambda p: not (p.get("rows_by_code") or {}),
    "t1_intraday": lambda p: not (p.get("rows") or {}),
}


def _classify_payload(asset_type: str, payload: dict, spec: CollectorSpec) -> dict:
    """payload → collection_report 条目（状态 + 可疑说明）。

    **total**：分类器自身异常也落成 error 条目，绝不向上抛（否则会击穿采集段隔离）。
    """
    try:
        check = _EMPTY_KEYS.get(asset_type, lambda p: not p)
        status = "empty" if check(payload) else "ok"
        note = spec.suspicious_check(payload) if spec.suspicious_check else None
        entry: dict = {"status": status}
        if note:
            entry["note"] = note
            if status == "ok":
                entry["status"] = "empty_suspicious"
        return entry
    except Exception as e:  # 分类器缺陷不得击穿采集段
        return {"status": "error", "error": f"classify: {type(e).__name__}: {e}"}


def _report_status(report, asset_type: str) -> Optional[str]:
    """从 collection_report 防御式读取单资产状态（条目非 dict → None）。"""
    entry = report.get(asset_type) if isinstance(report, dict) else None
    return entry.get("status") if isinstance(entry, dict) else None


def asset_health(asset_type: str, payload, status, trade_date: str) -> Optional[str]:
    """**可用性**谓词（compute 的唯一语义来源）：返回不可用原因，None = 可用。

    与 ``_prepare_assets`` 的既有语义一致：payload 存在 + 状态非坏 + 必然非空资产非 empty
    + 三类 as_of 匹配。非 dict payload / dragons.rows 非 list → ``malformed``（不进入 compute）。
    report 条目非 dict 时调用方传 ``status=None``（等同无条目）。
    """
    if payload is None:
        return "missing"
    if not isinstance(payload, dict):
        return "malformed"
    if isinstance(status, str) and status in _BAD_STATUS:
        return status
    if status == "empty" and asset_type in _MUST_NONEMPTY:
        return "empty"
    if asset_type == "dragons":
        rows = payload.get("rows")
        if rows is not None and not isinstance(rows, list):
            return "malformed"
    if asset_type in _AS_OF_CHECKED:
        as_of = payload.get("as_of")
        if as_of and as_of != trade_date:
            return "as_of_mismatch"
    return None


def collection_health(asset_type: str, payload, status, trade_date: str) -> Optional[str]:
    """**采集**谓词 = 可用性 ∪ 窗口降级（仅 dragons）。

    窗口降级是「可用但告警」：dragons 仍进入 compute，只是触发本地重读，
    以便后来补跑 scan 时把新一天纳入窗口。
    """
    reason = asset_health(asset_type, payload, status, trade_date)
    if reason is not None:
        return reason
    if asset_type == "dragons":
        marks = _window_degradations(payload)
        if marks:
            return marks[0]
    return None


def is_collectable(asset_type: str, trade_date: str, today: str) -> bool:
    """可采性矩阵：same_day_only 仅 ``trade_date == today`` 可采；未知类型不可采。"""
    spec = _collectors().get(asset_type)
    if spec is None:
        return False
    return not (spec.same_day_only and trade_date < today)


def _asset_dragon_rows(payload) -> list:
    """dragons payload → rows（防御式：非 dict / 非 list 一律空）。"""
    if not isinstance(payload, dict):
        return []
    return _list_of_dicts(payload.get("rows"))


def _asset_codes(payload) -> list[str]:
    """dragons payload → 并集 codes（保持首次出现顺序）。"""
    return _dragon_codes(_asset_dragon_rows(payload))


def _collect_all(trade_date: str, *, window_trade_dates: list,
                 asset_types: list[str],
                 window_days: int = WINDOW_TRADING_DAYS,
                 tdx: Optional[TdxProvider] = None,
                 existing: Optional[dict] = None,
                 now: Optional[datetime] = None,
                 services=None,
                 review_only: bool = False,
                 review_codes: Optional[list] = None) -> tuple[dict[str, dict], dict]:
    """采集段：只执行 ``asset_types`` 指定的 collector，逐项隔离异常。

    - 可采性由调用方（``plan_prep``）决定；入口对不可采/未知类型直接 ``ValueError``，
      不做 same_day_only 跳过、也不为跳过项写 unavailable（report 只记本次真实执行结果）。
    - dragons 不参与本次执行时，用 ``existing`` 的 dragons 预热候选 codes；仅当存量
      dragons 经 ``asset_health`` 判定可用（rows 可解析）时置 ``dragons_ready``。
      依赖 dragons codes 的采集器在未就绪时不执行（记 error），避免空 payload 覆盖存量。
    - 每个 collector：payload 必须是 dict → 分类（total）→ 通过后才写入 payloads；
      任何异常都不写 payload，保证「在 payloads 中 = 本次成功且 payload 与状态自洽」。
    - ``window_trade_dates`` 必填（由 ``resolve_window_dates`` 从已校验日历解析）；
      ``now`` 与 run_prep 的门禁共用同一实例（ctx.today / as_of 同源）。
    - ``services`` 为可选 M2 装配注入口：其 ``providers`` 非空时四个 provider 全部
      取自 bundle（不构造任何默认 provider）；省略或 bundle 为空时沿用旧构造路径。
    - ``review_only`` / ``review_codes``：review 独立修复模式，只请求缺失代码并与
      存量 payload 按键合并；调用方不得把本次 report 并入 ``collection_report``。
    """
    now = now or clock.now(SHANGHAI)
    today = now.strftime("%Y-%m-%d")
    invalid = [t for t in asset_types if not is_collectable(t, trade_date, today)]
    if invalid:
        raise ValueError(
            f"不可采资产被请求：{invalid}（trade_date={trade_date}, today={today}）")
    bundle = _providers_from_services(services)
    seeded = {k: v for k, v in (existing or {}).items() if k != "collection_report"}
    ctx = PrepContext(
        trade_date=trade_date, today=today,
        tdx=tdx or (bundle.tdx if bundle is not None else legacy("TdxProvider")()),
        ths=bundle.ths if bundle is not None else legacy("THSProvider")(),
        tencent=bundle.tencent if bundle is not None else legacy("TencentProvider")(),
        xueqiu=bundle.xueqiu if bundle is not None else legacy("XueqiuProvider")(),
        window_trade_dates=list(window_trade_dates),
        window_days=window_days,
        collected=dict(seeded),
        review_only=review_only,
        review_codes=list(review_codes or []))
    if "dragons" not in asset_types and existing:
        stored_report = existing.get("collection_report")
        stored_status = _report_status(stored_report, "dragons")
        stored_dragons = existing.get("dragons")
        if asset_health("dragons", stored_dragons, stored_status, trade_date) is None:
            ctx.dragons_rows = _asset_dragon_rows(stored_dragons)
            ctx.dragons_ready = True
            ctx.collected["dragons"] = stored_dragons
    payloads: dict[str, dict] = {}
    report: dict[str, dict] = {}
    for asset_type in asset_types:
        spec = _collectors().get(asset_type)
        if spec is None:
            continue
        if asset_type in _REVIEW_ONLY_ASSETS and not review_assets_enabled():
            continue
        if asset_type in _DRAGON_DEPENDENTS and not ctx.dragons_ready:
            report[asset_type] = {"status": "error",
                                  "error": "dragons 不可用，无法确定候选 codes"}
            continue
        try:
            payload = spec.collect(ctx)
            if not isinstance(payload, dict):
                raise TypeError(f"collector 返回 {type(payload).__name__}，预期 dict")
            entry = _classify_payload(asset_type, payload, spec)
            if entry.get("status") == "error":
                # 分类异常 = 本次采集失败：不写 payload，交 merge 层保留存量健康状态
                raise RuntimeError(entry.get("error") or "payload 分类失败")
            payloads[asset_type] = payload
            report[asset_type] = entry
            ctx.collected[asset_type] = payload
        except Exception as e:  # 单点失败不中断：不写 payload，记 error 继续下一个
            payloads.pop(asset_type, None)
            report[asset_type] = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    return payloads, report


# ─── 资产可用性过滤 / 窄口径修复 / code 覆盖 ────────────────────────────────

_REPORT_AUDIT_KEYS = {"report_reset"}


def _prepare_assets(assets: dict, trade_date: str) -> tuple[dict[str, dict], list[str]]:
    """按 collection_report + as_of 过滤不可用资产，返回 (可用资产, 降级名单)。

    可用性统一由 ``asset_health`` 判定（与增量采集共用唯一语义来源）：
    - status ∈ {error, unavailable, empty_suspicious} → 按缺失降级（不使用其值）；
    - status == empty 且资产正常交易日不可能为空（market_breadth/fg_boards）→ 同样降级；
    - payload.as_of != trade_date → 时点不符，降级（防止跨日残留/外部导入污染）；
    - payload 非 dict / dragons.rows 非 list → malformed，降级。
    **窗口降级不剔除 dragons**：可用的 dragons 额外把 ``_window_degradations`` 追加到
    degraded（仅用于报告/CLI 理由），仍进入 compute。
    遍历 ASSET_COLLECTORS 全集 ∪ assets 键 ∪ report 键：完全缺席的期望资产也必须在
    degraded 中可见（历史日 same_day_only 不采集、不写 report 时尤其重要）。
    原始 payload 仍保留在 prep_assets 中供审计，仅 compute 不消费。
    """
    report = assets.get("collection_report")
    report = report if isinstance(report, dict) else {}
    usable: dict[str, dict] = {}
    degraded: list[str] = []
    seen: set[str] = set()
    dynamic_keys = [k for k in list(assets) + list(report)
                    if k not in _REPORT_AUDIT_KEYS]
    for asset_type in list(_collectors()) + dynamic_keys:
        if asset_type in seen or asset_type == "collection_report":
            continue
        seen.add(asset_type)
        checked = _usable_payload(asset_type, assets.get(asset_type),
                                  _report_status(report, asset_type), trade_date)
        if checked is None:
            # M5 反馈资产缺失只由 feedback/plan_review 自身降级表达，不污染旧链路。
            if asset_type not in _FEEDBACK_ASSETS:
                degraded.append(asset_type)
            continue
        usable[asset_type] = checked
        if asset_type == "dragons":
            degraded.extend(_window_degradations(checked))
    # 窗口加宽后新入候选可能缺 fund_flow / cninfo 覆盖（历史日 same_day_only 无法补采）：
    # 只让降级原因可见，绝不伪造资金/公告数据（否则 verdict 静默降档会被误读为资金面变差）。
    dragon_codes = _asset_codes(usable.get("dragons"))
    if dragon_codes and _uncovered_assets(usable, dragon_codes, set()):
        degraded.append("dragons_window_widened_dependents_stale")
    return usable, sorted(set(degraded))


def _usable_payload(asset_type: str, payload, status, trade_date: str) -> Optional[dict]:
    """资产可用性窄化：非 dict 或 ``asset_health`` 判降级 → ``None``。

    非 dict 时 ``asset_health`` 本身必返回 ``missing`` / ``malformed``（非 None），
    因此这条窄化与「健康检查不通过」完全同构，只是把结论显式化给类型检查器。
    """
    if not isinstance(payload, dict):
        return None
    if asset_health(asset_type, payload, status, trade_date) is not None:
        return None
    return payload


def _window_degradations(payload) -> list[str]:
    """窗口口径降级标记（防御式读取：畸形 payload 不得击穿 compute）。

    - ``window_days`` 缺失/非 int → ``[]``（旧资产无口径信息，不假设）；
    - ``window_days < WINDOW_TRADING_DAYS`` → ``dragons_window_stale``
      （含 ``window_days=1`` 的旧单日资产：普通 prep 会本地重读自动升级，
      ``--recompute`` 仍按旧口径重放，保留 R1 既有语义）；
    - 窗口日期不足 → ``dragons_window_short``；
    - 某日因镜像缺行被标 error → ``dragons_mirror_missing_day``（数据丢失）；
    - 其它 empty/error 日 → ``dragons_partial``（真实空日或读取失败）。
    """
    if not isinstance(payload, dict):
        return []
    window_days = payload.get("window_days")
    if not isinstance(window_days, int) or isinstance(window_days, bool):
        return []
    marks: list[str] = []
    if window_days < WINDOW_TRADING_DAYS:
        marks.append("dragons_window_stale")
    window_dates = payload.get("window_trade_dates")
    window_dates = window_dates if isinstance(window_dates, list) else []
    if len(window_dates) < window_days:
        marks.append("dragons_window_short")
    days = _list_of_dicts(payload.get("days"))
    if any(_is_mirror_stale_day(day) for day in days):
        marks.append("dragons_mirror_missing_day")
    if any(day.get("status") in ("empty", "error") and not _is_mirror_stale_day(day)
           for day in days):
        marks.append("dragons_partial")
    return marks


def plan_repairs(assets: dict, trade_date: str, today: str) -> dict[str, dict]:
    """C3 窄口径存量修复：旧版本跳过采集时误写 ``unavailable`` 的 report 条目。

    安全条件（全部满足才修复，不采集）：
      status == "unavailable" ∧ same_day_only ∧ 历史日不可采 ∧ payload 是 dict
      ∧ payload.as_of == trade_date ∧ ``_classify_payload`` 自检为 ok。

    旧代码的 unavailable 分支只 ``continue``、从不写 payload，因此「同日 as_of 的完好
    payload + unavailable」只可能是跳过覆写造成的污染。**不扩散**到 error /
    empty_suspicious（它们可能对应真实坏数据）。
    """
    report = assets.get("collection_report")
    if not isinstance(report, dict):
        return {}
    repairs: dict[str, dict] = {}
    for asset_type, spec in _collectors().items():
        if not spec.same_day_only:
            continue
        if is_collectable(asset_type, trade_date, today):
            continue
        if _report_status(report, asset_type) != "unavailable":
            continue
        payload = assets.get(asset_type)
        if not isinstance(payload, dict) or payload.get("as_of") != trade_date:
            continue
        if _classify_payload(asset_type, payload, spec).get("status") != "ok":
            continue
        repairs[asset_type] = {"status": "ok", "restored": "legacy_unavailable"}
    return repairs


def plan_review_repairs(assets: dict, trade_date: str, today: str) -> ReviewRepairPlan:
    """review 独立覆盖修复计划（与 legacy ``plan_repairs`` / ``plan.collect`` 分离）。

    步骤固定：最新池 → 逐类型求差 → 只保留可采类型；不可采（历史日
    ``same_day_only``）进 ``unavailable``，只由 ``review.degraded`` 消费。
    ``review_assets_enabled=false`` 时整个计划不生成（行为等价于本期未上线）。

    不写任何「已修复」标记：失败后覆盖缺口仍在，下一轮会重新得出同一 ``missing``。
    """

    if not review_assets_enabled():
        return ReviewRepairPlan(enabled=False)
    source = assets if isinstance(assets, dict) else {}
    pool = derive_pool_codes(source)
    pool_codes = list(pool["codes"])
    missing: dict[str, list[str]] = {}
    collect: list[str] = []
    unavailable: list[str] = []
    for asset_type in _REVIEW_ASSET_TYPES:
        if asset_type not in _collectors():
            continue
        payload = source.get(asset_type)
        key = _REVIEW_CONTAINER_KEYS[asset_type]
        container = payload.get(key) if isinstance(payload, dict) else None
        container = container if isinstance(container, dict) else {}
        gaps = [code for code in pool_codes if code not in container]
        if not gaps:
            continue
        missing[asset_type] = gaps
        if is_collectable(asset_type, trade_date, today):
            collect.append(asset_type)
        else:
            unavailable.append(asset_type)
    return ReviewRepairPlan(
        collect=collect,
        missing=missing,
        pool_codes=pool_codes,
        unavailable=unavailable,
    )


def _uncovered_assets(assets: dict, codes: list[str], selected: set[str]) -> list[str]:
    """code 覆盖 diff：fund_flow/cninfo/stock_daily 的键未覆盖当前龙头并集 codes → 需补采。"""
    wanted = set(codes)
    if not wanted:
        return []                      # 无候选：覆盖要求为空（vacuous）
    missing: list[str] = []
    for asset_type, key in _CODE_COVERED_KEYS.items():
        if asset_type in selected:
            continue
        payload = assets.get(asset_type)
        keys = payload.get(key) if isinstance(payload, dict) else None
        keys = keys if isinstance(keys, dict) else {}
        if not wanted <= set(keys):
            missing.append(asset_type)
    return missing


def _stored_asset_healthy(stored: dict, asset_type: str, status,
                          trade_date: str) -> bool:
    """存量资产是否「可用且质量自检通过」（失败补采的保留判据）。"""
    spec = _collectors().get(asset_type)
    if spec is None:
        return False
    payload = _usable_payload(asset_type, stored.get(asset_type), status, trade_date)
    if payload is None:
        return False
    # D1 允许「非必然非空资产」的合法 empty（dragons/cninfo/fund_flow 的空是确定事实）：
    # 补采失败时应保留该合法空状态，而不是写成 error。
    return _classify_payload(asset_type, payload, spec).get("status") in ("ok", "empty")


def merge_report_entries(stored: dict, report: dict, payloads: dict,
                         trade_date: str, now: datetime) -> dict:
    """本次执行结果 → 待落库 report：**失败补采不得把既有健康资产降级**。

    - 成功（asset_type in payloads）：用新条目（payload 与状态自洽，含 empty_suspicious）；
    - 失败（不在 payloads）：若存量资产可用且质量自检通过，保留存量条目并追加
      ``last_attempt={status,error,at}``；存量条目缺失时新建仅含 ``last_attempt`` 的条目
      （不含 status，``asset_health`` 读到 status=None 仍判可用）。
    """
    existing = stored.get("collection_report")
    existing = existing if isinstance(existing, dict) else {}
    merged: dict = dict(report)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    for asset_type, entry in report.items():
        if asset_type in payloads:
            continue
        old = existing.get(asset_type)
        status = _report_status(existing, asset_type)
        if not _stored_asset_healthy(stored, asset_type, status, trade_date):
            continue
        base = dict(old) if isinstance(old, dict) else {}
        base["last_attempt"] = {**entry, "at": stamp}
        merged[asset_type] = base
    return merged
