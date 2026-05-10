"""
报告生成器 — 将 ScanLogger 的结构化日志转为自然语言报告

用法:
  reporter = ReportBuilder(logger)
  text = reporter.build_stock_report("600519")
  all_text = reporter.build_summary_report(ranking)
"""

from typing import Optional
from dragon_quant.logging.logger import ScanLogger, LogEntry


def _nn(v, default=""):
    """none/null → 默认值"""
    return default if v is None else v


class ReportBuilder:
    """从 ScanLogger 生成自然语言报告"""

    def __init__(self, logger: ScanLogger):
        self.logger = logger

    def build_stock_report(self, code: str, name: str = "",
                           board_count: int = 0,
                           concepts: Optional[list[str]] = None) -> str:
        """单只股票的完整分析报告"""
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

    def _format_drive(self, d: dict) -> str:
        details = d.get("details", d)
        bd = details.get("best_day_detail", {})
        voice = bd.get("voice", 0)
        follow = bd.get("follow", 0)
        board_det = bd.get("board_detail", {})
        seal_rank = board_det.get("seal_rank", "?")
        board_time = board_det.get("board_time", "?")
        is_yizi = board_det.get("is_yiziban", False)
        gap_det = board_det.get("gap_detail", {})
        consecutive_bonus = details.get("consecutive_bonus", 0)
        score = d.get("score", 0)

        parts = [f"- 🐉 带动性({score:.0f})"]

        voice_str = ""
        if voice >= 80:
            voice_str = "极强"
        elif voice >= 50:
            voice_str = "较好"
        elif voice >= 20:
            voice_str = "一般"
        elif voice > 0:
            voice_str = "较弱"

        follow_str = ""
        if follow >= 80:
            follow_str = "强"
        elif follow >= 50:
            follow_str = "较好"
        elif follow >= 20:
            follow_str = "一般"
        elif follow > 0:
            follow_str = "弱"

        parts.append(f"板块共鸣{voice_str}")

        # 封板描述
        if is_yizi:
            seal_desc = "一字板封死，无带动"
        elif board_time and board_time != "?":
            seal_desc = f"{board_time}封板，同板块排第{seal_rank}"
            peer_count = gap_det.get("peer_count", 0)
            if peer_count > 0:
                avg_gap = gap_det.get("avg_gap_min", 0)
                if avg_gap is not None:
                    seal_desc += f"，小弟平均间隔{avg_gap:.0f}min"
        else:
            seal_desc = "未检测到明确封板信号"

        parts.append(f"/{seal_desc}")
        if consecutive_bonus > 0:
            parts.append(f"/连板+{consecutive_bonus:.0f}")

        return "".join(parts)

    def _format_anti_drop(self, d: dict) -> str:
        details = d.get("details", d)
        score = d.get("score", 0)
        plunge_days = details.get("plunge_days", [])
        day_details = details.get("day_details", [])
        bonus = details.get("consecutive_plunge_bonus", 0)

        parts = [f"- 🛡️ 抗跌性({score:.0f}): "]

        if not plunge_days:
            parts.append("近30日无跳水日")
        elif len(plunge_days) == 1:
            parts.append(f"{plunge_days[0]}大盘跳水，该股")
            # 尝试从 day_details 获取更多信息
            if day_details:
                dd = day_details[0]
                stock_pct = dd.get("stock_pct", 0)
                market_pct = dd.get("market_pct", 0)
                if stock_pct > 0:
                    parts.append(f"逆势收红+{stock_pct:.1f}%")
                else:
                    parts.append(f"小幅跟跌{stock_pct:.1f}%（大盘{market_pct:.1f}%）")
        else:
            parts.append(f"{len(plunge_days)}个跳水日")
            if bonus > 0:
                parts.append(f"，连续暴跌中表现抗跌(+{bonus:.0f})")

        return "".join(parts)

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
            parts.append(f"行业排名前{pct_rank:.0f}%（{rank}/{total}）")
        else:
            parts.append("行业排名无法评估")

        # 东财返回的 pct 是原始值（如 531=5.31%），除 100 显示
        if deviation != 0:
            direction = "跑赢" if deviation > 0 else "跑输"
            parts.append(f"，{direction}行业中位数{abs(deviation)/100:.1f}%")

        if lead_lag > 0:
            parts.append(f"，有领先板块拉伸信号(+{lead_lag:.0f})")

        return "".join(parts)

    def _format_absorption(self, d: dict) -> str:
        details = d.get("details", d)
        score = d.get("score", 0)
        event_count = details.get("event_count", 0)
        best = details.get("best_event") or {}
        reason = details.get("fallback_reason", "")

        parts = [f"- 💰 资金承接({score:.0f}): "]

        if reason:
            parts.append(reason)
        elif event_count == 0:
            parts.append("未检测到显著虹吸信号")
        else:
            parts.append(f"检测到{event_count}次虹吸事件")
            if best:
                target_pct = best.get("target_pct", 0)
                fleeing = best.get("fleeing_count", 0)
                drawdown = best.get("drawdown_ratio", 0)
                parts.append(f"，最强窗口涨幅+{target_pct}%")
                parts.append(f"，{fleeing}个板块被抽血")
                if drawdown > 0:
                    parts.append(f"，回撤{drawdown:.0%}")

        return "".join(parts)

    def build_summary_report(self, ranking: list[dict]) -> str:
        """全量汇总报告"""
        summary = self.logger.summary()
        api = summary.get("api", {})

        lines = [
            f"🐉 龙头战法扫描报告",
            f"{'═'*50}",
            f"耗时: {summary['elapsed_s']}s | 日志: {summary['total_entries']}条 | 错误: {summary['error_count']}个",
            f"API: {api.get('total',0)}次调用, {api.get('ok',0)}成功, {api.get('error',0)}失败, 总耗时{api.get('total_ms',0):.0f}ms",
            "",
        ]

        # TOP 10 排名表
        lines.append(f"{'排名':4s} {'代码':8s} {'名称':8s} {'综合':>6s}  {'带动':>6s}  {'抗跌':>6s}  {'领涨':>6s}  {'承接':>6s}  等级")
        lines.append("-" * 75)
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
