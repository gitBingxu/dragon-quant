// 通用工具函数

export function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v > 0 ? "+" : "") + v.toFixed(2) + "%";
}

export function fmtNum(
  v: number | null | undefined,
  digits = 2
): string {
  if (v == null) return "—";
  return v.toFixed(digits);
}

/** 收益/回撤红绿着色：A股习惯红涨绿跌，>0 红，<0 绿，否则默认 */
export function pnlColor(v: number | null | undefined): string | undefined {
  if (v == null || v === 0) return undefined;
  return v > 0 ? "var(--mantine-color-red-5)" : "var(--mantine-color-teal-4)";
}

export type StatusMeta = { label: string; color: string };

export function statusMeta(status: string | null | undefined): StatusMeta {
  switch (status) {
    case "completed":
      return { label: "已完成", color: "teal" };
    case "pending":
      return { label: "待回测", color: "yellow" };
    case "pending_recompute":
      return { label: "待重算", color: "orange" };
    case "cannot_evaluate":
      return { label: "无法评估", color: "gray" };
    case "no_entry":
      return { label: "无介入", color: "gray" };
    case "error":
      return { label: "失败", color: "red" };
    default:
      return { label: status ?? "—", color: "gray" };
  }
}
