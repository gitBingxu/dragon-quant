import type { ReactNode } from "react";
import { IconChevronDown, IconChevronUp } from "./icons";

export interface Column<T> {
  key: string;
  header: ReactNode;
  align?: "left" | "right" | "center";
  width?: number | string;
  sortable?: boolean;
  className?: string;
  render: (row: T, index: number) => ReactNode;
}

export interface SortState {
  by: string;
  dir: "asc" | "desc";
}

const ALIGN: Record<string, string> = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
};

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  sort,
  onSort,
  onRowClick,
  empty,
  minWidth = 0,
  maxHeight,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
  sort?: SortState;
  onSort?: (key: string) => void;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  minWidth?: number;
  maxHeight?: number;
}) {
  return (
    <div className="dt-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table className="dt" style={minWidth ? { minWidth } : undefined}>
        <thead>
          <tr>
            {columns.map((column) => {
              const active = sort?.by === column.key;
              const content = (
                <span className="inline-flex items-center gap-1">
                  {column.header}
                  {column.sortable ? (
                    active ? (
                      sort?.dir === "asc" ? (
                        <IconChevronUp size={12} className="text-brand-bright" />
                      ) : (
                        <IconChevronDown size={12} className="text-brand-bright" />
                      )
                    ) : (
                      <IconChevronDown size={12} className="opacity-30" />
                    )
                  ) : null}
                </span>
              );
              return (
                <th
                  key={column.key}
                  className={ALIGN[column.align ?? "left"]}
                  style={column.width ? { width: column.width } : undefined}
                >
                  {column.sortable && onSort ? (
                    <button
                      type="button"
                      onClick={() => onSort(column.key)}
                      style={{ font: "inherit" }}
                      className={`cursor-pointer border-0 bg-transparent p-0 transition-colors hover:text-ink ${
                        active ? "text-brand-bright" : ""
                      }`}
                    >
                      {content}
                    </button>
                  ) : (
                    content
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length}>
                <div className="px-4 py-10 text-center text-[13px] text-ink-2">
                  {empty ?? "暂无数据"}
                </div>
              </td>
            </tr>
          ) : (
            rows.map((row, index) => (
              <tr
                key={rowKey(row, index)}
                data-clickable={onRowClick ? "true" : undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={`${ALIGN[column.align ?? "left"]} ${column.className ?? ""}`}
                  >
                    {column.render(row, index)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
