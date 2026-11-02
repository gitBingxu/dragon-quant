import type { PrepWatermark } from "../../api/assets";
import { Badge } from "../../components/Badge";
import { type Column } from "../../components/DataTable";
import { PaginatedDataTable } from "../../components/PaginatedDataTable";
import {
  ASSET_CATEGORY_LABELS,
  ASSET_STATUS_LABELS,
  LEGAL_ABSENCE_LABELS,
  assetTypeLabel,
  labelOf,
} from "../../lib/labels";

type AssetRow = PrepWatermark["assets"][number];

const COLUMNS: Column<AssetRow>[] = [
  {
    key: "type",
    header: "资产",
    render: (row) => (
      <span className="text-body text-ink" title={row.asset_type}>
        {assetTypeLabel(row.asset_type)}
      </span>
    ),
  },
  {
    key: "category",
    header: "类别",
    render: (row) => (
      <span className="text-body text-ink-2">
        {labelOf(ASSET_CATEGORY_LABELS, row.category)}
      </span>
    ),
  },
  {
    key: "expected",
    header: "期望",
    render: (row) =>
      row.expected ? (
        <Badge tone="outline">应有</Badge>
      ) : (
        <Badge tone="muted">不适用</Badge>
      ),
  },
  {
    key: "complete",
    header: "资产完备",
    render: (row) =>
      row.expected ? (
        <Badge tone={row.asset_complete ? "positive" : "danger"}>
          {row.asset_complete ? "达标" : "未达标"}
        </Badge>
      ) : (
        <span className="text-body text-ink-3">不适用</span>
      ),
  },
  {
    key: "status",
    header: "采集记录",
    render: (row) =>
      row.status ? (
        <Badge
          tone={
            row.status === "ok"
              ? "normal"
              : row.status === "empty"
                ? "outline"
                : "warning"
          }
        >
          {labelOf(ASSET_STATUS_LABELS, row.status)}
        </Badge>
      ) : (
        <span className="text-body text-ink-3">无采集记录</span>
      ),
  },
  {
    key: "present",
    header: "存在",
    render: (row) => (
      <span className={row.present ? "text-normal-text" : "text-ink-3"}>
        {row.present ? "有" : "—"}
      </span>
    ),
  },
  {
    key: "as_of",
    header: "数据日期",
    render: (row) => <span className="num text-body text-ink-2">{row.as_of ?? "—"}</span>,
  },
  {
    key: "exempt",
    header: "合法缺席",
    render: (row) =>
      row.legal_absence ? (
        <span className="text-body text-ink-2">
          {labelOf(LEGAL_ABSENCE_LABELS, row.legal_absence)}
        </span>
      ) : (
        <span className="text-body text-ink-3">—</span>
      ),
  },
];

/** 当日资产表：资产完备与采集报告分列，不把 report 当本次运行成功证明。 */
export function AssetDayTable({ watermark }: { watermark: PrepWatermark }) {
  return (
    <div className="grid gap-2">
      <div className="text-caption text-ink-2">
        当日应有资产 <span className="num text-ink">{watermark.expected_ok}/{watermark.expected_total}</span>
        {" · "}「采集记录」是该日期已知的最新状态，不代表本次运行一定成功。
      </div>
      <div className="overflow-hidden rounded-lg border border-hairline">
        <PaginatedDataTable
          columns={COLUMNS}
          rows={watermark.assets}
          rowKey={(row) => row.asset_type}
          resetKey={watermark.trade_date}
          minWidth={980}
          empty="该日没有资产记录。"
        />
      </div>
    </div>
  );
}
