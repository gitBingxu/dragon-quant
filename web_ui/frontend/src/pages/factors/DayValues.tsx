import type { FactorDayView, FactorValueRow } from "../../api/factors";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { StatCard } from "../../components/StatCard";
import { fmtNum } from "../../lib/format";

const MAX_ROWS = 200;

const VALUE_COLUMNS: Column<FactorValueRow>[] = [
  {
    key: "code",
    header: "代码",
    render: (row) => <span className="font-mono text-[12.5px] text-ink">{row.code}</span>,
  },
  {
    key: "factor",
    header: "因子",
    render: (row) => <span className="font-mono text-[12px] text-ink-2">{row.factor_name}</span>,
  },
  {
    key: "value",
    header: "值",
    align: "right",
    render: (row) => <span className="num text-ink">{fmtNum(row.value, 4)}</span>,
  },
  {
    key: "available",
    header: "可用时点",
    render: (row) => <span className="num text-[11.5px] text-ink-2">{row.available_at || "—"}</span>,
  },
  {
    key: "scan",
    header: "血缘 scan",
    render: (row) => (
      <span className="font-mono text-[11.5px] text-ink-3">{row.source_scan_id ?? "—"}</span>
    ),
  },
];

export function DayValues({ day }: { day: FactorDayView }) {
  const rows = day.rows.slice(0, MAX_ROWS);
  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <StatCard label="因子行数" value={day.rows.length} sub={`展示前 ${MAX_ROWS} 行`} />
        <StatCard label="build_id" value={day.build_id ?? "—"} tone="brand" />
        <StatCard
          label="缺失版本"
          value={day.missing_versions.length}
          tone={day.missing_versions.length ? "warn" : "neutral"}
          sub={day.missing_versions.join(", ") || "无缺失"}
        />
      </div>

      <div className="rounded-lg border border-hairline bg-white/[0.015] px-3.5 py-3">
        <div className="mb-2 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-ink-2">
          resolved_versions
        </div>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(day.resolved_versions).length === 0 ? (
            <span className="text-[12px] text-ink-3">—</span>
          ) : (
            Object.entries(day.resolved_versions).map(([name, version]) => (
              <Badge key={name} tone="outline">
                <span className="font-mono">{name}</span>
                <span className="font-mono text-ink-3">v{version}</span>
              </Badge>
            ))
          )}
        </div>
        <div className="mt-2 font-mono text-[10.5px] text-ink-3">
          coverage 来源：{day.coverage_source}
        </div>
      </div>

      {day.coverage ? (
        <div className="overflow-hidden rounded-lg border border-hairline">
          <table className="dt" style={{ minWidth: 520 }}>
            <thead>
              <tr>
                <th>因子</th>
                <th className="text-right">coverage</th>
                <th className="text-right">rows</th>
                <th>status</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(day.coverage).map(([name, cov]) => (
                <tr key={name}>
                  <td className="font-mono text-[12px] text-ink">{name}</td>
                  <td className="text-right num text-ink-2">{fmtNum(cov.coverage, 3)}</td>
                  <td className="text-right num text-ink-2">{cov.rows ?? "—"}</td>
                  <td className="text-[12px] text-ink-2">{cov.status ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="overflow-hidden rounded-lg border border-hairline">
        <DataTable
          columns={VALUE_COLUMNS}
          rows={rows}
          rowKey={(row, index) => `${row.code}-${row.factor_name}-${index}`}
          minWidth={640}
          empty="该日无因子值（缺失 = 无行）。"
        />
      </div>
    </div>
  );
}
