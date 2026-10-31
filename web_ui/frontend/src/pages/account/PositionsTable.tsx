import { useState } from "react";
import type { AccountPosition } from "../../api/legacy";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { Section } from "../../components/Section";
import { fmtNum, fmtPct, pnlClass } from "../../lib/format";
import { usePagination } from "../../lib/pagination";

const COLUMNS: Column<AccountPosition>[] = [
  {
    key: "stock",
    header: "股票",
    render: (row) => (
      <span className="text-[12.5px] text-ink">
        <span className="num text-ink-3">{row.code}</span> {row.name}
      </span>
    ),
  },
  {
    key: "entry",
    header: "买入日",
    render: (row) => <span className="num text-ink-2">{row.entry_date}</span>,
  },
  {
    key: "exit",
    header: "卖出日",
    render: (row) => <span className="num text-ink-2">{row.exit_date}</span>,
  },
  {
    key: "entry_price",
    header: "买入价",
    align: "right",
    render: (row) => <span className="num">{fmtNum(row.entry_price)}</span>,
  },
  {
    key: "exit_price",
    header: "卖出价",
    align: "right",
    render: (row) => <span className="num">{fmtNum(row.exit_price)}</span>,
  },
  {
    key: "return",
    header: "收益",
    align: "right",
    render: (row) => (
      <span className={`num ${pnlClass(row.realized_return)}`}>{fmtPct(row.realized_return)}</span>
    ),
  },
  {
    key: "hold",
    header: "持有天",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.hold_days}</span>,
  },
  {
    key: "reason",
    header: "退出逻辑",
    render: (row) => <span className="font-mono text-[11.5px] text-ink-2">{row.exit_reason_code}</span>,
  },
  {
    key: "post_return",
    header: "清仓5日内最高盈利",
    align: "right",
    render: (row) => (
      <span className="inline-flex items-center justify-end gap-1.5">
        <span className={`num ${pnlClass(row.post_exit_max_return)}`}>
          {fmtPct(row.post_exit_max_return)}
        </span>
        {row.post_exit_max_return != null &&
        row.post_exit_window_days != null &&
        row.post_exit_window_days < 5 ? (
          <Badge tone="neutral">{row.post_exit_window_days}/5</Badge>
        ) : null}
      </span>
    ),
  },
  {
    key: "post_drawdown",
    header: "清仓5日内最大回撤",
    align: "right",
    render: (row) => (
      <span className={`num ${pnlClass(row.post_exit_max_drawdown)}`}>
        {fmtPct(row.post_exit_max_drawdown)}
      </span>
    ),
  },
];

export function PositionsTable({ positions }: { positions: AccountPosition[] }) {
  const [pageSize] = useState(10);
  const pager = usePagination(positions, pageSize);
  return (
    <Section
      title="单票收益汇总"
      description="按已平仓持仓统计；窗口指标为卖出后 5 个交易日，可能含回测区间外数据（事后评估，不参与交易决策）"
      right={<Badge tone="outline">{positions.length} 笔</Badge>}
      bodyClassName="p-0"
      className="overflow-hidden"
    >
      <DataTable
        columns={COLUMNS}
        rows={pager.pageItems}
        rowKey={(row, index) => `${row.code}-${row.entry_date}-${index}`}
        minWidth={1100}
        empty="暂无已平仓持仓。"
      />
      <Pagination
        page={pager.page}
        pageCount={pager.pageCount}
        total={pager.total}
        pageSize={pageSize}
        onPageChange={pager.setPage}
      />
    </Section>
  );
}
