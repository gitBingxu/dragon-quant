import { getJson, qs } from "./client";

// ─── prep ──────────────────────────────────────────────────────────────────

export interface PrepAssetStatus {
  asset_type: string;
  present: boolean;
  status: string | null;
  note: string | null;
  error: string | null;
  as_of: string | null;
  usable_display: boolean;
  category: "core" | "same_day_only" | "review_conditional" | "auxiliary" | "report";
  expected: boolean;
  legal_absence: string | null;
  asset_complete: boolean;
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
  has_prior_snapshot: boolean;
  first_record_day: boolean;
  expected_ok: number;
  expected_total: number;
}

export interface ExemptAsset {
  asset_type: string;
  reason: string;
}

export interface PrepCalendarDay {
  trade_date: string;
  status: "complete" | "incomplete" | "missing";
  has_snapshot: boolean;
  has_assets: boolean;
  asset_count: number;
  expected_ok: number;
  expected_total: number;
  expected_missing: string[];
  expected_degraded: string[];
  core_ok: number;
  core_total: number;
  exempt: ExemptAsset[];
  report_error: string | null;
}

export type DefaultDateSource =
  | "local_bar_previous"
  | "local_bar_latest"
  | "record_date_fallback"
  | "today_fallback";

export type CalendarBasis =
  | "local_stock_daily_bars_union"
  | "record_dates";

export interface CalendarDefaults {
  /** 后端按本地日 K 计算的默认日期；URL `?date=` 优先。 */
  default_date: string;
  default_date_source: DefaultDateSource;
  calendar_basis: CalendarBasis;
  calendar_latest_date: string | null;
  degraded: boolean;
  degraded_reason: string | null;
}

export interface PrepCalendar extends CalendarDefaults {
  available: boolean;
  start_date: string;
  end_date: string;
  latest_record_date: string | null;
  days: PrepCalendarDay[];
}

export interface MainlineDerived {
  source: "review" | "legacy";
  index: number;
  board_name: string | null;
  stored_high_num: number | null;
  board_height: number | null;
  needs_recompute: boolean;
  evidence: string[];
}

export interface MarketDerived {
  source: "review" | "sentiment";
  stored_max_board_height: number | null;
  max_board_height: number | null;
  needs_recompute: boolean;
  evidence: string[];
}

export interface PrepDerived {
  mainlines: MainlineDerived[];
  market: MarketDerived;
  needs_recompute: boolean;
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
  derived: PrepDerived;
}

export interface MarketSide {
  available: boolean;
  trade_date: string | null;
  state: string | null;
  score: number | null;
  coeff: number | null;
  position_advice: string | null;
  position_pct: number | null;
}

export type VerificationConclusion = "correct" | "wrong" | "neutral" | "not_evaluable";
export type PlanAdvice = "empty" | "hold";

export interface VerificationEvidence {
  prev_state: string | null;
  current_state: string | null;
  prev_score: number | null;
  current_score: number | null;
  score_change: number | null;
  limit_up_cnt: number | null;
  limit_down_cnt: number | null;
  broken_rate: number | null;
  yesterday_premium_pct: number | null;
}

export interface MarketVerification {
  conclusion: VerificationConclusion;
  reason: string | null;
  advice: PlanAdvice | null;
  prev_trade_date: string | null;
  current_trade_date: string | null;
  evidence: VerificationEvidence;
}

export interface MarketComparison {
  prev: MarketSide;
  current: MarketSide;
  score_change: number | null;
  verification: MarketVerification;
}

export interface MainlineDelta {
  index: number;
  board_name: string | null;
  rank: number | null;
  prev_rank: number | null;
  prev_score: number | null;
  rank_delta: number | null;
  score_delta: number | null;
  state: string | null;
  unknown_reason: string | null;
}

export interface PlanName {
  code: string;
  name: string | null;
  source: "prev_plan_asset" | "prev_snapshot_context" | "current_snapshot" | "missing";
}

export interface PlanEffectItem {
  code: string;
  name: string | null;
  planned_action: string | null;
  planned_position: number | null;
  feedback_state: string;
  outcome_pct: number | null;
  actual_position: number | null;
  execution_known: boolean;
  unknown_reason: string | null;
}

export interface NoTradeItem {
  code: string;
  name: string | null;
  judgement: "correct_avoidance" | "missed_opportunity" | "data_gap";
  outcome_pct: number | null;
}

export interface PlanGroupOutcome {
  key: "recommended" | "not_recommended" | "unknown";
  count: number;
  with_outcome: number;
  mean_outcome_pct: number | null;
}

export interface PlanEffect {
  available: boolean;
  reason: string | null;
  plan_date: string | null;
  eval_date: string | null;
  checkpoint: string | null;
  items: PlanEffectItem[];
  no_trade: NoTradeItem[];
  groups: PlanGroupOutcome[];
  market: MarketComparison;
  degraded: string[];
}

export interface ReviewContext {
  trade_date: string;
  available: boolean;
  reason: string | null;
  prev_trade_date: string | null;
  prev_meta_error: string | null;
  mainlines: MainlineDelta[];
  plan_names: PlanName[];
  plan_effect: PlanEffect;
  degraded: string[];
}

export interface PlanFollowupCell {
  entry_date: string | null;
  exit_date: string | null;
  net_return_pct: number | null;
  outcome: "up" | "down" | "flat" | null;
  exclusion_reason: string | null;
}

export interface PlanFollowupRow {
  code: string;
  name: string | null;
  group: "recommended" | "not_recommended" | "data_insufficient";
  score: number | null;
  level: string | null;
  horizons: Record<string, PlanFollowupCell | undefined>;
}

export interface PlanFollowupGroup {
  group: PlanFollowupRow["group"];
  label: string;
  valid: number;
  excluded: number;
  mean_net_return_pct: number | null;
  median_net_return_pct: number | null;
}

export interface PlanFollowupHorizon {
  horizon: number;
  groups: PlanFollowupGroup[];
  spread: number | null;
}

export interface PlanFollowup {
  status: "ok" | "insufficient_data";
  reason: string | null;
  trade_date: string;
  plan_date: string | null;
  plan_date_source: "plan_review" | "previous_snapshot" | null;
  data_source: "local_stock_daily";
  cost_bps: number;
  horizons: number[];
  rows: PlanFollowupRow[];
  summary: PlanFollowupHorizon[];
  local_coverage: {
    plan_codes: number;
    loaded_codes: number;
    codes_with_bars: number;
    bar_dates: number;
    truncated: boolean;
  };
  warnings: string[];
  caveats: string[];
}

// ─── auction ───────────────────────────────────────────────────────────────

export interface AuctionCalendarDay {
  trade_date: string;
  status: "complete" | "incomplete" | "missing";
  has_snapshot: boolean;
  has_assets: boolean;
  snapshot_count: number;
  asset_count: number;
  snapshot_only_codes: string[];
  asset_only_codes: string[];
}

export interface AuctionCalendar extends CalendarDefaults {
  available: boolean;
  start_date: string;
  end_date: string;
  latest_record_date: string | null;
  days: AuctionCalendarDay[];
}

// ─── 全历史判断准确率（prep / auction 共用形状） ─────────────────────────

export interface AccuracyWindow {
  entry_offset: number;
  exit_offset: number;
  entry_price: string;
  exit_price: string;
  cost_bps: number;
}

export interface AccuracySummary {
  correct: number;
  valid: number;
  direction_valid?: number;
  neutral?: number;
  excluded: number;
  hit_rate: number | null;
  direction_denominator?: string;
  primary_horizon?: number;
  excluded_by_reason: Record<string, number>;
}

export interface AccuracyGroup {
  samples: number;
  stocks: number;
  days: number;
  mean_return_pct: number | null;
  median_return_pct: number | null;
  date_equal_mean_return_pct: number | null;
  wins: number;
  losses: number;
  flats: number;
  win_rate: number | null;
  avoided_loss_rate: number | null;
  missed_gain_rate: number | null;
}

export interface AccuracyHorizon {
  horizon: number;
  valid: number;
  direction_valid: number;
  neutral: number;
  correct: number;
  hit_rate: number | null;
  excluded_by_reason: Record<string, number>;
  groups: Record<string, AccuracyGroup>;
  spread: Record<string, unknown> | null;
  independent_dates: number;
}

export interface AccuracyStock {
  code: string;
  name: string | null;
  correct: number;
  valid: number;
  excluded: number;
  hit_rate: number | null;
}

export interface AccuracyResponse {
  status: "ok" | "insufficient_data";
  reason: string | null;
  /** 最新可验证日：其后仍有完整窗口交易日。 */
  as_of: string | null;
  latest_bar_date: string | null;
  snapshot_count: number;
  data_source: "local_stock_daily";
  calendar_basis: string;
  sample_unit: "stock_day";
  window: AccuracyWindow;
  summary: AccuracySummary;
  groups?: Record<string, AccuracyGroup>;
  spread?: Record<string, unknown> | null;
  horizons?: Record<string, AccuracyHorizon>;
  independent_dates?: number;
  independent_codes?: number;
  versions_seen?: Record<string, number>;
  axes?: Record<string, number>;
  stratification?: Record<string, unknown>;
  contract?: Record<string, unknown>;
  market_day_effect?: Record<string, unknown>;
  day_kind_counts: Record<string, number>;
  stocks: AccuracyStock[];
  excluded_snapshot_dates: Record<string, number>;
  warnings: string[];
  caveats: string[];
}

// ─── prep 当前选中日效果（主线次日 + 严格逐股） ───────────────────────────

export interface EffectBoardRow {
  rank: number | null;
  board_name: string | null;
  category: string | null;
  is_mainline: boolean;
  board_code: string | null;
  t_change_pct: number | null;
  next_change_pct: number | null;
  match: string | null;
  exclusion_reason: string | null;
  warnings: string[];
}

export interface EffectBoardSummary {
  evaluated: number;
  missing: number;
  mean_change_pct: number | null;
}

export interface EffectBoards {
  available: boolean;
  reason: string | null;
  source: string | null;
  rows: EffectBoardRow[];
  summary: EffectBoardSummary;
}

export type EffectStockGroup =
  | "recommended"
  | "not_recommended"
  | "data_insufficient";
export type EffectStockStatus = "completed" | "cannot_evaluate" | "pending";

export interface EffectStockRow {
  code: string;
  name: string | null;
  group: EffectStockGroup;
  role: string | null;
  review_score: number | null;
  eligibility: string | null;
  position_pct: number | null;
  status: EffectStockStatus;
  buy_date: string | null;
  buy_price: number | null;
  max_return_5d: number | null;
  max_drawdown_5d: number | null;
  max_return_hold_days: number | null;
  partial_window: boolean;
  reason: string | null;
}

export interface EffectGroupSummary {
  group: string;
  label: string;
  valid: number;
  partial?: number;
  cannot_evaluate: number;
  pending: number;
  mean_max_return_5d: number | null;
  median_max_return_5d: number | null;
  mean_max_drawdown_5d: number | null;
  median_max_drawdown_5d: number | null;
}

export interface EffectStocks {
  available: boolean;
  reason: string | null;
  rows: EffectStockRow[];
  summary: EffectGroupSummary[];
}

export interface PrepEffect {
  status: "ok" | "insufficient_data";
  reason: string | null;
  trade_date: string;
  next_trade_date: string | null;
  snapshot_version: string | null;
  data_source: "local_prep_assets+local_stock_daily";
  calendar_basis: string;
  boards: EffectBoards;
  stocks: EffectStocks;
  coverage: {
    boards_total: number;
    stocks_total: number;
    bar_dates: number;
  };
  warnings: string[];
  caveats: string[];
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
  name: string | null;
  collected: boolean;
  evaluated: boolean;
  collection_status: "ok" | "asset_only" | "snapshot_only" | "missing";
  key_evidence: Record<string, unknown> | null;
}

export interface AuctionRunView {
  snapshot_count: number;
  asset_count: number;
  snapshot_only_codes: string[];
  asset_only_codes: string[];
  quality_counts: Record<string, number>;
  error_rows: number;
  config_versions: string[];
  validation_statuses: Record<string, number>;
  created_at_min: string | null;
  created_at_max: string | null;
  evaluation_available: boolean;
}

export interface AuctionDayView {
  trade_date: string;
  available: boolean;
  count: number;
  snapshots: AuctionSnapshotRow[];
  summary: Record<string, number>;
  assets_count: number | null;
  run: AuctionRunView;
  names: Record<string, string>;
  asset_codes: string[];
}

export interface HorizonGroup {
  group: string;
  label: string;
  expected_direction: string | null;
  observations: number;
  valid: number;
  excluded: number;
  coverage: number | null;
  direction_evaluable: boolean;
  direction_hit_rate: number | null;
  mean_net_return_pct: number | null;
  median_net_return_pct: number | null;
  quality: "ok" | "low_confidence" | "not_evaluable";
  above_reference: boolean | null;
}

export interface HorizonEffect {
  horizon: number;
  groups: HorizonGroup[];
  spread: number | null;
}

export interface SelectedDayOutcome {
  code: string;
  horizon: number;
  entry_date: string | null;
  exit_date: string | null;
  net_return_pct: number | null;
  outcome: "up" | "down" | "flat" | null;
  exclusion_reason: string | null;
}

export interface LocalCoverage {
  snapshot_dates: number;
  window_codes: number;
  loaded_codes: number;
  selected_codes: number;
  codes_with_bars: number;
  bar_dates: number;
  truncated: boolean;
}

export interface AuctionEffect {
  status: "ok" | "insufficient_data";
  reason: string | null;
  date_from: string;
  date_to: string;
  data_source: "local_stock_daily";
  calendar_basis: string;
  cost_bps: number;
  horizons: HorizonEffect[];
  selected_day: SelectedDayOutcome[];
  local_coverage: LocalCoverage;
  warnings: string[];
  caveats: string[];
}

// ─── 请求 ──────────────────────────────────────────────────────────────────

export function fetchPrepDates(limit = 30) {
  return getJson<{ asset_dates: string[]; snapshot_dates: string[]; count: number }>(
    `/api/prep/dates${qs({ limit })}`
  );
}

export function fetchPrepCalendar(days = 365, end?: string) {
  return getJson<PrepCalendar>(`/api/prep/calendar${qs({ days, end })}`);
}

export function fetchPrepWatermark(tradeDate: string) {
  return getJson<PrepWatermark>(`/api/prep/${encodeURIComponent(tradeDate)}`);
}

export function fetchPrepSnapshot(tradeDate: string, slim = true) {
  return getJson<PrepSnapshotView>(
    `/api/prep/${encodeURIComponent(tradeDate)}/snapshot${qs({ slim })}`
  );
}

export function fetchReviewContext(tradeDate: string) {
  return getJson<ReviewContext>(
    `/api/prep/${encodeURIComponent(tradeDate)}/review-context`
  );
}

export function fetchPlanFollowup(tradeDate: string, horizons = "1,3,5") {
  return getJson<PlanFollowup>(
    `/api/prep/${encodeURIComponent(tradeDate)}/plan-followup${qs({ horizons })}`
  );
}

/** 当前选中日效果：主线次日涨跌 + 严格逐股收益/回撤。 */
export function fetchPrepEffect(tradeDate: string) {
  return getJson<PrepEffect>(
    `/api/prep/${encodeURIComponent(tradeDate)}/effect`
  );
}

/** 全历史判断准确率：固定截至最新可验证日，不随选中日变化。 */
export function fetchPrepAccuracy() {
  return getJson<AccuracyResponse>("/api/prep/accuracy");
}

export function fetchAuctionDates(limit = 30) {
  return getJson<{ snapshot_dates: string[]; asset_dates: string[]; count: number }>(
    `/api/auction/dates${qs({ limit })}`
  );
}

export function fetchAuctionCalendar(days = 365, end?: string) {
  return getJson<AuctionCalendar>(`/api/auction/calendar${qs({ days, end })}`);
}

export function fetchAuctionDay(tradeDate: string) {
  return getJson<AuctionDayView>(`/api/auction/${encodeURIComponent(tradeDate)}`);
}

export function fetchAuctionEffect(tradeDate: string, window = 20) {
  return getJson<AuctionEffect>(
    `/api/auction/${encodeURIComponent(tradeDate)}/effect${qs({ window })}`
  );
}

/** 竞价全历史判断准确率：固定截至最新可验证日，不随选中日变化。 */
export function fetchAuctionAccuracy() {
  return getJson<AccuracyResponse>("/api/auction/accuracy");
}
