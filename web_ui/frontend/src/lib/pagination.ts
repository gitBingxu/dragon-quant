import { useCallback, useEffect, useMemo, useState } from "react";

/** 表格分页默认值：与「超过 10 行才显示分页」的验收一致。 */
export const TABLE_DEFAULT_PAGE_SIZE = 10;
export const TABLE_PAGE_SIZE_OPTIONS = [10, 25, 50, 100];
export const TABLE_MIN_TOTAL_TO_SHOW = 10;

export interface PaginationOptions {
  /** 数据身份变化（日期 / 筛选）时回到第 1 页；undefined 表示不重置。 */
  resetKey?: string | number | null;
  /** 仅当 total > minTotalToShow 时建议显示分页；默认 0（保持旧行为）。 */
  minTotalToShow?: number;
}

export interface PaginationResult<T> {
  page: number;
  setPage: (page: number) => void;
  pageCount: number;
  pageItems: T[];
  total: number;
  showPagination: boolean;
}

/** 前端分页 hook：唯一页码计算入口，只对已排序 items 切片，不持有排序状态。 */
export function usePagination<T>(
  items: T[],
  pageSize: number,
  options?: PaginationOptions
): PaginationResult<T> {
  const size = Math.max(1, Math.floor(pageSize));
  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / size));
  const resetKey = options?.resetKey;
  const minTotalToShow = options?.minTotalToShow ?? 0;

  // resetKey 变化时渲染期直接取第 1 页，避免先渲染旧页再纠正的一帧空表。
  const [state, setState] = useState<{
    key: string | number | null | undefined;
    page: number;
  }>({ key: resetKey, page: 1 });
  const page = state.key === resetKey ? state.page : 1;

  useEffect(() => {
    if (state.key !== resetKey) {
      setState({ key: resetKey, page: 1 });
    }
  }, [resetKey, state.key]);

  // 数据缩短导致越界时立即返回最后一个有效页；effect 只负责收敛内部状态。
  const clampedPage = Math.min(Math.max(1, page), pageCount);

  useEffect(() => {
    if (state.key === resetKey && state.page !== clampedPage) {
      setState((prev) =>
        prev.key === resetKey ? { key: prev.key, page: clampedPage } : prev
      );
    }
  }, [resetKey, state.key, state.page, clampedPage]);

  const setPage = useCallback(
    (next: number) => {
      const target = Math.min(Math.max(1, Math.floor(next)), pageCount);
      setState({ key: resetKey, page: target });
    },
    [resetKey, pageCount]
  );

  const pageItems = useMemo(
    () => items.slice((clampedPage - 1) * size, clampedPage * size),
    [items, clampedPage, size]
  );

  return {
    page: clampedPage,
    setPage,
    pageCount,
    pageItems,
    total,
    showPagination: total > minTotalToShow,
  };
}
