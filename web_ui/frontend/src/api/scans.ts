import { getJson, qs } from "./client";

export interface ScanSummary {
  id: string;
  scan_date: string;
  elapsed_s: number | null;
  top_n: number | null;
  candidates_n: number | null;
  workers: number | null;
  created_at: string;
  source: string;
}

export interface ScanDetail extends ScanSummary {
  has_raw_output: boolean;
  raw_output_bytes: number | null;
}

export interface Dims {
  drive: number | null;
  leadership: number | null;
  anti_drop: number | null;
  liquidity: number | null;
  absorption: number | null;
}

export interface ScanStock {
  code: string;
  name: string;
  rank: number | null;
  composite_score: number | null;
  board_count: number | null;
  concepts: string[];
  dims: Dims;
  is_true_dragon: boolean | null;
  reject_reason: string | null;
  report_text: string;
}

export interface ScanStocksPage {
  data: ScanStock[];
  count: number;
  total: number;
  truncated: boolean;
}

export function fetchScans(limit = 50) {
  return getJson<{ data: ScanSummary[]; count: number }>(`/api/scans${qs({ limit })}`);
}

export function fetchScan(scanId: string) {
  return getJson<ScanDetail>(`/api/scans/${encodeURIComponent(scanId)}`);
}

export function fetchScanStocks(
  scanId: string,
  opts: { trueOnly?: boolean; limit?: number; offset?: number } = {}
) {
  return getJson<ScanStocksPage>(
    `/api/scans/${encodeURIComponent(scanId)}/stocks` +
      qs({ true_only: opts.trueOnly ?? false, limit: opts.limit ?? 200, offset: opts.offset ?? 0 })
  );
}
