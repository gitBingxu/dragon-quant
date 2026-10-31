import { IconChevronLeft, IconChevronRight } from "./icons";

function pageWindow(page: number, pageCount: number): number[] {
  const size = Math.min(5, pageCount);
  let start = Math.max(1, page - Math.floor(size / 2));
  start = Math.min(start, Math.max(1, pageCount - size + 1));
  return Array.from({ length: size }, (_, i) => start + i);
}

export function Pagination({
  page,
  pageCount,
  total,
  pageSize,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 25, 50, 100],
}: {
  page: number;
  pageCount: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  pageSizeOptions?: number[];
}) {
  if (total === 0) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);
  const pages = pageWindow(page, pageCount);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-hairline px-4 py-2.5">
      <div className="text-[11.5px] text-ink-2">
        第 <span className="num text-ink">{from}</span>–
        <span className="num text-ink">{to}</span> 条 · 共{" "}
        <span className="num text-ink">{total}</span> 条
      </div>
      <div className="flex items-center gap-3">
        {onPageSizeChange ? (
          <select
            className="select h-7 w-[92px] text-[12px]"
            value={String(pageSize)}
            onChange={(event) => onPageSizeChange(Number(event.currentTarget.value))}
          >
            {pageSizeOptions.map((size) => (
              <option key={size} value={size}>
                {size} 条/页
              </option>
            ))}
          </select>
        ) : null}
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="icon-btn h-7 w-7 disabled:cursor-not-allowed disabled:opacity-30"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
            aria-label="上一页"
          >
            <IconChevronLeft size={15} />
          </button>
          {pages[0] > 1 ? (
            <>
              <PageButton page={1} current={page} onClick={onPageChange} />
              {pages[0] > 2 ? <span className="px-1 text-ink-3">…</span> : null}
            </>
          ) : null}
          {pages.map((item) => (
            <PageButton key={item} page={item} current={page} onClick={onPageChange} />
          ))}
          {pages[pages.length - 1] < pageCount ? (
            <>
              {pages[pages.length - 1] < pageCount - 1 ? (
                <span className="px-1 text-ink-3">…</span>
              ) : null}
              <PageButton page={pageCount} current={page} onClick={onPageChange} />
            </>
          ) : null}
          <button
            type="button"
            className="icon-btn h-7 w-7 disabled:cursor-not-allowed disabled:opacity-30"
            disabled={page >= pageCount}
            onClick={() => onPageChange(page + 1)}
            aria-label="下一页"
          >
            <IconChevronRight size={15} />
          </button>
        </div>
      </div>
    </div>
  );
}

function PageButton({
  page,
  current,
  onClick,
}: {
  page: number;
  current: number;
  onClick: (page: number) => void;
}) {
  const active = page === current;
  return (
    <button
      type="button"
      onClick={() => onClick(page)}
      className={`num grid h-7 min-w-7 place-items-center rounded-md px-1.5 text-[12px] transition-colors ${
        active
          ? "bg-brand-soft text-brand-bright ring-1 ring-inset ring-brand-focus"
          : "text-ink-2 hover:bg-hover hover:text-ink"
      }`}
      aria-current={active ? "page" : undefined}
    >
      {page}
    </button>
  );
}
