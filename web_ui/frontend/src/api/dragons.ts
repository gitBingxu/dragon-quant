import { getJson, qs } from "./client";

export interface DragonPreview {
  code: string;
  name: string;
  rank: number | null;
  composite_score: number | null;
  is_true_dragon: boolean | null;
}

export interface DragonDateEntry {
  trade_date: string;
  count: number;
  preview: DragonPreview[];
}

export interface Dims {
  drive: number | null;
  leadership: number | null;
  anti_drop: number | null;
  liquidity: number | null;
  absorption: number | null;
}

export interface ReviewFields {
  buy_date: string | null;
  buy_price: number | null;
  max_return_5d: number | null;
  max_drawdown_5d: number | null;
  max_return_hold_days: number | null;
  review_status: string | null;
}

export interface DragonDetail {
  trade_date: string;
  code: string;
  name: string;
  source: string;
  scan_id: string | null;
  rank: number | null;
  composite_score: number | null;
  board_count: number | null;
  concepts: string[];
  report_text: string;
  is_true_dragon: boolean | null;
  quote: Record<string, number | null>;
  review_loose: ReviewFields;
  review_strict: ReviewFields | null;
  derived_status: string | null;
  caliber: string;
  dims: Dims | null;
  dims_source: "scan_stocks" | "scan_stocks_missing" | "scan_missing";
  reject_reason: string | null;
}

export function fetchDragonTradeDates(source: string, limit = 10) {
  return getJson<{ data: DragonDateEntry[]; count: number }>(
    `/api/dragons/trade-dates${qs({ source, limit })}`
  );
}

export function fetchDragonDetail(tradeDate: string, code: string, source: string, caliber?: string) {
  return getJson<DragonDetail>(
    `/api/dragons/${encodeURIComponent(tradeDate)}/${encodeURIComponent(code)}` +
      qs({ source, caliber })
  );
}

// ─── 龙头总览（冻结的 /api/dragons 契约） ──────────────────────────────────

/** Review 口径：strict=严格（首个交易日开盘价买入），loose=宽松（旧口径）。 */
export type ReviewCaliber = "strict" | "loose";

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
  // 严格口径（入选后首个交易日按开盘价买入）；v1 为 null。
  strict_buy_date: string | null;
  strict_buy_price: number | null;
  strict_max_return_5d: number | null;
  strict_max_drawdown_5d: number | null;
  strict_max_return_hold_days: number | null;
  strict_review_status: string | null;
  strict_review_reason: string | null;
  strict_reviewed: boolean;
  // 服务端统一派生状态（completed / pending / pending_recompute /
  // cannot_evaluate / error）；v1 等于旧 review_status。
  derived_status?: string | null;
}

export interface Summary {
  source: "v1" | "v2";
  // 旧键：宽松口径兼容。
  total: number;
  completed: number;
  pending: number;
  avg_return: number | null;
  win_rate: number | null;
  best_stock_code: string | null;
  best_stock_name: string | null;
  best_return: number | null;
  // 双口径 / 重算进度。
  avg_return_strict: number | null;
  win_rate_loose: number | null;
  win_rate_strict: number | null;
  win_rate_loose_n: number;
  win_rate_strict_n: number;
  cannot_evaluate: number;
  pending_recompute: number;
  failed_recompute: number;
  best_stock_code_strict: string | null;
  best_stock_name_strict: string | null;
  best_return_strict: number | null;
  derived_completed?: number;
  derived_pending?: number;
}

export interface DragonFilters {
  source?: "v1" | "v2";
  review_caliber?: ReviewCaliber;
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

export async function fetchDragons(
  filters: DragonFilters
): Promise<{ data: Dragon[]; count: number; summary: Summary }> {
  const params = new URLSearchParams();
  const keys: (keyof DragonFilters)[] = [
    "source",
    "review_caliber",
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
