import { useEffect, useState } from "react";
import {
  fetchScan,
  fetchScanStocks,
  type ScanDetail,
  type ScanStock,
} from "../../api/scans";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { Switch } from "../../components/Switch";
import { IconChevronLeft } from "../../components/icons";
import { IconAlert } from "../../components/icons-extra";
import { fmtNum } from "../../lib/format";
import { Link } from "../../router";

const DIMS: Array<{ key: keyof ScanStock["dims"]; label: string }> = [
  { key: "drive", label: "带动" },
  { key: "leadership", label: "领涨" },
  { key: "anti_drop", label: "抗跌" },
  { key: "liquidity", label: "流动" },
  { key: "absorption", label: "承接" },
];

const COLUMNS: Column<ScanStock>[] = [
  {
    key: "rank",
    header: "rank",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.rank ?? "—"}</span>,
  },
  {
    key: "code",
    header: "代码",
    render: (row) => <span className="font-mono text-[12.5px] text-ink">{row.code}</span>,
  },
  { key: "name", header: "名称", render: (row) => <span className="font-medium">{row.name}</span> },
  {
    key: "score",
    header: "综合",
    align: "right",
    render: (row) => (
      <span className="num font-medium text-ink">{fmtNum(row.composite_score, 1)}</span>
    ),
  },
  ...DIMS.map<Column<ScanStock>>((dim) => ({
    key: dim.key,
    header: dim.label,
    align: "right",
    render: (row) => <span className="num text-[12px] text-ink-2">{fmtNum(row.dims[dim.key], 1)}</span>,
  })),
  {
    key: "true",
    header: "真龙",
    render: (row) =>
      row.is_true_dragon == null ? (
        <span className="text-ink-3">—</span>
      ) : row.is_true_dragon ? (
        <Badge tone="brand" dot>
          真龙
        </Badge>
      ) : (
        <Badge tone="neutral">否</Badge>
      ),
  },
  {
    key: "board",
    header: "连板",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.board_count ?? "—"}</span>,
  },
  {
    key: "concepts",
    header: "概念",
    render: (row) => (
      <span className="text-[12px] text-ink-2">{(row.concepts || []).slice(0, 3).join(" · ") || "—"}</span>
    ),
  },
  {
    key: "reject",
    header: "否决原因",
    render: (row) => (
      <span
        className={`text-[12px] ${row.reject_reason ? "text-warn" : "text-ink-3"}`}
        title={row.reject_reason ?? undefined}
      >
        {row.reject_reason ?? "—"}
      </span>
    ),
  },
];

export function ScanDetailPage({ scanId }: { scanId: string }) {
  const [scan, setScan] = useState<ScanDetail | null>(null);
  const [rows, setRows] = useState<ScanStock[]>([]);
  const [trueOnly, setTrueOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setScan(null);
    setError(null);
    fetchScan(scanId)
      .then((detail) => {
        if (active) setScan(detail);
      })
      .catch((exc: unknown) => {
        if (active) setError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      active = false;
    };
  }, [scanId]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchScanStocks(scanId, { trueOnly, limit: 500 })
      .then((page) => {
        if (active) setRows(page.data);
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
  }, [scanId, trueOnly]);

  return (
    <>
      <Link
        to="/scans"
        className="mb-3 inline-flex items-center gap-1 text-[12px] text-ink-2 no-underline transition-colors hover:text-ink"
      >
        <IconChevronLeft size={14} />
        返回扫描列表
      </Link>

      <PageHeader
        eyebrow="SCANS · 全榜明细"
        title={<span className="font-mono text-[21px]">{scanId}</span>}
        description={scan ? `扫描日 ${scan.scan_date} · Top ${scan.top_n ?? "—"} · 候选 ${scan.candidates_n ?? "—"}` : undefined}
        actions={
          scan ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone="neutral">并发 {scan.workers ?? "—"}</Badge>
              <Badge tone="neutral">耗时 {fmtNum(scan.elapsed_s, 2)}s</Badge>
              <Badge tone={scan.has_raw_output ? "info" : "outline"}>
                原始输出 {scan.has_raw_output ? `${fmtNum((scan.raw_output_bytes ?? 0) / 1024, 1)} KB` : "无"}
              </Badge>
            </div>
          ) : undefined
        }
      />

      {error ? (
        <div className="alert-critical mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-[12.5px]">
          <IconAlert size={15} />
          加载失败：{error}
        </div>
      ) : null}

      <Section
        title="榜单明细"
        description="rank 为空的排最后，再按综合分降序"
        bodyClassName="p-0"
        right={
          <div className="flex items-center gap-3">
            <Switch checked={trueOnly} onChange={setTrueOnly} label="只看真龙" />
            <span className="text-[12px] text-ink-2">
              共 <span className="num text-ink">{rows.length}</span> 只
            </span>
          </div>
        }
      >
        {rows.length === 0 && !loading ? (
          <EmptyState
            title={trueOnly ? "没有真龙记录" : "该扫描没有股票记录"}
            description={trueOnly ? "关闭「只看真龙」查看全部候选。" : undefined}
          />
        ) : (
          <DataTable
            columns={COLUMNS}
            rows={rows}
            rowKey={(row) => row.code}
            minWidth={1080}
            empty={loading ? "正在加载…" : "暂无数据"}
          />
        )}
      </Section>
    </>
  );
}
