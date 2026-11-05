import type { ReactNode } from "react";
import { IconInfo } from "./icons";

/** 指标卡语义：价格/收益类只能 up/down；状态类用 danger/warning/normal/positive。 */
export type StatTone =
  | "neutral"
  | "up"
  | "down"
  | "brand"
  | "danger"
  | "warning"
  | "normal"
  | "positive"
  | "muted";

const VALUE_TONE: Record<StatTone, string> = {
  neutral: "text-ink",
  up: "text-up",
  down: "text-down",
  brand: "text-brand-bright",
  danger: "text-danger-text",
  warning: "text-warning",
  normal: "text-normal-text",
  positive: "text-positive-text",
  muted: "text-ink-2",
};

export function StatCard({
  label,
  value,
  sub,
  hint,
  labelExtra,
  headerAction,
  footer,
  tone = "neutral",
  icon,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  hint?: string;
  labelExtra?: ReactNode;
  /** 标签行内的操作（如「待回测」后面紧跟的「回测」按钮）。 */
  headerAction?: ReactNode;
  /** 卡片底部附加操作（如「待回测」卡片内的增量回测入口）。 */
  footer?: ReactNode;
  tone?: StatTone;
  icon?: ReactNode;
}) {
  return (
    <div className="panel group relative overflow-visible px-4 py-3.5">
      <div className="flex items-center gap-1.5">
        <span className="text-caption font-semibold uppercase tracking-[0.09em] text-ink-2">
          {label}
        </span>
        {labelExtra}
        {headerAction}
        {hint ? (
          <span title={hint} className="text-ink-3 transition-colors hover:text-ink-2">
            <IconInfo size={14} />
          </span>
        ) : null}
        {icon ? <span className="ml-auto text-ink-3">{icon}</span> : null}
      </div>
      <div
        className={`mt-1.5 text-metric font-semibold tracking-[-0.01em] ${VALUE_TONE[tone]}`}
      >
        {value}
      </div>
      {sub ? <div className="mt-1 text-caption text-ink-2">{sub}</div> : null}
      {footer}
    </div>
  );
}
