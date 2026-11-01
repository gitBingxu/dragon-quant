import type { ReviewCaliber, Summary } from "../../api/dragons";
import { HintIcon } from "../../components/HintIcon";
import { StatCard } from "../../components/StatCard";
import { fmtPct, pnlClass, pnlStatTone } from "../../lib/format";

const LOOSE_HINT =
  "宽松口径：入选后首个 high≠low 的交易日按最低价买入；一字板自动顺延。";
const STRICT_HINT =
  "严格口径：入选后首个交易日按开盘价买入；次日一字板按涨停价成交，不顺延。";

export function SummaryCards({
  summary,
  caliber,
}: {
  summary: Summary | null;
  caliber: ReviewCaliber;
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

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-7">
      <StatCard label="总记录" value={s?.total ?? 0} />
      <StatCard label="已回测" value={s?.derived_completed ?? s?.completed ?? 0} />
      <StatCard
        label="待回测"
        value={s?.derived_pending ?? s?.pending ?? 0}
        sub="入选满 5 个交易日后回测"
        tone={(s?.derived_pending ?? s?.pending ?? 0) > 0 ? "warning" : "neutral"}
      />
      <StatCard
        label="平均收益"
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
          `n=${strictN}` +
          ((s?.cannot_evaluate ?? 0) > 0 ? ` · 无法评估 ${s?.cannot_evaluate}` : "")
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
  );
}
