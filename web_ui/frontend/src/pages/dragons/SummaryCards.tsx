import type { ReactNode } from "react";
import type { ReviewCaliber, Summary } from "../../api/dragons";
import { HintIcon } from "../../components/HintIcon";
import { StatCard } from "../../components/StatCard";
import { fmtPct, pnlClass, pnlStatTone } from "../../lib/format";

const LOOSE_HINT =
  "宽松口径：入选后首个 high≠low 的交易日按最低价买入；一字板自动顺延。"
  + "收益为理想最低价下的事后最大潜在涨幅，不是账户盈利。";
const STRICT_HINT =
  "严格口径：入选后首个交易日按开盘价买入；次日一字板按涨停价成交，不顺延（假设价）。"
  + "收益为事后最大潜在涨幅；回撤是峰值前相对入场的不利波动，不是权益曲线峰谷回撤。";
const PENDING_HINT =
  "待回测口径：只统计收益窗口已走完（买入后已满 5 个交易日）且尚无严格结果的记录；"
  + "未成熟记录等窗口走完后自动进入待回测。";

export function SummaryCards({
  summary,
  coverage,
  caliber,
  pendingHeader,
  pendingFooter,
}: {
  summary: Summary | null;
  coverage?: Summary | null;
  caliber: ReviewCaliber;
  /** 「待回测」标签行内的增量回测入口；仅待回测 > 0 时由页面传入。 */
  pendingHeader?: ReactNode;
  /** 「待回测」卡片底部的状态/说明文案。 */
  pendingFooter?: ReactNode;
}) {
  const s = summary;
  const isStrict = caliber === "strict";

  const avgReturn = isStrict ? s?.avg_return_strict : s?.avg_return;
  const looseRate = s?.win_rate_loose;
  const looseN = s?.win_rate_loose_n;
  const strictRate = s?.win_rate_strict;
  const strictN = s?.win_rate_strict_n;
  const bestName = isStrict ? s?.best_stock_name_strict : s?.best_stock_name;
  const bestCode = isStrict ? s?.best_stock_code_strict : s?.best_stock_code;
  const bestReturn = isStrict ? s?.best_return_strict : s?.best_return;

  const caliberLabel = isStrict ? "严格口径" : "宽松口径";
  const partialWindow = s?.partial_window ?? s?.partial ?? 0;
  const partialMissing = s?.partial_missing_bars ?? 0;
  // 三种原因都没有可用的严格结果；卡片只汇总成一个「无法评估」。
  const strictUnavailable =
    (s?.cannot_evaluate ?? 0) + partialWindow + partialMissing;
  const covPartialWindow = coverage?.partial_window ?? coverage?.partial ?? 0;
  const covPartialMissing = coverage?.partial_missing_bars ?? 0;

  return (
    <div className="grid gap-2">
      <div className="grid gap-1 rounded-lg border border-hairline px-3.5 py-2 text-caption text-ink-2">
        <div>
          <span className="font-semibold text-ink">全量覆盖</span>
          （固定分母，不随筛选变化）：总记录 {coverage?.total ?? "—"} ·
          已回测 {coverage?.derived_completed ?? coverage?.completed ?? "—"} ·
          待回测 {coverage?.derived_pending ?? coverage?.pending ?? "—"}
          {(coverage?.derived_pending_immature ?? 0) > 0
            ? ` · 未成熟 ${coverage?.derived_pending_immature}`
            : ""}
          {covPartialWindow > 0 ? ` · 窗口未成熟 ${covPartialWindow}` : ""}
          {covPartialMissing > 0 ? ` · 窗口缺K线 ${covPartialMissing}` : ""}
        </div>
        <div>
          <span className="font-semibold text-ink">当前筛选</span>：
          {s?.total ?? 0} 条 · 已回测 {s?.derived_completed ?? s?.completed ?? 0} ·
          待回测 {s?.derived_pending ?? s?.pending ?? 0}
          {(s?.derived_pending_immature ?? 0) > 0
            ? ` · 未成熟 ${s?.derived_pending_immature}`
            : ""}
        </div>
      </div>
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-7">
      <StatCard label="总记录" value={s?.total ?? 0} />
      <StatCard label="已回测" value={s?.derived_completed ?? s?.completed ?? 0} />
      <StatCard
        label="待回测"
        value={s?.derived_pending ?? s?.pending ?? 0}
        tone={(s?.derived_pending ?? s?.pending ?? 0) > 0 ? "warning" : "neutral"}
        headerAction={pendingHeader}
        labelExtra={
          <HintIcon label="待回测口径" align="start">
            {PENDING_HINT}
          </HintIcon>
        }
        footer={pendingFooter}
      />
      <StatCard
        label="平均最大潜在涨幅"
        value={fmtPct(avgReturn)}
        sub={caliberLabel}
        tone={pnlStatTone(avgReturn)}
      />
      <StatCard
        label="宽松胜率"
        labelExtra={<HintIcon label="宽松胜率口径" align="start">{LOOSE_HINT}</HintIcon>}
        value={looseRate != null ? `${looseRate.toFixed(1)}%` : "—"}
        sub={`n=${looseN ?? 0}`}
        tone="neutral"
      />
      <StatCard
        label="严格胜率"
        labelExtra={<HintIcon label="严格胜率口径" align="end">{STRICT_HINT}</HintIcon>}
        value={strictRate != null ? `${strictRate.toFixed(1)}%` : "—"}
        sub={
          `n=${strictN ?? 0}` +
          (strictUnavailable > 0 ? ` · 无法评估 ${strictUnavailable}` : "")
        }
        tone="neutral"
      />
      <StatCard
        label="最佳"
        value={<span className="text-section">{bestName || "—"}</span>}
        sub={
          bestCode ? (
            <span>
              <span className="font-mono">{bestCode}</span>{" "}
              <span className={pnlClass(bestReturn)}>{fmtPct(bestReturn)}</span>
            </span>
          ) : undefined
        }
      />
    </div>
    </div>
  );
}
