import type { BuiltChart, ChartGeometry, ChartPoint } from "../../lib/chart";
import { fmtPct } from "../../lib/format";
import { EquityTooltip } from "./EquityTooltip";
import { AXIS, BENCH_COLOR, DD_COLOR, EQUITY_COLOR, GRID } from "./equityTheme";

/** 刻度用两位小数的「万」，避免 9.95万/10.0万 这类重复标签。 */
function fmtEquityTick(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e8) return (value / 1e8).toFixed(2) + "亿";
  if (abs >= 1e4) return (value / 1e4).toFixed(2) + "万";
  return Math.round(value).toLocaleString("zh-CN");
}

export function EquityPlot({
  chart,
  geom,
  hover,
  onMouseMove,
  onMouseLeave,
}: {
  chart: BuiltChart;
  geom: ChartGeometry;
  hover: ChartPoint | null;
  onMouseMove: (event: React.MouseEvent<SVGSVGElement>) => void;
  onMouseLeave: () => void;
}) {
  const last = chart.last;
  return (
    <svg
      viewBox={`0 0 ${geom.width} ${geom.height}`}
      width="100%"
      height={geom.height}
      role="img"
      aria-label="账户权益与回撤曲线"
      onMouseMove={onMouseMove}
      onMouseLeave={onMouseLeave}
      style={{ display: "block", cursor: "crosshair" }}
    >
      <defs>
        <linearGradient id="equity-wash" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={EQUITY_COLOR} stopOpacity="0.22" />
          <stop offset="100%" stopColor={EQUITY_COLOR} stopOpacity="0.02" />
        </linearGradient>
        <linearGradient id="drawdown-wash" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={DD_COLOR} stopOpacity="0.04" />
          <stop offset="100%" stopColor={DD_COLOR} stopOpacity="0.2" />
        </linearGradient>
      </defs>

      <rect x="0" y="0" width={geom.width} height={geom.height} fill="transparent" />

      {chart.equityTicks.map((tick) => (
        <g key={`eq-${tick.y}`}>
          <line
            x1={geom.equity.left}
            x2={geom.equity.right}
            y1={tick.y}
            y2={tick.y}
            stroke={GRID}
            strokeWidth="1"
          />
          <text
            x={geom.equity.right + 8}
            y={tick.y + 3.5}
            fontSize="10.5"
            fill="var(--color-ink-3)"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            {fmtEquityTick(tick.value)}
          </text>
        </g>
      ))}

      <text
        x={geom.equity.left}
        y={geom.drawdown.top - 12}
        fontSize="10"
        fill="var(--color-ink-3)"
        letterSpacing="0.08em"
      >
        回撤
      </text>
      {chart.drawdownTicks.map((tick) => (
        <g key={`dd-${tick.y}`}>
          <line
            x1={geom.drawdown.left}
            x2={geom.drawdown.right}
            y1={tick.y}
            y2={tick.y}
            stroke={tick.value === 0 ? AXIS : GRID}
            strokeWidth="1"
          />
          <text
            x={geom.drawdown.right + 8}
            y={tick.y + 3.5}
            fontSize="10.5"
            fill="var(--color-ink-3)"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            {tick.value === 0 ? "0%" : fmtPct(tick.value, 1)}
          </text>
        </g>
      ))}

      <line
        x1={geom.drawdown.left}
        x2={geom.drawdown.right}
        y1={geom.drawdown.bottom}
        y2={geom.drawdown.bottom}
        stroke={AXIS}
      />
      {chart.dateTicks.map((tick) => (
        <text
          key={`dt-${tick.x}`}
          x={tick.x}
          y={geom.height - 8}
          fontSize="10.5"
          fill="var(--color-ink-3)"
          textAnchor="middle"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          {tick.label}
        </text>
      ))}

      <path d={chart.equityArea} fill="url(#equity-wash)" />
      <path
        d={chart.benchmarkPath}
        fill="none"
        stroke={BENCH_COLOR}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d={chart.equityPath}
        fill="none"
        stroke={EQUITY_COLOR}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d={chart.drawdownArea} fill="url(#drawdown-wash)" />
      <path
        d={chart.drawdownPath}
        fill="none"
        stroke={DD_COLOR}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      {last ? (
        <>
          <circle
            cx={last.x}
            cy={last.equityY}
            r="3.5"
            fill={EQUITY_COLOR}
            stroke="var(--color-panel)"
            strokeWidth="2"
          />
          {last.benchmarkY != null ? (
            <circle
              cx={last.x}
              cy={last.benchmarkY}
              r="3.5"
              fill={BENCH_COLOR}
              stroke="var(--color-panel)"
              strokeWidth="2"
            />
          ) : null}
          <circle
            cx={last.x}
            cy={last.drawdownY}
            r="3.5"
            fill={DD_COLOR}
            stroke="var(--color-panel)"
            strokeWidth="2"
          />
        </>
      ) : null}

      {hover ? (
        <g pointerEvents="none">
          <line
            x1={hover.x}
            x2={hover.x}
            y1={geom.equity.top}
            y2={geom.drawdown.bottom}
            stroke="rgba(255,255,255,0.22)"
            strokeWidth="1"
          />
          <circle
            cx={hover.x}
            cy={hover.equityY}
            r="4.5"
            fill={EQUITY_COLOR}
            stroke="var(--color-panel)"
            strokeWidth="2"
          />
          {hover.benchmarkY != null ? (
            <circle
              cx={hover.x}
              cy={hover.benchmarkY}
              r="4.5"
              fill={BENCH_COLOR}
              stroke="var(--color-panel)"
              strokeWidth="2"
            />
          ) : null}
          <circle
            cx={hover.x}
            cy={hover.drawdownY}
            r="4.5"
            fill={DD_COLOR}
            stroke="var(--color-panel)"
            strokeWidth="2"
          />
          <EquityTooltip point={hover} geom={geom} />
        </g>
      ) : null}
    </svg>
  );
}
