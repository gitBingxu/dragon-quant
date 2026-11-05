import { useMemo, useState } from "react";
import type { AccountBenchmarkPoint, AccountSnapshot } from "../../api/account";
import { buildEquityChart, chartGeometry, nearestIndex } from "../../lib/chart";
import { fmtMoney, fmtPct } from "../../lib/format";
import { useElementSize } from "../../lib/hooks";
import { EquityPlot } from "./EquityPlot";
import { useEquityKeyboard } from "./useEquityKeyboard";

export function EquityChart({
  data,
  benchmark,
}: {
  data: AccountSnapshot[];
  benchmark: AccountBenchmarkPoint[];
}) {
  const { ref, width } = useElementSize<HTMLDivElement>();
  const geom = useMemo(() => chartGeometry(width), [width]);
  const chart = useMemo(() => buildEquityChart(data, benchmark, geom), [data, benchmark, geom]);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const hover =
    hoverIndex == null ? null : chart.points.find((point) => point.index === hoverIndex) ?? null;

  const handleMove = (event: React.MouseEvent<SVGSVGElement>) => {
    if (!chart.points.length) return;
    const svg = event.currentTarget;
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const local = point.matrixTransform(matrix.inverse());
    const x = Math.max(geom.equity.left, Math.min(geom.equity.right, local.x));
    setHoverIndex(nearestIndex(chart.points, x));
  };

  const keyboard = useEquityKeyboard({
    length: chart.points.length,
    hoverIndex,
    setHoverIndex,
  });

  return (
    <div
      ref={ref}
      className="w-full rounded-lg"
      tabIndex={0}
      role="group"
      aria-label="账户权益与回撤曲线，可用左右方向键移动读数，Home/End 跳转首尾"
      onKeyDown={keyboard.onKeyDown}
      onFocus={keyboard.onFocus}
    >
      <span className="sr-only" aria-live="polite">
        {hover
          ? `${hover.data.trade_date} 总权益 ${fmtMoney(hover.data.total_equity)}，累计收益 ${fmtPct(
              hover.data.cumulative_return
            )}，回撤 ${fmtPct(hover.data.drawdown)}`
          : ""}
      </span>
      <EquityPlot
        chart={chart}
        geom={geom}
        hover={hover}
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIndex(null)}
      />
    </div>
  );
}
