/**
 * 复盘核心结果（五区块第 2 块）：
 * 市场情绪 + 主线 + 是否建仓 + 推荐建仓标的 + 资产完备/异常摘要。
 *
 * 推荐集只读 `review_candidates` 中 `eligibility === "eligible"` 且
 * `position_pct > 0` 的标的（见 `buildRecommendedCandidates`），不从旧
 * `candidates[]` 兼容投影取信号；空值一律「数据不足」，不猜方向。
 */
import type { PrepSnapshotView, PrepWatermark } from "../../api/assets";
import { Badge, type BadgeTone } from "../../components/Badge";
import { type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { PaginatedDataTable } from "../../components/PaginatedDataTable";
import { fmtNum } from "../../lib/format";
import { UNKNOWN_REASON_LABELS, labelOf } from "../../lib/labels";
import { MainlineTable } from "./MainlineTable";
import { MarketStrip } from "./MarketStrip";
import {
  buildAnomalies,
  buildMainlineRows,
  buildMarketView,
  buildRecommendedCandidates,
  buildRunInfo,
  display,
  positionLabel,
  roleLabel,
  type RecommendedCandidate,
} from "./prepModel";

const RECOMMENDED_COLUMNS: Column<RecommendedCandidate>[] = [
  {
    key: "code",
    header: "代码",
    render: (row) => <span className="font-mono text-body text-ink">{row.code}</span>,
  },
  {
    key: "name",
    header: "名称",
    render: (row) => <span className="text-body text-ink">{display(row.name)}</span>,
  },
  {
    key: "role",
    header: "角色",
    render: (row) => (
      <span className="text-body text-ink-2">{roleLabel(row.role)}</span>
    ),
  },
  {
    key: "score",
    header: "评分",
    align: "right",
    render: (row) => <span className="num text-body text-ink">{fmtNum(row.score, 1)}</span>,
  },
  {
    key: "position",
    header: "建议仓位",
    align: "right",
    render: (row) => (
      <span className="num text-body text-ink">{positionLabel(row.positionPct)}</span>
    ),
  },
  {
    key: "mainline",
    header: "主线",
    render: (row) => <span className="text-body text-ink-2">{display(row.mainline)}</span>,
  },
];

function buildTone(positionPct: number | null): BadgeTone {
  if (positionPct == null) return "muted";
  return positionPct > 0 ? "positive" : "warning";
}

function buildLabel(positionPct: number | null): string {
  if (positionPct == null) return "数据不足";
  return positionPct > 0 ? "建议建仓" : "不建议建仓";
}

export function PrepCoreResult({
  snapshot,
  watermark,
}: {
  snapshot: PrepSnapshotView | null;
  watermark: PrepWatermark | null;
}) {
  const market = snapshot ? buildMarketView(snapshot) : null;
  const mainlines = snapshot ? buildMainlineRows(snapshot, null) : [];
  const recommended = snapshot ? buildRecommendedCandidates(snapshot) : [];
  const runInfo = snapshot ? buildRunInfo(snapshot) : null;
  const anomalies = watermark
    ? buildAnomalies(watermark, runInfo, null, snapshot)
    : [];
  const positionPct = market?.positionPct ?? null;
  const missingN = watermark
    ? Math.max(0, watermark.expected_total - watermark.expected_ok)
    : null;

  return (
    <div className="grid gap-4">
      <div className="grid gap-2">
        <div className="text-body font-medium text-ink">市场情绪</div>
        {snapshot ? (
          <MarketStrip market={market} />
        ) : (
          <div className="text-body text-ink-3">该日无复盘快照。</div>
        )}
      </div>

      <div className="grid gap-2">
        <div className="text-body font-medium text-ink">主线</div>
        {snapshot ? (
          <MainlineTable rows={mainlines} resetKey={snapshot.trade_date} />
        ) : (
          <div className="text-body text-ink-3">该日无复盘快照。</div>
        )}
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div className="grid content-start gap-2 rounded-md border border-hairline px-3 py-2.5">
          <div className="text-body font-medium text-ink">是否建仓</div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={buildTone(positionPct)}>{buildLabel(positionPct)}</Badge>
            <span className="text-body text-ink-2">
              仓位建议{" "}
              <span className="text-ink">{market?.positionAdvice ?? "—"}</span>
            </span>
          </div>
          <div className="text-body text-ink-2">
            建议仓位{" "}
            <span className="num text-ink">{positionLabel(positionPct)}</span>
          </div>
          {market?.unknownReason ? (
            <div className="text-caption text-ink-3">
              原因：
              {labelOf(UNKNOWN_REASON_LABELS, market.unknownReason, market.unknownReason)}
            </div>
          ) : null}
        </div>

        <div className="grid content-start gap-2">
          <div className="flex items-center gap-2">
            <div className="text-body font-medium text-ink">推荐建仓标的</div>
            <Badge tone="outline">{recommended.length} 只</Badge>
          </div>
          {snapshot ? (
            <div className="overflow-hidden rounded-lg border border-hairline">
              <PaginatedDataTable
                columns={RECOMMENDED_COLUMNS}
                rows={recommended}
                rowKey={(row) => row.code}
                resetKey={snapshot.trade_date}
                minWidth={640}
                empty="无推荐建仓标的（空仓、无合格候选或信号缺失）。"
              />
            </div>
          ) : (
            <EmptyState
              title="该日无复盘快照"
              description="无快照则不推断推荐标的；缺数据一律待评估。"
            />
          )}
        </div>
      </div>

      <div className="grid gap-2 rounded-md border border-hairline px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-body font-medium text-ink">资产完备与异常摘要</div>
          {watermark ? (
            <Badge
              tone={
                watermark.expected_total > 0 &&
                watermark.expected_ok === watermark.expected_total
                  ? "positive"
                  : "warning"
              }
            >
              完备 {watermark.expected_ok}/{watermark.expected_total}
            </Badge>
          ) : (
            <Badge tone="muted">该日无资产记录</Badge>
          )}
          <Badge tone={anomalies.length ? "warning" : "muted"}>
            异常 {anomalies.length} 项
          </Badge>
        </div>
        {watermark ? (
          <div className="text-caption text-ink-2">
            缺失资产 {missingN} 项；详细缺席原因见下方「当日资产明细」。
          </div>
        ) : null}
        {anomalies.length ? (
          <ul className="grid gap-1">
            {anomalies.slice(0, 3).map((item, index) => (
              <li
                key={`${item.text}-${index}`}
                className="flex items-start gap-2 text-body text-ink-2"
              >
                <Badge tone={item.level === "danger" ? "danger" : "warning"}>
                  {item.level === "danger" ? "异常" : "注意"}
                </Badge>
                <span>{item.text}</span>
              </li>
            ))}
            {anomalies.length > 3 ? (
              <li className="text-caption text-ink-3">
                另有 {anomalies.length - 3} 项，见下方明细。
              </li>
            ) : null}
          </ul>
        ) : (
          <div className="text-body text-ink-3">无运行异常。</div>
        )}
      </div>
    </div>
  );
}
