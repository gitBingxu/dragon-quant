import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Badge,
  Card,
  Container,
  Grid,
  Group,
  Pagination,
  Paper,
  Select,
  SimpleGrid,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useElementSize } from "@mantine/hooks";
import {
  fetchAccountPositions,
  fetchAccountBenchmark,
  fetchAccountEvents,
  fetchAccountRuns,
  fetchAccountSnapshots,
  fetchAccountTrades,
  type AccountBenchmarkPoint,
  type AccountTimelineEvent,
  type AccountPosition,
  type AccountRun,
  type AccountSnapshot,
  type AccountTrade,
} from "./api";
import { fmtNum, fmtPct, pnlColor } from "./utils";

function initialSource(): "v1" | "v2" {
  const params = new URLSearchParams(window.location.search);
  return params.get("source") === "v1" ? "v1" : "v2";
}

export function AccountApp() {
  const [source, setSource] = useState<"v1" | "v2">(() => initialSource());
  const [runs, setRuns] = useState<AccountRun[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [snapshots, setSnapshots] = useState<AccountSnapshot[]>([]);
  const [benchmark, setBenchmark] = useState<AccountBenchmarkPoint[]>([]);
  const [trades, setTrades] = useState<AccountTrade[]>([]);
  const [positions, setPositions] = useState<AccountPosition[]>([]);
  const [events, setEvents] = useState<AccountTimelineEvent[]>([]);

  const selectedRun = useMemo(
    () => runs.find((r) => r.id === runId) || null,
    [runs, runId]
  );
  const latest = snapshots[snapshots.length - 1] || null;

  const loadRuns = useCallback(async (src: "v1" | "v2") => {
    const resp = await fetchAccountRuns(src);
    setRuns(resp.data || []);
    setRunId((prev) => prev ?? resp.data?.[0]?.id ?? null);
  }, []);

  useEffect(() => {
    loadRuns(source).catch(() => {
      setRuns([]);
      setRunId(null);
    });
  }, [source, loadRuns]);

  useEffect(() => {
    if (!runId) {
      setSnapshots([]);
      setBenchmark([]);
      setTrades([]);
      setPositions([]);
      setEvents([]);
      return;
    }
    Promise.all([
      fetchAccountSnapshots(runId),
      fetchAccountBenchmark(runId),
      fetchAccountTrades(runId),
      fetchAccountPositions(runId),
      fetchAccountEvents(runId),
    ])
      .then(([s, b, t, p, e]) => {
        setSnapshots(s.data || []);
        setBenchmark(b.data || []);
        setTrades(t.data || []);
        setPositions(p.data || []);
        setEvents(e.data || []);
      })
      .catch(() => {
        setSnapshots([]);
        setBenchmark([]);
        setTrades([]);
        setPositions([]);
        setEvents([]);
      });
  }, [runId]);

  return (
    <Container size="100%" px="xl" py="lg">
      <Group justify="space-between" mb="lg">
        <Title order={1}>账户模拟交易</Title>
        <Group>
          <Select
            size="sm"
            w={130}
            data={[
              { value: "v2", label: "五维识别" },
              { value: "v1", label: "v1 历史" },
            ]}
            value={source}
            onChange={(v) => {
              const next = v === "v1" ? "v1" : "v2";
              setSource(next);
              setRunId(null);
            }}
            allowDeselect={false}
          />
          <Select
            size="sm"
            w={330}
            placeholder="选择回测批次"
            data={runs.map((r) => ({
              value: String(r.id),
              label: `#${r.id} ${r.date_from} ~ ${r.date_to} ${r.total_return?.toFixed(1)}%`,
            }))}
            value={runId ? String(runId) : null}
            onChange={(v) => setRunId(v ? Number(v) : null)}
            allowDeselect={false}
          />
        </Group>
      </Group>

      <AccountStats run={selectedRun} latest={latest} />

      <Grid gutter="md" mt="md">
        <Grid.Col span={{ base: 12, lg: 8 }}>
          <EquityCurve data={snapshots} benchmark={benchmark} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <CurrentPosition latest={latest} />
        </Grid.Col>
      </Grid>

      <TradesTable trades={trades} />
      <PositionsTable positions={positions} />
      <AccountTimeline events={events} positions={positions} />
    </Container>
  );
}

function AccountStats({
  run,
  latest,
}: {
  run: AccountRun | null;
  latest: AccountSnapshot | null;
}) {
  return (
    <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }} spacing="sm">
      <Stat label="初始资金">{fmtMoney(run?.initial_cash)}</Stat>
      <Stat label="当前权益">{fmtMoney(latest?.total_equity ?? run?.final_equity)}</Stat>
      <Stat label="累计收益">
        <Text span c={pnlColor(run?.total_return)} inherit>
          {fmtPct(run?.total_return)}
        </Text>
      </Stat>
      <Stat label="最大回撤">
        <Text span c={pnlColor(run?.max_drawdown)} inherit>
          {fmtPct(run?.max_drawdown)}
        </Text>
      </Stat>
      <Stat label="胜率">{run?.win_rate != null ? `${run.win_rate.toFixed(1)}%` : "—"}</Stat>
      <Stat label="交割单">{run?.trade_count ?? 0}</Stat>
    </SimpleGrid>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Card withBorder radius="md" padding="md">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
        {label}
      </Text>
      <Text fz={24} fw={700} mt={4}>
        {children}
      </Text>
    </Card>
  );
}

function EquityCurve({
  data,
  benchmark,
}: {
  data: AccountSnapshot[];
  benchmark: AccountBenchmarkPoint[];
}) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const { ref, width } = useElementSize();
  const geom = useMemo(() => chartGeometry(width), [width]);
  const chart = useMemo(() => buildChart(data, benchmark, geom), [data, benchmark, geom]);
  const hover = hoverIndex == null ? null : chart.points[hoverIndex] ?? null;

  const handleMove = (event: React.MouseEvent<SVGSVGElement>) => {
    if (!chart.points.length) return;
    const svg = event.currentTarget;
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const svgPoint = point.matrixTransform(matrix.inverse());
    const x = Math.max(geom.left, Math.min(geom.right, svgPoint.x));
    const idx = nearestIndex(chart.points, x);
    setHoverIndex(idx);
  };

  return (
    <Paper
      ref={ref}
      withBorder
      radius="md"
      p="md"
      style={{ background: "linear-gradient(180deg, #1f1f1f 0%, #1b1b1b 100%)" }}
    >
      <Group justify="space-between" mb="sm">
        <Group gap="lg">
          <Text fw={700}>资产与回撤曲线</Text>
          <Legend color="red" label="总权益" />
          <Legend color="yellow" label="上证指数" />
        </Group>
        <Group gap="md">
          {chart.last && (
            <>
              <Text size="xs" c="dimmed">
                最新权益 <Text span c="red.4">{fmtMoney(chart.last.total_equity)}</Text>
              </Text>
              <Text size="xs" c="dimmed">
                回撤 <Text span c="teal.4">{fmtPct(chart.last.drawdown)}</Text>
              </Text>
            </>
          )}
          <Text size="xs" c="dimmed">
            {data.length} 个交易日
          </Text>
        </Group>
      </Group>
      <svg
        viewBox={`0 0 ${geom.width} ${geom.height}`}
        width="100%"
        height={geom.height}
        role="img"
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIndex(null)}
        style={{ display: "block", cursor: chart.points.length ? "crosshair" : "default" }}
      >
        <defs>
          <linearGradient id="equityArea" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--mantine-color-red-5)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--mantine-color-red-5)" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <rect x="0" y="0" width={geom.width} height={geom.height} fill="transparent" />

        {chart.equityTicks.map((tick) => (
          <g key={`eq-${tick.y}`}>
            <line x1={geom.left} x2={geom.right} y1={tick.y} y2={tick.y} stroke="#303030" strokeDasharray="3 5" />
            <text x={geom.right + 8} y={tick.y + 4} fontSize="11" fill="#8f8f8f">
              {fmtCompactMoney(tick.value)}
            </text>
          </g>
        ))}
        {chart.dateTicks.map((tick) => (
          <g key={`dt-${tick.x}`}>
            <line x1={tick.x} x2={tick.x} y1={geom.top} y2={geom.bottom} stroke="#272727" />
            <text x={tick.x} y={geom.height - 10} fontSize="11" textAnchor="middle" fill="#8f8f8f">
              {tick.label}
            </text>
          </g>
        ))}

        <line x1={geom.left} x2={geom.right} y1={geom.equityBase} y2={geom.equityBase} stroke="#454545" />
        <line x1={geom.left} x2={geom.left} y1={geom.top} y2={geom.bottom} stroke="#3d3d3d" />
        <line x1={geom.right} x2={geom.right} y1={geom.top} y2={geom.bottom} stroke="#3d3d3d" />

        {chart.equityArea && <path d={chart.equityArea} fill="url(#equityArea)" />}
        <path d={chart.equityPath} fill="none" stroke="var(--mantine-color-red-5)" strokeWidth="2.4" />
        <path d={chart.benchmarkPath} fill="none" stroke="var(--mantine-color-yellow-5)" strokeWidth="2" />

        {hover && (
          <g>
            <line x1={hover.x} x2={hover.x} y1={geom.top} y2={geom.bottom} stroke="#888" strokeDasharray="4 4" />
            <line x1={geom.left} x2={geom.right} y1={hover.equityY} y2={hover.equityY} stroke="#664040" strokeDasharray="4 4" />
            <circle cx={hover.x} cy={hover.equityY} r="4" fill="var(--mantine-color-red-5)" stroke="#fff" strokeWidth="1.2" />
            {hover.benchmarkY != null && (
              <circle cx={hover.x} cy={hover.benchmarkY} r="4" fill="var(--mantine-color-yellow-5)" stroke="#fff" strokeWidth="1.2" />
            )}
            <HoverTip point={hover} geom={geom} />
          </g>
        )}
      </svg>
    </Paper>
  );
}

function HoverTip({ point, geom }: { point: ChartPoint; geom: ChartGeometry }) {
  const w = 188;
  const h = 116;
  const alignRight = point.x > geom.width - w - 28;
  const x = alignRight ? point.x - w - 14 : point.x + 14;
  const y = Math.max(12, Math.min(point.equityY - 56, geom.height - h - 12));
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={4} fill="#111" opacity="0.94" stroke="#555" />
      <text x={x + 12} y={y + 22} fontSize="12" fill="#e8e8e8" fontWeight={700}>
        {point.data.trade_date}
      </text>
      <text x={x + 12} y={y + 45} fontSize="12" fill="#bdbdbd">总权益</text>
      <text x={x + w - 12} y={y + 45} fontSize="12" fill="#ff6b6b" textAnchor="end">
        {fmtMoney(point.data.total_equity)}
      </text>
      <text x={x + 12} y={y + 66} fontSize="12" fill="#bdbdbd">累计收益</text>
      <text x={x + w - 12} y={y + 66} fontSize="12" fill={point.data.cumulative_return >= 0 ? "#ff6b6b" : "#20c997"} textAnchor="end">
        {fmtPct(point.data.cumulative_return)}
      </text>
      <text x={x + 12} y={y + 87} fontSize="12" fill="#bdbdbd">上证同期</text>
      <text x={x + w - 12} y={y + 87} fontSize="12" fill="#ffd43b" textAnchor="end">
        {point.benchmark ? fmtPct(point.benchmark.cumulative_return) : "—"}
      </text>
      <text x={x + 12} y={y + 108} fontSize="12" fill="#bdbdbd">回撤</text>
      <text x={x + w - 12} y={y + 108} fontSize="12" fill="#20c997" textAnchor="end">
        {fmtPct(point.data.drawdown)}
      </text>
    </g>
  );
}

function CurrentPosition({ latest }: { latest: AccountSnapshot | null }) {
  return (
    <Paper withBorder radius="md" p="md" h="100%">
      <Text fw={700} mb="sm">
        当前持仓
      </Text>
      {!latest?.position_code ? (
        <Text c="dimmed">空仓</Text>
      ) : (
        <>
          <Group justify="space-between">
            <Text fw={700}>{latest.position_name}</Text>
            <Badge variant="light">{latest.position_code}</Badge>
          </Group>
          <Text size="sm" mt="md">数量：{latest.position_qty}</Text>
          <Text size="sm">成本：{fmtNum(latest.position_cost)}</Text>
          <Text size="sm">市价：{fmtNum(latest.position_market_price)}</Text>
          <Text size="sm" c={pnlColor(latest.position_unrealized_return)}>
            浮盈亏：{fmtPct(latest.position_unrealized_return)}
          </Text>
        </>
      )}
    </Paper>
  );
}

function TradesTable({ trades }: { trades: AccountTrade[] }) {
  const pager = usePagination(trades, 10);
  return (
    <Paper withBorder radius="md" mt="md" style={{ overflow: "hidden" }}>
      <Table.ScrollContainer minWidth={1220}>
        <Table stickyHeader verticalSpacing="xs" highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>日期</Table.Th>
              <Table.Th>方向</Table.Th>
              <Table.Th>股票</Table.Th>
              <Table.Th>价格</Table.Th>
              <Table.Th>数量</Table.Th>
              <Table.Th>金额</Table.Th>
              <Table.Th>费用</Table.Th>
              <Table.Th>本笔收益</Table.Th>
              <Table.Th>现金余额</Table.Th>
              <Table.Th>逻辑</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {trades.length ? (
              pager.items.map((t, i) => (
                <Table.Tr key={`${t.trade_date}-${t.code}-${t.side}-${i}`}>
                  <Table.Td>{t.trade_date}</Table.Td>
                  <Table.Td>
                    <Badge color={t.side === "BUY" ? "red" : "teal"} variant="light">
                      {t.side === "BUY" ? "买入" : "卖出"}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{t.code} {t.name}</Table.Td>
                  <Table.Td>{fmtNum(t.price)}</Table.Td>
                  <Table.Td>{t.qty}</Table.Td>
                  <Table.Td>{fmtMoney(t.amount)}</Table.Td>
                  <Table.Td>{fmtMoney(t.fee)}</Table.Td>
                  <Table.Td c={pnlColor(t.realized_pnl)}>{fmtMoney(t.realized_pnl)}</Table.Td>
                  <Table.Td>{fmtMoney(t.cash_after)}</Table.Td>
                  <Table.Td maw={520}>
                    <Text size="sm">{t.reason_text || t.reason_code}</Text>
                  </Table.Td>
                </Table.Tr>
              ))
            ) : (
              <Table.Tr>
                <Table.Td colSpan={10}>
                  <Text ta="center" c="dimmed" py={32}>暂无交割单</Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Pager total={trades.length} pager={pager} />
    </Paper>
  );
}

function AccountTimeline({
  events,
  positions,
}: {
  events: AccountTimelineEvent[];
  positions: AccountPosition[];
}) {
  const pager = usePagination(events, 10);
  const closedByEntry = useMemo(() => {
    const m = new Map<string, AccountPosition>();
    for (const p of positions) m.set(`${p.entry_date}-${p.code}`, p);
    return m;
  }, [positions]);

  return (
    <Paper withBorder radius="md" mt="md" p="md">
      <Group justify="space-between" mb="md">
        <div>
          <Text fw={700}>账户决策时间线</Text>
          <Text size="xs" c="dimmed">
            展示每日买入、卖出、继续持有和空仓原因
          </Text>
        </div>
        <Badge variant="light">{events.length} 条</Badge>
      </Group>
      {events.length ? (
        <div style={{ display: "grid", gap: 12 }}>
          {pager.items.map((event, i) => (
            <TimelineItem
              key={`${event.event_date}-${event.event_type}-${event.code}-${i}`}
              event={event}
              position={event.event_type === "BUY" ? closedByEntry.get(`${event.event_date}-${event.code}`) : undefined}
            />
          ))}
        </div>
      ) : (
        <Text ta="center" c="dimmed" py={32}>
          暂无账户时间线。重新运行一次 review-account 后可展示空仓原因。
        </Text>
      )}
      <Pager total={events.length} pager={pager} />
    </Paper>
  );
}

function TimelineItem({
  event,
  position,
}: {
  event: AccountTimelineEvent;
  position?: AccountPosition;
}) {
  const meta = timelineMeta(event.event_type);
  const isIdle = event.event_type === "IDLE";
  const details = Array.isArray(event.signal?.details)
    ? (event.signal.details as Array<Record<string, unknown>>)
    : [];
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "120px 20px minmax(0, 1fr)",
        gap: 12,
        alignItems: "start",
      }}
    >
      <Text size="sm" c="dimmed" pt={2}>{event.event_date}</Text>
      <div
        style={{
          width: 12,
          height: 12,
          borderRadius: 999,
          background: `var(--mantine-color-${meta.color}-5)`,
          marginTop: 6,
          boxShadow: `0 0 0 4px var(--mantine-color-${meta.color}-0)`,
        }}
      />
      <Paper
        withBorder
        radius="sm"
        p="sm"
        style={isIdle ? { background: "#202020", borderColor: "#333" } : undefined}
      >
        <Group justify="space-between" gap="sm" wrap="nowrap">
          <Group gap="xs" wrap="wrap">
            <Badge color={meta.color} variant="light">{meta.label}</Badge>
            <Text fw={700} c={isIdle ? "gray.2" : undefined}>{event.title || meta.label}</Text>
            {event.code && <Badge variant="outline">{event.code}</Badge>}
          </Group>
          <Group gap="md" wrap="nowrap">
            {event.cash != null && <Text size="xs" c="dimmed">现金 {fmtMoney(event.cash)}</Text>}
            {event.total_equity != null && <Text size="xs" c="dimmed">权益 {fmtMoney(event.total_equity)}</Text>}
          </Group>
        </Group>
        <Text size="sm" mt={8} c={isIdle ? "gray.4" : undefined}>{event.detail || event.reason_code}</Text>
        {details.length > 0 && (
          <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
            {details.slice(0, 5).map((d, i) => (
              <Group key={i} gap="xs" wrap="nowrap" align="flex-start">
                <Badge size="xs" color={d.passed ? "green" : "gray"} variant="light">
                  {typeof d.rank === "number" ? `#${d.rank}` : "—"}
                </Badge>
                <Text size="xs" c={d.passed ? "green.7" : "dimmed"}>
                  {String(d.reason_text || d.reason_code || "")}
                </Text>
              </Group>
            ))}
          </div>
        )}
        {position && (
          <Group mt="sm" gap="md">
            <Text size="xs" c="dimmed">卖出日 {position.exit_date}</Text>
            <Text size="xs" c={pnlColor(position.realized_return)}>
              闭环收益 {fmtPct(position.realized_return)}
            </Text>
            <Text size="xs" c="dimmed">持有 {position.hold_days} 天</Text>
          </Group>
        )}
      </Paper>
    </div>
  );
}

function timelineMeta(type: AccountTimelineEvent["event_type"]) {
  if (type === "BUY") return { label: "买入", color: "red" as const };
  if (type === "SELL") return { label: "卖出", color: "teal" as const };
  if (type === "HOLD") return { label: "持仓", color: "blue" as const };
  return { label: "空仓", color: "gray" as const };
}

function PositionsTable({ positions }: { positions: AccountPosition[] }) {
  const pager = usePagination(positions, 10);
  return (
    <Paper withBorder radius="md" mt="md" style={{ overflow: "hidden" }}>
      <Group justify="space-between" px="md" py="sm">
        <div>
          <Text fw={700}>单票收益汇总</Text>
          <Text size="xs" c="dimmed">按已平仓持仓统计每只股票收益</Text>
        </div>
        <Badge variant="light">{positions.length} 笔</Badge>
      </Group>
      <Table.ScrollContainer minWidth={900}>
        <Table verticalSpacing="xs" highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>股票</Table.Th>
              <Table.Th>买入日</Table.Th>
              <Table.Th>卖出日</Table.Th>
              <Table.Th>买入价</Table.Th>
              <Table.Th>卖出价</Table.Th>
              <Table.Th>收益</Table.Th>
              <Table.Th>持有天</Table.Th>
              <Table.Th>退出逻辑</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {positions.length ? (
              pager.items.map((p, i) => (
                <Table.Tr key={`${p.code}-${p.entry_date}-${i}`}>
                  <Table.Td>{p.code} {p.name}</Table.Td>
                  <Table.Td>{p.entry_date}</Table.Td>
                  <Table.Td>{p.exit_date}</Table.Td>
                  <Table.Td>{fmtNum(p.entry_price)}</Table.Td>
                  <Table.Td>{fmtNum(p.exit_price)}</Table.Td>
                  <Table.Td c={pnlColor(p.realized_return)}>{fmtPct(p.realized_return)}</Table.Td>
                  <Table.Td>{p.hold_days}</Table.Td>
                  <Table.Td>{p.exit_reason_code}</Table.Td>
                </Table.Tr>
              ))
            ) : (
              <Table.Tr>
                <Table.Td colSpan={8}>
                  <Text ta="center" c="dimmed" py={32}>暂无已平仓持仓</Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Pager total={positions.length} pager={pager} />
    </Paper>
  );
}

function Pager<T>({
  total,
  pager,
}: {
  total: number;
  pager: ReturnType<typeof usePagination<T>>;
}) {
  if (pager.totalPages <= 1) return null;
  return (
    <Group justify="space-between" px="md" py="sm">
      <Text size="xs" c="dimmed">
        第 {(pager.page - 1) * pager.pageSize + 1}-{Math.min(pager.page * pager.pageSize, total)} 条，共 {total} 条
      </Text>
      <Pagination
        size="sm"
        total={pager.totalPages}
        value={pager.page}
        onChange={pager.setPage}
      />
    </Group>
  );
}

function usePagination<T>(items: T[], pageSize: number) {
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));

  useEffect(() => {
    setPage((current) => Math.min(current, totalPages));
  }, [totalPages]);

  const pageItems = useMemo(() => {
    const start = (page - 1) * pageSize;
    return items.slice(start, start + pageSize);
  }, [items, page, pageSize]);

  return { items: pageItems, page, pageSize, totalPages, setPage };
}

function Legend({ color, label }: { color: "red" | "teal" | "yellow"; label: string }) {
  return (
    <Group gap={6}>
      <span
        style={{
          width: 10,
          height: 10,
          background: `var(--mantine-color-${color}-5)`,
          display: "inline-block",
        }}
      />
      <Text size="xs" c="dimmed">{label}</Text>
    </Group>
  );
}

const CHART_H = 330;
const MIN_CHART_W = 420;

interface ChartGeometry {
  width: number;
  height: number;
  left: number;
  right: number;
  top: number;
  equityBase: number;
  bottom: number;
}

interface ChartPoint {
  x: number;
  equityY: number;
  benchmarkY: number | null;
  data: AccountSnapshot;
  benchmark: AccountBenchmarkPoint | null;
}

function chartGeometry(containerWidth: number): ChartGeometry {
  const width = Math.max(Math.floor(containerWidth || 0), MIN_CHART_W);
  const left = 54;
  const right = Math.max(left + 160, width - 70);
  return {
    width,
    height: CHART_H,
    left,
    right,
    top: 18,
    equityBase: 204,
    bottom: 296,
  };
}

function buildChart(
  data: AccountSnapshot[],
  benchmark: AccountBenchmarkPoint[],
  geom: ChartGeometry
) {
  const eqValues = data.map((d) => d.total_equity);
  const benchmarkByDate = new Map(benchmark.map((b) => [b.trade_date, b]));
  const benchmarkValues = data
    .map((d) => benchmarkByDate.get(d.trade_date)?.total_equity)
    .filter((v): v is number => typeof v === "number");
  const comparableValues = [...eqValues, ...benchmarkValues];
  const eqMinRaw = Math.min(...comparableValues, 0);
  const eqMaxRaw = Math.max(...comparableValues, 1);
  const eqPad = Math.max((eqMaxRaw - eqMinRaw) * 0.08, eqMaxRaw * 0.01, 1);
  const eqMin = eqMinRaw - eqPad;
  const eqMax = eqMaxRaw + eqPad;

  const points: ChartPoint[] = data.map((d, i) => {
    const x = geom.left + (data.length <= 1 ? 0 : (i / (data.length - 1)) * (geom.right - geom.left));
    const bench = benchmarkByDate.get(d.trade_date) || null;
    return {
      x,
      equityY: scale(d.total_equity, eqMin, eqMax, geom.equityBase, geom.top),
      benchmarkY: bench ? scale(bench.total_equity, eqMin, eqMax, geom.equityBase, geom.top) : null,
      data: d,
      benchmark: bench,
    };
  });
  const equityPath = linePath(points, "equityY");
  const benchmarkPath = optionalLinePath(points, "benchmarkY");
  return {
    points,
    equityPath,
    benchmarkPath,
    equityArea: areaPath(points, "equityY", geom.equityBase),
    equityTicks: ticks(eqMin, eqMax, 4).map((v) => ({
      value: v,
      y: scale(v, eqMin, eqMax, geom.equityBase, geom.top),
    })),
    dateTicks: dateTicks(points, geom),
    last: data[data.length - 1] || null,
  };
}

function scale(v: number, min: number, max: number, yMin: number, yMax: number) {
  const span = max - min || 1;
  return yMin - ((v - min) / span) * (yMin - yMax);
}

function linePath(points: ChartPoint[], key: "equityY") {
  if (!points.length) return "";
  return points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p[key].toFixed(1)}`).join(" ");
}

function optionalLinePath(points: ChartPoint[], key: "benchmarkY") {
  let path = "";
  let open = false;
  points.forEach((p) => {
    const y = p[key];
    if (y == null) {
      open = false;
      return;
    }
    path += `${open ? " L" : " M"} ${p.x.toFixed(1)} ${y.toFixed(1)}`;
    open = true;
  });
  return path.trim();
}

function areaPath(points: ChartPoint[], key: "equityY", baseY: number) {
  if (!points.length) return "";
  const first = points[0];
  const last = points[points.length - 1];
  return `${linePath(points, key)} L ${last.x.toFixed(1)} ${baseY} L ${first.x.toFixed(1)} ${baseY} Z`;
}

function ticks(min: number, max: number, count: number) {
  if (count <= 1) return [min];
  const step = (max - min) / (count - 1 || 1);
  return Array.from({ length: count }, (_, i) => min + step * i);
}

function dateTicks(points: ChartPoint[], geom: ChartGeometry) {
  if (!points.length) return [];
  const count = Math.min(Math.max(Math.floor((geom.right - geom.left) / 135), 2), 8, points.length);
  return Array.from({ length: count }, (_, i) => {
    const idx = count === 1 ? 0 : Math.round((i / (count - 1)) * (points.length - 1));
    const p = points[idx];
    return { x: p.x, label: p.data.trade_date.slice(5) };
  });
}

function nearestIndex(points: ChartPoint[], x: number) {
  let idx = 0;
  let best = Infinity;
  points.forEach((p, i) => {
    const d = Math.abs(p.x - x);
    if (d < best) {
      best = d;
      idx = i;
    }
  });
  return idx;
}

function fmtCompactMoney(v: number) {
  if (Math.abs(v) >= 10000) return `${(v / 10000).toFixed(1)}万`;
  return v.toFixed(0);
}

function fmtMoney(v: number | null | undefined) {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}
