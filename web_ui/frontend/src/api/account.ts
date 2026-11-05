// 账户回测 API 类型与 fetch 封装（字段对齐 dragon_quant/review_account/storage.py）。

// 前端固定 v2：请求层补 source，页面不感知数据版本。
const ACCOUNT_SOURCE = "v2" as const;

export interface AccountRun {
  id: number;
  source: "v1" | "v2";
  display_name: string | null;
  strategy_name: string;
  strategy_params: Record<string, unknown>;
  date_from: string;
  date_to: string;
  initial_cash: number;
  /** 增量回测的收益率/指数曲线起点（基准最后收盘权益）；全量 run 为 null。 */
  base_equity: number | null;
  final_equity: number;
  total_return: number;
  max_drawdown: number;
  trade_count: number;
  win_rate: number | null;
  created_at: string;
  /** 链式增量：基准 run id（全量 run 为 null）。 */
  base_run_id?: number | null;
  /** 本段起点权益（增量=基准最后收盘权益；全量=初始资金）。 */
  start_equity?: number | null;
  start_positions?: AccountPositionSeed[];
  start_realized_pnl?: number | null;
  /** 运行质量/候选池/降级与版本元数据；旧 run 为空对象（unknown）。 */
  run_meta?: Record<string, unknown>;
}

export interface AccountPositionSeed {
  code: string;
  qty: number;
  cost: number;
  entry_price: number;
  entry_date: string;
  mark?: number | null;
  realized_pnl?: number;
}

export interface CreateAccountRunInput {
  name: string;
  date_from: string;
  date_to: string;
  initial_cash?: number;
  strategy_name?: string;
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
  post_exit_max_return: number | null;
  post_exit_max_drawdown: number | null;
  post_exit_window_days: number | null;
  /** 精确金额与跨段归因（旧 run 为 null / false）。 */
  initial_cost?: number | null;
  realized_pnl?: number | null;
  period_pnl?: number | null;
  period_start_value?: number | null;
  inherited?: boolean;
}

export interface EntryReasonStatRow {
  reason_code: string;
  label: string;
  trades: number;
  wins: number;
  win_rate: number | null;
  avg_return: number | null;
  contribution: number | null;
  period_pnl?: number | null;
  codes?: number;
  has_open?: boolean;
}

export interface AccountPositionStats {
  initial_cash: number;
  /** 本段贡献分母（本段起点权益）。 */
  start_equity?: number;
  total_trades: number;
  /** period_pnl = 精确金额归因；legacy_approximation = 旧数据近似。 */
  contribution_basis?: "period_pnl" | "legacy_approximation" | "unknown";
  precise_attribution_count?: number;
  period_complete?: boolean;
  unknown_rows?: number;
  reconciliation?: {
    diff: number | null;
    within_tolerance: boolean;
    tolerance: number;
  } | null;
  rows: EntryReasonStatRow[];
}

export interface AccountSummary {
  run_id: number | null;
  start_equity: number | null;
  final_equity: number | null;
  initial_cash: number | null;
  total_return: number | null;
  max_drawdown: number | null;
  sharpe: number | null;
  trade_count: number | null;
  closed_count: number;
  open_count: number;
  win_rate: number | null;
  wins: number;
  losses: number;
  avg_win_pct: number | null;
  avg_loss_pct: number | null;
  net_profit_ratio: number | null;
  expectancy_amount: number | null;
  fees: number;
  realized_amount: number;
  unrealized_amount: number | null;
  unrealized_basis?: "mark" | "unknown";
  largest_loss_amount: number | null;
  /** period_pnl = 精确金额归因；legacy_approximation = 旧数据近似；unknown = 旧数据缺期初估值。 */
  contribution_basis: "period_pnl" | "legacy_approximation" | "unknown";
  data_quality: string | null;
  run_meta: Record<string, unknown>;
}

export async function fetchAccountRuns(): Promise<{ data: AccountRun[]; count: number }> {
  const params = new URLSearchParams({ source: ACCOUNT_SOURCE });
  const res = await fetch("/api/account/runs?" + params.toString());
  if (!res.ok) throw new Error(`account runs ${res.status}`);
  return res.json();
}

export async function createAccountRun(
  input: CreateAccountRunInput
): Promise<{ data: AccountRun; run_id: number }> {
  const res = await fetch("/api/account/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...input, source: ACCOUNT_SOURCE }),
  });
  if (!res.ok) {
    let message = `account run create ${res.status}`;
    try {
      const body = await res.json();
      if (body?.error) message = String(body.error);
    } catch {
      // keep the status-based fallback
    }
    throw new Error(message);
  }
  return res.json();
}

export async function createIncrementalAccountRun(
  runId: number
): Promise<{ data: AccountRun; run_id: number }> {
  const res = await fetch("/api/account/runs/incremental", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId }),
  });
  if (!res.ok) {
    let message = `account run incremental ${res.status}`;
    try {
      const body = await res.json();
      if (body?.error) message = String(body.error);
    } catch {
      // keep the status-based fallback
    }
    throw new Error(message);
  }
  return res.json();
}

export async function deleteAccountRun(
  runId: number
): Promise<{ deleted: boolean; run_id: number }> {
  const params = new URLSearchParams({ run_id: String(runId) });
  const res = await fetch("/api/account/runs?" + params.toString(), {
    method: "DELETE",
  });
  if (!res.ok) {
    let message = `account run delete ${res.status}`;
    try {
      const body = await res.json();
      if (body?.error) message = String(body.error);
    } catch {
      // keep the status-based fallback
    }
    throw new Error(message);
  }
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

export async function fetchAccountPositions(
  runId: number
): Promise<{ data: AccountPosition[]; stats: AccountPositionStats }> {
  const params = new URLSearchParams({ run_id: String(runId) });
  const res = await fetch("/api/account/positions?" + params.toString());
  if (!res.ok) throw new Error(`account positions ${res.status}`);
  return res.json();
}

export async function fetchAccountSummary(
  runId: number
): Promise<{ data: AccountSummary; run: AccountRun }> {
  const params = new URLSearchParams({ run_id: String(runId) });
  const res = await fetch("/api/account/summary?" + params.toString());
  if (!res.ok) throw new Error(`account summary ${res.status}`);
  return res.json();
}

export interface AccountExperiment {
  id: number;
  base_run_id: number;
  created_at: string;
  experiment_version: string;
  status: string | null;
  request: Record<string, unknown>;
  report: Record<string, unknown>;
}

export async function fetchAccountExperiments(
  runId: number
): Promise<{ data: AccountExperiment[] }> {
  const params = new URLSearchParams({ run_id: String(runId) });
  const res = await fetch("/api/account/experiments?" + params.toString());
  if (!res.ok) throw new Error(`account experiments ${res.status}`);
  return res.json();
}

export async function createAccountExperiment(
  runId: number,
  sellRules: string[] = []
): Promise<{ data: Record<string, unknown> }> {
  const res = await fetch("/api/account/experiments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId, sell_rules: sellRules, confirm: true }),
  });
  if (!res.ok) {
    let message = `account experiments ${res.status}`;
    try {
      const body = await res.json();
      if (body?.error) message = String(body.error);
    } catch {
      // keep the status-based fallback
    }
    throw new Error(message);
  }
  return res.json();
}
