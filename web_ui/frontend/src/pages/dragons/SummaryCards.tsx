import type { ReviewCaliber, Summary } from "../../api/dragons";
import { StatCard } from "../../components/StatCard";
import { fmtPct, pnlClass, pnlStatTone } from "../../lib/format";

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

  const pendingSub =
    !s || !isStrict ? undefined : `待重算 ${s.pending_recompute} · 失败 ${s.failed_recompute}`;

  const caliberLabel = isStrict ? "严格口径" : "宽松口径";

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-7">
      <StatCard label="总记录" value={s?.total ?? 0} />
      <StatCard label="已完成" value={s?.derived_completed ?? s?.completed ?? 0} />
      <StatCard
        label="待回测"
        value={s?.derived_pending ?? s?.pending ?? 0}
        sub={pendingSub}
        tone={(s?.derived_pending ?? s?.pending ?? 0) > 0 ? "warn" : "neutral"}
      />
      <StatCard
        label="平均收益"
        value={fmtPct(avgReturn)}
        sub={caliberLabel}
        tone={pnlStatTone(avgReturn)}
      />
      <StatCard
        label="宽松胜率"
        value={looseRate != null ? `${looseRate.toFixed(1)}%` : "—"}
        sub={`n=${looseN ?? 0}`}
        hint="入选次日按照最低价买入"
        tone="neutral"
      />
      <StatCard
        label="严格胜率"
        value={strictRate != null ? `${strictRate.toFixed(1)}%` : "—"}
        sub={
          `n=${strictN}` +
          ((s?.cannot_evaluate ?? 0) > 0 ? ` · 无法评估 ${s?.cannot_evaluate}` : "")
        }
        hint="入选次日按照开盘价买入"
        tone="neutral"
      />
      <StatCard
        label="最佳"
        value={<span className="text-[19px]">{bestName || "—"}</span>}
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
