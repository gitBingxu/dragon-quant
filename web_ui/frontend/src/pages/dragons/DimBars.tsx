import type { Dims } from "../../api/scans";
import { fmtNum } from "../../lib/format";

const DIMS: Array<{ key: keyof Dims; label: string; weight: string }> = [
  { key: "drive", label: "带动性", weight: "30%" },
  { key: "leadership", label: "领涨性", weight: "25%" },
  { key: "anti_drop", label: "抗跌性", weight: "15%" },
  { key: "liquidity", label: "流动性", weight: "20%" },
  { key: "absorption", label: "资金承接", weight: "10%" },
];

export function DimBars({ dims }: { dims: Dims | null }) {
  return (
    <div className="grid gap-3">
      {DIMS.map((dim) => {
        const value = dims ? dims[dim.key] : null;
        const width = Math.max(0, Math.min(100, value ?? 0));
        return (
          <div key={dim.key}>
            <div className="mb-1.5 flex items-baseline justify-between gap-2">
              <span className="text-[12px] text-ink-2">
                {dim.label}
                <span className="ml-1.5 font-mono text-[10px] text-ink-3">{dim.weight}</span>
              </span>
              <span className="num text-[12.5px] text-ink">{fmtNum(value, 1)}</span>
            </div>
            <div className="meter">
              <span
                style={{
                  width: `${width}%`,
                  background:
                    value == null
                      ? "transparent"
                      : "linear-gradient(90deg, var(--color-brand), var(--color-brand-bright))",
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
