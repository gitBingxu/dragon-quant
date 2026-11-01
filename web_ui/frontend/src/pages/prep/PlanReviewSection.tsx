import type { PrepSnapshotView, ReviewContext } from "../../api/assets";
import { Badge } from "../../components/Badge";
import { CollapsibleSection } from "../../components/CollapsibleSection";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { fmtNum, pnlClass } from "../../lib/format";
import {
  CHECKPOINT_LABELS,
  FEEDBACK_STATE_LABELS,
  GROUP_LABELS,
  JUDGEMENT_LABELS,
  MARKET_STATE_LABELS,
  PLANNED_ACTION_LABELS,
  labelOf,
} from "../../lib/labels";
import { display, positionLabel, type CandidateView } from "./prepModel";

type PlanItem = NonNullable<ReviewContext["plan_effect"]["items"][number]>;
type NoTrade = ReviewContext["plan_effect"]["no_trade"][number];

const ITEM_COLUMNS: Column<PlanItem>[] = [
  {
    key: "code",
    header: "代码",
    render: (row) => <span className="font-mono text-body text-ink">{row.code}</span>,
  },
  {
    key: "name",
    header: "名称",
    render: (row) => <span className="text-body text-ink">{row.name ?? "—"}</span>,
  },
  {
    key: "action",
    header: "计划",
    render: (row) => (
      <span className="text-body text-ink-2">
        {row.planned_action ? labelOf(PLANNED_ACTION_LABELS, row.planned_action) : "—"}
      </span>
    ),
  },
  {
    key: "position",
    header: "计划仓位",
    align: "right",
    render: (row) => (
      <span className="num text-body text-ink-2">{positionLabel(row.planned_position)}</span>
    ),
  },
  {
    key: "feedback",
    header: "次日反馈",
    render: (row) => (
      <Badge
        tone={
          row.feedback_state === "confirmed"
            ? "positive"
            : row.feedback_state === "invalidated"
              ? "warning"
              : "muted"
        }
      >
        {labelOf(FEEDBACK_STATE_LABELS, row.feedback_state, "待评估")}
      </Badge>
    ),
  },
  {
    key: "actual",
    header: "实际仓位",
    align: "right",
    render: (row) =>
      row.execution_known ? (
        <span className="num text-body text-ink-2">{display(row.actual_position)}</span>
      ) : (
        <span className="text-caption text-ink-3">实际成交未知</span>
      ),
  },
  {
    key: "outcome",
    header: "当日开→收",
    align: "right",
    render: (row) =>
      row.outcome_pct == null ? (
        <span className="text-caption text-ink-3">待评估</span>
      ) : (
        <span className={`num text-body ${pnlClass(row.outcome_pct)}`}>
          {fmtNum(row.outcome_pct, 2)}%
        </span>
      ),
  },
  {
    key: "reason",
    header: "未知原因",
    render: (row) => (
      <span className="text-caption text-ink-3">{row.unknown_reason ?? "—"}</span>
    ),
  },
];

function MarketCompare({ context }: { context: ReviewContext }) {
  const market = context.plan_effect.market;
  const rows = [
    { key: "trade_date", label: "快照日", prev: market.prev.trade_date, current: market.current.trade_date },
    { key: "state", label: "状态", prev: market.prev.state, current: market.current.state },
    { key: "score", label: "评分", prev: market.prev.score, current: market.current.score },
    { key: "coeff", label: "系数", prev: market.prev.coeff, current: market.current.coeff },
    {
      key: "advice",
      label: "仓位建议",
      prev: market.prev.position_advice,
      current: market.current.position_advice,
    },
    {
      key: "position",
      label: "参考仓位",
      prev:
        market.prev.position_pct == null
          ? null
          : `${Math.round(market.prev.position_pct * 1000) / 10}%`,
      current:
        market.current.position_pct == null
          ? null
          : `${Math.round(market.current.position_pct * 1000) / 10}%`,
    },
  ] as const;

  return (
    <div className="overflow-x-auto">
      <table className="dt" style={{ minWidth: 520 }}>
        <thead>
          <tr>
            <th>项</th>
            <th>前日（{context.prev_trade_date ?? "无前日快照"}）</th>
            <th>今日（{context.trade_date}）</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td className="text-body text-ink-2">{row.label}</td>
              <td className="text-body text-ink">
                {row.key === "state"
                  ? labelOf(MARKET_STATE_LABELS, row.prev as string | null)
                  : display(row.prev as string | number | null)}
              </td>
              <td className="text-body text-ink">
                {row.key === "state"
                  ? labelOf(MARKET_STATE_LABELS, row.current as string | null)
                  : display(row.current as string | number | null)}
              </td>
            </tr>
          ))}
          <tr>
            <td className="text-body text-ink-2">分数变化</td>
            <td colSpan={2} className="num text-body text-ink">
              {market.score_change == null ? "—" : market.score_change.toFixed(1)}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function NoTradeList({ items }: { items: NoTrade[] }) {
  if (!items.length) {
    return <div className="text-body text-ink-3">无否决股记录。</div>;
  }
  return (
    <div className="grid gap-1.5">
      {items.map((item) => (
        <div key={`${item.code}-${item.judgement}`} className="flex flex-wrap items-center gap-2 text-body">
          <Badge
            tone={
              item.judgement === "correct_avoidance"
                ? "positive"
                : item.judgement === "missed_opportunity"
                  ? "warning"
                  : "muted"
            }
          >
            {labelOf(JUDGEMENT_LABELS, item.judgement)}
          </Badge>
          <span className="font-mono text-ink-3">{item.code}</span>
          <span className="text-ink">{item.name ?? "—"}</span>
          <span className="text-ink-2">
            当日开→收{" "}
            {item.outcome_pct == null ? (
              <span className="text-ink-3">待评估</span>
            ) : (
              <span className={pnlClass(item.outcome_pct)}>{fmtNum(item.outcome_pct, 2)}%</span>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

function FeedbackDetails({ snapshot, candidates }: { snapshot: PrepSnapshotView | null; candidates: CandidateView[] }) {
  const feedback = snapshot?.feedback as Record<string, unknown> | null | undefined;
  const items = Array.isArray(feedback?.items) ? (feedback.items as Record<string, unknown>[]) : [];
  if (!feedback) {
    return <div className="text-body text-ink-3">该日没有生成次日反馈。</div>;
  }
  if (items.length === 0) {
    return <div className="text-body text-ink-3">反馈已生成，但无逐股条目。</div>;
  }
  const names = new Map(candidates.map((item) => [item.code, item.name]));
  return (
    <div className="grid gap-1.5">
      {items.map((item, index) => {
        const code = typeof item.code === "string" ? item.code : `row-${index}`;
        const state = typeof item.state === "string" ? item.state : null;
        return (
          <div key={`${code}-${index}`} className="rounded-md border border-hairline px-3 py-2 text-body">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-ink-3">{code}</span>
              <span className="text-ink">{names.get(code) ?? "—"}</span>
              <Badge tone="outline">{labelOf(FEEDBACK_STATE_LABELS, state, "待评估")}</Badge>
              <span className="text-ink-2">缺口 {display(item.gap_pct as number | null)}%</span>
              <span className="text-ink-2">竞价量比 {display(item.auction_ratio as number | null)}</span>
              <span className="text-ink-2">收盘 {display(item.close_pct as number | null)}%</span>
              {item.unknown_reason ? (
                <span className="text-caption text-ink-3">{String(item.unknown_reason)}</span>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** 上次计划复盘 / 成效：只陈述事实，不新增「空仓正确」阈值。 */
export function PlanReviewSection({
  context,
  snapshot,
  candidates,
}: {
  context: ReviewContext | null;
  snapshot: PrepSnapshotView | null;
  candidates: CandidateView[];
}) {
  const effect = context?.plan_effect;
  if (!context) {
    return <div className="text-body text-ink-3">该日无复盘上下文。</div>;
  }
  if (!effect || !effect.available) {
    return (
      <EmptyState
        title="首日无计划，不适用"
        description="没有 T-1 计划就无从复盘；数据缺失一律待评估，不补算。"
      />
    );
  }

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
        <Badge tone="outline">
          计划日 {effect.plan_date ?? "—"} → 评估日 {effect.eval_date ?? context.trade_date}
        </Badge>
        {effect.checkpoint ? (
          <Badge tone="muted">{labelOf(CHECKPOINT_LABELS, effect.checkpoint)}</Badge>
        ) : null}
        <span>「当日开盘到收盘」为计划日当日表现，不是次日收益。</span>
      </div>

      <div className="grid gap-2">
        <div className="text-body font-medium text-ink">前日市场建议 vs 今日实际</div>
        <MarketCompare context={context} />
      </div>

      <div className="grid gap-2">
        <div className="text-body font-medium text-ink">计划执行</div>
        <div className="overflow-hidden rounded-lg border border-hairline">
          <DataTable
            columns={ITEM_COLUMNS}
            rows={effect.items}
            rowKey={(row) => row.code}
            minWidth={980}
            empty="该日无计划条目。"
          />
        </div>
      </div>

      <div className="grid gap-2">
        <div className="text-body font-medium text-ink">推荐对照（只列样本数与平均涨跌）</div>
        <div className="flex flex-wrap gap-3">
          {effect.groups.map((group) => (
            <div key={group.key} className="rounded-md border border-hairline px-3 py-2 text-body">
              <div className="text-ink-2">{labelOf(GROUP_LABELS, group.key)}</div>
              <div className="num text-ink">
                {group.count} 只 · 有结果 {group.with_outcome}
              </div>
              <div className="text-ink-2">
                均值 {group.mean_outcome_pct == null ? "待评估" : `${fmtNum(group.mean_outcome_pct, 2)}%`}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="grid gap-2">
        <div className="text-body font-medium text-ink">否决股 / 未交易</div>
        <NoTradeList items={effect.no_trade} />
      </div>

      <CollapsibleSection title="次日反馈明细" defaultOpen={false}>
        <FeedbackDetails snapshot={snapshot} candidates={candidates} />
      </CollapsibleSection>
    </div>
  );
}
