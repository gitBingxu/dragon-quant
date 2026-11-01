import type { ScanStatus, ScanSummary } from "../../api/scans";
import { Badge } from "../../components/Badge";
import { fmtNum } from "../../lib/format";
import { SCAN_STATUS_LABELS } from "../../lib/labels";
import { Link } from "../../router";

function statusTone(status: ScanStatus): "normal" | "warning" | "outline" {
  if (status === "completed") return "normal";
  if (status === "completed_with_errors") return "warning";
  return "outline";
}

function parseCreatedAt(value: string): Date | null {
  const parsed = new Date(value.replace(" ", "T"));
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function elapsedText(value: string, now: Date): string {
  const created = parseCreatedAt(value);
  if (!created) return "—";
  const minutes = Math.floor((now.getTime() - created.getTime()) / 60000);
  if (minutes < 0) return "—";
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

function todayIso(): string {
  // 纯展示口径：按 Asia/Shanghai 的日历日，不参与任何后端判定。
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(
    new Date()
  );
}

/**
 * 扫描健康带：最近一次扫描的状态 / 时间 / 距今 / 当日速览。
 *
 * 无记录不渲染（列表已有 EmptyState）；全部数字取自 `GET /api/scans` 首屏，
 * 不额外请求。「今天」按本机时钟，仅作展示提示。
 */
export function ScanHealthBand({ rows }: { rows: ScanSummary[] }) {
  if (!rows.length) return null;
  const latest = rows.reduce((best, row) =>
    row.created_at > best.created_at ? row : best
  );
  const now = new Date();
  const today = todayIso();
  const completedAt = latest.created_at.includes(" ")
    ? latest.created_at.slice(11)
    : latest.created_at;

  return (
    <div
      aria-label="扫描健康"
      className="panel mb-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5 text-body"
    >
      <Badge tone={statusTone(latest.status)}>
        {SCAN_STATUS_LABELS[latest.status]}
      </Badge>
      <Link
        to={`/scans/${encodeURIComponent(latest.id)}`}
        className="font-mono text-body text-normal-text no-underline hover:underline"
      >
        {latest.id}
      </Link>
      <span className="text-ink-2" title="「距今」按本机时钟计算，仅用于展示">
        扫描日 <span className="num text-ink">{latest.scan_date}</span> · 完成于{" "}
        <span className="num text-ink">{completedAt}</span>（
        {elapsedText(latest.created_at, now)}）
      </span>
      <span className="text-ink-2">
        耗时 <span className="num text-ink">{fmtNum(latest.elapsed_s, 1)}s</span> · 真龙{" "}
        <span className="num text-ink">{latest.true_dragon_n ?? "—"}</span> · 候选{" "}
        <span className="num text-ink">{latest.candidates_n ?? "—"}</span> · 错误{" "}
        <span className={`num ${latest.error_n ? "text-warning" : "text-ink"}`}>
          {latest.error_n ?? "—"}
        </span>
      </span>
      {latest.scan_date !== today ? (
        <Badge tone="warning">今天尚未扫描（最近为 {latest.scan_date}）</Badge>
      ) : null}
    </div>
  );
}
