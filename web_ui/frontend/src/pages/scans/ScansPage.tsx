import { useEffect, useState } from "react";
import { fetchScans, type ScanSummary } from "../../api/scans";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { IconRadar } from "../../components/icons";
import { IconAlert } from "../../components/icons-extra";
import { fmtNum } from "../../lib/format";
import { Link, navigate } from "../../router";

const COLUMNS: Column<ScanSummary>[] = [
  {
    key: "id",
    header: "scan_id",
    render: (row) => (
      <Link
        to={`/scans/${encodeURIComponent(row.id)}`}
        className="font-mono text-[12.5px] text-brand-bright no-underline hover:underline"
      >
        {row.id}
      </Link>
    ),
  },
  { key: "date", header: "扫描日", render: (row) => <span className="num">{row.scan_date}</span> },
  {
    key: "top",
    header: "Top N",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.top_n ?? "—"}</span>,
  },
  {
    key: "candidates",
    header: "候选",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.candidates_n ?? "—"}</span>,
  },
  {
    key: "workers",
    header: "并发",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.workers ?? "—"}</span>,
  },
  {
    key: "elapsed",
    header: "耗时 (s)",
    align: "right",
    render: (row) => <span className="num text-ink-2">{fmtNum(row.elapsed_s, 2)}</span>,
  },
  {
    key: "created",
    header: "创建时间",
    render: (row) => <span className="num text-[11.5px] text-ink-3">{row.created_at}</span>,
  },
];

export function ScansPage() {
  const [rows, setRows] = useState<ScanSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    fetchScans()
      .then((payload) => {
        if (active) setRows(payload.data);
      })
      .catch((exc: unknown) => {
        if (active) setError(exc instanceof Error ? exc.message : String(exc));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <>
      <PageHeader
        eyebrow="SCANS · 扫描榜单"
        title="扫描榜单"
        description="只读 scans_v2 / scan_stocks_v2。点击任意记录进入全榜明细，查看五维评分与否决原因。"
        actions={
          <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-2">
            <IconRadar size={14} />
            共 <span className="num text-ink">{rows.length}</span> 次扫描
          </span>
        }
      />

      {error ? (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-critical/35 bg-critical-soft px-3.5 py-2.5 text-[12.5px] text-[#ff9093]">
          <IconAlert size={15} />
          加载失败：{error}
        </div>
      ) : null}

      <Section title="扫描记录" description="按创建时间倒序" bodyClassName="p-0">
        {rows.length === 0 && !loading ? (
          <EmptyState
            title="暂无扫描记录"
            description="先运行 dragon-quant scan --top 25 生成首份榜单。"
          />
        ) : (
          <DataTable
            columns={COLUMNS}
            rows={rows}
            rowKey={(row) => row.id}
            onRowClick={(row) => navigate(`/scans/${encodeURIComponent(row.id)}`)}
            minWidth={760}
            empty={loading ? "正在加载…" : "暂无扫描记录。"}
          />
        )}
      </Section>
    </>
  );
}
