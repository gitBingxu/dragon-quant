import type { ReactNode } from "react";
import { IconInfo } from "./icons";

export type StatTone = "neutral" | "up" | "down" | "brand" | "warn" | "muted";

const VALUE_TONE: Record<StatTone, string> = {
  neutral: "text-ink",
  up: "text-up",
  down: "text-down",
  brand: "text-brand-bright",
  warn: "text-warn",
  muted: "text-ink-2",
};

export function StatCard({
  label,
  value,
  sub,
  hint,
  tone = "neutral",
  icon,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  hint?: string;
  tone?: StatTone;
  icon?: ReactNode;
}) {
  return (
    <div className="panel group relative overflow-hidden px-4 py-3.5">
      <div className="flex items-center gap-1.5">
        <span className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-ink-2">
          {label}
        </span>
        {hint ? (
          <span title={hint} className="text-ink-3 transition-colors hover:text-ink-2">
            <IconInfo size={12} />
          </span>
        ) : null}
        {icon ? <span className="ml-auto text-ink-3">{icon}</span> : null}
      </div>
      <div
        className={`mt-1.5 text-[23px] font-semibold leading-tight tracking-[-0.01em] ${VALUE_TONE[tone]}`}
      >
        {value}
      </div>
      {sub ? <div className="mt-1 text-[11.5px] text-ink-2">{sub}</div> : null}
    </div>
  );
}
