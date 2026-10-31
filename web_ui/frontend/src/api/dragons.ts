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
