// 通用格式化纯函数（颜色走 CSS token，A 股红涨绿跌）。

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return (v > 0 ? "+" : "") + v.toFixed(digits) + "%";
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

export function fmtMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return (
    "¥" +
    v.toLocaleString("zh-CN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}

export function fmtCompactMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + " 亿";
  if (abs >= 1e4) return (v / 1e4).toFixed(1) + " 万";
  return v.toFixed(0);
}

/** A 股涨跌语义：正=红（涨），负=绿（跌）。 */
export type PnlTone = "up" | "down" | "flat";

export function pnlTone(v: number | null | undefined): PnlTone {
  if (v == null || v === 0 || Number.isNaN(v)) return "flat";
  return v > 0 ? "up" : "down";
}

export function pnlClass(v: number | null | undefined): string {
  const tone = pnlTone(v);
  if (tone === "up") return "text-up";
  if (tone === "down") return "text-down";
  return "text-ink-2";
}

/** 映射到 StatCard 的 tone（flat → neutral）。 */
export function pnlStatTone(v: number | null | undefined): "up" | "down" | "neutral" {
  const tone = pnlTone(v);
  return tone === "flat" ? "neutral" : tone;
}

export function pnlVar(v: number | null | undefined): string {
  const tone = pnlTone(v);
  if (tone === "up") return "var(--color-up)";
  if (tone === "down") return "var(--color-down)";
  return "var(--color-ink-2)";
}

export type StatusTone = "neutral" | "brand" | "warn" | "info" | "critical" | "down";

export interface StatusMeta {
  label: string;
  tone: StatusTone;
}

export function statusMeta(status: string | null | undefined): StatusMeta {
  switch (status) {
    case "completed":
      return { label: "已完成", tone: "info" };
    case "pending":
      return { label: "待回测", tone: "warn" };
    case "pending_recompute":
      return { label: "待重算", tone: "warn" };
    case "cannot_evaluate":
      return { label: "无法评估", tone: "neutral" };
    case "no_entry":
      return { label: "无介入", tone: "neutral" };
    case "error":
      return { label: "失败", tone: "critical" };
    default:
      return { label: status ?? "—", tone: "neutral" };
  }
}

export function defaultRunName(dateFrom: string, dateTo: string): string {
  return `${dateFrom || "开始日期"} ~ ${dateTo || "结束日期"}`;
}

export function formatDateInput(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function defaultDateRange(): { dateFrom: string; dateTo: string } {
  const end = new Date();
  const start = new Date(end);
  start.setDate(end.getDate() - 30);
  return { dateFrom: formatDateInput(start), dateTo: formatDateInput(end) };
}

export function runReturnLabel(run: { total_return: number | null }): string {
  return fmtPct(run.total_return);
}
