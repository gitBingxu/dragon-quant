import type { ChartGeometry, ChartPoint } from "../../lib/chart";
import { fmtMoney, fmtPct, pnlVar } from "../../lib/format";
import { BENCH_COLOR, DD_COLOR } from "./equityTheme";

/** hover 读数：数值在前、标签在后（dataviz tooltip 层级）。 */
export function EquityTooltip({
  point,
  geom,
}: {
  point: ChartPoint;
  geom: ChartGeometry;
}) {
  const width = 200;
  const height = 125;
  const alignRight = point.x > geom.width - width - 24;
  const x = alignRight ? point.x - width - 14 : point.x + 14;
  const y = Math.max(8, Math.min(point.equityY - 44, geom.drawdown.bottom - height - 6));
  const rows: Array<{ label: string; value: string; color?: string }> = [
    { label: "总权益", value: fmtMoney(point.data.total_equity) },
    {
      label: "当日收益",
      value: fmtPct(point.data.daily_return),
      color: pnlVar(point.data.daily_return),
    },
    {
      label: "上证",
      value: fmtPct(point.benchmark?.cumulative_return ?? null),
      color: BENCH_COLOR,
    },
    {
      label: "累计收益",
      value: fmtPct(point.data.cumulative_return),
      color: pnlVar(point.data.cumulative_return),
    },
    { label: "回撤", value: fmtPct(point.data.drawdown), color: DD_COLOR },
  ];
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx={8}
        fill="var(--color-panel-2)"
        stroke="rgba(255,255,255,0.12)"
      />
      <text
        x={x + 12}
        y={y + 21}
        fontSize="11"
        fill="var(--color-ink-2)"
        style={{ fontFamily: "var(--font-mono)" }}
      >
        {point.data.trade_date}
      </text>
      {rows.map((row, index) => (
        <g key={row.label}>
          <text x={x + 12} y={y + 42 + index * 17} fontSize="11.5" fill="var(--color-ink-2)">
            {row.label}
          </text>
          <text
            x={x + width - 12}
            y={y + 42 + index * 17}
            fontSize="11.5"
            fill={row.color ?? "var(--color-ink)"}
            textAnchor="end"
            style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}
          >
            {row.value}
          </text>
        </g>
      ))}
    </g>
  );
}
