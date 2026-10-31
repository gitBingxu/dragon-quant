import type { AccountBenchmarkPoint, AccountSnapshot } from "../api/account";

// 图表几何纯函数（零 React 依赖）。上下两块共享 x 轴：
// 上：总权益 vs 上证；下：回撤（绿）。绝不做双 y 轴。

export const CHART_HEIGHT = 356;
export const MIN_CHART_WIDTH = 420;

export interface PaneRect {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface ChartGeometry {
  width: number;
  height: number;
  equity: PaneRect;
  drawdown: PaneRect;
}

export type SegmentTone = "up" | "down" | "flat";

export interface EquitySegment {
  path: string;
  tone: SegmentTone;
}

export interface ChartPoint {
  index: number;
  x: number;
  equityY: number;
  benchmarkY: number | null;
  drawdownY: number;
  data: AccountSnapshot;
  benchmark: AccountBenchmarkPoint | null;
}

export interface BuiltChart {
  points: ChartPoint[];
  equityPath: string;
  equitySegments: EquitySegment[];
  benchmarkPath: string;
  equityArea: string;
  drawdownPath: string;
  drawdownArea: string;
  equityTicks: Array<{ value: number; y: number }>;
  drawdownTicks: Array<{ value: number; y: number }>;
  dateTicks: Array<{ x: number; label: string }>;
  last: ChartPoint | null;
}

export function chartGeometry(containerWidth: number): ChartGeometry {
  const width = Math.max(Math.floor(containerWidth || 0), MIN_CHART_WIDTH);
  const right = Math.max(200, width - 86);
  return {
    width,
    height: CHART_HEIGHT,
    equity: { left: 10, right, top: 14, bottom: 230 },
    drawdown: { left: 10, right, top: 272, bottom: 328 },
  };
}

export function buildEquityChart(
  data: AccountSnapshot[],
  benchmark: AccountBenchmarkPoint[],
  geom: ChartGeometry
): BuiltChart {
  const benchmarkByDate = new Map(benchmark.map((b) => [b.trade_date, b]));
  const points = data.map((d, i) => ({
    data: d,
    benchmark: benchmarkByDate.get(d.trade_date) ?? null,
    index: i,
  }));
  const xFor = (i: number) =>
    geom.equity.left +
    (points.length <= 1
      ? 0
      : (i / (points.length - 1)) * (geom.equity.right - geom.equity.left));

  const equityValues = points.flatMap((p) => {
    const values = [p.data.total_equity];
    if (p.benchmark) values.push(p.benchmark.total_equity);
    return values;
  });
  const eqMinRaw = equityValues.length ? Math.min(...equityValues) : 0;
  const eqMaxRaw = equityValues.length ? Math.max(...equityValues) : 1;
  const eqSpan = eqMaxRaw - eqMinRaw || Math.max(eqMaxRaw * 0.02, 1);
  const eqMin = eqMinRaw - eqSpan * 0.12;
  const eqMax = eqMaxRaw + eqSpan * 0.12;

  const drawdownValues = points.map((p) => Math.min(p.data.drawdown ?? 0, 0));
  const ddMin = Math.min(...drawdownValues, -0.5);
  const ddMax = 0;

  const chartPoints: ChartPoint[] = points.map((p) => ({
    index: p.index,
    x: xFor(p.index),
    equityY: scale(p.data.total_equity, eqMin, eqMax, geom.equity),
    benchmarkY: p.benchmark
      ? scale(p.benchmark.total_equity, eqMin, eqMax, geom.equity)
      : null,
    drawdownY: scale(p.data.drawdown ?? 0, ddMin, ddMax, geom.drawdown),
    data: p.data,
    benchmark: p.benchmark,
  }));

  return {
    points: chartPoints,
    equityPath: linePath(chartPoints, "equityY"),
    // 逐日方向分段：涨红、跌绿、持平中性（通达信/同花顺分时惯例）。
    equitySegments: chartPoints.slice(1).map((point, index) => {
      const previous = chartPoints[index];
      const delta = point.data.total_equity - previous.data.total_equity;
      const tone: SegmentTone = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
      return {
        path: `M ${previous.x.toFixed(1)} ${previous.equityY.toFixed(1)} L ${point.x.toFixed(
          1
        )} ${point.equityY.toFixed(1)}`,
        tone,
      };
    }),
    benchmarkPath: optionalLinePath(chartPoints, "benchmarkY"),
    equityArea: areaPath(chartPoints, "equityY", geom.equity.bottom),
    drawdownPath: linePath(chartPoints, "drawdownY"),
    drawdownArea: areaPath(chartPoints, "drawdownY", geom.drawdown.top),
    equityTicks: ticks(eqMin, eqMax, 4).map((value) => ({
      value,
      y: scale(value, eqMin, eqMax, geom.equity),
    })),
    drawdownTicks: [ddMax, ddMin / 2, ddMin].map((value) => ({
      value,
      y: scale(value, ddMin, ddMax, geom.drawdown),
    })),
    dateTicks: dateTicks(chartPoints, geom),
    last: chartPoints[chartPoints.length - 1] ?? null,
  };
}

export function scale(
  value: number,
  min: number,
  max: number,
  rect: PaneRect
): number {
  const span = max - min || 1;
  return rect.bottom - ((value - min) / span) * (rect.bottom - rect.top);
}

export function linePath(points: ChartPoint[], key: "equityY" | "drawdownY"): string {
  if (!points.length) return "";
  return points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p[key].toFixed(1)}`)
    .join(" ");
}

export function optionalLinePath(
  points: ChartPoint[],
  key: "benchmarkY"
): string {
  let path = "";
  let open = false;
  for (const point of points) {
    const y = point[key];
    if (y == null) {
      open = false;
      continue;
    }
    path += `${open ? " L" : " M"} ${point.x.toFixed(1)} ${y.toFixed(1)}`;
    open = true;
  }
  return path.trim();
}

function areaPath(points: ChartPoint[], key: "equityY" | "drawdownY", baseY: number) {
  if (!points.length) return "";
  const first = points[0];
  const last = points[points.length - 1];
  return `${linePath(points, key)} L ${last.x.toFixed(1)} ${baseY} L ${first.x.toFixed(
    1
  )} ${baseY} Z`;
}

function ticks(min: number, max: number, count: number): number[] {
  if (count <= 1) return [min];
  const step = (max - min) / (count - 1);
  return Array.from({ length: count }, (_, i) => min + step * i);
}

function dateTicks(
  points: ChartPoint[],
  geom: ChartGeometry
): Array<{ x: number; label: string }> {
  if (!points.length) return [];
  const span = geom.equity.right - geom.equity.left;
  const count = Math.min(
    Math.max(Math.floor(span / 130), 2),
    7,
    points.length
  );
  return Array.from({ length: count }, (_, i) => {
    const idx =
      count === 1 ? 0 : Math.round((i / (count - 1)) * (points.length - 1));
    const point = points[idx];
    return { x: point.x, label: point.data.trade_date.slice(5) };
  });
}

export function nearestIndex(points: ChartPoint[], x: number): number {
  if (!points.length) return 0;
  let best = 0;
  let bestDistance = Infinity;
  for (const point of points) {
    const distance = Math.abs(point.x - x);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = point.index;
    }
  }
  return best;
}
