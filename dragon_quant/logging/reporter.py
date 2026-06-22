"""
报告生成器 — 将 ScanLogger 的结构化日志转为自然语言报告

严格按照 fewshot.md 格式输出详细报告。
"""

from typing import Optional
from dragon_quant.logging.logger import ScanLogger, LogEntry


class ReportBuilder:
    """从 ScanLogger 生成自然语言报告"""

    def __init__(self, logger: ScanLogger):
        self.logger = logger

    def build_stock_report(self, code: str, name: str = "",
                           board_count: int = 0,
                           concepts: Optional[list[str]] = None,
                           composite_score: float = 0.0,
                           dimensions: Optional[dict] = None,
                           primary_sector_name: str = "") -> str:
        """单只股票的完整分析报告（fewshot 格式）"""
        dims = dimensions or {}

        # 等级判定
        if composite_score >= 80:
            grade = "龙头"
        elif composite_score >= 65:
            grade = "强票"
        elif composite_score >= 50:
            grade = "中等"
        else:
            grade = "偏弱"

        concept_str = concepts[0] if concepts else ""
        board_str = f"{board_count}连板" if board_count > 0 else ""

        lines = []
        header = f"{name}({code})"
        if concept_str:
            header += f"——{concept_str}"
        if board_str:
            header += f"——{board_str}"
        header += f"-{composite_score:.1f}分-{grade}"
        lines.append(header)

        # 带动性
        drive = dims.get("drive", {})
        if drive:
            lines.append(self._format_drive(drive, primary_sector_name, name))

        # 抗跌性
        anti = dims.get("anti_drop", {})
        if anti:
            lines.append(self._format_anti_drop(anti, name))

        # 领涨性
        lead = dims.get("leadership", {})
        if lead:
            lines.append(self._format_leadership(lead))

        # 资金承接
        absorb = dims.get("absorption", {})
        if absorb:
            lines.append(self._format_absorption(absorb, primary_sector_name))

        return "\n".join(lines)

    # ═══ 带动性 ═══

    def _format_drive(self, d: dict, sector_name: str = "", stock_name: str = "") -> str:
        details = d.get("details", d)
        bd = details.get("best_day_detail", {})
        voice = bd.get("voice", 0)
        follow = bd.get("follow", 0)
        board_det = bd.get("board_detail", {})
        board_lead = bd.get("board_leadership", 0)
        score = d.get("score", 0)

        sector_label = f"{sector_name}" if sector_name else "该"

        header = f"- 🐉 带动性({score:.0f}): 板块共鸣{voice:.0f}/小弟跟风{follow:.0f}/封板力度{board_lead:.0f}"
        lines = [header]

        # ─ 板块共鸣详情
        voice_raw = bd.get("voice_raw", {})
        if voice_raw:
            total = voice_raw.get("total", 0)
            scoring_total = voice_raw.get("scoring_total", total)
            sample_limit = voice_raw.get("sample_limit", scoring_total)
            lu = voice_raw.get("limit_up", 0)
            denom = max(scoring_total, 1)
            if lu / denom >= 0.10:
                level = "极强"
            elif lu / denom >= 0.05:
                level = "较强"
            elif lu > 0:
                level = "一般"
            else:
                level = "无"
            if scoring_total < total:
                lines.append(
                    f"    - 板块共鸣({voice:.0f})：{sector_label}板块全量共 {total} 只票，带动性评分按涨跌幅居前 {scoring_total} 只样本（上限 {sample_limit}）计算，其中 {lu} 只票涨停，板块共鸣度{level}；"
                )
            else:
                lines.append(f"    - 板块共鸣({voice:.0f})：{sector_label}板块共 {total} 只票，共 {lu} 只票涨停，板块共鸣度{level}；")

        # ─ 小弟跟风详情
        follow_raw = bd.get("follow_raw", {})
        if follow_raw:
            total = follow_raw.get("total", 0)
            scoring_total = follow_raw.get("scoring_total", total)
            sample_limit = follow_raw.get("sample_limit", scoring_total)
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
            if scoring_total < total:
                lines.append(
                    f"    - 小弟跟风({follow:.0f})：{sector_label}板块全量共 {total} 只票，跟风评分按涨跌幅居前 {scoring_total} 只样本（上限 {sample_limit}）计算，其中 {strong} 只涨幅超过 3%，{down} 只股票下跌，跟风属性{level}；"
                )
            else:
                lines.append(f"    - 小弟跟风({follow:.0f})：{sector_label}板块共 {total} 只票，其中 {strong} 只涨幅超过 3%，{down} 只股票下跌，跟风属性{level}；")

        # ─ 封板力度详情
        seal_rank = board_det.get("seal_rank", "?")
        board_time = board_det.get("board_time", "?")
        is_yizi = board_det.get("is_yiziban", False)
        sector_lu_total = board_det.get("sector_limit_up_total", 0)

        if is_yizi:
            lines.append(f"    - 封板力度({board_lead:.0f})：一字板封死，无带动效应；")
        elif board_time and board_time != "?":
            try:
                h, m = board_time.split(":")
                board_minutes = int(h) * 60 + int(m)
            except (ValueError, AttributeError):
                board_minutes = 9999
            is_morning = board_minutes <= 9 * 60 + 35
            if seal_rank == 1 and sector_lu_total > 0:
                if is_morning:
                    desc = f"{sector_label}板块共 {sector_lu_total} 只股票涨停，{stock_name}在 {board_time} 开盘即涨停，是最先涨停的票"
                else:
                    desc = f"{sector_label}板块共 {sector_lu_total} 只股票涨停，{stock_name}在 {board_time} 涨停，是最先涨停的票"
            elif sector_lu_total > 0:
                desc = f"{sector_label}板块共 {sector_lu_total} 只股票涨停，{stock_name}在 {board_time} 涨停，为第 {seal_rank} 只封板"
            else:
                desc = f"{stock_name}在 {board_time} 封板"
            lines.append(f"    - 封板力度({board_lead:.0f})：{desc}；")
        else:
            lines.append(f"    - 封板力度({board_lead:.0f})：未检测到明确封板时间；")

        return "\n".join(lines)

    # ═══ 抗跌性 ═══

    def _format_anti_drop(self, d: dict, stock_name: str = "") -> str:
        from datetime import datetime

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
            try:
                dt = datetime.strptime(day_str, "%Y-%m-%d")
                day_fmt = f"{dt.month}.{dt.day}"
            except Exception:
                day_fmt = day_str
            parts.append(f"{day_fmt} 大盘跳水，")
            if day_details:
                dd = day_details[0]
                stock_pct = dd.get("stock_pct", 0)
                market_pct = dd.get("market_pct", 0)
                if stock_pct > 0:
                    parts.append(f"{stock_name}逆势收红 +{stock_pct:.1f}%，抗跌性强；")
                elif stock_pct > market_pct:
                    parts.append(f"{stock_name}仅跌 {stock_pct:.1f}%（大盘跌 {market_pct:.1f}%），抗跌性较好；")
                else:
                    parts.append(f"{stock_name}跟跌 {stock_pct:.1f}%，抗跌性一般；")
            else:
                parts.append("抗跌性一般；")

        else:
            days_fmt = []
            for d in plunge_days[:5]:
                try:
                    dt = datetime.strptime(d, "%Y-%m-%d")
                    days_fmt.append(f"{dt.month}.{dt.day}")
                except Exception:
                    days_fmt.append(d)
            days_str = "/".join(days_fmt)
            parts.append(f"分析对比大盘近 15 日 K，{days_str} 日大盘跳水，")
            if bonus > 0:
                parts.append(f"连续暴跌中表现抗跌，额外加分；")
            else:
                avg_stock = sum(dd.get("stock_pct", 0) for dd in day_details) / max(len(day_details), 1)
                avg_market = sum(dd.get("market_pct", 0) for dd in day_details) / max(len(day_details), 1)
                if avg_stock > 0:
                    parts.append(f"但{stock_name}逆势收红，抗跌性强；")
                elif avg_stock > avg_market:
                    parts.append(f"但{stock_name}维持横盘，抗跌性较好；")
                else:
                    parts.append(f"{stock_name}跟随大盘下跌，抗跌性一般；")

        return "".join(parts)

    # ═══ 领涨性 ═══

    def _format_leadership(self, d: dict) -> str:
        details = d.get("details", d)
        score = d.get("score", 0)
        rank = details.get("intraday_rank", 0)
        total = details.get("total_components", 0)
        deviation = details.get("deviation", 0)
        lead_lag = details.get("lead_lag_bonus", 0)

        leadlag_str = ""
        if lead_lag > 0:
            leadlag_str = "，有领先板块拉伸信号"

        if total > 0:
            pct_rank = rank / total * 100
            direction = "跑赢" if deviation > 0 else "跑输"
            return (f"- 📊 领涨性({score:.0f}): "
                    f"行业排名前{pct_rank:.0f}%，"
                    f"{direction}中位数{abs(deviation):+.1f}%{leadlag_str}")
        else:
            return f"- 📊 领涨性({score:.0f}): 行业排名无法评估{leadlag_str}"

    # ═══ 资金承接 ═══

    def _format_absorption(self, d: dict, sector_name: str = "") -> str:
        details = d.get("details", d)
        score = d.get("score", 0)
        event_count = details.get("event_count", 0)
        all_events = details.get("all_events", [])
        # 兼容历史字段：absorption scorer 早期使用 reason，后续统一为 fallback_reason
        reason = details.get("fallback_reason") or details.get("reason") or ""

        parts = [f"- 💰 资金承接({score:.0f}): "]

        if reason:
            parts.append(f"{reason}")
            return "".join(parts)

        if event_count == 0 or not all_events:
            parts.append("未检测到显著跨板块资金虹吸信号")
            return "".join(parts)  + "；"

        sector_label = sector_name or "该板块"

        # 第一事件
        evt1 = all_events[0]
        dive_time1 = evt1.get("dive_time", "")
        rally_time1 = evt1.get("rally_time", "")
        time_diff1 = evt1.get("time_diff_min", 0)
        fleeing1 = evt1.get("fleeing_sectors", [])
        fleeing_names_list1 = [f.get("name", f.get("code", "?")) for f in fleeing1[:3]]
        fleeing_names1 = "、".join(fleeing_names_list1)
        target_pct1 = evt1.get("target_pct", 0)

        # fewshot 对齐：0.4% 也归类为“小幅拉伸”
        if abs(target_pct1) >= 1.5:
            stretch1 = "大幅拉伸"
        elif abs(target_pct1) >= 0.3:
            stretch1 = "迎来小幅拉伸"
        else:
            stretch1 = "拉伸"

        # fewshot 对齐：
        # - 单一板块："白酒板块跳水"
        # - 多板块："白酒、煤炭等板块跳水"
        if len(fleeing_names_list1) <= 1:
            parts.append(f"{dive_time1} {fleeing_names1}板块跳水，")
        else:
            parts.append(f"{dive_time1} {fleeing_names1}等板块跳水，")
        # fewshot 示例不输出“间隔xx分钟”
        parts.append(f"{rally_time1} {sector_label}{stretch1}（+{target_pct1}%）")

        # 第二事件
        if len(all_events) >= 2:
            evt2 = all_events[1]
            dive_time2 = evt2.get("dive_time", "")
            rally_time2 = evt2.get("rally_time", "")
            fleeing2 = evt2.get("fleeing_sectors", [])
            fleeing_names_list2 = [f.get("name", f.get("code", "?")) for f in fleeing2[:2]]
            fleeing_names2 = "、".join(fleeing_names_list2)
            target_pct2 = evt2.get("target_pct", 0)

            if abs(target_pct2) >= 1.5:
                stretch2 = "继续大幅拉伸"
            elif abs(target_pct2) >= 0.3:
                stretch2 = "继续小幅拉伸"
            else:
                stretch2 = "继续拉伸"

            if len(fleeing_names_list2) <= 1:
                parts.append(f"，{dive_time2} {fleeing_names2}板块跳水，")
            else:
                parts.append(f"，{dive_time2} {fleeing_names2}等板块跳水，")
            parts.append(f"{rally_time2} {sector_label}{stretch2}（+{target_pct2}%）")

        parts.append("；")
        return "".join(parts)

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

    # ═══════════════════════════════════════════════════════
    # scorers_v2 五维报告（带动/领涨/抗跌/流动/资金承接 + 真龙判定）
    # ═══════════════════════════════════════════════════════

    def build_stock_report_v2(self, code: str, name: str = "",
                              board_count: int = 0,
                              concepts: Optional[list[str]] = None,
                              composite_score: float = 0.0,
                              dimensions: Optional[dict] = None,
                              primary_sector_name: str = "",
                              is_true_dragon: bool = False,
                              reject_reason: Optional[str] = None) -> str:
        """单只股票的五维分析报告（v2）。"""
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
        lines.append(f"- 🐉 带动性({s('drive'):.0f}): {self._v2_drive(dims.get('drive', {}))}")
        lines.append(f"- 📊 领涨性({s('leadership'):.0f}): {self._v2_lead(dims.get('leadership', {}))}")
        lines.append(f"- 🛡️ 抗跌性({s('anti_drop'):.0f}): {self._v2_anti(dims.get('anti_drop', {}), primary_sector_name)}")
        lines.append(f"- 💧 流动性({s('liquidity'):.0f}): {self._v2_liq(dims.get('liquidity', {}))}")
        lines.append(f"- 💰 资金承接({s('absorption'):.0f}): {self._v2_abs(dims.get('absorption', {}))}")
        return "\n".join(lines)

    @staticmethod
    def _v2_drive(d: dict) -> str:
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
        lead_desc = "；".join(lead_parts) if lead_parts else (
            f"带动{lead.get('n_lead', 0)}次，被带{lead.get('n_follow', 0)}次"
        )

        return (f"封板最早{early:.0f}：{seal}；"
                f"带动板块{det.get('s_lead', 0):.0f}：{lead_desc}；"
                f"板块共鸣{det.get('s_voice', 0):.0f}："
                f"涨停{voice.get('n_limit', 0)}只/强势{voice.get('n_strong', 0)}只")

    @staticmethod
    def _v2_lead(d: dict) -> str:
        det = d.get("details", {})
        return (f"连板{det.get('s_board', 0):.0f}(本{det.get('board_count', 0)}板/最高{det.get('b_max', 0)}板)"
                f"/涨幅{det.get('s_pct', 0):.0f}(5日{det.get('fived_pct', 0):.1f}%,"
                f"排名{det.get('pct_rank', '-')}/{det.get('pct_n', '-')})")

    @staticmethod
    def _v2_anti(d: dict, primary_sector_name: str = "") -> str:
        det = d.get("details", {})
        if det.get("degraded"):
            return "数据不足，给中性分"
        market = ReportBuilder._fmt_dip_event(det.get("market", {}), "大盘", "跳水")
        sector_label = primary_sector_name or "主板块"
        sector = ReportBuilder._fmt_dip_event(det.get("sector", {}), sector_label, "回落")
        return (f"大盘维度{det.get('s_market', 0):.0f}：{market}；"
                f"板块维度{det.get('s_sector', 0):.0f}：{sector}")

    @staticmethod
    def _v2_liq(d: dict) -> str:
        det = d.get("details", {})
        n_open = det.get("n_open", -1)
        open_str = "未知" if n_open < 0 else f"{n_open}次"
        return (f"换手{det.get('s_turnover', 0):.0f}(换手率{det.get('turnover_rate', 0):.1f}%)"
                f"/封板{det.get('s_seal', 0):.0f}(强度{det.get('s_seal_strength', 0):.0f},"
                f"开板{open_str})")

    @staticmethod
    def _v2_abs(d: dict) -> str:
        det = d.get("details", {})
        if det.get("fallback") or det.get("event_count", 0) == 0:
            return det.get("fallback_reason", "暂无显著虹吸信号")
        be = det.get("best_event") or (det.get("all_events") or [{}])[0]
        fleeing = be.get("fleeing_sectors", [])
        names = ReportBuilder._fmt_fleeing_sectors(fleeing)
        return (f"检测到{det.get('event_count', 0)}次资金承接；"
                f"{be.get('dive_time', '时间缺失')} {names}板块跳水"
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
                f"该股同期{ReportBuilder._fmt_pct(stock_chg)}（{perf}）")

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

    def build_summary_report_v2(self, ranking: list[dict]) -> str:
        """五维全量排名表（v2）。"""
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
