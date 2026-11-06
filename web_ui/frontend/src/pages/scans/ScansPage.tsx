import { useEffect, useMemo, useState } from "react";
import { fetchScans, type ScanStatus, type ScanSummary } from "../../api/scans";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { IconRadar } from "../../components/icons";
import { IconAlert } from "../../components/icons-extra";
import { fmtNum } from "../../lib/format";
import { SCAN_STATUS_LABELS } from "../../lib/labels";
import { Link, navigate } from "../../router";
import { ScanHealthBand } from "./ScanHealthBand";

function statusTone(status: ScanStatus): "normal" | "warning" | "outline" {
  if (status === "completed") return "normal";
  if (status === "completed_with_errors") return "warning";
  return "outline";
}

const COLUMNS: Column<ScanSummary>[] = [
  {
    key: "id",
    header: "扫描 ID",
    render: (row) => (
      <Link
        to={`/scans/${encodeURIComponent(row.id)}`}
        className="font-mono text-body text-normal-text no-underline hover:underline"
      >
        {row.id}
      </Link>
    ),
  },
  {
    key: "status",
    header: "状态",
    render: (row) => (
      <Badge tone={statusTone(row.status)}>{SCAN_STATUS_LABELS[row.status]}</Badge>
    ),
  },
  { key: "date", header: "扫描日", render: (row) => <span className="num">{row.scan_date}</span> },
  {
    key: "created",
    header: "完成时间",
    render: (row) => <span className="num text-ink-3">{row.created_at}</span>,
  },
  {
    key: "elapsed",
    header: "耗时 (s)",
    align: "right",
    render: (row) => <span className="num text-ink-2">{fmtNum(row.elapsed_s, 2)}</span>,
  },
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
    key: "dragons",
    header: "真龙",
    align: "right",
    render: (row) => <span className="num text-ink">{row.true_dragon_n ?? "—"}</span>,
  },
  {
    key: "logs",
    header: "日志",
    align: "right",
    render: (row) => (
      <span className={`num ${row.log_entries ? "text-ink-2" : "text-ink-3"}`}>
        {row.log_entries}
      </span>
    ),
  },
  {
    key: "errors",
    header: "错误",
    align: "right",
    render: (row) =>
      row.error_n == null ? (
        <span className="text-ink-3">—</span>
      ) : (
        <span className={`num ${row.error_n ? "text-warning" : "text-ink-2"}`}>{row.error_n}</span>
      ),
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

  const counts = useMemo(() => {
    const base: Record<ScanStatus, number> = {
      completed: 0,
      completed_with_errors: 0,
      logs_missing: 0,
    };
    for (const row of rows) base[row.status] = (base[row.status] ?? 0) + 1;
    return base;
  }, [rows]);

  return (
    <>
      <PageHeader
        eyebrow="SCANS · 龙头扫描"
        title="扫描榜单"
        description="只读 scans_v2 / scan_stocks_v2 / scan_logs_v2。状态由日志聚合推导：无日志不等于失败；未落库的崩溃没有记录，页面不伪造。"
        actions={
          <span className="inline-flex items-center gap-1.5 text-caption text-ink-2">
            <IconRadar size={14} />
            共 <span className="num text-ink">{rows.length}</span> 次扫描
          </span>
        }
      />

      {error ? (
        <div className="alert-danger mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
          <IconAlert size={16} />
          加载失败：{error}
        </div>
      ) : null}

      <ScanHealthBand rows={rows} />

      <Section
        title="扫描记录"
        description="按创建时间倒序"
        bodyClassName="p-0"
        right={
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone="normal">已完成 {counts.completed}</Badge>
            <Badge tone="warning">有异常 {counts.completed_with_errors}</Badge>
            <Badge tone="outline">无日志 {counts.logs_missing}</Badge>
          </div>
        }
      >
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
            minWidth={1180}
            empty={loading ? "正在加载…" : "暂无扫描记录。"}
          />
        )}
      </Section>
    </>
  );
}
