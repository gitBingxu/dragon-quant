import { useState } from "react";
import type { FactorSpecView } from "../../api/factors";
import { Badge } from "../../components/Badge";
import type { Column } from "../../components/DataTable";
import { PaginatedDataTable } from "../../components/PaginatedDataTable";
import { Select, TextField } from "../../components/Select";
import {
  EMPTY_FILTERS,
  filterFactors,
  filterResetKey,
  isFilterActive,
  sourceOptions,
  type FactorFilterState,
  type OriginFilter,
} from "./factorFilters";

const ORIGIN_OPTIONS = [
  { value: "all", label: "全部归属" },
  { value: "builtin", label: "系统内建" },
  { value: "workspace", label: "Workspace" },
];

function originLabel(origin: FactorSpecView["origin"]): string {
  return origin === "workspace" ? "Workspace" : "系统内建";
}

function buildColumns(selected: string): Column<FactorSpecView>[] {
  return [
    {
      key: "name",
      header: "因子",
      render: (row) => (
        <div className="min-w-0">
          <div
            className={`font-mono text-body ${
              row.name === selected ? "text-brand-bright" : "text-ink"
            }`}
          >
            {row.name}
          </div>
          {row.label ? <div className="text-caption text-ink-3">{row.label}</div> : null}
        </div>
      ),
    },
    {
      key: "origin",
      header: "归属",
      render: (row) => (
        <Badge tone={row.origin === "workspace" ? "brand" : "neutral"}>
          {originLabel(row.origin)}
        </Badge>
      ),
    },
    {
      key: "source",
      header: "数据来源",
      render: (row) => <span className="text-body text-ink-2">{row.source ?? "—"}</span>,
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
          {row.db_registered ? null : <Badge tone="warning">未注册</Badge>}
        </span>
      ),
    },
    {
      key: "pit",
      header: "PIT",
      render: (row) => <span className="text-body text-ink-2">{row.pit_rule ?? "—"}</span>,
    },
    {
      key: "description",
      header: "说明",
      render: (row) => (
        <span className="text-body text-ink-2" title={row.description ?? undefined}>
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
  const [filters, setFilters] = useState<FactorFilterState>(EMPTY_FILTERS);
  const rows = filterFactors(factors, filters);
  const active = isFilterActive(filters);
  const sources = sourceOptions(factors);
  const sourceChoices = [
    { value: "", label: "全部来源" },
    ...sources.map((source) => ({ value: source, label: source })),
  ];

  return (
    <div>
      <div className="flex flex-wrap items-end gap-3 border-b border-hairline px-4 py-3 sm:px-[18px]">
        <TextField
          label="搜索"
          value={filters.query}
          placeholder="名称 / 标签 / 说明"
          width={220}
          onChange={(value) => setFilters((prev) => ({ ...prev, query: value }))}
        />
        <Select
          label="归属"
          value={filters.origin}
          options={ORIGIN_OPTIONS}
          width={150}
          onChange={(value) =>
            setFilters((prev) => ({ ...prev, origin: value as OriginFilter }))
          }
        />
        <Select
          label="数据来源"
          value={filters.source}
          options={sourceChoices}
          width={150}
          onChange={(value) => setFilters((prev) => ({ ...prev, source: value }))}
        />
        <button
          type="button"
          className="btn btn-ghost"
          disabled={!active}
          onClick={() => setFilters(EMPTY_FILTERS)}
        >
          清空筛选
        </button>
        <span className="ml-auto text-body text-ink-2" aria-live="polite">
          筛选结果 <span className="num text-ink">{rows.length}</span> / 共{" "}
          <span className="num text-ink">{factors.length}</span>
        </span>
      </div>
      <PaginatedDataTable
        columns={buildColumns(selected)}
        rows={rows}
        rowKey={(row) => row.name}
        onRowClick={(row) => onSelect(row.name)}
        rowClassName={(row) => (row.name === selected ? "bg-hover" : undefined)}
        resetKey={filterResetKey(filters)}
        minWidth={980}
        empty={
          active
            ? "没有符合筛选条件的因子。"
            : "代码注册表为空。"
        }
      />
    </div>
  );
}
