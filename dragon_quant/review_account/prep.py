"""盘后复盘：两段式流水线（采集资产 → 纯函数计算），产出可扩展 JSON 快照。

供 `prep` CLI 命令调用；review-account / buy-sell 经 `collect_candidates` 消费快照。

架构（见 docs/solutions/2026-10-21-prep-incremental-rerun-design.md）：
  - collect 段：默认**增量**——先读存量资产 + collection_report，逐资产做健康判定，
    全健康则零采集直接 compute；否则只补采不健康且可采的 asset_type（dragons 重采且
    code 集合变化时级联 fund_flow/cninfo），逐 collector 隔离异常，payload 与本次
    report 条目经 ``assets_db.save_prep_assets`` 单事务落库。
  - compute 段：只从 prep_assets 读回（快照 = 资产的纯函数），逐因子错误隔离；
    ``--recompute`` 时用已存资产重算快照（零检测零采集，算法升级可重放历史口径）。
  - same_day_only 资产（FG/实时行情类）仅 ``trade_date == today`` 可采；历史日不采集、
    不写 report，由 compute 按缺失降级，防止时点污染。
  - collection_report 语义 = 「该日期资产当前最佳状态」：只合并本次实际执行的条目，
    补采失败不覆盖既有健康状态（记 ``last_attempt``），零采集时完全不动。

数据源：tmdx（MacClient）优先，THS dataapi 次之；排除东财。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from dragon_quant.providers.tdx import TdxProvider
from dragon_quant.providers.ths import THSProvider
from dragon_quant.providers.tencent import TencentProvider
from dragon_quant.utils.trading import tdx_trade_dates
from dragon_quant.providers.xueqiu import XueqiuProvider
from dragon_quant.providers.macro import (
    CALIBER_PREVIOUS, CALIBER_SAME_DAY, MACRO_INDEX_KEYS, MACRO_KEYS,
    SPEC_BY_KEY, YahooMacroSource, collect_macro,
)
from dragon_quant.review_account.verdicts import (
    evaluate_candidates, find_risk_announcement,
)
from dragon_quant.storage import db, assets_db

SHANGHAI = ZoneInfo("Asia/Shanghai")

# ─── 常量（集中调参）────────────────────────────────────────────────────────

# FG(风格)板块清单：昨日涨停溢价/晋级交叉/龙虎榜名单（T-1 语义，仅当日采集）
FG_BOARD_NAMES = ["昨日涨停", "昨日跌停", "昨日连板", "昨日首板", "昨日断板", "昨日上榜"]

CNINFO_WINDOW_DAYS = 7   # 公告窗口：保留 [T-7, T] 内公告
CNINFO_MAX_PAGES = 5     # 翻页上限保护；触顶且未到窗口 → truncated

# 复盘窗口：含 T 在内的最近 N 个日历交易日（T-2 / T-1 / T）
WINDOW_TRADING_DAYS = 3

_LIMIT_TOL_UP = 0.999    # 涨停/炸板判定容差（涨停价的 0.1%）
_LIMIT_TOL_DOWN = 1.001

# 全 A 扫描有效样本下限：低于此值视为 data source 未填充（非真实市场极值）
SAMPLE_N_MIN = 3000

# collection_report 中「不可用」的状态：compute 按缺失降级，绝不使用其值
_BAD_STATUS = {"error", "unavailable", "empty_suspicious"}
# 正常交易日不可能为空的资产：status=empty 也按缺失处理
_MUST_NONEMPTY = {"market_breadth", "fg_boards", "macro_global"}
# 需要校验 as_of == trade_date 的资产：仅 same_day_only（实时/时点快照）类。
# 注意 cninfo_announcements 的 as_of 是采集时间而非数据时点（公告可历史回溯），
# 其窗口正确性由采集阶段 [T-window, T] 过滤保证，不做 as_of 相等校验。
_AS_OF_CHECKED = {"market_breadth", "fg_boards", "fund_flow"}

# 依赖 dragons 候选 codes 的资产：dragons 重采且 code 集合变化时级联补采
_DRAGON_DEPENDENTS = ("fund_flow", "cninfo_announcements")
# code 覆盖校验：payload 键里应覆盖当前龙头并集 codes
_CODE_COVERED_KEYS = {"fund_flow": "quotes", "cninfo_announcements": "items"}

# 盘后运行门禁：当日 ≥16:40 才允许（A50 期指日盘 16:30 收盘 + 缓冲）
_AFTER_CLOSE = (16, 40)


class PrepError(ValueError):
    """prep 日期门禁/资产缺失等可预期错误（CLI 捕获后打印并退出）。"""


# ─── v0.1 情绪 → 建议仓位启发式（阈值不变，输入口径升级为 T 日权威值）─────────


def position_advice(limit_up_cnt: int, limit_down_cnt: int) -> tuple:
    """情绪 → 建议仓位启发式（阈值集中于此，后续回测调参）。"""
    if limit_up_cnt >= 80 and limit_down_cnt <= 5:
        return "重仓", 0.8
    if limit_down_cnt >= 30:
        return "空仓", 0.0
    if limit_down_cnt >= 10 or limit_up_cnt <= 15:
        return "轻仓", 0.3
    return "正常", 0.5


# ─── 采集层 ────────────────────────────────────────────────────────────────


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
    tencent: Optional[object] = None
    xueqiu: Optional[object] = None


@dataclass
class CollectorSpec:
    collect: Callable[[PrepContext], dict]
    same_day_only: bool
    # 空结果可疑检查：返回 note 字符串则记 empty_suspicious（区分「数据源未填充」与「真空」）
    suspicious_check: Optional[Callable[[dict], Optional[str]]] = None


def _collect_dragons(ctx: PrepContext) -> dict:
    """窗口内逐日冻结真龙（compute 的唯一真龙来源；含逐日 scan_id/version 血缘）。

    逐日异常隔离：单日读取失败只把该日标 error，其它日继续；仅当**并集无任何行
    且存在 error 日**时 raise，避免把取数故障伪装成「今日无龙头」。
    """
    window = list(ctx.window_trade_dates or [ctx.trade_date])
    rows_all: list[dict] = []
    days: list[dict] = []
    errors: list[str] = []
    for day in window:
        try:
            raw_rows = db.get_dragons(day, source="v2")
            if not isinstance(raw_rows, list):
                raise TypeError(f"get_dragons 返回 {type(raw_rows).__name__}，预期 list")
            day_rows = _list_of_dicts(raw_rows)
            scan_ids = sorted({r.get("scan_id") for r in day_rows if r.get("scan_id")})
            versions = sorted({r.get("version") for r in day_rows if r.get("version")})
            days.append({
                "trade_date": day,
                # 同日多次 scan（不同 topN）无法唯一归属 → null，不输出列表
                "scan_id": scan_ids[0] if len(scan_ids) == 1 else None,
                "version": versions[0] if len(versions) == 1 else None,
                "count": len(day_rows),
                "status": "ok" if day_rows else "empty",
            })
        except Exception as e:  # 单日失败不中断：记 error 继续下一个交易日
            message = f"{type(e).__name__}: {e}"
            errors.append(f"{day}: {message}")
            days.append({"trade_date": day, "count": 0, "status": "error", "error": message})
            continue
        for row in day_rows:
            item = dict(row)
            item["trade_date"] = day       # 保留真实入选日（供 compute 去重/血缘）
            rows_all.append(item)

    if not rows_all and errors:
        raise RuntimeError("三日龙头读取失败：" + "；".join(errors))

    ctx.dragons_rows = rows_all
    ctx.dragons_ready = True          # 含真空（窗口内无真龙）：codes 为空是确定事实
    all_scan_ids = sorted({d["scan_id"] for d in days if d.get("scan_id")})
    all_versions = sorted({d["version"] for d in days if d.get("version")})
    single_day = len(window) == 1
    return {
        "trade_date": ctx.trade_date,
        # 顶层兼容键仅对单日窗口有值；多日窗口血缘一律看 days[]
        "scan_id": all_scan_ids[0] if single_day and len(all_scan_ids) == 1 else None,
        "version": all_versions[0] if single_day and len(all_versions) == 1 else None,
        "count": len(rows_all),
        "rows": rows_all,
        "window_days": ctx.window_days,
        "window_trade_dates": window,
        "days": days,
    }


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


def _collect_fund_flow(ctx: PrepContext) -> dict:
    codes = _dragon_codes(ctx.dragons_rows)
    quotes = ctx.tdx.batch_get_quotes(codes) if codes else []
    return {
        "as_of": ctx.today,
        "quotes": {q.code: {"main_net_amount": q.main_net_amount, "pct": q.pct,
                            "turnover_rate": q.turnover_rate, "amount": q.amount}
                   for q in quotes},
    }


def _fund_flow_suspicious(payload: dict) -> Optional[str]:
    quotes = payload.get("quotes") or {}
    if not quotes:
        return None  # 无候选导致的空由 empty 状态表达
    values = [q.get("main_net_amount") for q in quotes.values()]
    if all(v is None for v in values) or all((v or 0) == 0 for v in values):
        return "main_net_amount 全为 None/0，疑数据源未填充（周六/非交易时段常见）"
    return None


def _collect_cninfo(ctx: PrepContext) -> dict:
    """公告采集：逐页翻至 date <= 窗口起点，过滤未来公告（look-ahead 防护）。"""
    window_start = (datetime.fromisoformat(ctx.trade_date)
                    - timedelta(days=CNINFO_WINDOW_DAYS)).strftime("%Y-%m-%d")
    items = {}
    truncated_codes = []
    for code in _dragon_codes(ctx.dragons_rows):
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
            truncated_codes.append(code)
        items[code] = {
            "announcements": [a for a in anns
                              if window_start <= (a.get("date") or "") <= ctx.trade_date],
            "max_announcement_date": max((a.get("date") or "" for a in anns), default=None),
            "truncated": truncated and bool(anns),
        }
    return {"as_of": ctx.today, "window_days": CNINFO_WINDOW_DAYS,
            "window_start": window_start, "items": items,
            "truncated_codes": sorted(truncated_codes)}


def _collect_macro(ctx: PrepContext) -> dict:
    """宏观扰动：日经/A50 期指（同日）、美债 10Y、黄金（T-1）。

    源对象：tdx（扩展行情日 K，主链路）、yahoo（仅美债/黄金回退）。
    日经/A50 的 priority 只有 tdx，无回退；全部失败由 collect_macro 抛 RuntimeError。
    腾讯/雪球 provider 的宏观方法保留历史契约，但当前 MACRO_SPECS 无该源路由。
    """
    sources = {
        "tdx": ctx.tdx,
        "yahoo": YahooMacroSource(),
    }
    return collect_macro(sources, ctx.trade_date, as_of=ctx.trade_date)


def _macro_suspicious(payload: dict) -> Optional[str]:
    if not (payload.get("instruments") or {}):
        return "宏观品种全部缺失"
    return None


ASSET_COLLECTORS: dict[str, CollectorSpec] = {
    # 顺序有意义：dragons 先采（fund_flow/cninfo 依赖其候选 codes）
    "dragons": CollectorSpec(_collect_dragons, same_day_only=False),
    "ths_limit_up_pool": CollectorSpec(_collect_ths_limit_up_pool, same_day_only=False,
                                       suspicious_check=_pool_suspicious),
    "ths_block_top": CollectorSpec(_collect_ths_block_top, same_day_only=False),
    "market_breadth": CollectorSpec(_collect_market_breadth, same_day_only=True,
                                    suspicious_check=_breadth_suspicious),
    "fg_boards": CollectorSpec(_collect_fg_boards, same_day_only=True,
                               suspicious_check=_fg_suspicious),
    "fund_flow": CollectorSpec(_collect_fund_flow, same_day_only=True,
                               suspicious_check=_fund_flow_suspicious),
    "cninfo_announcements": CollectorSpec(_collect_cninfo, same_day_only=False),
    # 宏观日 K canonical：对历史日可确定性重建，不设 same_day_only
    "macro_global": CollectorSpec(_collect_macro, same_day_only=False,
                                  suspicious_check=_macro_suspicious),
}

# 各类资产的「真空」判定键（用于 collection_report 的 empty 状态）
_EMPTY_KEYS = {
    "dragons": lambda p: p.get("count", 0) == 0,
    "ths_limit_up_pool": lambda p: not (p.get("rows") or []) and not p.get("total"),
    "ths_block_top": lambda p: not (p.get("rows") or []),
    "market_breadth": lambda p: p.get("sample_n", 0) == 0,
    "fg_boards": lambda p: not any((b.get("rows") or [])
                                   for b in (p.get("boards") or {}).values()),
    "fund_flow": lambda p: not (p.get("quotes") or {}),
    "cninfo_announcements": lambda p: not (p.get("items") or {}),
    "macro_global": lambda p: not (p.get("instruments") or {}),
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
    spec = ASSET_COLLECTORS.get(asset_type)
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


@dataclass(frozen=True)
class PrepPlan:
    """增量采集计划：collect = 本次要跑的 collector；repairs = 只改 report 的修复项。"""

    collect: list[str]
    repairs: dict[str, dict]

    def is_empty(self) -> bool:
        return not self.collect and not self.repairs


def _bar_date(b) -> str:
    return datetime.fromtimestamp(b.timestamp / 1000, SHANGHAI).strftime("%Y-%m-%d")


def _trading_dates(tdx: TdxProvider) -> list[str]:
    """TDX 指数日K → 升序交易日列表（共享 ``utils.trading.tdx_trade_dates``）。"""
    try:
        return tdx_trade_dates(tdx)
    except ValueError as exc:
        raise PrepError(str(exc)) from exc


def resolve_window_dates(trade_date: str, calendar: list,
                         size: int = WINDOW_TRADING_DAYS) -> list[str]:
    """取 `<= trade_date` 的最近 `size` 个交易日（升序）。

    纯函数：不做 I/O；日历不足时返回全部可用日期，由 compute 段标
    ``dragons_window_short`` 降级。
    """
    eligible = sorted(d for d in (calendar or []) if d <= trade_date)
    return eligible[-size:] if size > 0 else []


def _validate_trade_date(trade_date: str, tdx: TdxProvider,
                         now: Optional[datetime] = None) -> list[str]:
    """prep 本地日期门禁（不依赖雪球日历），返回完整交易日列表。

    - T > 今日 → 报错；
    - T == 今日：<16:40 报「盘后数据尚未就绪」（A50 期指 16:30 收盘 + 缓冲；
      不查日历，雪球日历盘中不含当日会误判）；
      ≥16:40 用 TDX 指数日K校验当日确为交易日（排除周末/节假日盘后误跑）；
    - T < 今日：TDX 指数日K自建日历校验；日历不可用与非交易日报错严格区分。
    """
    now = now or datetime.now(SHANGHAI)
    today = now.strftime("%Y-%m-%d")
    if trade_date > today:
        raise PrepError(f"复盘日 {trade_date} 是未来日期")
    if trade_date == today and (now.hour, now.minute) < _AFTER_CLOSE:
        raise PrepError(f"{trade_date} 盘后数据尚未就绪，请 16:40 后运行 prep")
    dates = _trading_dates(tdx)
    if trade_date not in dates:
        raise PrepError(f"{trade_date} 非交易日")
    return dates


def _collect_all(trade_date: str, *, window_trade_dates: list,
                 asset_types: list[str],
                 window_days: int = WINDOW_TRADING_DAYS,
                 tdx: Optional[TdxProvider] = None,
                 existing: Optional[dict] = None,
                 now: Optional[datetime] = None) -> tuple[dict[str, dict], dict]:
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
    """
    now = now or datetime.now(SHANGHAI)
    today = now.strftime("%Y-%m-%d")
    invalid = [t for t in asset_types if not is_collectable(t, trade_date, today)]
    if invalid:
        raise ValueError(
            f"不可采资产被请求：{invalid}（trade_date={trade_date}, today={today}）")
    ctx = PrepContext(trade_date=trade_date, today=today,
                      tdx=tdx or TdxProvider(), ths=THSProvider(),
                      tencent=TencentProvider(), xueqiu=XueqiuProvider(),
                      window_trade_dates=list(window_trade_dates),
                      window_days=window_days)
    if "dragons" not in asset_types and existing:
        stored_report = existing.get("collection_report")
        stored_status = _report_status(stored_report, "dragons")
        stored_dragons = existing.get("dragons")
        if asset_health("dragons", stored_dragons, stored_status, trade_date) is None:
            ctx.dragons_rows = _asset_dragon_rows(stored_dragons)
            ctx.dragons_ready = True
    payloads: dict[str, dict] = {}
    report: dict[str, dict] = {}
    for asset_type in asset_types:
        spec = ASSET_COLLECTORS.get(asset_type)
        if spec is None:
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
        except Exception as e:  # 单点失败不中断：不写 payload，记 error 继续下一个
            payloads.pop(asset_type, None)
            report[asset_type] = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    return payloads, report


# ─── 计算层（纯函数，只读 assets，无 I/O）─────────────────────────────────────

_LIANBAN_RE = re.compile(r"(\d+)天(\d+)板")


def _lianban_count(high_days) -> int:
    """high_days「N天M板」→ 连板数（N==M 且 M≥2 视为连板；否则 0）。

    字段语义待交易日实测核对；若 pool 无数值连板字段，compute 退化为
    again_limit_rate（见 compute_sentiment 注释）。
    """
    if not high_days:
        return 0
    m = _LIANBAN_RE.search(str(high_days))
    if not m:
        return 0
    days, boards = int(m.group(1)), int(m.group(2))
    return boards if days == boards and boards >= 2 else 0


def _board_height(row: dict) -> Optional[int]:
    """block_top 行 → 最高板高度：优先数值 high_num，缺失解析 high「N天M板」取 M。"""
    hn = row.get("high_num")
    try:
        if hn is not None and int(hn) > 0:
            return int(hn)
    except (TypeError, ValueError):
        pass
    m = _LIANBAN_RE.search(str(row.get("high") or ""))
    return int(m.group(2)) if m else None


def _hhmmss(ts) -> Optional[str]:
    """epoch 秒 → "HH:MM:SS"（Asia/Shanghai）；异常返回 None。"""
    try:
        return datetime.fromtimestamp(int(ts), SHANGHAI).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return None


def _list_of_dicts(value) -> list:
    """类型收敛：仅保留 list 中的 dict 元素（畸形 payload 不抛异常）。"""
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _num_or_none(value) -> Optional[float]:
    """float 归一；NaN/inf/异常 → None。"""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def _fg_board_rows(fg: dict, name: str) -> tuple[list, bool]:
    """取 FG 单板块成员；返回 (rows, 是否可用)。

    板块名不存在 / status=="missing" 时 rows=[]，但**不可用**——调用方必须把
    该维度当作未知（None），不能当成「0 家」。否则在 THS 缺失 + 板块改名/列表
    不全的双重故障下会产出错误的仓位建议。
    """
    board = fg.get(name)
    if not isinstance(board, dict) or board.get("status") == "missing":
        return [], False
    return _list_of_dicts(board.get("rows")), True


# collection_report 内的审计键（不是资产类型，compute/增量检测都必须跳过）
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
    for asset_type in list(ASSET_COLLECTORS) + dynamic_keys:
        if asset_type in seen or asset_type == "collection_report":
            continue
        seen.add(asset_type)
        payload = assets.get(asset_type)
        if asset_health(asset_type, payload, _report_status(report, asset_type),
                        trade_date) is not None:
            degraded.append(asset_type)
            continue
        usable[asset_type] = payload
        if asset_type == "dragons":
            degraded.extend(_window_degradations(payload))
    return usable, sorted(set(degraded))


def _window_degradations(payload) -> list[str]:
    """窗口口径降级标记（仅新资产；旧资产无 window_days 时一律跳过）。

    防御式读取：payload 畸形（str/list）或字段缺失不得击穿 compute。
    """
    if not isinstance(payload, dict):
        return []
    window_days = payload.get("window_days")
    if not isinstance(window_days, int) or isinstance(window_days, bool) or window_days <= 1:
        return []
    marks: list[str] = []
    window_dates = payload.get("window_trade_dates")
    window_dates = window_dates if isinstance(window_dates, list) else []
    if len(window_dates) < window_days:
        marks.append("dragons_window_short")
    if any(day.get("status") in ("empty", "error")
           for day in _list_of_dicts(payload.get("days"))):
        marks.append("dragons_partial")
    return marks


# ─── 增量采集计划（默认模式：只补采不健康项）─────────────────────────────────


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
    for asset_type, spec in ASSET_COLLECTORS.items():
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


def _uncovered_assets(assets: dict, codes: list[str], selected: set[str]) -> list[str]:
    """code 覆盖 diff：fund_flow/cninfo 的键未覆盖当前龙头并集 codes → 需补采。"""
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


def plan_prep(assets: dict, trade_date: str, today: str, *,
              force: bool = False) -> PrepPlan:
    """产出增量采集计划（按 ASSET_COLLECTORS 注册表顺序）。

    - ``force``：collect = 全部可采资产；repairs 仍按 C3 计算（force 不抑制修复）。
    - 默认：collect = collection_health 不健康且可采的资产；窗口降级的 dragons
      会触发本地重读（零网络）。dragons 不在 collect 时，对 code 覆盖不足的
      fund_flow/cninfo 追加补采。
    - 历史日 same_day_only 且不可采、但满足 C3 的旧 unavailable → 进 repairs。
    - dragons 重采成功后的依赖级联由 ``run_prep`` 依据 code 集合变化处理。
    """
    repairs = plan_repairs(assets, trade_date, today)
    if force:
        collect = [t for t in ASSET_COLLECTORS
                   if is_collectable(t, trade_date, today)]
        return PrepPlan(collect, repairs)
    report = assets.get("collection_report")
    selected: set[str] = set()
    for asset_type in ASSET_COLLECTORS:
        if collection_health(asset_type, assets.get(asset_type),
                             _report_status(report, asset_type), trade_date) is None:
            continue
        if asset_type in repairs:      # 已判定可修复：视作就绪，不采集
            continue
        if is_collectable(asset_type, trade_date, today):
            selected.add(asset_type)
    if "dragons" not in selected:
        codes = _asset_codes(assets.get("dragons"))
        for asset_type in _uncovered_assets(assets, codes, selected):
            if is_collectable(asset_type, trade_date, today):
                selected.add(asset_type)
    return PrepPlan([t for t in ASSET_COLLECTORS if t in selected], repairs)


def _stored_asset_healthy(stored: dict, asset_type: str, status,
                          trade_date: str) -> bool:
    """存量资产是否「可用且质量自检通过」（失败补采的保留判据）。"""
    spec = ASSET_COLLECTORS.get(asset_type)
    if spec is None:
        return False
    payload = stored.get(asset_type)
    if asset_health(asset_type, payload, status, trade_date) is not None:
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


def compute_sentiment(assets: dict) -> dict:
    """情绪温度 v0.2：THS T 日权威 + TDX 宽度 + FG T-1 交叉，双口径并列留痕。

    每个来源分组独立容错：单个资产畸形只让对应因子为 null 并记入 factor_errors，
    不影响其余因子（与 compute 段逐因子隔离共同保证「单点失败不中断」）。
    调用方应已用 ``_prepare_assets`` 过滤不可用资产。
    """
    local_errors: dict[str, str] = {}

    def _safe(name: str, fn, default=None):
        try:
            return fn()
        except Exception as e:
            local_errors[name] = f"{type(e).__name__}: {e}"
            return default

    def _dict(name: str) -> dict:
        v = assets.get(name)
        return v if isinstance(v, dict) else {}

    pool = _dict("ths_limit_up_pool")
    breadth = _dict("market_breadth")
    fg = _dict("fg_boards").get("boards")
    fg = fg if isinstance(fg, dict) else {}
    block_top = _list_of_dicts(_dict("ths_block_top").get("rows"))
    pool_rows = _list_of_dicts(pool.get("rows"))

    degraded: list[str] = []
    factor_sources: dict[str, str] = {}

    # T 日涨停家数（THS 权威）；缺失 → null（不拿 FG T-1 冒充 T）
    limit_up_ths = pool.get("total") if pool else None
    if limit_up_ths is None:
        degraded.append("ths_limit_up_pool")
    else:
        factor_sources["limit_up_cnt_ths"] = "ths_limit_up_pool"

    # FG T-1 交叉口径（不进公式，除非 THS 缺失且显式标记）
    fg_up_rows, fg_up_ok = _fg_board_rows(fg, "昨日涨停")
    fg_down_rows, fg_down_ok = _fg_board_rows(fg, "昨日跌停")
    lhb_rows, lhb_ok = _fg_board_rows(fg, "昨日上榜")
    limit_up_t1 = len(fg_up_rows) if fg_up_ok else None
    limit_down_t1 = len(fg_down_rows) if fg_down_ok else None
    if fg and not (fg_up_ok and fg_down_ok):
        degraded.append("fg_boards_partial")   # 单板块缺失：对应维度未知而非 0

    # 市场宽度（up/down/成交额/跌停/炸板）
    up_cnt = breadth.get("up_cnt")
    down_cnt = breadth.get("down_cnt")
    total_amount = breadth.get("total_amount")
    # 资产缺失（宽度未采集）≠ 字段畸形：只在键存在时取值，不制造 factor_errors
    limit_down_cnt = (len(breadth["limit_down"])
                      if isinstance(breadth.get("limit_down"), list) else None)
    broken_cnt = (len(breadth["broken"])
                  if isinstance(breadth.get("broken"), list) else None)
    if not breadth:
        degraded.append("market_breadth")
    if broken_cnt is None:
        def _broken_from_pool():
            if not pool_rows:            # 无涨停池数据时不伪造 0，保持未知
                return None
            return sum(1 for r in pool_rows if (r.get("open_num") or 0) > 0)

        broken_cnt = _safe("broken_from_open_num", _broken_from_pool)
        if broken_cnt is not None:
            factor_sources["broken_cnt"] = "ths_limit_up_pool(open_num>0)"

    # 昨日涨停溢价：FG 昨日涨停成员当日 (close/pre_close-1) 均值 %
    def _premium():
        rets = [(r["close"] / r["pre_close"] - 1) * 100 for r in fg_up_rows
                if (r.get("pre_close") or 0) > 0 and (r.get("close") or 0) > 0]
        return round(sum(rets) / len(rets), 2) if rets else None

    premium = _safe("yesterday_premium", _premium)

    # 晋级率：今日连板家数(high_days 判定) / 昨日涨停家数(FG T-1 即「昨日」)
    def _promotion():
        if not pool_rows or not limit_up_t1:
            return None
        lianban = sum(1 for r in pool_rows if _lianban_count(r.get("high_days")) >= 2)
        return round(lianban / limit_up_t1, 3)

    promotion = _safe("promotion_rate", _promotion)
    if promotion is not None:
        factor_sources["promotion_rate"] = "ths_limit_up_pool(high_days) / fg(T-1)"

    # 最高板：block_top high_num（缺失解析 high 文本）
    max_height = _safe("max_board_height",
                       lambda: max((h for h in (_board_height(r) for r in block_top) if h),
                                   default=None))

    # 龙虎榜名单数：FG 昨日上榜
    lhb_cnt = len(lhb_rows) if lhb_ok else None

    # 仓位公式输入：THS(T) 优先；缺失显式回退 FG(T-1) 并标记 degraded
    pos_up, pos_up_src = ((limit_up_ths, "ths_t") if limit_up_ths is not None
                          else (limit_up_t1, "fg_t1"))
    pos_down, pos_down_src = ((limit_down_cnt, "breadth_t") if limit_down_cnt is not None
                              else (limit_down_t1, "fg_t1"))
    if (pos_up is not None and pos_up_src == "fg_t1") or \
       (pos_down is not None and pos_down_src == "fg_t1"):
        degraded.append("position_input_fg_t1")

    if pos_up is not None and pos_down is not None:
        advice, pct = position_advice(pos_up, pos_down)
        total = pos_up + pos_down
        score = round(pos_up / total * 100, 1) if total else None
    else:
        advice, pct, score = None, None, None

    limit_up_cnt = limit_up_ths  # 对外主口径：T 日精确（缺失为 null）
    out = {
        "up_cnt": up_cnt,
        "down_cnt": down_cnt,
        "limit_up_cnt": limit_up_cnt,
        "limit_down_cnt": limit_down_cnt,
        "total_amount": total_amount,
        "temperature_score": score,
        "position_advice": advice,
        "position_pct": pct,
        "limit_up_cnt_ths": limit_up_ths,
        "limit_down_cnt_ths": None,  # THS pool 不含跌停（占位）
        "limit_up_cnt_t1_fg": limit_up_t1,
        "limit_down_cnt_t1_fg": limit_down_t1,
        "position_input": {"limit_up_cnt": pos_up_src if pos_up is not None else None,
                           "limit_down_cnt": pos_down_src if pos_down is not None else None},
        "broken_cnt": broken_cnt,
        "broken_rate": (round(broken_cnt / (broken_cnt + limit_up_cnt), 3)
                        if broken_cnt is not None and limit_up_cnt else None),
        "yesterday_premium_pct": premium,
        "promotion_rate": promotion,
        "max_board_height": max_height,
        "lhb_cnt": lhb_cnt,
        "factor_sources": factor_sources,
        "degraded": sorted(set(degraded)),
    }
    if local_errors:
        out["factor_errors"] = local_errors
    return out


def compute_main_boards(assets: dict) -> list:
    """题材主线：block_top 主路径（题材+涨停家数+最高板+真龙命中数）；
    缺失回退 v0.1 真龙 concepts 计数（元素仅 board_name/dragon_count，消费方须 .get()）。"""
    bt_payload = assets.get("ths_block_top")
    bt_payload = bt_payload if isinstance(bt_payload, dict) else {}
    bt_raw = bt_payload.get("rows")
    bt_rows = [r for r in bt_raw if isinstance(r, dict)] if isinstance(bt_raw, list) else []
    dragon_payload = assets.get("dragons")
    dragon_payload = dragon_payload if isinstance(dragon_payload, dict) else {}
    dragon_raw = dragon_payload.get("rows")
    dragon_rows = [d for d in dragon_raw if isinstance(d, dict)] if isinstance(dragon_raw, list) else []
    dragon_codes = {d.get("code") for d in dragon_rows}
    if bt_rows:
        boards = [{
            "board_name": r.get("name"),
            "limit_up_num": r.get("limit_up_num"),
            "high": r.get("high"),
            "dragon_count": sum(1 for s in (r.get("stock_list") or [])
                                if isinstance(s, dict) and s.get("code") in dragon_codes),
        } for r in bt_rows]
        boards.sort(key=lambda b: (-(b.get("limit_up_num") or 0),
                                   str(b.get("board_name") or "")))
        return boards[:5]
    # 并集口径：同一 code 跨日重复入选只计一次（保持首次出现顺序）
    counter: dict[str, int] = {}
    seen_codes: set[str] = set()
    for d in dragon_rows:
        code = d.get("code")
        if code:
            if code in seen_codes:
                continue
            seen_codes.add(code)
        concepts = d.get("concepts")
        for c in (concepts if isinstance(concepts, list) else []):
            counter[c] = counter.get(c, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"board_name": name, "dragon_count": cnt} for name, cnt in ranked[:3]]


def _candidate_preference(candidate: dict) -> tuple:
    """去重偏好键：与 ``market.candidate_key`` 对齐（-综合分, rank）。"""
    score = candidate.get("composite_score")
    rank = candidate.get("rank")
    return (-(score if isinstance(score, (int, float)) else 0),
            rank if isinstance(rank, int) else 999999)


def _prefer_candidate(new: dict, old: dict) -> bool:
    """新候选是否更优：偏好键更小；完全相同则入选日更新者胜。"""
    key_new, key_old = _candidate_preference(new), _candidate_preference(old)
    if key_new != key_old:
        return key_new < key_old
    return str(new.get("trade_date") or "") > str(old.get("trade_date") or "")


def compute_candidates(assets: dict, trade_date: str) -> list:
    """候选池：窗口内真龙并集（同 code 保留更优）+ 同日 THS 归因 + T 日资金/公告。

    - 每只候选保留**行内真实入选日**（旧资产无行内日期时回退 ``trade_date``）；
    - 归因字段仅在候选入选日与 THS 池日期一致时填充，跨日候选为 None（未知不伪造）；
    - ``main_net_amount`` / ``announcements`` 是 T 日时点信息，对所有候选附加。

    调用方应已用 ``_prepare_assets`` 过滤不可用资产——例如 fund_flow 处于
    empty_suspicious 时该资产被剔除，main_net_amount 自然为 None（而非误导性 0）。
    """
    dragon_payload = assets.get("dragons")
    dragon_payload = dragon_payload if isinstance(dragon_payload, dict) else {}
    dragon_raw = dragon_payload.get("rows")
    dragon_rows = [d for d in dragon_raw if isinstance(d, dict)] if isinstance(dragon_raw, list) else []

    pool_payload = assets.get("ths_limit_up_pool")
    pool_payload = pool_payload if isinstance(pool_payload, dict) else {}
    pool_raw = pool_payload.get("rows")
    pool_rows = [r for r in pool_raw if isinstance(r, dict)] if isinstance(pool_raw, list) else []
    pool_by_code = {r.get("code"): r for r in pool_rows}
    pool_day = pool_payload.get("trade_date")

    flow = (assets.get("fund_flow") or {}).get("quotes")
    flow = flow if isinstance(flow, dict) else {}
    anns = (assets.get("cninfo_announcements") or {}).get("items")
    anns = anns if isinstance(anns, dict) else {}

    merged: dict[str, dict] = {}
    for d in dragon_rows:
        code = d.get("code")
        if not code:
            continue
        cand = dict(d)
        cand["trade_date"] = d.get("trade_date") or dragon_payload.get("trade_date") or trade_date
        previous = merged.get(code)
        if previous is None or _prefer_candidate(cand, previous):
            merged[code] = cand

    pool_available = bool(pool_rows)
    out = []
    for cand in merged.values():
        code = cand.get("code")
        p = pool_by_code.get(code) if pool_available else None
        # 入选日归因（既有契约）：仅候选入选日 == 今日池日期时填充，跨日为 null
        same_day = pool_available and pool_day is not None and cand.get("trade_date") == pool_day
        p_same_day = p if same_day else None
        cand["reason_type"] = p_same_day.get("reason_type") if p_same_day else None
        cand["limit_up_type"] = p_same_day.get("limit_up_type") if p_same_day else None
        cand["open_num"] = p_same_day.get("open_num") if p_same_day else None
        cand["is_again_limit"] = bool(p_same_day.get("is_again_limit")) if p_same_day else None
        cand["first_limit_up_time"] = (_hhmmss(p_same_day.get("first_limit_up_time"))
                                       if p_same_day else None)

        q = flow.get(code)
        q = q if isinstance(q, dict) else None
        item = anns.get(code)
        raw_anns = item.get("announcements") if isinstance(item, dict) else None
        announcements = raw_anns if isinstance(raw_anns, list) else []
        cand["main_net_amount"] = q.get("main_net_amount") if q else None
        cand["announcements"] = announcements

        # facts：今日事实一次算清（in_pool_today 与入选日解耦）
        t_pct = _num_or_none(p.get("change_rate")) if p else None
        if t_pct is None and q is not None:
            t_pct = _num_or_none(q.get("pct"))
        t_turnover = _num_or_none(p.get("turnover_rate")) if p else None
        if t_turnover is None and q is not None:
            t_turnover = _num_or_none(q.get("turnover_rate"))
        announcements_available = isinstance(item, dict)
        risk = find_risk_announcement(announcements, trade_date,
                                      announcements_available)
        cand["facts"] = {
            "pool_available": pool_available,
            "in_pool_today": p is not None,
            "t_pct": t_pct,
            "t_turnover": t_turnover,
            "t_amount": _num_or_none(q.get("amount")) if q else None,
            "open_num": p.get("open_num") if p else None,
            "high_days": p.get("high_days") if p else None,
            "reason_type_today": p.get("reason_type") if p else None,
            "limit_up_type": p.get("limit_up_type") if p else None,
            "first_limit_up_time": (_hhmmss(p.get("first_limit_up_time"))
                                    if p else None),
            "limit_up_suc_rate": (_num_or_none(p.get("limit_up_suc_rate"))
                                  if p else None),
            "main_net_amount": _num_or_none(q.get("main_net_amount")) if q else None,
            "flow_available": q is not None,
            "announcements_available": announcements_available,
            "announcement_count": len(announcements),
            "risk_announcement": risk,
        }
        out.append(cand)
    return out


def _dragon_source(assets: dict) -> dict:
    """真龙来源血缘：多日窗口输出逐日 dates；旧资产补 window_days=1 以保持口径可判定。"""
    d = assets.get("dragons")
    d = d if isinstance(d, dict) else {}
    if not d:
        # dragons 资产整体不可用：与「legacy 单日资产」区分（window_days=0）
        return {"trade_date": None, "scan_id": None, "version": None, "count": None,
                "window_days": 0, "dates": [], "version_mixed": False}
    days = _list_of_dicts(d.get("days"))
    if days:
        dates = [{"trade_date": day.get("trade_date"), "scan_id": day.get("scan_id"),
                  "version": day.get("version"), "count": day.get("count"),
                  "status": day.get("status")} for day in days]
        window_days = d.get("window_days")
        window_days = window_days if isinstance(window_days, int) else len(days)
    else:
        count = d.get("count") or 0
        dates = [{"trade_date": d.get("trade_date"), "scan_id": d.get("scan_id"),
                  "version": d.get("version"), "count": count,
                  "status": "ok" if count else "empty"}]
        window_days = 1
    versions = {item.get("version") for item in dates if item.get("version")}
    return {
        "trade_date": d.get("trade_date"),
        "scan_id": d.get("scan_id"),
        "version": d.get("version"),
        "count": d.get("count"),
        "window_days": window_days,
        "dates": dates,
        "version_mixed": len(versions) > 1,
    }


def _empty_macro() -> dict:
    return {
        "available": False,
        "as_of": None,
        "session": None,
        "instruments": {},
        "degraded": ["macro_global"],
        "sources": {},
    }


def _macro_stale(session_date, trade_date: str) -> bool:
    try:
        lag = (datetime.fromisoformat(trade_date).date()
               - datetime.fromisoformat(str(session_date)[:10]).date()).days
        return lag > 4
    except (TypeError, ValueError):
        return False


def _macro_session_kind(session_date, trade_date: str) -> Optional[str]:
    """逐品种收盘口径：同日 / 上一交易日 / 不可用。"""
    if not isinstance(session_date, str) or len(session_date) < 10:
        return None
    day = session_date[:10]
    if day == trade_date:
        return "same_day"
    if day < trade_date:
        return "previous_session"
    return None


def _macro_session(instruments: dict) -> Optional[str]:
    """外围指数组（日经/A50）的收盘口径聚合：same_day / previous_session / mixed。"""
    kinds = []
    for key in MACRO_INDEX_KEYS:
        rec = instruments.get(key)
        if not isinstance(rec, dict):
            continue
        kind = rec.get("session_kind")
        if isinstance(kind, str) and kind:
            kinds.append(kind)
    if not kinds:
        return None
    if len(set(kinds)) > 1:
        return "mixed"
    return kinds[0]


def _normalize_macro_record(rec: dict, key: str, trade_date: str) -> Optional[dict]:
    spec = SPEC_BY_KEY.get(key)
    if spec is None or not isinstance(rec, dict):
        return None
    price = _num_or_none(rec.get("price"))
    if price is None or price <= 0:
        return None
    session_date = rec.get("session_date")
    session_date = session_date[:10] if isinstance(session_date, str) else None
    # 缺失/未来日期视为不可用，不伪装成「上一交易日」
    session_kind = _macro_session_kind(session_date, trade_date)
    if session_kind is None:
        return None
    return {
        "key": key,
        "label": str(rec.get("label") or spec.label),
        "kind": str(rec.get("kind") or spec.kind),
        "price_unit": str(rec.get("price_unit") or spec.price_unit),
        "chg_unit": str(rec.get("chg_unit") or spec.chg_unit),
        "caliber": (CALIBER_SAME_DAY if spec.same_day_close
                    else CALIBER_PREVIOUS),
        "price": price,
        "prev_close": _num_or_none(rec.get("prev_close")),
        "chg": _num_or_none(rec.get("chg")),
        "pct": _num_or_none(rec.get("pct")),
        "chg_bp": _num_or_none(rec.get("chg_bp")),
        "chg_bp_approx": (rec.get("chg_bp_approx")
                          if isinstance(rec.get("chg_bp_approx"), bool) else None),
        "precision_note": (str(rec.get("precision_note"))
                           if rec.get("precision_note") else None),
        "source": str(rec.get("source") or ""),
        "code": str(rec.get("code") or ""),
        "market": str(rec.get("market") or ""),
        "session_date": session_date,
        "session_kind": session_kind,
        "raw_date": rec.get("raw_date"),
        "raw_time": rec.get("raw_time"),
        "stale": _macro_stale(session_date, trade_date),
    }


def compute_macro(assets: dict, trade_date: str) -> dict:
    """宏观资产 → 快照归一结果（纯函数）；缺失/畸形 → available=False。"""
    payload = assets.get("macro_global")
    if not isinstance(payload, dict):
        return _empty_macro()
    raw = payload.get("instruments")
    if not isinstance(raw, dict):
        return _empty_macro()
    instruments = {}
    for key in MACRO_KEYS:
        rec = _normalize_macro_record(raw.get(key), key, trade_date)
        if rec is not None:
            instruments[key] = rec
    if not instruments:
        return _empty_macro()

    raw_degraded = payload.get("degraded")
    # 旧资产可能残留 us_* 等已下线 key：只保留当前 MACRO_KEYS，避免旧键泄漏到 CLI/JSON
    degraded = {str(item) for item in raw_degraded
                if isinstance(item, str) and item in MACRO_KEYS} \
        if isinstance(raw_degraded, list) else set()
    degraded |= {key for key in MACRO_KEYS if key not in instruments}
    raw_sources = payload.get("sources")
    sources = ({key: value for key, value in raw_sources.items()
                if key in MACRO_KEYS}
               if isinstance(raw_sources, dict) else {})
    return {
        "available": True,
        "as_of": payload.get("as_of"),
        "session": _macro_session(instruments),
        "instruments": instruments,
        "degraded": sorted(degraded),
        "sources": sources,
    }


def _compute_all(trade_date: str, assets: dict) -> dict:
    """compute 段：先按 collection_report/as_of 过滤不可用资产，再逐因子错误隔离。

    collection_report 中 error/unavailable/empty_suspicious（及必然非空资产的 empty）
    一律按缺失降级——残缺数据绝不进入 position_pct 等下游信号。
    """
    usable, degraded = _prepare_assets(assets, trade_date)
    errors: dict[str, str] = {}

    def _guard(name: str, fn, default):
        try:
            return fn()
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"
            return default

    sentiment = _guard("sentiment", lambda: compute_sentiment(usable),
                       {"degraded": ["compute_error"]})
    main_boards = _guard("main_boards", lambda: compute_main_boards(usable), [])
    candidates = _guard("candidates", lambda: compute_candidates(usable, trade_date), [])
    candidates = _guard("verdicts",
                        lambda: evaluate_candidates(candidates, main_boards, trade_date),
                        candidates)
    macro = _guard("macro", lambda: compute_macro(usable, trade_date), _empty_macro())

    macro_marks: list[str] = []
    if not macro.get("available"):
        macro_marks.append("macro_global")
    else:
        if macro.get("degraded"):
            macro_marks.append("macro_partial")
        # 预期同日但未拿到（源端延迟/品种休市）：显式降级，避免 AC2/AC3 静默失败
        instruments = macro.get("instruments") or {}
        same_day_ok = all(
            isinstance(instruments.get(key), dict)
            and instruments[key].get("session_kind") == "same_day"
            for key in MACRO_INDEX_KEYS)
        if not same_day_ok:
            macro_marks.append("macro_same_day_missing")

    if errors:
        sentiment["factor_errors"] = {**sentiment.get("factor_errors", {}), **errors}
    sentiment["degraded"] = sorted(
        set(sentiment.get("degraded") or []) | set(degraded) | set(macro_marks))
    return {
        "trade_date": trade_date,
        "version": "0.5",
        "sentiment": sentiment,
        "main_boards": main_boards,
        "macro": macro,
        "candidates": candidates,
        "dragon_source": _dragon_source(usable),
    }


# ─── 入口 ──────────────────────────────────────────────────────────────────


def _run_collection(trade_date: str, tdx: TdxProvider, calendar: list,
                    stored: dict, plan: PrepPlan, today: str,
                    now: datetime) -> dict:
    """执行 plan.collect（dragons 两阶段 + code 变化级联），返回读回后的完整资产。

    阶段 1 只采 dragons（若在计划内），成功后比较新旧 code 集合决定是否级联
    fund_flow/cninfo；阶段 2 按注册表顺序采集其余项，existing 已含新 dragons。
    两次 ``_collect_all`` 共用同一 ``now``（跨零点不产生不同 as_of）。
    """
    window = resolve_window_dates(trade_date, calendar, WINDOW_TRADING_DAYS)
    payloads: dict[str, dict] = {}
    report: dict[str, dict] = {}
    to_run = set(plan.collect)
    if "dragons" in to_run:
        p, r = _collect_all(trade_date, window_trade_dates=window,
                            window_days=WINDOW_TRADING_DAYS, tdx=tdx,
                            asset_types=["dragons"], existing=stored, now=now)
        payloads.update(p)
        report.update(r)
        to_run.discard("dragons")
        if "dragons" in payloads:
            # 级联条件 = dragons 候选 code 集合发生变化（窗口缺日本身不触发网络补采）
            changed = (set(_asset_codes(payloads["dragons"]))
                       != set(_asset_codes(stored.get("dragons"))))
            if changed:
                for dep in _DRAGON_DEPENDENTS:
                    if (dep not in to_run and dep not in payloads
                            and is_collectable(dep, trade_date, today)):
                        to_run.add(dep)
    targets = [t for t in ASSET_COLLECTORS if t in to_run]
    if targets:
        # 预热用「存量 + 本次已采 payload + 本次已产 report」：阶段 1 新 dragons 的
        # report 尚未落库，必须一并覆盖，否则旧 error 状态会让依赖采集器误判未就绪。
        base_report = stored.get("collection_report")
        base_report = base_report if isinstance(base_report, dict) else {}
        seed = {**stored, **payloads,
                "collection_report": {**base_report, **report}}
        p, r = _collect_all(trade_date, window_trade_dates=window,
                            window_days=WINDOW_TRADING_DAYS, tdx=tdx,
                            asset_types=targets, existing=seed, now=now)
        payloads.update(p)
        report.update(r)
    merged = merge_report_entries(stored, report, payloads, trade_date, now)
    merged.update(plan.repairs)          # 修复条目最后写入（与本次采集不重叠）
    assets_db.save_prep_assets(trade_date, payloads, merged)
    return assets_db.get_prep_assets(trade_date)   # 读回：快照 = 资产的纯函数


def run_prep(trade_date: str, recompute: bool = False, *,
             force: bool = False, now: Optional[datetime] = None,
             on_plan: Optional[Callable[[PrepPlan], None]] = None) -> dict:
    """执行盘后复盘并落库，返回完整复盘结果 dict。

    正常模式（默认**增量**）：日期门禁 → 读存量资产 → 健康判定 → 只补采不健康且可采的
    资产（dragons 重采且 code 集合变化时级联 fund_flow/cninfo）→ report 按键合并
    （失败补采不降级 + 窄口径修复）→ 读回 → 计算 → 覆盖快照。
    资产完好且无可修复项时零采集（含 dragons 本地读取），report 完全不动。
    ``force=True`` 只影响 collect（重采全部可采资产），repairs 仍按 C3 计算。

    recompute 模式：零检测零采集，用已存资产重算（无资产时报错，不写空快照）。
    recompute 只读本地已存资产、不触网采集，故不做交易日/时点门禁（仅拒绝未来日期），
    计算段的 as_of 校验仍会拦下时点不符的资产。
    """
    if recompute:
        if trade_date > datetime.now(SHANGHAI).strftime("%Y-%m-%d"):
            raise PrepError(f"复盘日 {trade_date} 是未来日期")
        assets = assets_db.get_prep_assets(trade_date)
        if not assets:
            raise PrepError(f"{trade_date} 无可重算资产（请先正常采集 prep）")
    else:
        now = now or datetime.now(SHANGHAI)
        today = now.strftime("%Y-%m-%d")
        tdx = TdxProvider()
        calendar = _validate_trade_date(trade_date, tdx, now)  # 与采集共用同一时钟
        stored = assets_db.get_prep_assets(trade_date)
        plan = plan_prep(stored, trade_date, today, force=force)
        if on_plan is not None:
            on_plan(plan)
        if plan.is_empty():
            assets = stored                    # 零采集零写入（AC1）
        else:
            assets = _run_collection(trade_date, tdx, calendar, stored, plan,
                                     today, now)
    result = _compute_all(trade_date, assets)
    assets_db.save_prep_snapshot(trade_date, result, source="v2")
    return result
