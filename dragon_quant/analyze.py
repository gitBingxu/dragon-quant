#!/usr/bin/env python3
"""
龙头战法量化分析 — 主入口

用法:
    python3 analyze.py 002xxx                    # 分析单只股票
    python3 analyze.py 002xxx --verbose          # 详细输出
    python3 analyze.py 002xxx --json             # JSON 输出
"""

import sys
import json
import argparse
import time
import os
from datetime import datetime

from dragon_quant.eastmoney_api import (
    get_limit_up_list,
    get_industry_components,
    get_stock_kline,
    get_stock_quote,
    get_market_index_kline,
    infer_consecutive_boards,
    get_sector_5min_kline,
    get_stock_5min_kline,
    get_api_calls_and_clear,
    _INDUSTRY_CODE_TO_NAME,
)
from dragon_quant.drive_analysis import calc_drive_score
from dragon_quant.anti_drop import calc_anti_drop_score
from dragon_quant.leadership import calc_leading_score
from dragon_quant.absorption import calc_absorption_score
from dragon_quant.log_builder import (
    build_drive_logs,
    build_anti_drop_logs,
    build_leadership_logs,
    build_absorption_logs,
)


def _load_shared_data(filepath, max_age_minutes=10):
    """校验并加载共享数据。返回 (limit_up_list, market_kline) 或 (None, None)。"""
    if not filepath or not os.path.exists(filepath):
        return None, None

    mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
    age_seconds = (datetime.now() - mtime).total_seconds()
    if age_seconds > max_age_minutes * 60:
        print(f"  ⚠️ 共享数据过期({age_seconds:.0f}s)，自行拉取", file=sys.stderr)
        return None, None

    try:
        with open(filepath) as f:
            data = json.load(f)
    except Exception:
        return None, None

    today = datetime.now().strftime("%Y%m%d")
    td = data.get("trading_date", "")
    if td != today:
        if td > today:
            print(f"  ⚠️ 共享数据日期({td})在未来，自行拉取", file=sys.stderr)
        else:
            print(f"  ⚠️ 共享数据日期({td})≠今天，自行拉取", file=sys.stderr)
        return None, None

    print(f"  ✅ 复用共享数据 ({os.path.basename(filepath)})", file=sys.stderr)
    return data.get("limit_up_list", []), data.get("market_kline", [])


def analyze_stock(code: str, verbose: bool = False, shared_data_file: str = None) -> dict:
    """对单只股票执行四维分析。"""

    t_start = time.time()
    _dim_times = {}

    print(f"🔍 正在分析 {code}...", file=sys.stderr)

    # ── 数据采集 ──
    _errors = []
    limit_up_list, market_kline = _load_shared_data(shared_data_file)
    if limit_up_list is None:
        try:
            print("  📡 获取涨停榜...", file=sys.stderr)
            limit_up_list = get_limit_up_list()
        except Exception as e:
            print(f"  ❌ 涨停榜获取失败 ({type(e).__name__}): {e}", file=sys.stderr)
            _errors.append(("涨停榜", f"{type(e).__name__}: {e}"))
            limit_up_list = []

    try:
        print("  📊 获取个股K线...", file=sys.stderr)
        stock_kline = get_stock_kline(code, days=20)
    except Exception as e:
        print(f"  ❌ 个股K线获取失败 ({type(e).__name__}): {e}", file=sys.stderr)
        _errors.append(("个股K线", f"{type(e).__name__}: {e}"))
        stock_kline = []

    if market_kline is None:
        try:
            print("  📈 获取大盘指数K线...", file=sys.stderr)
            market_kline = get_market_index_kline("1.000001", days=20)
        except Exception as e:
            print(f"  ❌ 大盘K线获取失败 ({type(e).__name__}): {e}", file=sys.stderr)
            _errors.append(("大盘K线", f"{type(e).__name__}: {e}"))
            market_kline = []

    print("  🏷️  获取实时行情...", file=sys.stderr)
    quote = get_stock_quote(code)
    if quote.get("price", 0) == 0 and quote.get("pct", 0) == 0 and not quote.get("name"):
        _errors.append(("实时行情", "获取失败，返回默认值"))

    # 找到该股近 3 个涨停日，并推算连板数 + 补日期
    stock_limit_ups = [lu for lu in limit_up_list if lu["code"] == code]
    # 用最新 K 线日期填充涨停日期
    latest_date = stock_kline[-1]["date"] if stock_kline else ""
    for lu in stock_limit_ups:
        lu["consecutive"] = infer_consecutive_boards(code, stock_kline)
        if not lu.get("date"):
            lu["date"] = latest_date
    stock_limit_ups.sort(key=lambda x: x.get("consecutive", 1), reverse=True)
    recent_limit_ups = stock_limit_ups[:3]

    if not recent_limit_ups:
        print(f"  ⚠️  {code} 近期无涨停记录，带动性/承接性分析受限", file=sys.stderr)

    # 行业信息：优先从涨停榜获取，其次从实时行情
    industry_name = recent_limit_ups[0]["industry_name"] if recent_limit_ups else ""
    industry_code = recent_limit_ups[0]["industry_code"] if recent_limit_ups else ""

    if not industry_name:
        # 尝试从实时行情推断（可能为空）
        industry_name = "未知"

    # ── 维度一：带动性 ──
    print("  🐉 分析带动性...", file=sys.stderr)
    t_dim = time.time()
    drive_result = {"score": 30, "breakdown": {"error": "无涨停日数据"}}
    co_limitup_map = {}

    if recent_limit_ups and industry_code:
        # 获取行业成分股
        try:
            industry_components = get_industry_components(industry_code if industry_code else industry_name)
        except Exception as e:
            print(f"  ⚠️ 行业成分股获取失败 ({type(e).__name__}): {e}", file=sys.stderr)
            _errors.append(("行业成分股", f"{type(e).__name__}: {e}"))
            industry_components = []

        # 构建 co_limitup_map：同行业其他涨停股
        for ld in recent_limit_ups:
            co_list = [
                lu for lu in limit_up_list
                if lu.get("industry_name") == industry_name
                and lu["code"] != code
            ]
            co_limitup_map[ld.get("date", "")] = co_list

        try:
            drive_result = calc_drive_score(
                code, recent_limit_ups, co_limitup_map, industry_components
            )
        except Exception as e:
            print(f"  ⚠️ 带动性分析失败 ({type(e).__name__}): {e}", file=sys.stderr)
            _errors.append(("带动性", f"{type(e).__name__}: {e}"))
            drive_result = {"score": 30, "breakdown": {"error": str(e)}, "fallback": True}
    else:
        industry_components = []
    _dim_times["drive"] = round((time.time() - t_dim) * 1000, 1)

    # ── 维度二：抗跌性 ──
    print("  🛡️  分析抗跌性...", file=sys.stderr)
    t_dim = time.time()
    try:
        anti_drop_result = calc_anti_drop_score(stock_kline, market_kline)
    except Exception as e:
        print(f"  ⚠️ 抗跌性分析失败 ({type(e).__name__}): {e}", file=sys.stderr)
        _errors.append(("抗跌性", f"{type(e).__name__}: {e}"))
        anti_drop_result = {"score": 50, "drop_days_count": 0, "breakdown": {"error": str(e)}, "fallback": True}
    _dim_times["anti_drop"] = round((time.time() - t_dim) * 1000, 1)

    # ── 维度三：领涨性 ──
    print("  📊 分析领涨性...", file=sys.stderr)
    t_dim = time.time()
    if not industry_components and industry_code:
        try:
            industry_components = get_industry_components(industry_code)
        except Exception as e:
            print(f"  ⚠️ 行业成分股获取失败 ({type(e).__name__}): {e}", file=sys.stderr)
            _errors.append(("行业成分股", f"{type(e).__name__}: {e}"))
    limit_dates = [ld.get("date", "") for ld in recent_limit_ups]
    leading_result = calc_leading_score(stock_kline, industry_components, limit_dates, code)
    _dim_times["leading"] = round((time.time() - t_dim) * 1000, 1)

    # ── 维度四：资金承接性 ──
    print("  💰 分析资金承接性...", file=sys.stderr)
    t_dim = time.time()
    absorption_result = {"score": 50, "breakdown": {"note": "无行业数据"}}
    all_sectors = {}
    if industry_code:
        try:
            from eastmoney_api import get_sector_5min_kline
            # 加载目标板块 + 涨停榜中其他活跃板块用于跨板块对比
            target_kline = get_sector_5min_kline(industry_code)
            if target_kline and len(target_kline) >= 40:
                all_sectors[industry_code] = target_kline
            # 补充涨停榜中频率最高的 6 个板块
            ind_count = {}
            for lu in limit_up_list:
                ic = lu.get("industry_code", "")
                if ic and ic != industry_code and ic not in all_sectors:
                    ind_count[ic] = ind_count.get(ic, 0) + 1
            for ic in sorted(ind_count, key=ind_count.get, reverse=True)[:6]:
                try:
                    kl = get_sector_5min_kline(ic)
                    time.sleep(0.1)
                    if kl and len(kl) >= 40:
                        all_sectors[ic] = kl
                except Exception as e2:
                    print(f"    ⚠️ 板块 {ic} 5分钟K线失败 ({type(e2).__name__})", file=sys.stderr)
                    continue
            if industry_code in all_sectors:
                absorption_result = calc_absorption_score(industry_code, all_sectors)
        except Exception as e:
            print(f"  ⚠️ 资金承接性分析失败 ({type(e).__name__}): {e}", file=sys.stderr)
            _errors.append(("资金承接性", f"{type(e).__name__}: {e}"))
            absorption_result = {"score": 50, "breakdown": {"error": str(e)}, "fallback": True}
    _dim_times["absorption"] = round((time.time() - t_dim) * 1000, 1)

    # ── 5分钟K线数据（用于详细日志）──
    t_dim = time.time()
    stock_5min = []
    companions = []
    sector_5min = all_sectors.get(industry_code, []) if industry_code else []
    try:
        print("  ⏱️  加载5分钟K线...", file=sys.stderr)
        stock_5min = get_stock_5min_kline(code)
        time.sleep(0.1)
        # 加载同行业小弟
        if industry_name:
            co_list = [
                lu for lu in limit_up_list
                if lu.get("industry_name") == industry_name
                and lu["code"] != code
            ][:3]
            for cp in co_list:
                try:
                    cp_kl = get_stock_5min_kline(cp["code"])
                    time.sleep(0.1)
                    if cp_kl:
                        companions.append({
                            "code": cp["code"], "name": cp["name"],
                            "kline": cp_kl,
                        })
                except Exception as e2:
                    print(f"    ⚠️ 同伴 {cp.get('code','?')} 5分钟K线失败 ({type(e2).__name__})", file=sys.stderr)
                    continue
    except Exception as e:
        print(f"  ⚠️ 5分钟K线加载失败 ({type(e).__name__}): {e}", file=sys.stderr)
    _dim_times["5min_kline"] = round((time.time() - t_dim) * 1000, 1)

    # ── 四维叙事日志 ──
    logs = {}
    date_str = recent_limit_ups[0].get("date", "") if recent_limit_ups else ""
    bt = drive_result.get("best_day", {}).get("board_time", "")
    name = quote.get("name", "")
    logs["drive"] = build_drive_logs(
        code, name, drive_result, stock_5min, companions, sector_5min,
        industry_name, date_str
    )
    logs["anti_drop"] = build_anti_drop_logs(anti_drop_result)
    logs["leading"] = build_leadership_logs(leading_result)
    logs["absorption"] = build_absorption_logs(
        absorption_result, _INDUSTRY_CODE_TO_NAME, stock_5min,
        sector_5min, date_str, bt, name, industry_name
    )

    # ── 综合评分 ──
    composite = (
        drive_result["score"] * 0.35
        + anti_drop_result["score"] * 0.15
        + leading_result["score"] * 0.25
        + absorption_result["score"] * 0.25
    )

    # 评级
    if composite >= 85:
        rating = "🐉 真龙"
    elif composite >= 70:
        rating = "⭐ 强票"
    elif composite >= 50:
        rating = "📊 中规中矩"
    else:
        rating = "🐔 杂毛"

    return {
        "code": code,
        "name": quote.get("name", ""),
        "price": quote.get("price", 0),
        "pct": quote.get("pct", 0),
        "industry": industry_name,
        "industry_code": industry_code,
        "composite_score": round(composite, 1),
        "rating": rating,
        "drive": drive_result,
        "anti_drop": anti_drop_result,
        "leading": leading_result,
        "absorption": absorption_result,
        "logs": logs,
        "_errors": _errors,
        "_fallback": len(_errors) > 0,
        "_elapsed_ms": round((time.time() - t_start) * 1000, 1),
        "_dim_times": _dim_times,
        "_api_calls": get_api_calls_and_clear(),
    }


def print_report(result: dict, verbose: bool = False):
    """格式化输出分析报告。"""
    print()
    print("=" * 60)
    print(f"  🐉 龙头战法量化分析 — {result['name']}({result['code']})")
    print("=" * 60)

    score = result["composite_score"]
    rating = result["rating"].replace("🐉 ", "").replace("⭐ ", "").replace("📊 ", "").replace("🐔 ", "")
    cons = result.get("est_cons", 1)

    print(f"\n{result['name']}({result['code']})——{result['industry']}——{cons}连板")
    print(f"    1. 综合评分: {score:.1f}，{rating}")

    logs = result.get("logs", {})
    ds = result["drive"]["score"]
    dl = logs.get("drive", "")
    print(f"    - 🐉 带动性({ds:.0f}): {dl}")

    ads = result["anti_drop"]["score"]
    al = logs.get("anti_drop", "")
    print(f"    - 🛡️ 抗跌性({ads:.0f}): {al}")

    lds = result["leading"]["score"]
    ll = logs.get("leading", "")
    print(f"    - 📊 领涨性({lds:.0f}): {ll}")

    abs_ = result["absorption"]["score"]
    abl = logs.get("absorption", "")
    print(f"    - 💰 资金承接({abs_:.0f}): {abl}")

    print(f"    2. 买点建议：")
    print(f"    - xxx 后续迭代")


def _build_reasons(result: dict) -> list[tuple[str, str]]:
    """根据四维得分生成选择理由。"""
    reasons = []

    # 带动性
    try:
        d = result.get("drive", {})
        ds = d.get("score", 0)
        best = d.get("best_day", {}) or {}
        bk = best.get("breakdown", {}) or {}
        voice = bk.get("voice_score", 0)
        follow = bk.get("follow_score", 0)
        board = bk.get("board_leadership_score", 0)

        if ds >= 85:
            reasons.append(("🐉 带动性",
                f"板块共鸣{voice:.0f}/跟风{follow:.0f}/决策力{board:.0f}，"
                f"同板块小弟跟风紧密，实打实的带头大哥"))
        elif ds >= 70:
            reasons.append(("🐉 带动性",
                f"板块共鸣{voice:.0f}/跟风{follow:.0f}/决策力{board:.0f}，"
                f"有带动效应但板块共振还不够强"))
        elif ds >= 50:
            reasons.append(("🐉 带动性",
                f"板块共鸣{voice:.0f}/跟风{follow:.0f}/决策力{board:.0f}，"
                f"带动性一般，板块效应不明显"))
        else:
            reasons.append(("🐉 带动性", f"数据不足，无法评估带动效应"))
    except Exception:
        reasons.append(("🐉 带动性", "分析异常"))

    # 抗跌性
    try:
        ad = result.get("anti_drop", {})
        ads = ad.get("score", 0)
        drop_count = ad.get("drop_days_count", 0)
        if ads >= 70:
            reasons.append(("🛡️ 抗跌性",
                f"近{drop_count}次大盘跳水中表现坚挺，资金承接力强"))
        elif ads >= 40:
            reasons.append(("🛡️ 抗跌性",
                f"近{drop_count}次大盘跳水中抗跌一般，有跟跌倾向"))
        elif drop_count > 0:
            reasons.append(("🛡️ 抗跌性",
                f"近{drop_count}次大盘跳水中表现偏弱，需警惕系统性风险"))
        else:
            reasons.append(("🛡️ 抗跌性", "近期无跳水日，抗跌性待验证"))
    except Exception:
        reasons.append(("🛡️ 抗跌性", "分析异常"))

    # 领涨性
    try:
        ld = result.get("leading", {})
        lds = ld.get("score", 0)
        lbk = ld.get("breakdown", {}) or {}
        rank = lbk.get("avg_pct_rank", 0.5)
        median = lbk.get("industry_median_pct", 0)
        if lds >= 70:
            reasons.append(("📊 领涨性",
                f"行业排名前{rank*100:.0f}%，持续跑赢板块中位数{median:+.1f}%"))
        elif lds >= 50:
            reasons.append(("📊 领涨性",
                f"行业排名约{rank*100:.0f}%分位，与板块中位数{median:+.1f}%持平"))
        else:
            reasons.append(("📊 领涨性", f"行业排名靠后，非板块领涨品种"))
    except Exception:
        reasons.append(("📊 领涨性", "分析异常"))

    # 资金承接性
    try:
        ab = result.get("absorption", {})
        abs_ = ab.get("score", 0)
        evt = ab.get("event_count", 0)
        if abs_ >= 70:
            reasons.append(("💰 资金承接",
                f"发现{evt}次跨板块虹吸事件，资金主动涌入且持续到收盘"))
        elif abs_ >= 50:
            reasons.append(("💰 资金承接",
                f"暂未发现显著的跨板块资金虹吸信号" if evt == 0 else
                f"发现{evt}次虹吸事件但强度偏弱"))
        else:
            reasons.append(("💰 资金承接", "资金承接信号弱，板块间无资金集中迹象"))
    except Exception:
        reasons.append(("💰 资金承接", "分析异常"))

    return reasons


def main():
    parser = argparse.ArgumentParser(description="龙头战法量化分析")
    parser.add_argument("code", help="股票代码，如 002xxx 或 600519")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--json", "-j", action="store_true", help="JSON 输出")
    parser.add_argument("--shared-data", help="共享数据 JSON 文件路径（由 preload.py 生成）")
    args = parser.parse_args()

    result = analyze_stock(args.code, verbose=args.verbose or args.json,
                           shared_data_file=args.shared_data)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(result, verbose=args.verbose)


if __name__ == "__main__":
    main()
