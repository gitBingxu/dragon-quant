/**
 * 复盘快照展示投影（纯函数，无 React / 无网络）。
 *
 * `review` / `review_candidates` 优先，缺失时回退旧的 `sentiment` /
 * `main_boards` / `candidates`（Strategy：显式回退链，不把版本判断散落在 JSX）。
 * 空值一律返回 null，由 UI 显示「—」；不写 0/50 冒充。
 *
 * `high_num` 只消费服务端 `derived`（已解码 + 需重算标记），前端不再把原始
 * 打包值当板数渲染。
 */
import type {
  NoTradeItem,
  PlanFollowup,
  PlanFollowupRow,
  PrepSnapshotView,
  PrepWatermark,
  ReviewContext,
} from "../../api/assets";
import { UNKNOWN_REASON_LABELS, labelOf } from "../../lib/labels";

type Dict = Record<string, unknown>;

function asDict(value: unknown): Dict | null {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as Dict)
    : null;
}

function asList(value: unknown): Dict[] | null {
  return Array.isArray(value)
    ? value.filter((item): item is Dict => item != null && typeof item === "object")
    : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && !Number.isNaN(value) ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function bool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function textList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

// ─── 市场 ──────────────────────────────────────────────────────────────────

export interface MarketInputs {
  limitUpCnt: number | null;
  limitDownCnt: number | null;
  premiumPct: number | null;
  promotionRate: number | null;
  maxBoardHeight: number | null;
  brokenRate: number | null;
  totalAmountYi: number | null;
}

export interface MarketView {
  state: string | null;
  score: number | null;
  coeff: number | null;
  positionAdvice: string | null;
  positionPct: number | null;
  evidence: string[];
  warnings: string[];
  partial: boolean;
  unknownReason: string | null;
  maxBoardHeight: number | null;
  needsRecompute: boolean;
  inputs: MarketInputs;
}

function sentimentEvidence(sentiment: Dict, height: number | null): string[] {
  const evidence: string[] = [];
  const up = num(sentiment.limit_up_cnt);
  const down = num(sentiment.limit_down_cnt);
  if (up != null) {
    evidence.push(`涨停 ${up} 家${down != null ? ` / 跌停 ${down} 家` : ""}`);
  }
  const broken = num(sentiment.broken_rate);
  if (broken != null) evidence.push(`炸板率 ${(broken * 100).toFixed(1)}%`);
  if (height != null) evidence.push(`最高 ${height} 板`);
  const premium = num(sentiment.yesterday_premium_pct);
  if (premium != null) evidence.push(`昨日涨停溢价 ${premium > 0 ? "+" : ""}${premium}%`);
  const promotion = num(sentiment.promotion_rate);
  if (promotion != null) evidence.push(`晋级率 ${(promotion * 100).toFixed(1)}%`);
  return evidence;
}

export function buildMarketView(snapshot: PrepSnapshotView): MarketView | null {
  const review = asDict(snapshot.review);
  const market = asDict(review?.market);
  const sentiment = asDict(snapshot.sentiment);
  const derived = snapshot.derived?.market ?? null;
  if (!market && !sentiment) return null;

  const portfolio = asDict(review?.portfolio);
  const derivedEvidence = derived?.evidence ?? [];
  const reviewEvidence = market ? textList(market.evidence) : [];
  const fallbackEvidence = sentiment
    ? sentimentEvidence(sentiment, derived?.max_board_height ?? null)
    : [];
  // 服务端 derived 已把「最高 N 板」改写为展示板数；优先用它。
  const evidence = derivedEvidence.length
    ? derivedEvidence
    : reviewEvidence.length
      ? reviewEvidence
      : fallbackEvidence;

  // 单票 position_pct 与 sentiment.position_pct 同名不同义：后者是 0~1 的市场
  // 参考仓位，展示前转成百分比；review.market 若直接给百分比则原样使用。
  // 真实原始指标：review.market.inputs 优先，旧快照回退 sentiment；最高板只用 derived。
  const rawInputs = asDict(market?.inputs) ?? {};
  const sentimentRaw = sentiment ?? {};
  const ratioPct = (value: unknown): number | null => {
    const number = num(value);
    return number == null ? null : Math.round(number * 1000) / 10;
  };
  const totalAmount = num(rawInputs.total_amount) ?? num(sentimentRaw.total_amount);
  const inputs: MarketInputs = {
    limitUpCnt: num(rawInputs.limit_up_cnt) ?? num(sentimentRaw.limit_up_cnt),
    limitDownCnt: num(rawInputs.limit_down_cnt) ?? num(sentimentRaw.limit_down_cnt),
    premiumPct:
      num(rawInputs.yesterday_premium_pct) ?? num(sentimentRaw.yesterday_premium_pct),
    promotionRate:
      ratioPct(rawInputs.promotion_rate) ?? ratioPct(sentimentRaw.promotion_rate),
    maxBoardHeight: derived?.max_board_height ?? null,
    brokenRate: ratioPct(rawInputs.broken_rate) ?? ratioPct(sentimentRaw.broken_rate),
    totalAmountYi: totalAmount == null ? null : Math.round((totalAmount / 1e8) * 10) / 10,
  };

  const sentimentPosition = num(sentiment?.position_pct);
  const marketPosition = num(market?.position_pct);
  const positionPct =
    marketPosition ??
    (sentimentPosition != null
      ? Math.round(sentimentPosition * 1000) / 10
      : null);

  return {
    state: text(market?.state),
    score: num(market?.score) ?? num(sentiment?.temperature_score),
    coeff: num(market?.coeff),
    positionAdvice:
      text(market?.position_advice) ?? text(sentiment?.position_advice),
    positionPct,
    evidence,
    warnings: textList(portfolio?.warnings).concat(text(portfolio?.warning) ?? []),
    partial: bool(market?.partial) ?? false,
    unknownReason: text(market?.unknown_reason),
    maxBoardHeight: derived?.max_board_height ?? null,
    needsRecompute: bool(derived?.needs_recompute) ?? false,
    inputs,
  };
}

// ─── 主线（含跨日 delta / 需重算） ───────────────────────────────────────

export interface MainlineRow {
  index: number;
  rank: number | null;
  name: string | null;
  score: number | null;
  limitUpNum: number | null;
  continuousPlateNum: number | null;
  concentration: number | null;
  highBoard: number | null;
  highNeedsRecompute: boolean;
  state: string | null;
  evidence: string[];
  prevRank: number | null;
  prevScore: number | null;
  rankDelta: number | null;
  scoreDelta: number | null;
  unknownReason: string | null;
}

function snapshotMainlines(snapshot: PrepSnapshotView): Dict[] {
  const review = asDict(snapshot.review);
  const reviewed = asList(review?.mainlines);
  if (reviewed) return reviewed;
  return asList(snapshot.main_boards) ?? [];
}

export function buildMainlineRows(
  snapshot: PrepSnapshotView,
  context: ReviewContext | null
): MainlineRow[] {
  const rows = snapshotMainlines(snapshot);
  const derived = snapshot.derived?.mainlines ?? [];
  return rows.map((item, index) => {
    const server = derived[index];
    const delta = context?.mainlines?.[index];
    // 只消费服务端 derived：绝不把原始打包 high_num 当板数渲染。
    const highBoard = server?.board_height ?? null;
    return {
      index,
      rank: num(item.rank) ?? index + 1,
      name: text(item.board_name),
      score: num(item.mainline_score),
      limitUpNum: num(item.limit_up_num),
      continuousPlateNum: num(item.continuous_plate_num),
      concentration: num(item.concentration),
      highBoard,
      highNeedsRecompute: server?.needs_recompute ?? false,
      state: text(item.state),
      evidence: server?.evidence?.length
        ? server.evidence
        : textList(item.evidence),
      prevRank: delta?.prev_rank ?? null,
      prevScore: delta?.prev_score ?? null,
      rankDelta: delta?.rank_delta ?? null,
      scoreDelta: delta?.score_delta ?? null,
      unknownReason: delta?.unknown_reason ?? null,
    };
  });
}

// ─── 候选 ──────────────────────────────────────────────────────────────────

export interface CandidateView {
  code: string;
  name: string | null;
  score: number | null;
  grade: string | null;
  mainline: string | null;
  role: string | null;
  positionPct: number | null;
  eligibility: string | null;
  reason: string | null;
  expectations: Dict | null;
  topDrivers: Dict[];
  topDrags: Dict[];
}

function candidateReason(item: Dict): string | null {
  return (
    text(item.reason_code) ??
    text(item.ignore_reason) ??
    (textList(item.warnings).join("；") || null)
  );
}

export function buildCandidateViews(snapshot: PrepSnapshotView): CandidateView[] {
  const reviewed = asList(snapshot.review_candidates);
  if (reviewed) {
    return reviewed.map((item) => ({
      code: text(item.code) ?? "",
      name: text(item.name),
      score: num(item.review_score),
      grade: text(item.review_grade),
      mainline: text(asDict(item.mainline)?.board_name),
      role: text(item.role),
      positionPct: num(item.position_pct),
      eligibility: text(item.eligibility),
      reason: candidateReason(item),
      expectations: asDict(item.expectations),
      topDrivers: asList(item.top_drivers) ?? [],
      topDrags: asList(item.top_drags) ?? [],
    }));
  }

  const legacy = asList(snapshot.candidates) ?? [];
  return legacy.map((item) => {
    const eligible = bool(item.review_eligible);
    return {
      code: text(item.code) ?? "",
      name: text(item.name),
      score: num(item.composite_score),
      grade: null,
      mainline: null,
      role: null,
      positionPct: num(item.position_pct),
      eligibility: eligible === true ? "eligible" : eligible === false ? "not_qualified" : null,
      reason: text(item.ignore_reason),
      expectations: null,
      topDrivers: [],
      topDrags: [],
    };
  });
}

// ─── 运行概况 ──────────────────────────────────────────────────────────────

export interface RunInfo {
  windowDays: number | null;
  dragonCount: number | null;
  macroAvailable: boolean | null;
  macroSession: string | null;
  macroInstruments: number | null;
  degraded: string[];
}

export function buildRunInfo(snapshot: PrepSnapshotView): RunInfo {
  const review = asDict(snapshot.review);
  const macro = asDict(snapshot.macro);
  const dragonSource = asDict(snapshot.dragon_source);
  const feedback = asDict(snapshot.feedback);
  const planReview = asDict(snapshot.plan_review);
  const sentiment = asDict(snapshot.sentiment);
  const instruments = macro ? asDict(macro.instruments) : null;

  const degraded = textList(review?.degraded);
  const add = (items: string[]) => {
    for (const item of items) if (!degraded.includes(item)) degraded.push(item);
  };
  add(textList(sentiment?.degraded));
  add(textList(feedback?.degraded));
  add(textList(planReview?.degraded));
  add(textList(macro?.degraded));

  return {
    windowDays: num(dragonSource?.window_days),
    dragonCount: num(dragonSource?.count),
    macroAvailable: macro ? bool(macro.available) : null,
    macroSession: macro ? text(macro.session) : null,
    macroInstruments: instruments ? Object.keys(instruments).length : null,
    degraded,
  };
}

// ─── 展示映射（空值 → 「—」，不编造 0/50） ────────────────────────────────

const MARKET_STATE_LABELS: Record<string, string> = {
  strong: "强",
  neutral: "中性",
  weak: "弱",
  ice: "冰点",
  unknown: "数据不足",
};

const ROLE_LABELS: Record<string, string> = {
  leader: "龙头",
  vanguard: "先锋",
  mainstay: "中军",
  capacity: "容量",
  follower: "跟风",
  diagnostic_non_mainline: "非主线诊断",
};

const ELIGIBILITY_LABELS: Record<string, string> = {
  eligible: "可参与",
  not_qualified: "不合格",
  unknown: "数据不足",
};

export function marketStateLabel(value: string | null): string {
  return value ? MARKET_STATE_LABELS[value] ?? value : "—";
}

export function roleLabel(value: string | null): string {
  return value ? ROLE_LABELS[value] ?? value : "—";
}

export function eligibilityLabel(value: string | null): string {
  return value ? ELIGIBILITY_LABELS[value] ?? value : "—";
}

export function positionLabel(value: number | null): string {
  if (value == null) return "数据不足";
  if (value === 0) return "不建议新建仓";
  return `${value}%`;
}

export function display(value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  return String(value);
}

// ─── 运行异常（RunOverview / PrepSummaryBand 共用，避免两处各算一遍） ────

export interface Anomaly {
  level: "danger" | "warning";
  text: string;
}

export function buildAnomalies(
  watermark: PrepWatermark,
  runInfo: RunInfo | null,
  context: ReviewContext | null,
  snapshot: PrepSnapshotView | null
): Anomaly[] {
  const items: Anomaly[] = [];
  for (const row of watermark.assets) {
    if (row.status === "error" || row.status === "unavailable") {
      items.push({
        level: "danger",
        text: `${row.asset_type} 采集异常${row.error ? `：${row.error}` : ""}`,
      });
    } else if (row.status === "empty_suspicious") {
      items.push({ level: "warning", text: `${row.asset_type} 空态可疑` });
    } else if (row.expected && !row.present) {
      items.push({ level: "warning", text: `缺少当日应有资产 ${row.asset_type}` });
    }
  }
  for (const reason of runInfo?.degraded ?? []) {
    items.push({
      level: "warning",
      text: `复盘降级：${labelOf(UNKNOWN_REASON_LABELS, reason, reason)}`,
    });
  }
  const planEffect = context?.plan_effect;
  for (const reason of planEffect?.degraded ?? []) {
    items.push({ level: "warning", text: `成效降级：${reason}` });
  }
  if (context?.prev_meta_error) {
    items.push({ level: "warning", text: `前日快照损坏：${context.prev_meta_error}` });
  }
  const feedback = snapshot?.feedback;
  if (feedback && Array.isArray((feedback as Record<string, unknown>).degraded)) {
    for (const reason of (feedback as Record<string, unknown>).degraded as string[]) {
      items.push({ level: "warning", text: `反馈降级：${reason}` });
    }
  }
  return items;
}

// ─── 否决股成效（T+1/3/5，按 code join，不重算收益） ─────────────────────

export const FOLLOWUP_HORIZONS = ["1", "3", "5"] as const;

export interface NoTradeOutcome {
  code: string;
  name: string | null;
  judgement: NoTradeItem["judgement"];
  outcomePct: number | null;
  cells: Record<string, { pct: number | null; reason: string | null }>;
  /** 计划日快照里没有该股样本 → 全部「待评估 · 计划日无该股样本」。 */
  sampleMissing: boolean;
}

export function buildNoTradeOutcomes(
  noTrade: NoTradeItem[],
  followup: PlanFollowup | null
): NoTradeOutcome[] {
  const byCode = new Map<string, PlanFollowupRow>();
  for (const row of followup?.rows ?? []) byCode.set(row.code, row);
  return noTrade.map((item) => {
    const sample = byCode.get(item.code) ?? null;
    const cells: NoTradeOutcome["cells"] = {};
    for (const horizon of FOLLOWUP_HORIZONS) {
      const cell = sample?.horizons?.[horizon];
      cells[horizon] = {
        pct: cell?.net_return_pct ?? null,
        reason: cell?.exclusion_reason ?? null,
      };
    }
    return {
      code: item.code,
      name: sample?.name ?? item.name,
      judgement: item.judgement,
      outcomePct: item.outcome_pct,
      cells,
      sampleMissing: sample == null,
    };
  });
}

// ─── 结论带 KPI（T+1 概览；单日样本只描述，不下策略结论） ───────────────

export interface FollowupKpis {
  recommendedPct: number | null;
  rejectedPct: number | null;
  spreadPct: number | null;
  validSamples: number;
  totalSamples: number;
  /** 各组有效样本数（均值旁展示 n，避免把总样本数当组样本数）。 */
  recommendedN: number;
  rejectedN: number;
}

export function buildFollowupKpis(followup: PlanFollowup | null): FollowupKpis {
  const horizon =
    followup?.summary?.find((item) => item.horizon === 1) ?? null;
  const groupOf = (key: string) =>
    horizon?.groups.find((group) => group.group === key) ?? null;
  const recommended = groupOf("recommended");
  const rejected = groupOf("not_recommended");
  const recommendedN = recommended?.valid ?? 0;
  const rejectedN = rejected?.valid ?? 0;
  return {
    recommendedPct: recommended?.mean_net_return_pct ?? null,
    rejectedPct: rejected?.mean_net_return_pct ?? null,
    spreadPct: horizon?.spread ?? null,
    validSamples: recommendedN + rejectedN,
    totalSamples:
      (recommended?.valid ?? 0) +
      (recommended?.excluded ?? 0) +
      (rejected?.valid ?? 0) +
      (rejected?.excluded ?? 0),
    recommendedN,
    rejectedN,
  };
}
