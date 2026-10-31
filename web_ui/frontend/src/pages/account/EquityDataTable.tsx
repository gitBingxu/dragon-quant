import type { AccountBenchmarkPoint, AccountSnapshot } from "../../api/account";
import { DataTable, type Column } from "../../components/DataTable";
import { fmtMoney, fmtPct, pnlClass } from "../../lib/format";

interface Row {
  snapshot: AccountSnapshot;
  benchmark: AccountBenchmarkPoint | null;
}

const COLUMNS: Column<Row>[] = [
  {
    key: "date",
    header: "交易日",
    render: (row) => <span className="num text-ink">{row.snapshot.trade_date}</span>,
  },
  {
    key: "equity",
    header: "总权益",
    align: "right",
    render: (row) => <span className="num text-ink">{fmtMoney(row.snapshot.total_equity)}</span>,
  },
  {
    key: "daily",
    header: "当日收益",
    align: "right",
    render: (row) => (
      <span className={`num ${pnlClass(row.snapshot.daily_return)}`}>
        {fmtPct(row.snapshot.daily_return)}
      </span>
    ),
  },
  {
    key: "return",
    header: "累计收益",
    align: "right",
    render: (row) => (
      <span className={`num ${pnlClass(row.snapshot.cumulative_return)}`}>
        {fmtPct(row.snapshot.cumulative_return)}
      </span>
    ),
  },
  {
    key: "drawdown",
    header: "回撤",
    align: "right",
    render: (row) => (
      <span className="num text-down">{fmtPct(row.snapshot.drawdown)}</span>
    ),
  },
  {
    key: "benchmark",
    header: "上证同期",
    align: "right",
    render: (row) => (
      <span className="num text-ink-2">{fmtPct(row.benchmark?.cumulative_return ?? null)}</span>
    ),
  },
];

export function EquityDataTable({
  data,
  benchmark,
}: {
  data: AccountSnapshot[];
  benchmark: AccountBenchmarkPoint[];
}) {
  const byDate = new Map(benchmark.map((point) => [point.trade_date, point]));
  const rows: Row[] = data.map((snapshot) => ({
    snapshot,
    benchmark: byDate.get(snapshot.trade_date) ?? null,
  }));
  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-hairline">
      <DataTable
        columns={COLUMNS}
        rows={rows}
        rowKey={(row) => row.snapshot.trade_date}
        maxHeight={304}
        minWidth={560}
        empty="暂无快照数据。"
      />
    </div>
  );
}
