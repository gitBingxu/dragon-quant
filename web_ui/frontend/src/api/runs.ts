// scan / prep / auction 运行作业：spec / 可点击判定 / 状态 / 显式 POST。
//
// 错误体里可能带 blockers（不满足可点击条件）或字段级 detail（参数非法），
// 因此这里直接读 body，而不是只拿 client.ts 的通用 ApiError。

export type RunJobName = "scan" | "prep" | "auction";
export type RunState = "idle" | "running" | "succeeded" | "failed";

export interface RunParamChoice {
  value: string;
  label: string;
}

export interface RunParamDependsOn {
  param: string;
  value: string;
}

export interface RunParamSpec {
  name: string;
  label: string;
  kind: "int" | "bool" | "enum";
  default: unknown;
  help?: string;
  minimum?: number;
  maximum?: number;
  choices?: RunParamChoice[];
  depends_on?: RunParamDependsOn;
}

export interface RunSpec {
  job: RunJobName;
  label: string;
  quick_label: string;
  custom_label: string;
  window: { start: string; end: string };
  params: RunParamSpec[];
}

export interface RunBlocker {
  code: string;
  text: string;
}

export interface RunEligibility {
  job: RunJobName;
  enabled: boolean;
  blockers: RunBlocker[];
  window: { start: string; end: string };
  trading_day: boolean;
  trading_day_source: string;
  already_done: boolean;
  scan_done: boolean;
  server_now: string;
  trade_date: string;
  job_state: RunState;
}

export interface RunSnapshot {
  mode: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  exit_code: number | null;
  output_tail: string | null;
  job: RunJobName | null;
  label: string | null;
}

export interface RunStatusPayload {
  state: RunState;
  job: RunJobName | null;
  label: string | null;
  params: Record<string, unknown> | null;
  last_run: RunSnapshot | null;
}

export class RunHttpError extends Error {
  status: number;
  payload: Record<string, unknown>;
  blockers: RunBlocker[];

  constructor(
    status: number,
    message: string,
    payload: Record<string, unknown>
  ) {
    super(message);
    this.status = status;
    this.payload = payload;
    this.blockers = Array.isArray(payload.blockers)
      ? (payload.blockers as RunBlocker[])
      : [];
  }
}

async function readBody(res: Response): Promise<Record<string, unknown>> {
  try {
    const body = await res.json();
    return body && typeof body === "object"
      ? (body as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

async function getJsonOrThrow<T>(path: string, fallback: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await readBody(res);
  if (!res.ok) {
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : typeof body.error === "string"
          ? body.error
          : `${fallback}（${res.status}）`;
    throw new RunHttpError(res.status, detail, body);
  }
  return body as unknown as T;
}

export function fetchRunSpec(job: RunJobName): Promise<RunSpec> {
  return getJsonOrThrow(`/api/runs/${job}/spec`, "参数定义读取失败");
}

export function fetchRunEligibility(job: RunJobName): Promise<RunEligibility> {
  return getJsonOrThrow(`/api/runs/${job}/eligibility`, "可执行状态读取失败");
}

export async function fetchRunStatus(
  job: RunJobName
): Promise<RunStatusPayload> {
  const res = await fetch(`/api/runs/${job}/status`, {
    headers: { Accept: "application/json" },
  });
  const body = await readBody(res);
  // 作业位是共享的：任务刚结束时 job 可能属于别的命令，但运行态本身仍可读，
  // 这里只把「未知 job」当成明确错误。
  if (res.status === 404) {
    throw new RunHttpError(
      404,
      typeof body.detail === "string" ? body.detail : "任务状态读取失败",
      body
    );
  }
  if (!res.ok) {
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : `任务状态读取失败（${res.status}）`;
    throw new RunHttpError(res.status, detail, body);
  }
  return body as unknown as RunStatusPayload;
}

export async function startRun(
  job: RunJobName,
  params: Record<string, unknown>
): Promise<RunStatusPayload> {
  const res = await fetch(`/api/runs/${job}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(params),
  });
  const body = await readBody(res);
  if (!res.ok) {
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : typeof body.error === "string"
          ? body.error
          : `命令启动失败（${res.status}）`;
    throw new RunHttpError(res.status, detail, body);
  }
  return body as unknown as RunStatusPayload;
}
