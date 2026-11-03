import { getJson, postJson, qs } from "./client";

export interface BsRunCounters {
  rows_scanned: number;
  rows_skipped_terminal: number;
  rows_evaluated: number;
  rows_no_signal: number;
  rows_bought: number;
  rows_sold: number;
  rows_partial: number;
  rows_error: number;
}

export interface BsRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  range_from: string | null;
  range_to: string | null;
  limit_n: number | null;
  force: boolean;
  evaluator_version: string;
  strategy_name: string;
  strategy_params: Record<string, unknown>;
  strategy_hash: string;
  counters: BsRunCounters;
  note: string | null;
}

export interface BuySignalDoc {
  code: string;
  label: string;
  window: string;
  summary: string;
  details: string[];
}

export interface SellRuleDoc {
  name: string;
  label: string;
  default_enabled: boolean;
  summary: string;
  trigger: string;
}

export interface BsThresholds {
  buy_window_days: number;
  window_skip: number;
  window_size: number;
  buy_win_return_gt: number;
  buy_win_drawdown_gt: number;
  sell_good_drawdown_le: number;
  sell_fly_return_ge: number;
  max_replay_trading_days: number;
}

export interface BsStrategy {
  version: string;
  buy_signals: BuySignalDoc[];
  sell_rules: SellRuleDoc[];
  thresholds: BsThresholds;
}

export interface BuySideStats {
  samples: number;
  wins: number;
  win_rate: number | null;
  avg_return: number | null;
  avg_drawdown: number | null;
}

export interface RuleBreakdown {
  rule: string;
  label: string;
  count: number;
}

export interface BsBuySummary {
  triggered: number;
  trigger_rate: number | null;
  strict: BuySideStats;
  loose: BuySideStats;
  rule_breakdown: RuleBreakdown[];
  samples: number;
  wins: number;
  win_rate: number | null;
  avg_return: number | null;
  avg_drawdown: number | null;
}

export interface BsSellSummary {
  triggered: number;
  samples: number;
  good: number;
  good_rate: number | null;
  flew: number;
  flew_rate: number | null;
  neutral: number;
  neutral_rate: number | null;
  avg_return: number | null;
  avg_drawdown: number | null;
  rule_breakdown: RuleBreakdown[];
}

export interface BsSummary {
  rows_total: number;
  status_counts: Record<string, number>;
  buy: Omit<BsBuySummary, "samples" | "wins" | "win_rate" | "avg_return" | "avg_drawdown"> & {
    trigger_rate: number | null;
  };
  sell: BsSellSummary;
}

export interface BsOverview {
  run: BsRun | null;
  strategy: BsStrategy;
  summary: BsSummary;
  stale_rows: number;
}

export interface BsBuyRow {
  trade_date: string;
  code: string;
  name: string;
  eval_status: string;
  eval_note: string | null;
  data_quality: string | null;
  buy_date: string | null;
  buy_rule_code: string | null;
  buy_rule_label: string | null;
  buy_loose_price: number | null;
  buy_strict_price: number | null;
  buy_loose_max_return_5d: number | null;
  buy_loose_max_drawdown_5d: number | null;
  buy_strict_max_return_5d: number | null;
  buy_strict_max_drawdown_5d: number | null;
  buy_max_return_hold_days: number | null;
  buy_window_partial: boolean;
  loose_win: boolean | null;
  strict_win: boolean | null;
  buy_data_quality: string | null;
}

export interface BsHalfExit {
  date: string;
  price: number | null;
  qty: number | null;
  rule_code: string | null;
  rule_label: string | null;
}

export interface BsSellRow {
  trade_date: string;
  code: string;
  name: string;
  eval_status: string;
  eval_note: string | null;
  data_quality: string | null;
  sell_date: string | null;
  sell_rule_code: string | null;
  sell_rule_label: string | null;
  sell_price: number | null;
  sell_hold_days: number | null;
  sell_max_return_5d: number | null;
  sell_max_drawdown_5d: number | null;
  sell_max_return_hold_days: number | null;
  sell_window_partial: boolean;
  verdict: string;
  verdict_label: string;
  half_exit: BsHalfExit | null;
  sell_data_quality: string | null;
}

export interface BsFilters {
  date_from?: string;
  date_to?: string;
  code?: string;
  name?: string;
  rule?: string;
  partial?: string;
  sort_by?: string;
  sort_dir?: string;
}

export type BsJobState = "idle" | "running" | "succeeded" | "failed";
export type BsJobMode = "incremental" | "force";
export type BsJobTrigger = "auto" | "manual";

export interface BsJobRun {
  mode: BsJobMode;
  trigger: BsJobTrigger;
  started_at: string;
  finished_at: string | null;
  exit_code: number | null;
  output_tail: string | null;
}

export interface BsJobStatus {
  state: BsJobState;
  autogen_enabled: boolean;
  last_run: BsJobRun | null;
}

export function fetchBsOverview() {
  return getJson<BsOverview>("/api/bs/overview");
}

export function fetchBsJobStatus() {
  return getJson<BsJobStatus>("/api/bs/jobs/status");
}

export function postBsRebuild() {
  return postJson<BsJobStatus>("/api/bs/rebuild", {});
}

export function fetchBsBuys(
  filters: BsFilters
): Promise<{ data: BsBuyRow[]; count: number; summary: BsBuySummary }> {
  return getJson(`/api/bs/buys${qs(filters as Record<string, string>)}`);
}

export function fetchBsSells(filters: BsFilters) {
  return getJson<{ data: BsSellRow[]; count: number }>(
    `/api/bs/sells${qs(filters as Record<string, string>)}`
  );
}
