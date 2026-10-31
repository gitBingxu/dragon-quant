import { getJson, qs } from "./client";

export interface PrepAssetStatus {
  asset_type: string;
  present: boolean;
  status: string | null;
  note: string | null;
  error: string | null;
  as_of: string | null;
  usable_display: boolean;
}

export interface PrepCollectionReport {
  asset_type: string;
  status: string | null;
  note: string | null;
  error: string | null;
}

export interface PrepWatermark {
  trade_date: string;
  available: boolean;
  assets: PrepAssetStatus[];
  collection_report: PrepCollectionReport[];
  missing: string[];
  degraded: string[];
  has_snapshot: boolean;
}

export interface PrepSnapshotView {
  trade_date: string;
  version: string | null;
  created_at: string | null;
  blocks_present: string[];
  sentiment: Record<string, unknown> | null;
  main_boards: Array<Record<string, unknown>> | null;
  macro: Record<string, unknown> | null;
  dragon_source: Record<string, unknown> | null;
  candidates: Array<Record<string, unknown>> | null;
  review_candidates: Array<Record<string, unknown>> | null;
  review: Record<string, unknown> | null;
  feedback: Record<string, unknown> | null;
  plan_review: Record<string, unknown> | null;
  slim: boolean;
  raw_size_bytes: number;
}

export interface AuctionSnapshotRow {
  code: string;
  version: string | null;
  is_weak: boolean | null;
  strength: number | null;
  level: string | null;
  direction: string | null;
  rank: number | null;
  validation_status: string | null;
  data_quality: Record<string, unknown> | null;
  risk_events: unknown[] | null;
  reject_reason: string | null;
  error: string | null;
}

export interface AuctionDayView {
  trade_date: string;
  available: boolean;
  count: number;
  snapshots: AuctionSnapshotRow[];
  summary: Record<string, number>;
  assets_count: number | null;
}

export function fetchPrepDates(limit = 30) {
  return getJson<{ asset_dates: string[]; snapshot_dates: string[]; count: number }>(
    `/api/prep/dates${qs({ limit })}`
  );
}

export function fetchPrepWatermark(tradeDate: string) {
  return getJson<PrepWatermark>(`/api/prep/${encodeURIComponent(tradeDate)}`);
}

export function fetchPrepSnapshot(tradeDate: string, slim = true) {
  return getJson<PrepSnapshotView>(
    `/api/prep/${encodeURIComponent(tradeDate)}/snapshot${qs({ slim })}`
  );
}

export function fetchAuctionDates(limit = 30) {
  return getJson<{ snapshot_dates: string[]; asset_dates: string[]; count: number }>(
    `/api/auction/dates${qs({ limit })}`
  );
}

export function fetchAuctionDay(tradeDate: string) {
  return getJson<AuctionDayView>(`/api/auction/${encodeURIComponent(tradeDate)}`);
}
