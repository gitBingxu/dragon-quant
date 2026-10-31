import type { AuctionDayView } from "../../api/assets";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { StatCard, type StatTone } from "../../components/StatCard";
import { fmtNum } from "../../lib/format";

type AuctionRow = AuctionDayView["snapshots"][number];

// 强度档不是收益语义：使用品牌紫→中性灰的强度梯度，避免与红涨/绿跌混淆。
const SUMMARY_META: Record<string, { label: string; color: string; tone: StatTone }> = {
  strong: { label: "强", color: "var(--color-brand-bright)", tone: "brand" },
  medium: { label: "中", color: "var(--color-brand)", tone: "brand" },
  weak: { label: "弱", color: "var(--color-ink-3)", tone: "muted" },
  vetoed: { label: "否决", color: "var(--color-warn)", tone: "warn" },
  unavailable: {
    label: "不可用",
    color: "color-mix(in oklab, var(--color-ink-3) 50%, transparent)",
    tone: "muted",
  },
  unknown: {
    label: "未知",
    color: "color-mix(in oklab, var(--color-ink-3) 30%, transparent)",
    tone: "muted",
  },
};

const COLUMNS: Column<AuctionRow>[] = [
  {
    key: "code",
    header: "代码",
    render: (row) => <span className="font-mono text-[12.5px] text-ink">{row.code}</span>,
  },
  {
    key: "rank",
    header: "rank",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.rank ?? "—"}</span>,
  },
  {
    key: "level",
    header: "强度档",
    render: (row) => {
      if (!row.level) return <span className="text-ink-3">—</span>;
      const tone =
        row.level === "强" ? "brand" : row.level === "中" ? "info" : "neutral";
      return <Badge tone={tone}>{row.level}</Badge>;
    },
  },
  {
    key: "strength",
    header: "strength",
    align: "right",
    render: (row) => <span className="num text-ink">{fmtNum(row.strength)}</span>,
  },
  {
    key: "direction",
    header: "方向",
    render: (row) => <span className="text-[12px] text-ink-2">{row.direction ?? "—"}</span>,
  },
  {
    key: "status",
    header: "校验",
    render: (row) => <span className="text-[12px] text-ink-2">{row.validation_status ?? "—"}</span>,
  },
  {
    key: "reject",
    header: "否决 / 弱",
    render: (row) => (
      <span className={`text-[12px] ${row.reject_reason ? "text-warn" : "text-ink-3"}`}>
        {row.reject_reason ?? (row.is_weak ? "弱" : "—")}
      </span>
    ),
  },
];

export function AuctionSection({ auction }: { auction: AuctionDayView }) {
  const entries = Object.entries(auction.summary).filter(([, value]) => value > 0);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);

  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {Object.entries(SUMMARY_META).map(([key, meta]) => (
          <StatCard
            key={key}
            label={`${meta.label}档`}
            value={auction.summary[key] ?? 0}
            tone={meta.tone}
          />
        ))}
      </div>

      {total > 0 ? (
        <div className="rounded-lg border border-hairline bg-raise px-3.5 py-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-ink-2">
              竞价强度分布
            </span>
            <span className="font-mono text-[10.5px] text-ink-3">{total} 只</span>
          </div>
          <div className="flex h-2.5 w-full gap-[2px] overflow-hidden rounded-full">
            {entries.map(([key, value]) => (
              <div
                key={key}
                title={`${SUMMARY_META[key]?.label ?? key} ${value}`}
                style={{
                  width: `${(value / total) * 100}%`,
                  background: SUMMARY_META[key]?.color ?? "var(--color-ink-3)",
                }}
              />
            ))}
          </div>
          <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1.5">
            {entries.map(([key, value]) => (
              <span key={key} className="inline-flex items-center gap-1.5 text-[11.5px] text-ink-2">
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ background: SUMMARY_META[key]?.color ?? "var(--color-ink-3)" }}
                />
                {SUMMARY_META[key]?.label ?? key}
                <span className="num text-ink">{value}</span>
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <div className="overflow-hidden rounded-lg border border-hairline">
        <DataTable
          columns={COLUMNS}
          rows={auction.snapshots}
          rowKey={(row) => row.code}
          minWidth={820}
          empty="该日无竞价快照。"
        />
      </div>
    </div>
  );
}
