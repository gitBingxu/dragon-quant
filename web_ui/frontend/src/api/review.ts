// 龙头回测增量作业 API：只读状态 + 显式 POST 触发（写操作）。

export type ReviewJobState = "idle" | "running" | "succeeded" | "failed";

export interface ReviewJobSnapshot {
  mode: "incremental" | "force";
  trigger: "auto" | "manual";
  started_at: string;
  finished_at: string | null;
  exit_code: number | null;
  output_tail: string | null;
}

/** CLI 既有 `--recompute-all` 汇总输出（解析不到时为 null，不得伪报成功）。 */
export interface ReviewJobSummary {
  processed?: number;
  completed?: number;
  cannot_evaluate?: number;
  failed?: number;
  skipped?: number;
  skipped_existing?: number;
  skipped_immature?: number;
}

export interface ReviewJobPayload {
  state: ReviewJobState;
  last_run: ReviewJobSnapshot | null;
  summary?: ReviewJobSummary | null;
}

/** HTTP 错误携带状态码与后端中文 detail，供 UI 区分 409 / 400 / 500。 */
export class ReviewJobHttpError extends Error {
  status: number;
  payload: Record<string, unknown>;

  constructor(status: number, message: string, payload: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

async function readBody(res: Response): Promise<Record<string, unknown>> {
  try {
    const body = await res.json();
    return body && typeof body === "object" ? body : {};
  } catch {
    return {};
  }
}

export async function fetchReviewJobStatus(): Promise<ReviewJobPayload> {
  const res = await fetch("/api/review/jobs/status");
  if (!res.ok) {
    throw new ReviewJobHttpError(
      res.status,
      `回测状态读取失败（${res.status}）`,
      await readBody(res)
    );
  }
  return res.json();
}

export async function startIncrementalReview(): Promise<ReviewJobPayload> {
  const res = await fetch("/api/review/jobs/incremental", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  const body = await readBody(res);
  if (!res.ok) {
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : typeof body.error === "string"
          ? body.error
          : `回测启动失败（${res.status}）`;
    throw new ReviewJobHttpError(res.status, detail, body);
  }
  return body as unknown as ReviewJobPayload;
}
