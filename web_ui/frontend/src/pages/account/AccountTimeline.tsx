import { useMemo, useState } from "react";
import type { AccountPosition, AccountTimelineEvent } from "../../api/legacy";
import { Badge } from "../../components/Badge";
import { EmptyState } from "../../components/EmptyState";
import { Pagination } from "../../components/Pagination";
import { Section } from "../../components/Section";
import { fmtMoney, fmtPct, pnlClass } from "../../lib/format";
import { usePagination } from "../../lib/pagination";

const EVENT_META: Record<
  AccountTimelineEvent["event_type"],
  { label: string; color: string; tone: "up" | "down" | "info" | "neutral" }
> = {
  BUY: { label: "买入", color: "var(--color-up)", tone: "up" },
  SELL: { label: "卖出", color: "var(--color-down)", tone: "down" },
  HOLD: { label: "持仓", color: "var(--color-info)", tone: "info" },
  IDLE: { label: "空仓", color: "var(--color-ink-3)", tone: "neutral" },
};

export function AccountTimeline({
  events,
  positions,
}: {
  events: AccountTimelineEvent[];
  positions: AccountPosition[];
}) {
  const [pageSize] = useState(10);
  const pager = usePagination(events, pageSize);
  const closedByEntry = useMemo(() => {
    const map = new Map<string, AccountPosition>();
    for (const position of positions) {
      map.set(`${position.entry_date}-${position.code}`, position);
    }
    return map;
  }, [positions]);

  return (
    <Section
      title="账户决策时间线"
      description="每日买入、卖出、继续持有与空仓原因"
      right={<Badge tone="outline">{events.length} 条</Badge>}
      bodyClassName="px-4 py-3.5 sm:px-[18px]"
    >
      {events.length === 0 ? (
        <EmptyState
          title="暂无账户时间线"
          description="重新运行一次 review-account 后可展示空仓原因。"
        />
      ) : (
        <div className="grid gap-2.5">
          {pager.pageItems.map((event, index) => (
            <TimelineItem
              key={`${event.event_date}-${event.event_type}-${event.code}-${index}`}
              event={event}
              position={
                event.event_type === "BUY"
                  ? closedByEntry.get(`${event.event_date}-${event.code}`)
                  : undefined
              }
            />
          ))}
        </div>
      )}
      <Pagination
        page={pager.page}
        pageCount={pager.pageCount}
        total={pager.total}
        pageSize={pageSize}
        onPageChange={pager.setPage}
      />
    </Section>
  );
}

function TimelineItem({
  event,
  position,
}: {
  event: AccountTimelineEvent;
  position?: AccountPosition;
}) {
  const meta = EVENT_META[event.event_type];
  const isIdle = event.event_type === "IDLE";
  const details = Array.isArray(event.signal?.details)
    ? (event.signal.details as Array<Record<string, unknown>>)
    : [];
  return (
    <div className="grid grid-cols-[86px_14px_minmax(0,1fr)] items-start gap-3">
      <span className="num pt-1 text-[11.5px] text-ink-3">{event.event_date}</span>
      <span className="relative flex justify-center pt-1.5">
        <span
          className="h-2.5 w-2.5 rounded-full"
          style={{ background: meta.color, boxShadow: "0 0 0 4px rgba(255,255,255,0.06)" }}
        />
      </span>
      <div
        className={`rounded-lg border px-3.5 py-2.5 ${
          isIdle ? "border-hairline bg-white/[0.012]" : "border-hairline bg-panel-2/60"
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Badge tone={meta.tone}>{meta.label}</Badge>
            <span className={`text-[13px] font-medium ${isIdle ? "text-ink-2" : "text-ink"}`}>
              {event.title || meta.label}
            </span>
            {event.code ? (
              <span className="font-mono text-[11px] text-ink-3">{event.code}</span>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-3 font-mono text-[10.5px] text-ink-3">
            {event.cash != null ? <span>现金 {fmtMoney(event.cash)}</span> : null}
            {event.total_equity != null ? <span>权益 {fmtMoney(event.total_equity)}</span> : null}
          </div>
        </div>
        <p className={`mt-1.5 text-[12px] leading-relaxed ${isIdle ? "text-ink-2" : "text-ink-2"}`}>
          {event.detail || event.reason_code}
        </p>
        {details.length > 0 ? (
          <div className="mt-2 grid gap-1">
            {details.slice(0, 5).map((detail, index) => (
              <div key={index} className="flex items-start gap-2">
                <Badge tone={detail.passed ? "brand" : "neutral"}>
                  {typeof detail.rank === "number" ? `#${detail.rank}` : "—"}
                </Badge>
                <span className={`text-[11.5px] ${detail.passed ? "text-ink-2" : "text-ink-3"}`}>
                  {String(detail.reason_text || detail.reason_code || "")}
                </span>
              </div>
            ))}
          </div>
        ) : null}
        {position ? (
          <div className="mt-2 flex flex-wrap items-center gap-3 font-mono text-[10.5px] text-ink-3">
            <span>卖出日 {position.exit_date}</span>
            <span className={pnlClass(position.realized_return)}>
              闭环收益 {fmtPct(position.realized_return)}
            </span>
            <span>持有 {position.hold_days} 天</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
