import { Badge } from "../../components/Badge";
import { type Column } from "../../components/DataTable";
import { PaginatedDataTable } from "../../components/PaginatedDataTable";
import { fmtNum } from "../../lib/format";
import { MAINLINE_STATE_LABELS, UNKNOWN_REASON_LABELS, labelOf } from "../../lib/labels";
import { display, type MainlineRow } from "./prepModel";

function deltaText(value: number | null, unit = ""): string {
  if (value == null) return "—";
  if (value === 0) return `0${unit}`;
  return `${value > 0 ? "+" : ""}${value}${unit}`;
}

const COLUMNS: Column<MainlineRow>[] = [
  {
    key: "rank",
    header: "排名",
    align: "right",
    render: (row) => <span className="num text-ink-2">{display(row.rank)}</span>,
  },
  {
    key: "name",
    header: "板块",
    render: (row) => <span className="font-medium text-ink">{display(row.name)}</span>,
  },
  {
    key: "state",
    header: "状态",
    render: (row) => (
      <Badge tone={row.state === "strengthening" ? "positive" : row.state === "weakening" ? "warning" : "outline"}>
        {row.state ? labelOf(MAINLINE_STATE_LABELS, row.state) : "未评估"}
      </Badge>
    ),
  },
  {
    key: "score",
    header: "评分",
    align: "right",
    render: (row) => <span className="num text-ink">{fmtNum(row.score, 1)}</span>,
  },
  {
    key: "limitUp",
    header: "涨停",
    align: "right",
    render: (row) => <span className="num text-ink-2">{display(row.limitUpNum)}</span>,
  },
  {
    key: "high",
    header: "最高板",
    align: "right",
    render: (row) => (
      <span className="inline-flex items-center gap-1.5">
        <span className="num text-ink">{display(row.highBoard)}</span>
        {row.highNeedsRecompute ? <Badge tone="warning">需重算</Badge> : null}
      </span>
    ),
  },
  {
    key: "continuous",
    header: "连续在榜",
    align: "right",
    render: (row) => <span className="num text-ink-2">{display(row.continuousPlateNum)}</span>,
  },
  {
    key: "concentration",
    header: "集中度",
    align: "right",
    render: (row) => (
      <span className="num text-ink-2">
        {row.concentration == null ? "—" : `${(row.concentration * 100).toFixed(1)}%`}
      </span>
    ),
  },
  {
    key: "rankDelta",
    header: "排名变化",
    align: "right",
    render: (row) => (
      // 中性箭头：排名上升不是「上涨」，不使用 A 股红绿。
      <span className="num text-ink-2">
        {row.rankDelta == null ? "—" : `${row.rankDelta > 0 ? "↑" : row.rankDelta < 0 ? "↓" : "→"} ${deltaText(row.rankDelta)}`}
      </span>
    ),
  },
  {
    key: "scoreDelta",
    header: "分数变化",
    align: "right",
    render: (row) => (
      <span className="num text-ink-2">
        {row.scoreDelta == null ? "—" : deltaText(Math.round(row.scoreDelta * 10) / 10)}
      </span>
    ),
  },
  {
    key: "reason",
    header: "未知原因",
    render: (row) => (
      <span className="text-caption text-ink-3">
        {row.unknownReason ? labelOf(UNKNOWN_REASON_LABELS, row.unknownReason) : "—"}
      </span>
    ),
  },
];

export function MainlineTable({
  rows,
  resetKey,
}: {
  rows: MainlineRow[];
  resetKey?: string | number | null;
}) {
  if (rows.length === 0) {
    return <div className="text-body text-ink-3">该日无主线数据。</div>;
  }
  return (
    <div className="overflow-hidden rounded-lg border border-hairline">
      <PaginatedDataTable
        columns={COLUMNS}
        rows={rows}
        rowKey={(row) => `${row.name ?? "mainline"}-${row.index}`}
        resetKey={resetKey}
        minWidth={980}
      />
    </div>
  );
}
