import type { ReactNode } from "react";

/** 语义状态：danger/warning/normal/positive 与 A 股 up/down 严格分离。 */
export type BadgeTone =
  | "neutral"
  | "brand"
  | "up"
  | "down"
  | "danger"
  | "warning"
  | "normal"
  | "positive"
  | "outline"
  | "muted";

const TONES: Record<BadgeTone, string> = {
  neutral: "badge-neutral",
  brand: "badge-brand",
  up: "badge-up",
  down: "badge-down",
  danger: "badge-danger",
  warning: "badge-warning",
  normal: "badge-normal",
  positive: "badge-positive",
  outline: "badge-outline",
  muted: "badge-muted",
};

export function Badge({
  tone = "neutral",
  children,
  dot = false,
  className = "",
}: {
  tone?: BadgeTone;
  children: ReactNode;
  dot?: boolean;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-caption font-medium leading-5 ${TONES[tone]} ${className}`}
    >
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
      {children}
    </span>
  );
}
