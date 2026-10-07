"""
报告生成器 — 将 ScanLogger 的结构化日志转为自然语言报告
"""

from typing import Optional
from dragon_quant.logging.logger import ScanLogger


class ReportBuilder:
    """从 ScanLogger 生成自然语言报告"""

    def __init__(self, logger: ScanLogger):
        self.logger = logger

    # ═══════════════════════════════════════════════════════
    # 五维报告（带动/领涨/抗跌/流动/资金承接 + 真龙判定）
    # ═══════════════════════════════════════════════════════

    def build_stock_report(self, code: str, name: str = "",
                           board_count: int = 0,
                           concepts: Optional[list[str]] = None,
                           composite_score: float = 0.0,
                           dimensions: Optional[dict] = None,
                           primary_sector_name: str = "",
                           is_true_dragon: bool = False,
                           reject_reason: Optional[str] = None) -> str:
        """单只股票的五维分析报告。"""
        dims = dimensions or {}

        def s(dim):
            return dims.get(dim, {}).get("score", 0.0)

        verdict = "✓真龙" if is_true_dragon else "✗非真龙"
        concept_str = (concepts[0] if concepts else "")
        board_str = f"{board_count}连板" if board_count > 0 else ""

        header = f"{name}({code})"
        if concept_str:
            header += f"——{concept_str}"
        if board_str:
            header += f"——{board_str}"
        header += f"-{composite_score:.1f}分-{verdict}"

        lines = [header]
        if not is_true_dragon and reject_reason:
            lines.append(f"- ⛔ 一票否决: {reject_reason}")
        lines.append(f"- 🐉 带动性({s('drive'):.0f}): {self._drive(dims.get('drive', {}))}")
        lines.append(f"- 📊 领涨性({s('leadership'):.0f}): {self._lead(dims.get('leadership', {}))}")
        lines.append(f"- 🛡️ 抗跌性({s('anti_drop'):.0f}): {self._anti(dims.get('anti_drop', {}), primary_sector_name)}")
        lines.append(f"- 💧 流动性({s('liquidity'):.0f}): {self._liq(dims.get('liquidity', {}))}")
        lines.append(f"- 资金承接({s('absorption'):.0f}): {self._abs(dims.get('absorption', {}))}")
        for dim, result in dims.items():
            details = result.get("details", {})
            if details.get("error"):
                lines.append(f"- {dim} 评分异常：{details['error']}")
        return "\n".join(lines)

    @staticmethod
    def _drive(d: dict) -> str:
        det = d.get("details", {})
        early = det.get("s_early", 0)
        early_det = det.get("early", {})
        lead = det.get("lead", {})
        voice = det.get("voice", {})

        if early_det.get("sealed"):
            seal = (f"{early_det.get('seal_time', '时间缺失')}封板，"
                    f"封单量{ReportBuilder._fmt_hands(early_det.get('bid1_volume', 0))}，"
                    f"涨停池第{early_det.get('rank', '-')}/{early_det.get('pool_size', '-')}" )
        else:
            seal = f"未识别到稳定封板，涨停池{early_det.get('pool_size', 0)}只"

        lead_parts = []
        for e in lead.get("lead_events", [])[:2]:
            lead_parts.append(
                f"{e.get('event_time', '-')}个股拉升{ReportBuilder._fmt_pct(e.get('stock_gain_pct', 0))}，"
                f"随后板块拉升{ReportBuilder._fmt_pct(e.get('sector_gain_pct', 0))}"
            )
        for e in lead.get("follow_events", [])[:2]:
            lead_parts.append(
                f"{e.get('sector_event_time', '-')}板块先拉升{ReportBuilder._fmt_pct(e.get('sector_gain_pct', 0))}，"
                f"{e.get('stock_follow_time', '-')}个股跟随{ReportBuilder._fmt_pct(e.get('stock_gain_pct', 0))}"
            )
        lead_desc = lead.get("reason") or ("；".join(lead_parts) if lead_parts else (
            f"带动{lead.get('n_lead', 0)}次，被带{lead.get('n_follow', 0)}次"
        ))
        if early_det.get("degraded"):
            seal += f"；数据降级：{early_det.get('reason', '封板比较样本不完整')}"

        return (f"封板最早{early:.0f}：{seal}；"
                f"带动板块{det.get('s_lead', 0):.0f}：{lead_desc}；"
                f"板块共鸣{det.get('s_voice', 0):.0f}："
                f"涨停{voice.get('n_limit', 0)}只/强势{voice.get('n_strong', 0)}只")

    @staticmethod
    def _lead(d: dict) -> str:
        det = d.get("details", {})
        return (f"连板{det.get('s_board', 0):.0f}(本{det.get('board_count', 0)}板/最高{det.get('b_max', 0)}板)"
                f"/涨幅{det.get('s_pct', 0):.0f}(5日{det.get('fived_pct', 0):.1f}%,"
                f"排名{det.get('pct_rank', '-')}/{det.get('pct_n', '-')})")

    @staticmethod
    def _anti(d: dict, primary_sector_name: str = "") -> str:
        det = d.get("details", {})
        if det.get("degraded"):
            return "数据不足，给中性分"
        market = ReportBuilder._fmt_dip_event(det.get("market", {}), "大盘", "跳水")
        sector_label = primary_sector_name or "主板块"
        sector = ReportBuilder._fmt_dip_event(det.get("sector", {}), sector_label, "回落")
        return (f"大盘维度{det.get('s_market', 0):.0f}：{market}；"
                f"板块维度{det.get('s_sector', 0):.0f}：{sector}")

    @staticmethod
    def _liq(d: dict) -> str:
        det = d.get("details", {})
        n_open = det.get("n_open", -1)
        open_str = "未知" if n_open < 0 else f"{n_open}次"
        return (f"换手{det.get('s_turnover', 0):.0f}(换手率{det.get('turnover_rate', 0):.1f}%)"
                f"/封板{det.get('s_seal', 0):.0f}(强度{det.get('s_seal_strength', 0):.0f},"
                f"开板{open_str})"
                + ("；数据降级：" + "、".join(det.get("reasons") or ["换手比较样本不足"])
                   if det.get("degraded") else ""))

    @staticmethod
    def _abs(d: dict) -> str:
        det = d.get("details", {})
        if det.get("fallback") or det.get("event_count", 0) == 0:
            return det.get("fallback_reason", "暂无显著虹吸信号")
        be = det.get("best_event") or (det.get("all_events") or [{}])[0]
        fleeing = be.get("fleeing_sectors", [])
        names = ReportBuilder._fmt_fleeing_sectors(fleeing)
        summary = f"检测到{det.get('event_count', 0)}次资金承接；"
        if "raw_event_count" in det:
            summary = (f"{det['raw_event_count']}个窗口合并为{det['event_count']}次独立承接，"
                       f"取衰减后最强{det['selected_event_count']}次均值；"
                       f"代表事件原始{be['score']:.2f}分，距最新样本{be['age_trade_days']}个交易日，"
                       f"衰减系数{be['recency_weight']:.3f}，调整后{be['adjusted_score']:.2f}分；")
        return (summary + f"{be.get('dive_time', '时间缺失')} {names}板块跳水"
                f"(平均{ReportBuilder._fmt_pct(be.get('fleeing_avg_drop', 0))})，"
                f"{be.get('rally_time', '时间缺失')} 目标板块拉升"
                f"{ReportBuilder._fmt_pct(be.get('target_pct', 0))}，承接上述板块出逃资金")

    @staticmethod
    def _fmt_hands(v) -> str:
        try:
            vol = float(v or 0)
        except (TypeError, ValueError):
            vol = 0.0
        if vol >= 10000:
            return f"{vol / 10000:.1f}万手"
        return f"{vol:.0f}手"

    @staticmethod
    def _fmt_pct(v) -> str:
        try:
            pct = float(v or 0)
        except (TypeError, ValueError):
            pct = 0.0
        return f"{pct:+.2f}%"

    @staticmethod
    def _fmt_dip_event(det: dict, label: str, verb: str) -> str:
        if det.get("degraded"):
            return det.get("reason", "数据不足")
        if det.get("no_dip"):
            return f"{label}无有效{verb}段"
        event = det.get("deepest_event") or (det.get("dip_events") or [{}])[0]
        if not event:
            return f"{label}无有效{verb}段"
        base_drop = event.get("base_drop_pct", 0)
        stock_chg = event.get("stock_change_pct", 0)
        perf = ReportBuilder._anti_perf_desc(base_drop, stock_chg)
        return (f"{event.get('start_time', '-')}-{event.get('bottom_time', '-')} "
                f"{label}{verb}{ReportBuilder._fmt_pct(base_drop)}，"
                f"该股同期{ReportBuilder._fmt_pct(stock_chg)}（{perf}）"
                + (f"；{det['rebound_reason']}" if det.get("rebound_reason") else ""))

    @staticmethod
    def _anti_perf_desc(base_drop, stock_chg) -> str:
        try:
            base = float(base_drop or 0)
            stock = float(stock_chg or 0)
        except (TypeError, ValueError):
            return "表现未知"
        if stock > 0:
            return "逆势上涨"
        if abs(stock) < abs(base):
            return "明显少跌"
        return "跟随回落"

    @staticmethod
    def _fmt_fleeing_sectors(fleeing: list[dict]) -> str:
        if not fleeing:
            return "多个"
        parts = []
        for s in fleeing[:3]:
            parts.append(f"{s.get('name', s.get('code', '未知'))}({ReportBuilder._fmt_pct(s.get('drop_pct', 0))})")
        suffix = "等" if len(fleeing) > 3 else ""
        return "、".join(parts) + suffix

    def build_summary_report(self, ranking: list[dict]) -> str:
        """五维全量排名表。"""
        summary = self.logger.summary()
        lines = [
            f"🐉 龙头战法扫描报告（v2 五维识别真龙）",
            f"{'═'*72}",
            f"耗时: {summary['elapsed_s']}s | 日志: {summary['total_entries']}条 | 错误: {summary['error_count']}个",
            "",
            f"{'排名':4s} {'代码':8s} {'名称':8s} {'综合':>6s}  {'带动':>6s}  {'领涨':>6s}  {'抗跌':>6s}  {'流动':>6s}  {'承接':>6s}  真龙",
            "-" * 90,
        ]
        for i, r in enumerate(ranking[:10]):
            dims = r.get("dimensions", {})

            def s(dim):
                return dims.get(dim, {}).get("score", 0)
            mark = "🐉" if r.get("is_true_dragon") else "✗"
            lines.append(
                f"{i+1:4d} {r['code']:8s} {r.get('name', ''):8s} "
                f"{r.get('composite_score', 0):6.1f}  "
                f"{s('drive'):6.1f}  {s('leadership'):6.1f}  {s('anti_drop'):6.1f}  "
                f"{s('liquidity'):6.1f}  {s('absorption'):6.1f}   {mark}"
            )
        return "\n".join(lines)
