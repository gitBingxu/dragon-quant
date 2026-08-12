// 后端 API 类型定义与 fetch 封装。字段对齐 dragon_quant/storage/db.py。

export interface Dragon {
  source: "v1" | "v2";
  trade_date: string;
  code: string;
  name: string;
  scan_id: string;
  rank: number | null;
  composite_score: number | null;
  board_count: number | null;
  open_px: number | null;
  close_px: number | null;
  high_px: number | null;
  low_px: number | null;
  pct: number | null;
  turnover_rate: number | null;
  amount: number | null;
  market_cap: number | null;
  concepts: string[];
  report_text: string;
  is_true_dragon: boolean | null;
  buy_date: string | null;
  buy_price: number | null;
  max_return_5d: number | null;
  max_drawdown_5d: number | null;
  max_return_hold_days: number | null;
  review_status: string | null;
  version: string;
}

export interface Summary {
  source: "v1" | "v2";
  total: number;
  completed: number;
  pending: number;
  avg_return: number | null;
  win_rate: number | null;
  best_stock_code: string | null;
  best_stock_name: string | null;
  best_return: number | null;
}

export interface DragonFilters {
  source?: "v1" | "v2";
  code?: string;
  name?: string;
  date_from?: string;
  date_to?: string;
  score_min?: string;
  score_max?: string;
  return_min?: string;
  return_max?: string;
  drawdown_min?: string;
  drawdown_max?: string;
  version_min?: string;
  version_max?: string;
  status?: string;
  sort_by: string;
  sort_dir: string;
}

export async function fetchSummary(source: "v1" | "v2" = "v2"): Promise<Summary> {
  const params = new URLSearchParams({ source });
  const res = await fetch("/api/summary?" + params.toString());
  if (!res.ok) throw new Error(`summary ${res.status}`);
  return res.json();
}

export async function fetchDragons(
  filters: DragonFilters
): Promise<{ data: Dragon[]; count: number }> {
  const params = new URLSearchParams();
  const keys: (keyof DragonFilters)[] = [
    "source",
    "code",
    "name",
    "date_from",
    "date_to",
    "score_min",
    "score_max",
    "return_min",
    "return_max",
    "drawdown_min",
    "drawdown_max",
    "version_min",
    "version_max",
    "status",
    "sort_by",
    "sort_dir",
  ];
  for (const k of keys) {
    const v = filters[k];
    if (v != null && String(v).trim() !== "") params.set(k, String(v).trim());
  }
  const res = await fetch("/api/dragons?" + params.toString());
  if (!res.ok) throw new Error(`dragons ${res.status}`);
  return res.json();
}

export interface AccountRun {
  id: number;
  source: "v1" | "v2";
  strategy_name: string;
  strategy_params: Record<string, unknown>;
  date_from: string;
  date_to: string;
  initial_cash: number;
  final_equity: number;
  total_return: number;
  max_drawdown: number;
  trade_count: number;
  win_rate: number | null;
  created_at: string;
}

export interface AccountSnapshot {
  trade_date: string;
  cash: number;
  market_value: number;
  total_equity: number;
  daily_return: number;
  cumulative_return: number;
  drawdown: number;
  position_code: string;
  position_name: string;
  position_qty: number;
  position_cost: number | null;
  position_market_price: number | null;
  position_unrealized_return: number | null;
}

export interface AccountBenchmarkPoint {
  trade_date: string;
  close: number;
  total_equity: number;
  cumulative_return: number;
}

export interface AccountTrade {
  trade_date: string;
  code: string;
  name: string;
  side: "BUY" | "SELL";
  price: number;
  qty: number;
  amount: number;
  fee: number;
  realized_pnl: number | null;
  cash_after: number;
  position_after: number;
  reason_code: string;
  reason_text: string;
  signal: Record<string, unknown>;
}

export interface AccountTimelineEvent {
  event_date: string;
  event_type: "BUY" | "SELL" | "HOLD" | "IDLE";
  code: string;
  name: string;
  title: string;
  detail: string;
  reason_code: string;
  cash: number | null;
  total_equity: number | null;
  signal: Record<string, unknown>;
}

export interface AccountPosition {
  code: string;
  name: string;
  entry_date: string;
  entry_price: number;
  qty: number;
  entry_reason_code: string;
  entry_signal: Record<string, unknown>;
  exit_date: string;
  exit_price: number;
  exit_reason_code: string;
  exit_signal: Record<string, unknown>;
  realized_return: number;
  hold_days: number;
  status: string;
}

export async function fetchAccountRuns(
  source: "v1" | "v2" = "v2"
): Promise<{ data: AccountRun[]; count: number }> {
  const params = new URLSearchParams({ source });
  const res = await fetch("/api/account/runs?" + params.toString());
  if (!res.ok) throw new Error(`account runs ${res.status}`);
  return res.json();
}

export async function fetchAccountSnapshots(runId: number): Promise<{ data: AccountSnapshot[] }> {
  const params = new URLSearchParams({ run_id: String(runId) });
  const res = await fetch("/api/account/snapshots?" + params.toString());
  if (!res.ok) throw new Error(`account snapshots ${res.status}`);
  return res.json();
}

export async function fetchAccountBenchmark(
  runId: number,
  code = "SH000001"
): Promise<{ data: AccountBenchmarkPoint[]; code: string; name: string }> {
  const params = new URLSearchParams({ run_id: String(runId), code });
  const res = await fetch("/api/account/benchmark?" + params.toString());
  if (!res.ok) throw new Error(`account benchmark ${res.status}`);
  return res.json();
}

export async function fetchAccountTrades(runId: number): Promise<{ data: AccountTrade[] }> {
  const params = new URLSearchParams({ run_id: String(runId) });
  const res = await fetch("/api/account/trades?" + params.toString());
  if (!res.ok) throw new Error(`account trades ${res.status}`);
  return res.json();
}

export async function fetchAccountEvents(runId: number): Promise<{ data: AccountTimelineEvent[] }> {
  const params = new URLSearchParams({ run_id: String(runId) });
  const res = await fetch("/api/account/events?" + params.toString());
  if (!res.ok) throw new Error(`account events ${res.status}`);
  return res.json();
}

export async function fetchAccountPositions(runId: number): Promise<{ data: AccountPosition[] }> {
  const params = new URLSearchParams({ run_id: String(runId) });
  const res = await fetch("/api/account/positions?" + params.toString());
  if (!res.ok) throw new Error(`account positions ${res.status}`);
  return res.json();
}
