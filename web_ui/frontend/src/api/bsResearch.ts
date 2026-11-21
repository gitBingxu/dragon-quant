// BS 账户决策研究 API（结构化 account/policy/study payload；不接受服务器路径/脚本）。
// 所有响应同时带原始 data 与只读中文 view；前端只渲染 view，审计 JSON 折叠展示。

import { ApiError } from "./client";

export interface BsAdvicePayload {
  account: Record<string, unknown>;
  policy: Record<string, unknown> | null;
  cutoff: string;
}

export interface BsResponse<T = Record<string, any>> {
  data: T;
  view: Record<string, any>;
}

export interface BsExperimentListResponse {
  data: Record<string, any>[];
  view: Record<string, any>;
  meta: { total: number; truncated: boolean; next_cursor: number | null };
}

async function readJson(res: Response): Promise<any> {
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = payload?.detail ?? payload?.error ?? `接口返回 ${res.status}`;
    throw new ApiError(res.status, String(payload?.error ?? res.status), String(detail));
  }
  return payload;
}

export async function fetchBsAdvice(payload: BsAdvicePayload): Promise<BsResponse> {
  const res = await fetch("/api/research/bs-advice", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  return (await readJson(res)) as BsResponse;
}

export async function runBsReview(
  account: Record<string, unknown>,
  policy: Record<string, unknown>,
  dateFrom: string,
  dateTo: string,
  cohortMode: "rolling" | "fixed" = "rolling"
): Promise<BsResponse> {
  const res = await fetch("/api/research/bs-review", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      account,
      policy,
      date_from: dateFrom,
      date_to: dateTo,
      cohort_mode: cohortMode,
      persist: true,
    }),
  });
  return (await readJson(res)) as BsResponse;
}

export interface BsExperimentOptions {
  study?: Record<string, unknown> | null;
  enhancedExperimentId?: number | null;
  newAttempt?: boolean;
}

export async function runBsExperiment(
  account: Record<string, unknown>,
  policy: Record<string, unknown>,
  dateFrom: string,
  dateTo: string,
  cohortMode: "rolling" | "fixed" = "rolling",
  options: BsExperimentOptions = {}
): Promise<BsResponse> {
  const res = await fetch("/api/research/bs-experiments", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      account,
      policy,
      date_from: dateFrom,
      date_to: dateTo,
      cohort_mode: cohortMode,
      confirm: true,
      new_attempt: Boolean(options.newAttempt),
      ...(options.study ? { study: options.study } : {}),
      ...(options.enhancedExperimentId != null
        ? { enhanced_experiment_id: options.enhancedExperimentId }
        : {}),
    }),
  });
  return (await readJson(res)) as BsResponse;
}

export async function fetchBsLedger(
  ledgerId: string,
  limit = 100,
  cursor?: string | null
): Promise<BsResponse> {
  const search = new URLSearchParams({ ledger_id: ledgerId, limit: String(limit) });
  if (cursor) search.set("cursor", cursor);
  const res = await fetch(`/api/research/bs-ledger?${search.toString()}`, {
    headers: { Accept: "application/json" },
  });
  return (await readJson(res)) as BsResponse;
}

export async function listBsExperiments(
  limit = 20,
  cursor?: number | null
): Promise<BsExperimentListResponse> {
  const search = new URLSearchParams({ limit: String(limit) });
  if (cursor != null) search.set("cursor", String(cursor));
  const res = await fetch(`/api/research/bs-experiments?${search.toString()}`, {
    headers: { Accept: "application/json" },
  });
  return (await readJson(res)) as BsExperimentListResponse;
}

export async function showBsExperiment(
  experimentId: number,
  opts: { includeDetails?: boolean; includeFrozen?: boolean } = {}
): Promise<BsResponse> {
  const search = new URLSearchParams();
  if (opts.includeDetails) search.set("include_details", "true");
  if (opts.includeFrozen) search.set("include_frozen", "true");
  const suffix = search.toString() ? `?${search.toString()}` : "";
  const res = await fetch(`/api/research/bs-experiments/${experimentId}${suffix}`, {
    headers: { Accept: "application/json" },
  });
  return (await readJson(res)) as BsResponse;
}

export async function replayBsExperiment(experimentId: number): Promise<BsResponse> {
  const res = await fetch(
    `/api/research/bs-experiments/${experimentId}/replay`,
    { method: "POST", headers: { Accept: "application/json" } }
  );
  return (await readJson(res)) as BsResponse;
}

export async function resumeBsExperiment(experimentId: number): Promise<BsResponse> {
  const res = await fetch(
    `/api/research/bs-experiments/${experimentId}/resume`,
    { method: "POST", headers: { Accept: "application/json" } }
  );
  return (await readJson(res)) as BsResponse;
}
