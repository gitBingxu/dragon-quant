import { useState } from "react";
import type { AccountTrade } from "../../api/account";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { Pagination } from "../../components/Pagination";
import { Section } from "../../components/Section";
import { fmtMoney, fmtNum, pnlClass } from "../../lib/format";
import { usePagination } from "../../lib/pagination";

const COLUMNS: Column<AccountTrade>[] = [
  {
    key: "date",
    header: "日期",
    render: (row) => <span className="num text-ink-2">{row.trade_date}</span>,
  },
  {
    key: "side",
    header: "方向",
    render: (row) => (
      <Badge tone={row.side === "BUY" ? "up" : "down"}>{row.side === "BUY" ? "买入" : "卖出"}</Badge>
    ),
  },
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
    key: "price",
    header: "价格",
    align: "right",
    render: (row) => <span className="num">{fmtNum(row.price)}</span>,
  },
  {
    key: "qty",
    header: "数量",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.qty}</span>,
  },
  {
    key: "amount",
    header: "金额",
    align: "right",
    render: (row) => <span className="num text-ink-2">{fmtMoney(row.amount)}</span>,
  },
  {
    key: "fee",
    header: "费用",
    align: "right",
    render: (row) => <span className="num text-ink-3">{fmtMoney(row.fee)}</span>,
  },
  {
    key: "pnl",
    header: "本笔收益",
    align: "right",
    render: (row) => <span className={`num ${pnlClass(row.realized_pnl)}`}>{fmtMoney(row.realized_pnl)}</span>,
  },
  {
    key: "cash",
    header: "现金余额",
    align: "right",
    render: (row) => <span className="num text-ink-2">{fmtMoney(row.cash_after)}</span>,
  },
  {
    key: "reason",
    header: "逻辑",
    render: (row) => (
      <span className="block max-w-[380px] truncate text-[12px] text-ink-2" title={row.reason_text || row.reason_code}>
        {row.reason_text || row.reason_code}
      </span>
    ),
  },
];

export function TradesTable({ trades }: { trades: AccountTrade[] }) {
  const [pageSize] = useState(10);
  const pager = usePagination(trades, pageSize);
  return (
    <Section
      title="交割单"
      description="逐笔成交、费用与本笔实现盈亏"
      right={<Badge tone="outline">{trades.length} 笔</Badge>}
      bodyClassName="p-0"
      className="overflow-hidden"
    >
      <DataTable
        columns={COLUMNS}
        rows={pager.pageItems}
        rowKey={(row, index) => `${row.trade_date}-${row.code}-${row.side}-${index}`}
        minWidth={1220}
        empty="暂无交割单。"
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
