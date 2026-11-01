import type {
  MarketVerification,
  PlanFollowup,
  PrepSnapshotView,
  ReviewContext,
} from "../../api/assets";
import { Badge, type BadgeTone } from "../../components/Badge";
import { CollapsibleSection } from "../../components/CollapsibleSection";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { fmtNum, pnlClass } from "../../lib/format";
import {
  CHECKPOINT_LABELS,
  EXCLUSION_REASON_LABELS,
  FEEDBACK_STATE_LABELS,
  GROUP_LABELS,
  JUDGEMENT_LABELS,
  MARKET_STATE_LABELS,
  PLAN_ADVICE_LABELS,
  PLANNED_ACTION_LABELS,
  VERIFICATION_CONCLUSION_LABELS,
  VERIFICATION_REASON_LABELS,
  labelOf,
} from "../../lib/labels";
import {
  FOLLOWUP_HORIZONS,
  buildNoTradeOutcomes,
  display,
  positionLabel,
  type CandidateView,
  type NoTradeOutcome,
} from "./prepModel";

type PlanItem = NonNullable<ReviewContext["plan_effect"]["items"][number]>;

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

const VERIFICATION_TONE: Record<string, BadgeTone> = {
  correct: "positive",
  wrong: "warning",
  neutral: "muted",
  not_evaluable: "outline",
};

function fmtScore(value: number | null): string {
  return value == null ? "—" : value.toFixed(1);
}

function fmtRatio(value: number | null): string {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function fmtSigned(value: number | null): string {
  if (value == null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}`;
}

function verificationTitle(verification: MarketVerification): string {
  const evidence = verification.evidence;
  if (verification.conclusion === "not_evaluable") {
    return labelOf(VERIFICATION_REASON_LABELS, verification.reason, "待评估");
  }
  const advice = verification.advice
    ? labelOf(PLAN_ADVICE_LABELS, verification.advice)
    : "前日建议未知";
  const change = evidence.score_change;
  const direction =
    change == null ? "温度不可比" : change > 0 ? "温度上升" : change < 0 ? "温度下降" : "温度持平";
  const state = `市场 ${labelOf(MARKET_STATE_LABELS, evidence.prev_state)} → ${labelOf(
    MARKET_STATE_LABELS,
    evidence.current_state
  )}`;
  const suffix =
    verification.conclusion === "correct"
      ? "与建议方向一致"
      : verification.conclusion === "wrong"
        ? "与建议相反"
        : "未达明确方向";
  return `${advice}，次日${direction} ${change == null ? "" : fmtSigned(change)} · ${state} → ${suffix}`;
}

function VerificationCard({ verification }: { verification: MarketVerification }) {
  const evidence = verification.evidence;
  const tone = VERIFICATION_TONE[verification.conclusion] ?? "outline";
  return (
    <div className="rounded-md border border-hairline px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2 text-body">
        <Badge tone={tone}>
          {labelOf(VERIFICATION_CONCLUSION_LABELS, verification.conclusion, "待评估")}
        </Badge>
        <span className="text-ink">{verificationTitle(verification)}</span>
      </div>
      <div className="mt-1 text-caption text-ink-2">
        依据：温度 {fmtScore(evidence.prev_score)} → {fmtScore(evidence.current_score)} · 涨停{" "}
        {display(evidence.limit_up_cnt)} / 跌停 {display(evidence.limit_down_cnt)} · 炸板率{" "}
        {fmtRatio(evidence.broken_rate)} · 昨日涨停溢价{" "}
        {evidence.yesterday_premium_pct == null
          ? "—"
          : `${fmtSigned(evidence.yesterday_premium_pct)}%`}
      </div>
      <div className="mt-0.5 text-caption text-ink-3">
        口径：仅陈述次日是否按建议方向兑现，不是策略有效性结论。
      </div>
    </div>
  );
}

function buildOutcomeColumns(
  followup: PlanFollowup | null
): Column<NoTradeOutcome>[] {
  return [
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
    key: "judgement",
    header: "次日判定",
    render: (row) => (
      <Badge
        tone={
          row.judgement === "correct_avoidance"
            ? "positive"
            : row.judgement === "missed_opportunity"
              ? "warning"
              : "muted"
        }
      >
        {labelOf(JUDGEMENT_LABELS, row.judgement)}
      </Badge>
    ),
  },
  {
    key: "outcome",
    header: "当日开→收",
    align: "right",
    render: (row) =>
      row.outcomePct == null ? (
        <span className="text-caption text-ink-3">待评估</span>
      ) : (
        <span className={`num text-body ${pnlClass(row.outcomePct)}`}>
          {fmtNum(row.outcomePct, 2)}%
        </span>
      ),
  },
  ...FOLLOWUP_HORIZONS.map<Column<NoTradeOutcome>>((horizon) => ({
    key: `h${horizon}`,
    header: `T+${horizon}`,
    align: "right",
    width: 72,
    render: (row) => {
      const cell = row.cells[horizon];
      if (!cell || cell.pct == null) {
        return (
          <span
            className="text-caption text-ink-3"
            title={cell?.reason ? labelOf(EXCLUSION_REASON_LABELS, cell.reason, cell.reason) : undefined}
          >
            待评估
          </span>
        );
      }
      return (
        <span className={`num text-body ${pnlClass(cell.pct)}`}>
          {fmtNum(cell.pct, 2)}%
        </span>
      );
    },
  })),
  {
    key: "note",
    header: "说明",
    render: (row) => (
      <span className="text-caption text-ink-3">
        {row.sampleMissing
          ? followup
            ? "计划日无该股样本"
            : "成效未读取"
          : "—"}
      </span>
    ),
  },
  ];
}

function NoTradeTable({
  items,
  followup,
}: {
  items: NoTradeOutcome[];
  followup: PlanFollowup | null;
}) {
  if (!items.length) {
    return <div className="text-body text-ink-3">无否决股记录。</div>;
  }
  const horizonCaveat = followup?.caveats.find((item) => item.includes("T+3/T+5"));
  return (
    <div className="grid gap-1.5">
      <div className="overflow-hidden rounded-lg border border-hairline">
        <DataTable
          columns={buildOutcomeColumns(followup)}
          rows={items}
          rowKey={(row) => `${row.code}-${row.judgement}`}
          minWidth={860}
          empty="无否决股记录。"
        />
      </div>
      <div className="flex flex-wrap gap-2 text-caption text-ink-3">
        <span>
          「次日判定」为 T+1 口径；T+1、T+3、T+5 为可观察净收益，二者可能不一致。
        </span>
        {horizonCaveat ? <span>· {horizonCaveat}</span> : null}
      </div>
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

/** 上次计划复盘 / 成效：判定结论只在后端产生，前端只渲染。 */
export function PlanReviewSection({
  context,
  snapshot,
  candidates,
  followup,
}: {
  context: ReviewContext | null;
  snapshot: PrepSnapshotView | null;
  candidates: CandidateView[];
  followup: PlanFollowup | null;
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

  const noTradeOutcomes = buildNoTradeOutcomes(effect.no_trade, followup);

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
        <div className="text-body font-medium text-ink">空仓验证</div>
        <VerificationCard verification={effect.market.verification} />
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
        <div className="text-caption text-ink-3">
          以上均值仅描述计划日当日开→收表现，非策略结论。
        </div>
      </div>

      <div className="grid gap-2">
        <div className="text-body font-medium text-ink">否决股 / 未交易</div>
        <NoTradeTable items={noTradeOutcomes} followup={followup} />
      </div>

      <CollapsibleSection title="次日反馈明细" defaultOpen={false}>
        <FeedbackDetails snapshot={snapshot} candidates={candidates} />
      </CollapsibleSection>
    </div>
  );
}
