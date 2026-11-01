import type { PlanFollowup, PrepWatermark, ReviewContext } from "../../api/assets";
import { Badge, type BadgeTone } from "../../components/Badge";
import { fmtPct } from "../../lib/format";
import {
  VERIFICATION_CONCLUSION_LABELS,
  labelOf,
} from "../../lib/labels";
import { buildFollowupKpis } from "./prepModel";

const VERIFICATION_TONE: Record<string, BadgeTone> = {
  correct: "positive",
  wrong: "warning",
  neutral: "muted",
  not_evaluable: "outline",
};

/**
 * 复盘结论带：资产齐备度 / 运行异常 / T+1 成效 / 空仓验证。
 *
 * 只做概览，明细留在 `RunOverview` 与成效表格；否决 T+1 取计划日快照组聚合，
 * 回退路径下与逐行表格可能不同集合（以表格为准）。
 */
export function PrepSummaryBand({
  watermark,
  anomalyCount,
  context,
  followup,
  followupLoading,
  followupFailed,
}: {
  watermark: PrepWatermark;
  anomalyCount: number;
  context: ReviewContext | null;
  followup: PlanFollowup | null;
  followupLoading: boolean;
  followupFailed: boolean;
}) {
  const total = watermark.expected_total;
  const missing = Math.max(0, total - watermark.expected_ok);
  const complete = total > 0 && watermark.expected_ok === total;
  const kpis = buildFollowupKpis(followup);
  const verification = context?.plan_effect?.available
    ? context.plan_effect.market.verification
    : null;

  const effectText = (value: number | null, samples: number) => {
    if (followupLoading) return "成效读取中…";
    if (followupFailed) return "成效数据读取失败";
    if (value == null) return "待评估";
    return `${fmtPct(value)}（n=${samples}）`;
  };

  return (
    <div className="panel flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5 text-body">
      <span className="text-caption font-semibold text-ink-2">结论</span>
      <Badge tone={complete ? "positive" : "warning"}>
        {complete ? "资产齐备" : `资产缺 ${missing} 项`}
      </Badge>
      <span className="text-ink-2">
        运行异常 <span className="num text-ink">{anomalyCount}</span>
      </span>
      <span className="text-ink-2">
        推荐 T+1{" "}
        <span className="num text-ink">
          {effectText(kpis.recommendedPct, kpis.recommendedN)}
        </span>
      </span>
      <span className="text-ink-2" title="计划日快照组聚合口径；下方否决股表格逐行为准">
        否决 T+1{" "}
        <span className="num text-ink">
          {effectText(kpis.rejectedPct, kpis.rejectedN)}
        </span>
      </span>
      <span className="text-ink-2">
        T+1 强弱差{" "}
        <span className="num text-ink">
          {followupFailed
            ? "成效数据读取失败"
            : followupLoading
              ? "成效读取中…"
              : kpis.spreadPct == null
                ? "待评估"
                : fmtPct(kpis.spreadPct)}
        </span>
      </span>
      <span className="text-ink-2">
        有效样本 <span className="num text-ink">{kpis.validSamples}</span>
      </span>
      <span className="inline-flex items-center gap-1.5 text-ink-2">
        空仓验证
        {verification ? (
          <Badge tone={VERIFICATION_TONE[verification.conclusion] ?? "outline"}>
            {labelOf(VERIFICATION_CONCLUSION_LABELS, verification.conclusion, "待评估")}
          </Badge>
        ) : (
          <Badge tone="outline">
            {context?.plan_effect && !context.plan_effect.available
              ? "无 T-1 计划"
              : "待评估"}
          </Badge>
        )}
      </span>
    </div>
  );
}
