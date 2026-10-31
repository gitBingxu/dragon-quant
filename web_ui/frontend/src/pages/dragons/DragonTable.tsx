import { useMemo, useState } from "react";
import type { Dragon, ReviewCaliber } from "../../api/legacy";
import { Badge } from "../../components/Badge";
import { DataTable, type Column, type SortState } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { caliberField, type CaliberField } from "../../lib/caliber";
import { fmtNum, fmtPct, pnlClass, statusMeta } from "../../lib/format";
import { usePagination } from "../../lib/pagination";

interface Col {
  key: string;
  label: string;
  sortable?: boolean;
  field?: CaliberField;
}

const COLUMNS: Col[] = [
  { key: "code", label: "代码", sortable: true },
  { key: "name", label: "名称", sortable: true },
  { key: "trade_date", label: "入选日", sortable: true },
  { key: "buy_date", label: "买入日", sortable: true, field: "buy_date" },
  { key: "rank", label: "排名", sortable: true },
  { key: "composite_score", label: "综合分", sortable: true },
  { key: "is_true_dragon", label: "真龙" },
  { key: "board_count", label: "连板", sortable: true },
  { key: "concepts", label: "概念" },
  { key: "buy_price", label: "买入价", sortable: true, field: "buy_price" },
  { key: "max_return_5d", label: "最大收益%", sortable: true, field: "max_return_5d" },
  { key: "max_drawdown_5d", label: "最大回撤%", sortable: true, field: "max_drawdown_5d" },
  { key: "max_return_hold_days", label: "持有天", sortable: true, field: "max_return_hold_days" },
  { key: "status", label: "状态", sortable: true, field: "status" },
];

function displayStatus(row: Dragon): string | null {
  return row.derived_status != null ? row.derived_status : row.review_status;
}

export function DragonTable({
  data,
  caliber,
  sort,
  onSort,
  onRowClick,
}: {
  data: Dragon[];
  caliber: ReviewCaliber;
  sort: SortState;
  onSort: (by: string) => void;
  onRowClick: (row: Dragon) => void;
}) {
  const [pageSize, setPageSize] = useState(25);
  const pager = usePagination(data, pageSize);

  const columns = useMemo<Column<Dragon>[]>(
    () =>
      COLUMNS.map((col) => ({
        key: col.key,
        header: col.label,
        sortable: col.sortable,
        align:
          col.key === "composite_score" ||
          col.key === "buy_price" ||
          col.key === "max_return_5d" ||
          col.key === "max_drawdown_5d"
            ? "right"
            : "left",
        render: (row) => renderCell(row, col, caliber),
      })),
    [caliber]
  );

  return (
    <div className="panel overflow-hidden">
      <DataTable
        columns={columns}
        rows={pager.pageItems}
        rowKey={(row) => `${row.source}-${row.trade_date}-${row.code}`}
        sort={sort}
        onSort={onSort}
        onRowClick={onRowClick}
        minWidth={1220}
        empty="没有符合条件的记录，调整筛选后重试。"
      />
      <Pagination
        page={pager.page}
        pageCount={pager.pageCount}
        total={pager.total}
        pageSize={pageSize}
        onPageChange={pager.setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          pager.setPage(1);
        }}
      />
    </div>
  );
}

function renderCell(row: Dragon, col: Col, caliber: ReviewCaliber) {
  const field = col.field ? caliberField(caliber, col.field) : null;
  switch (col.key) {
    case "code":
      return <span className="font-mono text-[12.5px] text-ink">{row.code}</span>;
    case "name":
      return <span className="font-medium text-ink">{row.name}</span>;
    case "trade_date":
      return <span className="num">{row.trade_date}</span>;
    case "buy_date":
      return <span className="num text-ink-2">{(field && row[field]) || "—"}</span>;
    case "rank":
      return <span className="num text-ink-2">{row.rank ?? "—"}</span>;
    case "composite_score":
      return <span className="num font-medium text-ink">{fmtNum(row.composite_score, 1)}</span>;
    case "is_true_dragon":
      return row.is_true_dragon == null ? (
        <span className="text-ink-3">—</span>
      ) : row.is_true_dragon ? (
        <Badge tone="brand" dot>
          真龙
        </Badge>
      ) : (
        <span className="text-ink-3">—</span>
      );
    case "board_count":
      return <span className="num text-ink-2">{row.board_count ?? "—"}</span>;
    case "concepts":
      return (
        <span className="text-[12px] text-ink-2">
          {(row.concepts || []).slice(0, 3).join(" · ") || "—"}
        </span>
      );
    case "buy_price":
      return <span className="num">{fmtNum(field ? (row[field] as number | null) : null)}</span>;
    case "max_return_5d": {
      const value = field ? (row[field] as number | null) : null;
      return <span className={`num ${pnlClass(value)}`}>{fmtPct(value)}</span>;
    }
    case "max_drawdown_5d": {
      const value = field ? (row[field] as number | null) : null;
      return <span className={`num ${pnlClass(value)}`}>{fmtPct(value)}</span>;
    }
    case "max_return_hold_days":
      return <span className="num text-ink-2">{field ? (row[field] ?? "—") : "—"}</span>;
    case "status": {
      const meta = statusMeta(displayStatus(row));
      return <Badge tone={meta.tone}>{meta.label}</Badge>;
    }
    default:
      return "—";
  }
}
