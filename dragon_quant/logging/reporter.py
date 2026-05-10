"""
报告生成器 — 将 ScanLogger 的结构化日志转为自然语言报告

严格按照 fewshot 格式输出详细报告。
"""

from typing import Optional
from dragon_quant.logging.logger import ScanLogger, LogEntry


class ReportBuilder:
    """从 ScanLogger 生成自然语言报告"""

    def __init__(self, logger: ScanLogger):
        self.logger = logger

    def build_stock_report(self, code: str, name: str = "",
                           board_count: int = 0,
                           concepts: Optional[list[str]] = None) -> str:
        """单只股票的完整分析报告（fewshot 格式）"""
        ctx = self.logger.report_context(code)
        dims = ctx.get("dimensions", {})

        composite = 0.0
        for d in dims.values():
            composite += d["score"] * d["weight"]

        # 等级判定
        if composite >= 80:
            grade = "🐉 龙头"
        elif composite >= 65:
            grade = "🔥 强票"
        elif composite >= 50:
            grade = "📈 中等"
        else:
            grade = "📉 偏弱"

        concept_str = " · ".join(concepts[:2]) if concepts else ""
        board_str = f"{board_count}连板" if board_count > 0 else ""

        lines = []
        header = f"{name}({code})"
        if concept_str:
            header += f"——{concept_str}"
        if board_str:
            header += f"——{board_str}"
        header += f"——{composite:.1f}分——{grade}"
        lines.append(header)

        # 带动性
        drive = dims.get("drive", {})
        if drive:
            lines.append(self._format_drive(drive))

        # 抗跌性
        anti = dims.get("anti_drop", {})
        if anti:
            lines.append(self._format_anti_drop(anti))

        # 领涨性
        lead = dims.get("leadership", {})
        if lead:
            lines.append(self._format_leadership(lead))

        # 资金承接
        absorb = dims.get("absorption", {})
        if absorb:
            lines.append(self._format_absorption(absorb))

        return "\n".join(lines)

    # ═══ 带动性 ═══

    def _format_drive(self, d: dict) -> str:
        details = d.get("details", d)
        bd = details.get("best_day_detail", {})
        voice = bd.get("voice", 0)
        follow = bd.get("follow", 0)
        board_det = bd.get("board_detail", {})
        board_lead = bd.get("board_leadership", 0)
        consecutive_bonus = details.get("consecutive_bonus", 0)
        score = d.get("score", 0)

        # 子分
        header = f"- 🐉 带动性({score:.0f}): 板块共鸣{voice:.0f}/小弟跟风{follow:.0f}/封板力度{board_lead:.0f}"

        lines = [header]

        # ─ 板块共鸣详情
        voice_raw = bd.get("voice_raw", {})
        if voice_raw:
            total = voice_raw.get("total", 0)
            lu = voice_raw.get("limit_up", 0)
            if lu / max(total, 1) >= 0.10:
                level = "极强"
            elif lu / max(total, 1) >= 0.05:
                level = "较强"
            elif lu > 0:
                level = "一般"
            else:
                level = "无"
            lines.append(f"  - 板块共鸣：板块共 {total} 只票，共 {lu} 只涨停，板块共鸣度{level}；")

        # ─ 小弟跟风详情
        follow_raw = bd.get("follow_raw", {})
        if follow_raw:
            total = follow_raw.get("total", 0)
            strong = follow_raw.get("strong", 0)
            down = follow_raw.get("down", 0)
            if follow >= 80:
                level = "强"
            elif follow >= 50:
                level = "较好"
            elif follow >= 20:
                level = "一般"
            else:
                level = "弱"
            lines.append(f"  - 小弟跟风：板块共 {total} 只票，其中 {strong} 只涨幅超 3%，{down} 只下跌，跟风属性{level}；")

        # ─ 封板力度详情
        seal_rank = board_det.get("seal_rank", "?")
        board_time = board_det.get("board_time", "?")
        is_yizi = board_det.get("is_yiziban", False)
        sector_lu_total = board_det.get("sector_limit_up_total", 0)

        if is_yizi:
            lines.append(f"  - 封板力度：一字板封死，无带动效应；")
        elif board_time and board_time != "?":
            if seal_rank == 1 and sector_lu_total > 0:
                desc = f"板块共 {sector_lu_total} 只涨停，{board_time} 封板，是最先涨停的票"
            elif sector_lu_total > 0:
                desc = f"板块共 {sector_lu_total} 只涨停，{board_time} 涨停，为第 {seal_rank} 只封板"
            else:
                desc = f"{board_time} 封板"
            lines.append(f"  - 封板力度：{desc}；")
        else:
            lines.append(f"  - 封板力度：未检测到明确封板时间；")

        return "\n".join(lines)

    # ═══ 抗跌性 ═══

    def _format_anti_drop(self, d: dict) -> str:
        details = d.get("details", d)
        score = d.get("score", 0)
        plunge_days = details.get("plunge_days", [])
        day_details = details.get("day_details", [])
        bonus = details.get("consecutive_plunge_bonus", 0)

        parts = [f"- 🛡️ 抗跌性({score:.0f}): "]

        if not plunge_days:
            parts.append("近 30 日无跳水日，无法评估抗跌性；")
        elif len(plunge_days) == 1:
            day_str = plunge_days[0]
            parts.append(f"{day_str} 大盘跳水，")
            if day_details:
                dd = day_details[0]
                stock_pct = dd.get("stock_pct", 0)
                market_pct = dd.get("market_pct", 0)
                if stock_pct > 0:
                    parts.append(f"该股逆势收红 +{stock_pct:.1f}%，抗跌性强；")
                elif stock_pct > market_pct:
                    parts.append(f"该股仅跌 {stock_pct:.1f}%（大盘跌 {market_pct:.1f}%），抗跌性较好；")
                else:
                    parts.append(f"该股跟跌 {stock_pct:.1f}%，抗跌性一般；")
            else:
                parts.append("抗跌性一般；")
        else:
            days_str = "/".join(plunge_days[:5])
            parts.append(f"近 30 日共 {len(plunge_days)} 个跳水日（{days_str}），")
            if bonus > 0:
                parts.append(f"连续暴跌中表现抗跌，额外加分；")
            else:
                # 算平均相对表现
                avg_stock = sum(dd.get("stock_pct", 0) for dd in day_details) / max(len(day_details), 1)
                avg_market = sum(dd.get("market_pct", 0) for dd in day_details) / max(len(day_details), 1)
                if avg_stock > avg_market:
                    parts.append(f"个股平均跑赢大盘，抗跌性较好；")
                else:
                    parts.append(f"个股跟随大盘下跌，抗跌性一般；")

        return "".join(parts)

    # ═══ 领涨性 ═══

    def _format_leadership(self, d: dict) -> str:
        details = d.get("details", d)
        score = d.get("score", 0)
        rank = details.get("intraday_rank", 0)
        total = details.get("total_components", 0)
        deviation = details.get("deviation", 0)
        lead_lag = details.get("lead_lag_bonus", 0)

        parts = [f"- 📊 领涨性({score:.0f}): "]

        if total > 0:
            pct_rank = rank / total * 100
            parts.append(f"行业排名前 {pct_rank:.0f}%（{rank}/{total}）")
        else:
            parts.append("行业排名无法评估")

        if deviation != 0:
            direction = "跑赢" if deviation > 0 else "跑输"
            parts.append(f"，{direction}中位数 {abs(deviation):+.1f}%")

        if lead_lag > 0:
            parts.append(f"，有领先板块拉伸信号")

        parts.append("；")
        return "".join(parts)

    # ═══ 资金承接 ═══

    def _format_absorption(self, d: dict) -> str:
        details = d.get("details", d)
        score = d.get("score", 0)
        event_count = details.get("event_count", 0)
        best = details.get("best_event") or {}
        reason = details.get("fallback_reason", "")

        parts = [f"- 💰 资金承接({score:.0f}): "]

        if reason:
            parts.append(f"{reason}；")
            return "".join(parts)

        if event_count == 0:
            parts.append("未检测到显著跨板块资金虹吸信号；")
            return "".join(parts)

        # 取最佳事件详情
        if best:
            target_pct = best.get("target_pct", 0)
            start_time = best.get("start_time", "?")
            end_time = best.get("end_time", "?")
            fleeing = best.get("fleeing_sectors", [])

            # 描述流出板块
            if fleeing:
                fleeing_names = [f.get("name", f.get("code", "?")) for f in fleeing[:3]]
                fleeing_str = "、".join(fleeing_names)
                parts.append(f"{start_time} {fleeing_str} 等板块跳水，")
                parts.append(f"{start_time}~{end_time} 该板块拉伸 +{target_pct}%")

                # 如果有第二个显著事件
                if event_count >= 2:
                    # 找时间上不同的第二事件
                    all_events_sorted = sorted(
                        [e for e in self._get_all_events(details)],
                        key=lambda e: e.get("start_time", "99:99")
                    )
                    for evt in all_events_sorted:
                        if evt.get("start_time") != start_time:
                            f2 = evt.get("fleeing_sectors", [])
                            if f2:
                                f2_names = [x.get("name", x.get("code", "?")) for x in f2[:2]]
                                parts.append(f"，{evt.get('start_time')} {'、'.join(f2_names)} 跳水")
                                parts.append(f"，板块继续拉伸 +{evt.get('target_pct', 0)}%")
                            break

                parts.append("；")
            else:
                parts.append(f"检测到 {event_count} 次虹吸事件，最强窗口涨幅 +{target_pct}%，{best.get('fleeing_count', 0)} 个板块被抽血；")
        else:
            parts.append(f"检测到 {event_count} 次虹吸事件；")

        return "".join(parts)

    def _get_all_events(self, details: dict) -> list[dict]:
        """从日志中提取所有事件（当前 scorer 只在 best_event 里存了最强的，
        但我们可以从 fleeing_sectors 数据重建）"""
        # 当前结构只存 best_event，返回一个单元素列表
        best = details.get("best_event")
        if best:
            return [best]
        return []

    # ═══ 汇总 ═══

    def build_summary_report(self, ranking: list[dict]) -> str:
        """全量排名表"""
        summary = self.logger.summary()
        api = summary.get("api", {})

        lines = [
            f"🐉 龙头战法扫描报告",
            f"{'═'*60}",
            f"耗时: {summary['elapsed_s']}s | 日志: {summary['total_entries']}条 | 错误: {summary['error_count']}个",
            "",
        ]

        lines.append(f"{'排名':4s} {'代码':8s} {'名称':8s} {'综合':>6s}  {'带动':>6s}  {'抗跌':>6s}  {'领涨':>6s}  {'承接':>6s}  等级")
        lines.append("-" * 78)
        for i, r in enumerate(ranking[:10]):
            dims = r.get("dimensions", {})
            composite = r.get("composite_score", 0)
            if composite >= 80:
                grade = "🐉"
            elif composite >= 65:
                grade = "🔥"
            elif composite >= 50:
                grade = "📈"
            else:
                grade = "📉"
            lines.append(
                f"{i+1:4d} {r['code']:8s} {r.get('name', ''):8s} "
                f"{composite:6.1f}  "
                f"{dims.get('drive',{}).get('score',0):6.1f}  "
                f"{dims.get('anti_drop',{}).get('score',0):6.1f}  "
                f"{dims.get('leadership',{}).get('score',0):6.1f}  "
                f"{dims.get('absorption',{}).get('score',0):6.1f}   "
                f"{grade}"
            )

        return "\n".join(lines)
