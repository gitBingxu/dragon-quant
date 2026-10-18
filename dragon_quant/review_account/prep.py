"""盘后复盘：两段式流水线（采集资产 → 纯函数计算），产出可扩展 JSON 快照。

供 `prep` CLI 命令调用；review-account / buy-sell 经 `collect_candidates` 消费快照。

架构（见 review_account/prep因子扩展技术方案.md）：
  - collect 段：遍历 ASSET_COLLECTORS 注册表，逐 collector 隔离异常，
    全部 payload 经 ``prep_db.save_prep_assets`` 单事务落库（含 collection_report）。
  - compute 段：只从 prep_assets 读回（快照 = 资产的纯函数），逐因子错误隔离；
    ``--recompute`` 时用已存资产重算快照（算法升级可重放历史口径）。
  - same_day_only 资产（FG/实时行情类）补跑历史日时跳过，防止时点污染。

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
from dragon_quant.storage import db, prep_db

SHANGHAI = ZoneInfo("Asia/Shanghai")

# ─── 常量（集中调参）────────────────────────────────────────────────────────

# FG(风格)板块清单：昨日涨停溢价/晋级交叉/龙虎榜名单（T-1 语义，仅当日采集）
FG_BOARD_NAMES = ["昨日涨停", "昨日跌停", "昨日连板", "昨日首板", "昨日断板", "昨日上榜"]

CNINFO_WINDOW_DAYS = 7   # 公告窗口：保留 [T-7, T] 内公告
CNINFO_MAX_PAGES = 5     # 翻页上限保护；触顶且未到窗口 → truncated

_LIMIT_TOL_UP = 0.999    # 涨停/炸板判定容差（涨停价的 0.1%）
_LIMIT_TOL_DOWN = 1.001

# 全 A 扫描有效样本下限：低于此值视为 data source 未填充（非真实市场极值）
SAMPLE_N_MIN = 3000

# collection_report 中「不可用」的状态：compute 按缺失降级，绝不使用其值
_BAD_STATUS = {"error", "unavailable", "empty_suspicious"}
# 正常交易日不可能为空的资产：status=empty 也按缺失处理
_MUST_NONEMPTY = {"market_breadth", "fg_boards"}
# 需要校验 as_of == trade_date 的资产：仅 same_day_only（实时/时点快照）类。
# 注意 cninfo_announcements 的 as_of 是采集时间而非数据时点（公告可历史回溯），
# 其窗口正确性由采集阶段 [T-window, T] 过滤保证，不做 as_of 相等校验。
_AS_OF_CHECKED = {"market_breadth", "fg_boards", "fund_flow"}

# 盘后运行门禁：当日 ≥15:30 才允许（TDX 盘后刷新缓冲）
_AFTER_CLOSE = (15, 30)


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
    """采集器共享上下文（dragons 先采，fund_flow/cninfo 消费其候选 codes）。"""
    trade_date: str
    today: str
    tdx: TdxProvider
    ths: THSProvider
    dragons_rows: list = field(default_factory=list)


@dataclass
class CollectorSpec:
    collect: Callable[[PrepContext], dict]
    same_day_only: bool
    # 空结果可疑检查：返回 note 字符串则记 empty_suspicious（区分「数据源未填充」与「真空」）
    suspicious_check: Optional[Callable[[dict], Optional[str]]] = None


def _collect_dragons(ctx: PrepContext) -> dict:
    """R 日真龙冻结（compute 的唯一真龙来源；含 scan_id/version 血缘）。"""
    rows = db.get_dragons(ctx.trade_date, source="v2")
    ctx.dragons_rows = rows
    scan_ids = sorted({r.get("scan_id") for r in rows if r.get("scan_id")})
    versions = sorted({r.get("version") for r in rows if r.get("version")})
    return {
        "trade_date": ctx.trade_date,
        "scan_id": scan_ids[0] if len(scan_ids) == 1 else (scan_ids or None),
        "version": versions[0] if len(versions) == 1 else (versions or None),
        "count": len(rows),
        "rows": rows,
    }


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
    codes = [d.get("code") for d in ctx.dragons_rows if d.get("code")]
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
    for d in ctx.dragons_rows:
        code = d.get("code")
        if not code:
            continue
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
}


def _bar_date(b) -> str:
    return datetime.fromtimestamp(b.timestamp / 1000, SHANGHAI).strftime("%Y-%m-%d")


def _validate_trade_date(trade_date: str, tdx: TdxProvider,
                         now: Optional[datetime] = None) -> None:
    """prep 本地日期门禁（不依赖雪球日历）。

    - T > 今日 → 报错；
    - T == 今日：<15:30 报「盘中」（不查日历，雪球日历 15:05 前不含当日会误判）；
      ≥15:30 用 TDX 指数日K校验当日确为交易日（排除周末/节假日盘后误跑）；
    - T < 今日：TDX 指数日K自建日历校验；日历不可用与非交易日报错严格区分。
    """
    now = now or datetime.now(SHANGHAI)
    today = now.strftime("%Y-%m-%d")
    if trade_date > today:
        raise PrepError(f"复盘日 {trade_date} 是未来日期")
    if trade_date == today and (now.hour, now.minute) < _AFTER_CLOSE:
        raise PrepError(f"{trade_date} 当日盘中，请 15:30 收盘后再运行 prep")
    bars = tdx.get_kline("SH000001", days=120)
    dates = {_bar_date(b) for b in bars}
    if not dates:
        raise PrepError("交易日历数据源失败（TDX 指数日K为空），请稍后重试")
    if trade_date not in dates:
        raise PrepError(f"{trade_date} 非交易日")


def _collect_all(trade_date: str, tdx: Optional[TdxProvider] = None) -> tuple[dict[str, dict], dict]:
    """采集段：遍历注册表，逐 collector 隔离异常，返回 (payloads, report)。"""
    ctx = PrepContext(trade_date=trade_date,
                      today=datetime.now(SHANGHAI).strftime("%Y-%m-%d"),
                      tdx=tdx or TdxProvider(), ths=THSProvider())
    payloads: dict[str, dict] = {}
    report: dict[str, dict] = {}
    for asset_type, spec in ASSET_COLLECTORS.items():
        if spec.same_day_only and trade_date < ctx.today:
            report[asset_type] = {"status": "unavailable", "note": "补跑历史日无该时点数据"}
            continue
        try:
            payload = spec.collect(ctx)
            payloads[asset_type] = payload
            check = _EMPTY_KEYS.get(asset_type, lambda p: not p)
            status = "empty" if check(payload) else "ok"
            note = spec.suspicious_check(payload) if spec.suspicious_check else None
            entry: dict = {"status": status}
            if note:
                entry["note"] = note
                if status == "ok":
                    entry["status"] = "empty_suspicious"
            report[asset_type] = entry
        except Exception as e:  # 单点失败不中断：记 error 继续下一个 collector
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


def _prepare_assets(assets: dict, trade_date: str) -> tuple[dict[str, dict], list[str]]:
    """按 collection_report + as_of 过滤不可用资产，返回 (可用资产, 降级名单)。

    - status ∈ {error, unavailable, empty_suspicious} → 按缺失降级（不使用其值）；
    - status == empty 且资产正常交易日不可能为空（market_breadth/fg_boards）→ 同样降级；
    - payload.as_of != trade_date → 时点不符，降级（防止跨日残留/外部导入污染）。
    原始 payload 仍保留在 prep_assets 中供审计，仅 compute 不消费。
    """
    report = assets.get("collection_report") or {}
    usable: dict[str, dict] = {}
    degraded: list[str] = []
    # 遍历 assets ∪ report：采集整体失败且无历史残留时资产不在 assets 中，
    # 但必须在 degraded 里可见（否则 CLI 摘要完全静默）。
    for asset_type in list(assets) + [k for k in report if k not in assets]:
        if asset_type == "collection_report":
            continue
        payload = assets.get(asset_type)
        status = (report.get(asset_type) or {}).get("status")
        if payload is None:
            degraded.append(asset_type)
            continue
        reason = None
        if status in _BAD_STATUS:
            reason = status
        elif status == "empty" and asset_type in _MUST_NONEMPTY:
            reason = "empty"
        elif (asset_type in _AS_OF_CHECKED and isinstance(payload, dict)
              and payload.get("as_of") and payload["as_of"] != trade_date):
            reason = "as_of_mismatch"
        if reason:
            degraded.append(asset_type)
        else:
            usable[asset_type] = payload
    return usable, sorted(set(degraded))


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
    bt_raw = (assets.get("ths_block_top") or {}).get("rows")
    bt_rows = [r for r in bt_raw if isinstance(r, dict)] if isinstance(bt_raw, list) else []
    dragon_raw = (assets.get("dragons") or {}).get("rows")
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
    counter: dict[str, int] = {}
    for d in dragon_rows:
        concepts = d.get("concepts")
        for c in (concepts if isinstance(concepts, list) else []):
            counter[c] = counter.get(c, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"board_name": name, "dragon_count": cnt} for name, cnt in ranked[:3]]


def compute_candidates(assets: dict, trade_date: str) -> list:
    """候选池：真龙 + THS 归因/板型 + 主力净额 + 公告标记（只增 key）。

    调用方应已用 ``_prepare_assets`` 过滤不可用资产——例如 fund_flow 处于
    empty_suspicious 时该资产被剔除，main_net_amount 自然为 None（而非误导性 0）。
    """
    pool_raw = (assets.get("ths_limit_up_pool") or {}).get("rows")
    pool_rows = [r for r in pool_raw if isinstance(r, dict)] if isinstance(pool_raw, list) else []
    pool_by_code = {r.get("code"): r for r in pool_rows}
    flow = (assets.get("fund_flow") or {}).get("quotes")
    flow = flow if isinstance(flow, dict) else {}
    anns = (assets.get("cninfo_announcements") or {}).get("items")
    anns = anns if isinstance(anns, dict) else {}
    dragon_raw = (assets.get("dragons") or {}).get("rows")
    dragon_rows = [d for d in dragon_raw if isinstance(d, dict)] if isinstance(dragon_raw, list) else []
    out = []
    for d in dragon_rows:
        cand = dict(d)
        cand["trade_date"] = trade_date
        p = pool_by_code.get(d.get("code"))
        cand["reason_type"] = p.get("reason_type") if p else None
        cand["limit_up_type"] = p.get("limit_up_type") if p else None
        cand["open_num"] = p.get("open_num") if p else None
        cand["is_again_limit"] = bool(p.get("is_again_limit")) if p else None
        cand["first_limit_up_time"] = _hhmmss(p.get("first_limit_up_time")) if p else None
        q = flow.get(d.get("code"))
        cand["main_net_amount"] = q.get("main_net_amount") if isinstance(q, dict) else None
        item = anns.get(d.get("code"))
        cand["announcements"] = (item.get("announcements") or []) if isinstance(item, dict) else []
        out.append(cand)
    return out


def _dragon_source(assets: dict) -> dict:
    d = assets.get("dragons") or {}
    return {"trade_date": d.get("trade_date"), "scan_id": d.get("scan_id"),
            "version": d.get("version"), "count": d.get("count")}


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
    if errors:
        sentiment["factor_errors"] = {**sentiment.get("factor_errors", {}), **errors}
    sentiment["degraded"] = sorted(set(sentiment.get("degraded") or []) | set(degraded))
    return {
        "trade_date": trade_date,
        "version": "0.2",
        "sentiment": sentiment,
        "main_boards": main_boards,
        "candidates": candidates,
        "dragon_source": _dragon_source(usable),
    }


# ─── 入口 ──────────────────────────────────────────────────────────────────


def run_prep(trade_date: str, recompute: bool = False) -> dict:
    """执行盘后复盘并落库，返回完整复盘结果 dict。

    正常模式：日期门禁 → 采集（单事务落资产）→ 读回资产 → 计算 → 落快照。
    recompute 模式：跳过采集，用已存资产重算（无资产时报错，不写空快照）。
    recompute 只读本地已存资产、不触网采集，故不做交易日/时点门禁（仅拒绝未来日期），
    计算段的 as_of 校验仍会拦下时点不符的资产。
    """
    if recompute:
        if trade_date > datetime.now(SHANGHAI).strftime("%Y-%m-%d"):
            raise PrepError(f"复盘日 {trade_date} 是未来日期")
        assets = prep_db.get_prep_assets(trade_date)
        if not assets:
            raise PrepError(f"{trade_date} 无可重算资产（请先正常采集 prep）")
    else:
        tdx = TdxProvider()
        _validate_trade_date(trade_date, tdx)   # 复用同一 provider，避免二次握手
        payloads, report = _collect_all(trade_date, tdx)
        prep_db.save_prep_assets(trade_date, payloads, report)
        assets = prep_db.get_prep_assets(trade_date)  # 读回：快照 = 资产的纯函数
    result = _compute_all(trade_date, assets)
    prep_db.save_prep_snapshot(trade_date, result, source="v2")
    return result
