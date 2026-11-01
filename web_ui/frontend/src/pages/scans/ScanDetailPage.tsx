import { useEffect, useState } from "react";
import {
  fetchScan,
  fetchScanLogs,
  fetchScanStocks,
  type ScanDetail,
  type ScanLogsPage,
  type ScanStock,
} from "../../api/scans";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { Select } from "../../components/Select";
import { StatCard } from "../../components/StatCard";
import { Switch } from "../../components/Switch";
import { IconChevronLeft } from "../../components/icons";
import { IconAlert } from "../../components/icons-extra";
import { fmtNum } from "../../lib/format";
import { SCAN_STATUS_LABELS } from "../../lib/labels";
import { Link } from "../../router";

const PAGE_SIZE = 100;
const LEVELS = [
  { value: "", label: "全部级别" },
  { value: "error", label: "error" },
  { value: "warn", label: "warn" },
  { value: "info", label: "info" },
];

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
    header: "排名",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.rank ?? "—"}</span>,
  },
  {
    key: "code",
    header: "代码",
    render: (row) => <span className="font-mono text-body text-ink">{row.code}</span>,
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
    render: (row) => <span className="num text-body text-ink-2">{fmtNum(row.dims[dim.key], 1)}</span>,
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
      <span className="text-body text-ink-2">{(row.concepts || []).slice(0, 3).join(" · ") || "—"}</span>
    ),
  },
  {
    key: "reject",
    header: "否决原因",
    render: (row) => (
      <span
        className={`text-body ${row.reject_reason ? "text-warning" : "text-ink-3"}`}
        title={row.reject_reason ?? undefined}
      >
        {row.reject_reason ?? "—"}
      </span>
    ),
  },
];

function levelTone(level: string): "danger" | "warning" | "normal" | "muted" {
  if (level === "error") return "danger";
  if (level === "warn") return "warning";
  if (level === "info") return "normal";
  return "muted";
}

export function ScanDetailPage({ scanId }: { scanId: string }) {
  const [scan, setScan] = useState<ScanDetail | null>(null);
  const [rows, setRows] = useState<ScanStock[]>([]);
  const [trueOnly, setTrueOnly] = useState(false);
  const [logs, setLogs] = useState<ScanLogsPage | null>(null);
  const [level, setLevel] = useState("");
  const [offset, setOffset] = useState(0);
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

  useEffect(() => {
    let active = true;
    fetchScanLogs(scanId, { level: level || undefined, offset, limit: PAGE_SIZE })
      .then((page) => {
        if (active) setLogs(page);
      })
      .catch(() => {
        if (active) setLogs(null);
      });
    return () => {
      active = false;
    };
  }, [scanId, level, offset]);

  const total = logs?.total ?? 0;
  const pageEnd = Math.min(offset + PAGE_SIZE, total);

  return (
    <>
      <Link
        to="/scans"
        className="mb-3 inline-flex items-center gap-1 text-body text-ink-2 no-underline transition-colors hover:text-ink"
      >
        <IconChevronLeft size={14} />
        返回扫描列表
      </Link>

      <PageHeader
        eyebrow="SCANS · 全榜明细"
        title={<span className="font-mono text-page">{scanId}</span>}
        description={
          scan
            ? `扫描日 ${scan.scan_date} · Top ${scan.top_n ?? "—"} · 候选 ${scan.candidates_n ?? "—"}`
            : undefined
        }
        actions={
          scan ? (
            <Badge tone={scan.status === "completed" ? "normal" : "warning"}>
              {SCAN_STATUS_LABELS[scan.status]}
            </Badge>
          ) : undefined
        }
      />

      {error ? (
        <div className="alert-danger mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
          <IconAlert size={16} />
          加载失败：{error}
        </div>
      ) : null}

      {scan ? (
        <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
          <StatCard label="创建时间" value={scan.created_at || "—"} />
          <StatCard label="耗时" value={`${fmtNum(scan.elapsed_s, 2)}s`} />
          <StatCard label="并发 / Top N" value={`${scan.workers ?? "—"} / ${scan.top_n ?? "—"}`} />
          <StatCard label="候选" value={scan.candidates_n ?? "—"} />
          <StatCard label="真龙" value={scan.true_dragon_n ?? "—"} tone="positive" />
          <StatCard
            label="日志 / 错误"
            value={`${scan.log_entries} / ${scan.error_n ?? "—"}`}
            tone={scan.error_n ? "warning" : "neutral"}
            sub={scan.has_raw_output ? `原始输出 ${fmtNum((scan.raw_output_bytes ?? 0) / 1024, 1)} KB` : "无原始输出"}
          />
        </div>
      ) : null}

      <div className="grid gap-3">
        <Section title="错误摘要" description="阶段、API 统计与评分记录数；不含原始 data">
          {logs?.summary ? (
            <div className="grid gap-2">
              <div className="flex flex-wrap gap-2 text-caption text-ink-2">
                <Badge tone="outline">总条目 {logs.summary.total_entries}</Badge>
                <Badge tone="outline">
                  API {logs.summary.api_ok}/{logs.summary.api_total} 成功
                </Badge>
                <Badge tone={logs.summary.api_error ? "warning" : "outline"}>
                  API 失败 {logs.summary.api_error}
                </Badge>
                <Badge tone="outline">评分记录 {logs.summary.scorer_count}</Badge>
                <Badge tone={logs.summary.error_count ? "danger" : "outline"}>
                  错误 {logs.summary.error_count}
                </Badge>
              </div>
              <ul className="grid gap-1 text-body text-ink-2">
                {logs.summary.phases.map((phase) => (
                  <li key={phase.key}>
                    <span className="font-mono text-ink-3">{phase.label}</span> {phase.message || "—"}
                  </li>
                ))}
              </ul>
            </div>
          ) : logs && !logs.available ? (
            <div className="text-body text-ink-2">该次扫描没有保存日志（日志可能已被清理）。</div>
          ) : (
            <div className="text-body text-ink-3">正在读取日志摘要…</div>
          )}
        </Section>

        <Section
          title="日志"
          description="时间 / 级别 / 类别 / 消息 / 代码；不展示 data JSON"
          bodyClassName="p-0"
          right={
            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={level}
                options={LEVELS}
                onChange={(next) => {
                  setLevel(next);
                  setOffset(0);
                }}
                width={140}
                label="级别"
              />
              <span className="text-caption text-ink-2">
                {total ? `${offset + 1}-${pageEnd} / ${total}` : "0 条"}
              </span>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                上一页
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={pageEnd >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                下一页
              </button>
            </div>
          }
        >
          {!logs || !logs.available || logs.entries.length === 0 ? (
            <EmptyState
              title={logs && !logs.available ? "该次扫描没有保存日志" : "当前筛选无日志"}
              description="无日志不是失败：状态显示为「已完成·无日志」。"
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="dt" style={{ minWidth: 860 }}>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>级别</th>
                    <th>类别</th>
                    <th>消息</th>
                    <th>代码</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.entries.map((entry, index) => (
                    <tr key={`${entry.ts}-${index}`}>
                      <td className="num text-body text-ink-2">{entry.time ?? "—"}</td>
                      <td>
                        <Badge tone={levelTone(entry.level)}>{entry.level || "—"}</Badge>
                      </td>
                      <td className="font-mono text-body text-ink-3">{entry.category}</td>
                      <td className="text-body text-ink">{entry.message}</td>
                      <td className="font-mono text-body text-ink-3">{entry.code || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>

        <Section
          title="当日榜单"
          description="rank 为空的排最后，再按综合分降序"
          bodyClassName="p-0"
          right={
            <div className="flex items-center gap-3">
              <Switch checked={trueOnly} onChange={setTrueOnly} label="只看真龙" />
              <span className="text-caption text-ink-2">
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
      </div>
    </>
  );
}
