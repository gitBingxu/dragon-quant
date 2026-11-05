import { getJson, qs } from "./client";

export interface FactorSpecView {
  name: string;
  label: string | null;
  direction: string | null;
  unit: string | null;
  frequency: string | null;
  source: string | null;
  available_phase: string | null;
  pit_rule: string | null;
  lookback: number | null;
  enabled: boolean;
  depends_on: string[];
  valid_from: string | null;
  deprecated_at: string | null;
  description: string | null;
  code_version: string | null;
  code_version_source: string | null;
  db_version: string | null;
  db_registered: boolean;
  params: Record<string, unknown> | null;
  spec_hash: string | null;
}

export interface FactorValueRow {
  code: string;
  factor_name: string;
  value: number;
  available_at: string;
  source_scan_id: string | null;
}

export interface FactorCoverage {
  coverage: number | null;
  rows: number | null;
  status: string | null;
  warnings: string[];
}

export interface FactorDayView {
  trade_date: string;
  resolved_versions: Record<string, string>;
  missing_versions: string[];
  rows: FactorValueRow[];
  coverage: Record<string, FactorCoverage> | null;
  coverage_source: "build_run" | "build_run_missing";
  build_id: number | null;
}

export interface FactorRun {
  id: number;
  trade_date: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  ok: boolean | null;
  scan: Record<string, unknown> | null;
  warnings: string[];
  factors: Array<{ name: string; status: string | null; rows: number | null; coverage: number | null; reason: string | null }> | null;
  report_error: string | null;
}

export function fetchFactors(opts: { source?: string; enabledOnly?: boolean } = {}) {
  return getJson<{ data: FactorSpecView[]; count: number }>(
    `/api/factors${qs({ source: opts.source, enabled_only: opts.enabledOnly ?? false })}`
  );
}

export function fetchFactorValues(params: {
  tradeDate: string;
  name?: string;
  code?: string;
  version?: string;
}) {
  return getJson<FactorDayView>(
    `/api/factors/values${qs({
      trade_date: params.tradeDate,
      name: params.name,
      code: params.code,
      version: params.version,
    })}`
  );
}

export function fetchFactorRuns(params: { tradeDate?: string; limit?: number } = {}) {
  return getJson<{ data: FactorRun[]; count: number }>(
    `/api/factors/runs${qs({ trade_date: params.tradeDate, limit: params.limit ?? 20 })}`
  );
}
