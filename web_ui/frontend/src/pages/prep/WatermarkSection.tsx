import type { PrepWatermark } from "../../api/assets";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { StatCard } from "../../components/StatCard";

type AssetRow = PrepWatermark["assets"][number];

const COLUMNS: Column<AssetRow>[] = [
  {
    key: "type",
    header: "资产",
    render: (row) => <span className="font-mono text-[12.5px] text-ink">{row.asset_type}</span>,
  },
  {
    key: "present",
    header: "存在",
    render: (row) => (
      <span className={row.present ? "text-down" : "text-ink-3"}>{row.present ? "✓" : "—"}</span>
    ),
  },
  {
    key: "status",
    header: "状态",
    render: (row) =>
      row.status ? (
        <Badge tone={row.status === "ok" ? "brand" : row.status === "empty" ? "neutral" : "warn"}>
          {row.status}
        </Badge>
      ) : (
        <span className="text-ink-3">—</span>
      ),
  },
  {
    key: "as_of",
    header: "as_of",
    render: (row) => <span className="num text-[12px] text-ink-2">{row.as_of ?? "—"}</span>,
  },
  {
    key: "usable",
    header: "展示可用",
    render: (row) => (
      <Badge tone={row.usable_display ? "brand" : "neutral"}>{row.usable_display ? "是" : "否"}</Badge>
    ),
  },
  {
    key: "note",
    header: "说明",
    render: (row) => (
      <span className="text-[12px] text-ink-2" title={row.note ?? row.error ?? undefined}>
        {row.note ?? row.error ?? "—"}
      </span>
    ),
  },
];

export function WatermarkSection({ watermark }: { watermark: PrepWatermark }) {
  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="资产可用"
          value={watermark.available ? "是" : "否"}
          tone={watermark.available ? "brand" : "warn"}
        />
        <StatCard
          label="复盘快照"
          value={watermark.has_snapshot ? "有" : "无"}
          tone={watermark.has_snapshot ? "brand" : "muted"}
        />
        <StatCard
          label="缺失资产"
          value={watermark.missing.length}
          tone={watermark.missing.length ? "warn" : "neutral"}
          sub={
            watermark.missing.length
              ? `${watermark.missing.slice(0, 3).join("、")}${
                  watermark.missing.length > 3 ? ` 等 ${watermark.missing.length} 项` : ""
                }`
              : "无"
          }
          hint={watermark.missing.join(", ") || undefined}
        />
        <StatCard
          label="降级资产"
          value={watermark.degraded.length}
          tone={watermark.degraded.length ? "warn" : "neutral"}
          sub={watermark.degraded.length ? watermark.degraded.slice(0, 3).join("、") : "无"}
          hint={watermark.degraded.join(", ") || undefined}
        />
      </div>
      <div className="overflow-hidden rounded-lg border border-hairline">
        <DataTable
          columns={COLUMNS}
          rows={watermark.assets}
          rowKey={(row) => row.asset_type}
          minWidth={760}
          empty="该日无资产记录。"
        />
      </div>
    </div>
  );
}
