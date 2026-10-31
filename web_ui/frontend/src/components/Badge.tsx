import type { ReactNode } from "react";

export type BadgeTone =
  | "neutral"
  | "brand"
  | "up"
  | "down"
  | "warn"
  | "info"
  | "critical"
  | "outline";

const TONES: Record<BadgeTone, string> = {
  neutral: "badge-neutral",
  brand: "badge-brand",
  up: "badge-up",
  down: "badge-down",
  warn: "badge-warn",
  info: "badge-info",
  critical: "badge-critical",
  outline: "badge-outline",
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
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4 ${TONES[tone]} ${className}`}
    >
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
      {children}
    </span>
  );
}
