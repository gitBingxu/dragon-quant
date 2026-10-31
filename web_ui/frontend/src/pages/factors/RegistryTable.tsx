import type { FactorSpecView } from "../../api/factors";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";

function buildColumns(selected: string): Column<FactorSpecView>[] {
  return [
  {
    key: "name",
    header: "因子",
    render: (row) => (
      <div className="min-w-0">
        <div
          className={`font-mono text-[12.5px] ${
            row.name === selected ? "text-brand-bright" : "text-ink"
          }`}
        >
          {row.name}
        </div>
        {row.label ? <div className="text-[11px] text-ink-3">{row.label}</div> : null}
      </div>
    ),
  },
  {
    key: "source",
    header: "source",
    render: (row) => <span className="text-[12px] text-ink-2">{row.source ?? "—"}</span>,
  },
  {
    key: "code_version",
    header: "代码版本",
    render: (row) => <span className="num text-ink-2">{row.code_version ?? "—"}</span>,
  },
  {
    key: "db_version",
    header: "DB 版本",
    render: (row) => (
      <span className="inline-flex items-center gap-1.5">
        <span className="num text-ink-2">{row.db_version ?? "—"}</span>
        {row.db_registered ? null : <Badge tone="warn">未注册</Badge>}
      </span>
    ),
  },
  {
    key: "pit",
    header: "PIT",
    render: (row) => <span className="text-[12px] text-ink-2">{row.pit_rule ?? "—"}</span>,
  },
  {
    key: "enabled",
    header: "默认启用",
    render: (row) => (
      <Badge tone={row.enabled ? "brand" : "neutral"}>{row.enabled ? "是" : "否"}</Badge>
    ),
  },
  {
    key: "description",
    header: "说明",
    render: (row) => (
      <span className="text-[12px] text-ink-2" title={row.description ?? undefined}>
        {row.description ?? "—"}
      </span>
    ),
  },
  ];
}

export function RegistryTable({
  factors,
  selected,
  onSelect,
}: {
  factors: FactorSpecView[];
  selected: string;
  onSelect: (name: string) => void;
}) {
  return (
    <DataTable
      columns={buildColumns(selected)}
      rows={factors}
      rowKey={(row) => row.name}
      onRowClick={(row) => onSelect(row.name)}
      minWidth={980}
      empty="代码注册表为空。"
    />
  );
}
