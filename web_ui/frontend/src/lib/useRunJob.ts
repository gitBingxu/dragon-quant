// scan / prep / auction 运行作业状态：spec + 可点击判定 + 提交 + 轮询。
//
// - 可点击判定**只信服务端**（交易日 / 时间窗 / 当日已完成 / 共享作业忙）；
// - 请求串行 setTimeout 轮询（不重叠），按 started_at 作业身份守卫终态；
// - 成功后回调页面刷新；失败展示退出码与输出最后一行，绝不伪报成功；
// - 挂载时只读恢复「运行中」状态：刷新页面不会重复 POST。

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchRunEligibility,
  fetchRunSpec,
  fetchRunStatus,
  RunHttpError,
  startRun,
  type RunEligibility,
  type RunJobName,
  type RunSpec,
  type RunStatusPayload,
} from "../api/runs";

const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 1800; // 约 1 小时；扫描/复盘可能跑很久
const ELIGIBILITY_INTERVAL_MS = 30000;

export type RunPhase = "loading" | "idle" | "running" | "succeeded" | "failed";
export type RunTone = "info" | "warn" | "danger";

export interface StartResult {
  ok: boolean;
  error: string | null;
}

function messageOf(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

function lastLine(text: string | null | undefined): string | null {
  if (!text) return null;
  const lines = text.trim().split("\n").filter(Boolean);
  return lines.length ? lines[lines.length - 1].slice(0, 240) : null;
}

export interface RunJobController {
  spec: RunSpec | null;
  eligibility: RunEligibility | null;
  phase: RunPhase;
  message: string | null;
  tone: RunTone;
  canRun: boolean;
  disabledReason: string | null;
  customOpen: boolean;
  openCustom: () => void;
  closeCustom: () => void;
  submitting: boolean;
  formError: string | null;
  start: (params?: Record<string, unknown>) => Promise<StartResult>;
  submitCustom: (params: Record<string, unknown>) => Promise<boolean>;
}

export function useRunJob(
  job: RunJobName,
  onCompleted?: (tradeDate: string) => void | Promise<void>
): RunJobController {
  const [spec, setSpec] = useState<RunSpec | null>(null);
  const [eligibility, setEligibility] = useState<RunEligibility | null>(null);
  const [phase, setPhase] = useState<RunPhase>("loading");
  const [message, setMessage] = useState<string | null>(null);
  const [tone, setTone] = useState<RunTone>("info");
  const [customOpen, setCustomOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const mountedRef = useRef(true);
  const phaseRef = useRef<RunPhase>("loading");
  const eligibilityRef = useRef<RunEligibility | null>(null);
  const onCompletedRef = useRef(onCompleted);
  const timerRef = useRef<number | null>(null);
  const attemptsRef = useRef(0);
  const trackedStartRef = useRef<string | null>(null);
  const terminalStartRef = useRef<string | null>(null);
  const startLockRef = useRef(false);
  const pollOnceRef = useRef<() => void>(() => {});

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    eligibilityRef.current = eligibility;
  }, [eligibility]);

  useEffect(() => {
    onCompletedRef.current = onCompleted;
  }, [onCompleted]);

  const stopPolling = useCallback(() => {
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, [stopPolling]);

  const refreshEligibility = useCallback(async () => {
    try {
      const next = await fetchRunEligibility(job);
      if (mountedRef.current) setEligibility(next);
      return next;
    } catch (exc) {
      if (mountedRef.current) {
        setTone("warn");
        setMessage(`可执行状态读取失败：${messageOf(exc)}`);
      }
      return null;
    }
  }, [job]);

  // spec + 首次判定：并行拉取，失败只降级为禁用按钮（不崩溃）。
  useEffect(() => {
    let active = true;
    Promise.all([fetchRunSpec(job), fetchRunEligibility(job)])
      .then(([nextSpec, nextEligibility]) => {
        if (!active) return;
        setSpec(nextSpec);
        setEligibility(nextEligibility);
      })
      .catch((exc: unknown) => {
        if (!active) return;
        setTone("warn");
        setMessage(`运行配置读取失败：${messageOf(exc)}`);
      })
      .finally(() => {
        if (!active) return;
        setPhase((current) => (current === "loading" ? "idle" : current));
      });
    return () => {
      active = false;
    };
  }, [job]);

  // 时间窗 / 其他任务状态会变化：空闲时定期刷新判定（服务端日历按日缓存）。
  useEffect(() => {
    const interval = window.setInterval(() => {
      if (phaseRef.current === "running") return;
      void refreshEligibility();
    }, ELIGIBILITY_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [refreshEligibility]);

  const schedulePoll = useCallback(() => {
    if (timerRef.current != null) return;
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      pollOnceRef.current();
    }, POLL_INTERVAL_MS);
  }, []);

  const applyFailure = useCallback((payload: RunStatusPayload) => {
    stopPolling();
    startLockRef.current = false;
    terminalStartRef.current = payload.last_run?.started_at ?? null;
    setPhase("failed");
    setTone("danger");
    const code = payload.last_run?.exit_code;
    const tail = lastLine(payload.last_run?.output_tail);
    setMessage(
      `任务执行失败${code != null ? `（退出码 ${code}）` : ""}` +
        `${tail ? `：${tail}` : ""}。请查看命令输出后重试。`
    );
  }, [stopPolling]);

  const finishSuccess = useCallback(
    async (payload: RunStatusPayload) => {
      stopPolling();
      startLockRef.current = false;
      terminalStartRef.current = payload.last_run?.started_at ?? null;
      setPhase("succeeded");
      setTone("info");
      setMessage("任务执行成功，正在刷新页面数据…");
      const tradeDate = eligibilityRef.current?.trade_date ?? "";
      try {
        await onCompletedRef.current?.(tradeDate);
      } catch (exc) {
        if (!mountedRef.current) return;
        setTone("warn");
        setMessage(`任务已完成，但页面刷新失败：${messageOf(exc)}`);
        return;
      }
      if (!mountedRef.current) return;
      setMessage("任务执行成功。");
      void refreshEligibility();
    },
    [refreshEligibility, stopPolling]
  );

  const pollOnce = useCallback(async () => {
    if (!mountedRef.current) return;
    attemptsRef.current += 1;
    if (attemptsRef.current > MAX_POLLS) {
      setTone("warn");
      setMessage("前端已停止等待，任务可能仍在运行；刷新页面可恢复状态。");
      return;
    }
    let payload: RunStatusPayload;
    try {
      payload = await fetchRunStatus(job);
    } catch (exc) {
      if (!mountedRef.current) return;
      setTone("warn");
      setMessage(`状态读取失败（${messageOf(exc)}），正在重试…任务不会被取消。`);
      schedulePoll();
      return;
    }
    if (!mountedRef.current) return;
    const start = payload.last_run?.started_at ?? null;

    // 终态守卫：同一作业的过期 running 响应不得覆盖终态。
    if (terminalStartRef.current != null) return;
    if (trackedStartRef.current && start && start !== trackedStartRef.current) {
      stopPolling();
      startLockRef.current = false;
      setPhase("idle");
      setTone("warn");
      setMessage("任务状态已变化，请刷新查看结果。");
      void refreshEligibility();
      return;
    }
    if (payload.state === "running") {
      if (start) trackedStartRef.current = start;
      setTone("info");
      setMessage("任务正在执行中…");
      schedulePoll();
      return;
    }
    stopPolling();
    if (payload.state === "succeeded") {
      await finishSuccess(payload);
      return;
    }
    if (payload.state === "failed") {
      applyFailure(payload);
      return;
    }
    startLockRef.current = false;
    setPhase("idle");
    setTone("warn");
    setMessage("未获取到任务结果，请刷新确认。");
  }, [applyFailure, finishSuccess, job, refreshEligibility, schedulePoll, stopPolling]);

  useEffect(() => {
    pollOnceRef.current = () => {
      void pollOnce();
    };
  }, [pollOnce]);

  // 挂载只读恢复：运行中的任务直接挂接并禁用按钮（绝不重复 POST）。
  useEffect(() => {
    let active = true;
    fetchRunStatus(job)
      .then((payload) => {
        if (!active || !mountedRef.current) return;
        if (
          payload.state === "running" &&
          payload.job === job &&
          payload.last_run?.started_at
        ) {
          trackedStartRef.current = payload.last_run.started_at;
          startLockRef.current = true;
          setPhase("running");
          setTone("info");
          setMessage("已有任务在运行，正在等待完成…");
          attemptsRef.current = 0;
          schedulePoll();
        }
      })
      .catch(() => {
        // 只读探针失败：保持 idle，点击时由服务端判定兜底。
      });
    return () => {
      active = false;
    };
  }, [job, schedulePoll]);

  const start = useCallback(
    async (params: Record<string, unknown> = {}): Promise<StartResult> => {
      if (startLockRef.current || phaseRef.current === "running") {
        return { ok: false, error: null };
      }
      startLockRef.current = true;
      terminalStartRef.current = null;
      trackedStartRef.current = null;
      setPhase("running");
      setTone("info");
      setMessage("正在启动任务…");
      try {
        const payload = await startRun(job, params);
        if (!mountedRef.current) return { ok: true, error: null };
        trackedStartRef.current = payload.last_run?.started_at ?? null;
        attemptsRef.current = 0;
        if (payload.state === "running") {
          setMessage("任务正在执行中…");
          schedulePoll();
        } else if (payload.state === "succeeded") {
          await finishSuccess(payload);
        } else {
          applyFailure(payload);
        }
        return { ok: true, error: null };
      } catch (exc) {
        startLockRef.current = false;
        const error = messageOf(exc);
        if (!mountedRef.current) return { ok: false, error };
        setPhase("idle");
        if (exc instanceof RunHttpError && exc.status === 409) {
          setTone("warn");
          setMessage(error);
          void refreshEligibility();
          return { ok: false, error };
        }
        setTone("danger");
        setMessage(`任务启动失败：${error}`);
        void refreshEligibility();
        return { ok: false, error };
      }
    },
    [applyFailure, finishSuccess, job, refreshEligibility, schedulePoll]
  );

  const submitCustom = useCallback(
    async (params: Record<string, unknown>): Promise<boolean> => {
      setSubmitting(true);
      setFormError(null);
      const result = await start(params);
      if (!mountedRef.current) return false;
      setSubmitting(false);
      if (!result.ok) {
        if (result.error) setFormError(result.error);
        return false;
      }
      setCustomOpen(false);
      return true;
    },
    [start]
  );

  const openCustom = useCallback(() => {
    setFormError(null);
    setCustomOpen(true);
  }, []);

  const closeCustom = useCallback(() => {
    if (submitting) return;
    setCustomOpen(false);
    setFormError(null);
  }, [submitting]);

  const busy = phase === "running";
  const canRun = eligibility?.enabled === true && !busy;
  const blockedText =
    eligibility && eligibility.blockers.length
      ? eligibility.blockers.map((item) => item.text).join("；")
      : null;
  const disabledReason = busy
    ? "任务正在执行中，请等待完成"
    : !eligibility
      ? "正在读取可执行状态…"
      : eligibility.enabled
        ? null
        : blockedText ?? "当前不可执行";

  return {
    spec,
    eligibility,
    phase,
    message,
    tone,
    canRun,
    disabledReason,
    customOpen,
    openCustom,
    closeCustom,
    submitting,
    formError,
    start,
    submitCustom,
  };
}
