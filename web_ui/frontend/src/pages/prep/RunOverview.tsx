import type { PrepSnapshotView, PrepWatermark, ReviewContext } from "../../api/assets";
import { Badge } from "../../components/Badge";
import { CollapsibleSection } from "../../components/CollapsibleSection";
import { EmptyState } from "../../components/EmptyState";
import { StatCard } from "../../components/StatCard";
import { buildAnomalies, type RunInfo } from "./prepModel";

/** 运行概况 + 运行异常（异常折叠在概况下方，不混进成效主表）。 */
export function RunOverview({
  watermark,
  snapshot,
  runInfo,
  context,
}: {
  watermark: PrepWatermark;
  snapshot: PrepSnapshotView | null;
  runInfo: RunInfo | null;
  context: ReviewContext | null;
}) {
  const anomalies = buildAnomalies(watermark, runInfo, context, snapshot);
  const missingN = watermark.assets.filter((row) => row.expected && !row.present).length;
  const degradedN = watermark.assets.filter(
    (row) => row.expected && row.present && !row.asset_complete
  ).length;

  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatCard label="交易日" value={watermark.trade_date} />
        <StatCard
          label="当日应有资产"
          value={`${watermark.expected_ok}/${watermark.expected_total}`}
          tone={
            watermark.expected_total > 0 && watermark.expected_ok === watermark.expected_total
              ? "positive"
              : "warning"
          }
          sub="已达标 / 当日应有"
        />
        <StatCard
          label="缺失资产"
          value={missingN}
          tone={missingN ? "warning" : "neutral"}
          sub={missingN ? "当日应有但缺失" : "无"}
        />
        <StatCard
          label="降级资产"
          value={degradedN}
          tone={degradedN ? "warning" : "neutral"}
          sub={degradedN ? "采集异常或不可用" : "无"}
        />
        <StatCard
          label="复盘快照"
          value={watermark.has_snapshot ? (snapshot?.version ?? "有") : "无"}
          tone={watermark.has_snapshot ? "normal" : "muted"}
          sub={snapshot?.created_at ?? "—"}
        />
        <StatCard
          label="候选 / 详细池"
          value={`${snapshot?.review_candidates?.length ?? 0} / ${runInfo?.dragonCount ?? "—"}`}
          sub={runInfo?.windowDays != null ? `龙头窗口 ${runInfo.windowDays} 日` : "—"}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
        <Badge tone={watermark.first_record_day ? "muted" : "outline"}>
          {watermark.first_record_day ? "首日" : "非首日"}
        </Badge>
        <Badge tone={watermark.has_prior_snapshot ? "normal" : "muted"}>
          {watermark.has_prior_snapshot ? "有前日快照" : "无前日快照"}
        </Badge>
        {snapshot?.plan_review?.plan_date ? (
          <span>上次计划日 {String(snapshot.plan_review.plan_date)}</span>
        ) : (
          <span>无上次计划</span>
        )}
        {runInfo?.macroAvailable != null ? (
          <span>
            宏观 {runInfo.macroAvailable ? "可用" : "不可用"}
            {runInfo.macroSession ? ` · ${runInfo.macroSession}` : ""}
          </span>
        ) : null}
      </div>

      <CollapsibleSection
        title={`运行异常 ${anomalies.length ? `(${anomalies.length})` : ""}`}
        defaultOpen={anomalies.length > 0 && anomalies.length <= 5}
      >
        {anomalies.length === 0 ? (
          <EmptyState title="无运行异常" description="资产完备、快照与复盘上下文均可用。" />
        ) : (
          <ul className="grid gap-1.5">
            {anomalies.map((item, index) => (
              <li key={`${item.text}-${index}`} className="flex items-start gap-2 text-body">
                <Badge tone={item.level === "danger" ? "danger" : "warning"}>
                  {item.level === "danger" ? "异常" : "注意"}
                </Badge>
                <span className="text-ink-2">{item.text}</span>
              </li>
            ))}
          </ul>
        )}
      </CollapsibleSection>
    </div>
  );
}

