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
  neutral: "border-white/10 bg-white/5 text-ink-2",
  brand: "border-brand/35 bg-brand-soft text-brand-bright",
  up: "border-up/35 bg-up-soft text-up",
  down: "border-down/35 bg-down-soft text-down",
  warn: "border-warn/35 bg-warn-soft text-warn",
  info: "border-info/35 bg-info-soft text-info",
  critical: "border-critical/40 bg-critical-soft text-[#ff9093]",
  outline: "border-white/15 bg-transparent text-ink-2",
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
