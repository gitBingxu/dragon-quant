// 账户图表配色（与 styles.css token 对齐）。
// 收益语义红线：红=涨/正收益，绿=跌/负收益；品牌紫只做身份/强调。
import type { SegmentTone } from "../../lib/chart";

export const BENCH_COLOR = "var(--color-ink-2)";
export const DD_COLOR = "var(--color-down)";
export const GRID = "var(--color-hairline)";
export const AXIS = "var(--color-hairline-2)";

export function segmentColor(tone: SegmentTone): string {
  if (tone === "up") return "var(--color-up)";
  if (tone === "down") return "var(--color-down)";
  return "var(--color-ink-3)";
}
