import { useState } from "react";
import { DataTable, type DataTableProps } from "./DataTable";
import { Pagination } from "./Pagination";
import {
  TABLE_DEFAULT_PAGE_SIZE,
  TABLE_MIN_TOTAL_TO_SHOW,
  TABLE_PAGE_SIZE_OPTIONS,
  usePagination,
} from "../lib/pagination";

/**
 * 表格分页壳：组合 DataTable + Pagination，内部走唯一共享 hook。
 *
 * 装饰式复用——表格渲染契约不变，只附加分页关注点；调用点把 `<DataTable`
 * 换成 `<PaginatedDataTable` 即启用，未迁移的页面保持原行为。
 * 默认 10 条/页，10/25/50/100 可选，仅当总行数 > 10 时渲染分页。
 */
export interface PaginatedDataTableProps<T> extends DataTableProps<T> {
  defaultPageSize?: number;
  pageSizeOptions?: number[];
  resetKey?: string | number | null;
  minTotalToShow?: number;
}

export function PaginatedDataTable<T>({
  defaultPageSize = TABLE_DEFAULT_PAGE_SIZE,
  pageSizeOptions = TABLE_PAGE_SIZE_OPTIONS,
  resetKey,
  minTotalToShow = TABLE_MIN_TOTAL_TO_SHOW,
  ...tableProps
}: PaginatedDataTableProps<T>) {
  const [pageSize, setPageSize] = useState(defaultPageSize);
  const pager = usePagination(tableProps.rows, pageSize, {
    resetKey,
    minTotalToShow,
  });

  return (
    <>
      <DataTable<T> {...tableProps} rows={pager.pageItems} />
      {pager.showPagination ? (
        <Pagination
          page={pager.page}
          pageCount={pager.pageCount}
          total={pager.total}
          pageSize={pageSize}
          pageSizeOptions={pageSizeOptions}
          onPageChange={pager.setPage}
          onPageSizeChange={(next) => {
            setPageSize(next);
            pager.setPage(1);
          }}
        />
      ) : null}
    </>
  );
}
