import type { FactorRun } from "../../api/factors";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { fmtNum } from "../../lib/format";

const COLUMNS: Column<FactorRun>[] = [
  {
    key: "id",
    header: "id",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.id}</span>,
  },
  {
    key: "date",
    header: "交易日",
    render: (row) => <span className="num text-ink">{row.trade_date}</span>,
  },
  {
    key: "ok",
    header: "结果",
    render: (row) =>
      row.ok == null ? (
        <span className="text-ink-3">—</span>
      ) : (
        <Badge tone={row.ok ? "brand" : "critical"}>{row.ok ? "成功" : "失败"}</Badge>
      ),
  },
  {
    key: "factors",
    header: "因子数",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.factors?.length ?? 0}</span>,
  },
  {
    key: "coverage",
    header: "覆盖率",
    align: "right",
    render: (row) => {
      const values = (row.factors ?? [])
        .map((item) => item.coverage)
        .filter((value): value is number => typeof value === "number");
      const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
      return <span className="num text-ink-2">{avg == null ? "—" : fmtNum(avg, 3)}</span>;
    },
  },
  {
    key: "warnings",
    header: "warnings",
    render: (row) => (
      <span className="text-[12px] text-ink-2" title={row.warnings.join("; ") || undefined}>
        {row.warnings.length ? `${row.warnings.length} 条` : "—"}
      </span>
    ),
  },
  {
    key: "created",
    header: "创建时间",
    render: (row) => <span className="num text-[11.5px] text-ink-3">{row.created_at}</span>,
  },
];

export function BuildAudit({ runs }: { runs: FactorRun[] }) {
  return (
    <DataTable
      columns={COLUMNS}
      rows={runs}
      rowKey={(row) => String(row.id)}
      minWidth={760}
      empty="该日无 build run。"
    />
  );
}
