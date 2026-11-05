/**
 * 龙头总览「待回测」卡片内的增量回测入口（KR1 / KR2）。
 *
 * 以状态 hook + 插槽（action / status / overlay）暴露，按钮渲染在「待回测」
 * 文案后面，状态文案留在卡片底部；浮层用 fixed 定位锚定按钮，不受插槽位置影响。
 *
 * - 只在卡片对应待回测计数 > 0 时显示「回测」按钮（loading / 未知不显示）；
 * - 首次出现自动弹说明；只有点击「我知道了」才写版本化 localStorage 键，
 *   读取/写入异常都不崩溃；**从不**在仅展示时记已读；
 * - 点击显式 POST；运行期间按钮禁用并显示进度，成功后刷新当前筛选列表、
 *   全量覆盖与卡片，失败可重试；只读页面绝不自动回测；
 * - 挂载时只读 GET 恢复「运行中 / 最近完成」状态：运行中立即挂接现有作业并
 *   禁用按钮（不再 POST），刷新或导航回来也能恢复；
 * - 状态轮询用串行 setTimeout（不重叠），并按 `started_at` 作业身份守卫，
 *   过期 running 响应不得覆盖终态；网络失败只提示重试，不暗示任务被取消；
 * - 作业状态（进程退出码）与业务结果（processed/failed/...）分开展示：
 *   CLI 对单条 error 仍正常退出，exit 0 不能当作业务全部成功。
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  fetchReviewJobStatus,
  ReviewJobHttpError,
  startIncrementalReview,
  type ReviewJobPayload,
  type ReviewJobSummary,
} from "../../api/review";

const INTRO_KEY = "dq.dragons.review-intro.v1";
const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 300;
const RESTORE_WINDOW_MS = 10 * 60 * 1000;

function readIntroSeen(): boolean {
  try {
    return window.localStorage.getItem(INTRO_KEY) === "1";
  } catch {
    // 隐私模式 / 禁用 storage：按未读处理，不崩溃。
    return false;
  }
}

function writeIntroSeen(): void {
  try {
    window.localStorage.setItem(INTRO_KEY, "1");
  } catch {
    // 写失败不阻塞交互；下次可能再次提示，但不伪造已读。
  }
}

function messageOf(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

function lastLine(text: string | null | undefined): string | null {
  if (!text) return null;
  const lines = text.trim().split("\n").filter(Boolean);
  return lines.length ? lines[lines.length - 1].slice(0, 300) : null;
}

type Phase = "idle" | "running" | "succeeded" | "failed";
type MessageTone = "info" | "warn" | "danger";

export interface ReviewRefreshResult {
  /** 是否成功用最新数据刷新了列表/全量覆盖。 */
  refreshed: boolean;
  /** 刷新被更晚的查询接管（不是失败）。 */
  stale?: boolean;
  /** 全量覆盖的剩余待处理数（仅 refreshed 时有意义）。 */
  remaining?: number | null;
  error?: string;
}

/** 业务汇总 → 用户可见文案：没有汇总 / 有 failed 时绝不伪报全部成功。 */
export function composeSummaryText(
  summary: ReviewJobSummary | null | undefined,
  remaining: number | null
): { text: string; tone: MessageTone } {
  const failed = summary?.failed ?? 0;
  const completed = summary?.completed ?? 0;
  const cannot = summary?.cannot_evaluate ?? 0;
  const partial = summary?.partial ?? 0;
  const skipped = summary?.skipped ?? 0;
  const processed = summary?.processed;
  const tail =
    remaining != null && remaining > 0
      ? ` 仍有 ${remaining} 条待处理（可能为未成熟、行情失败或历史不足），请查看状态/错误。`
      : "";
  if (!summary) {
    return {
      tone: remaining != null && remaining > 0 ? "warn" : "info",
      text:
        "任务执行结束，未取得业务汇总" +
        (remaining != null && remaining > 0
          ? `；仍有 ${remaining} 条待处理`
          : "") +
        "，请查看状态/错误。",
    };
  }
  // 只有 failed（K 线拉取失败 / 历史不足）才是失败；窗口不完整单列。
  if (failed > 0) {
    return {
      tone: "warn",
      text:
        `任务部分失败：失败 ${failed} 条（完成 ${completed}、` +
        `无法评估 ${cannot}、窗口不完整 ${partial}、跳过 ${skipped}）。` +
        `请检查错误后重试。${tail}`,
    };
  }
  if (processed === 0) {
    return {
      tone: "info",
      text:
        "任务执行结束：没有需要补算的成熟标的" +
        (skipped ? `（跳过 ${skipped} 条未成熟/已完成）` : "") +
        `。${tail}`,
    };
  }
  if (partial > 0) {
    return {
      tone: "info",
      text:
        `任务执行结束：完成 ${completed} 条、无法评估 ${cannot} 条；` +
        `另有 ${partial} 条收益窗口不完整（未走完或窗口内缺 K 线/停牌），` +
        `已排除在正式统计之外。${tail}`,
    };
  }
  return {
    tone: tail ? "warn" : "info",
    text:
      `任务执行结束：完成 ${completed} 条、无法评估 ${cannot} 条、` +
      `跳过 ${skipped} 条。${tail}`,
  };
}

export interface ReviewJobParts {
  /** 「回测」按钮（待回测 > 0 时）；放在「待回测」文案后面。 */
  action: ReactNode;
  /** 卡片底部的说明 / 进度 / 结果文案。 */
  status: ReactNode;
  /** 首次说明浮层（fixed 定位，渲染位置不影响位置）。 */
  overlay: ReactNode;
}

export function useIncrementalReview({
  pending,
  onFinished,
  onSessionStart,
  enabled = true,
}: {
  /** 卡片对应的当前筛选待回测数；null = 未知（加载中/失败）。 */
  pending: number | null;
  /** 作业成功后刷新当前筛选与全量覆盖；返回结构化刷新结果。 */
  onFinished: () => Promise<ReviewRefreshResult>;
  /** 启动/挂接作业时通知父级：即使 pending 归零也保留组件与结果反馈。 */
  onSessionStart?: () => void;
  /** 入口未启用时跳过快照恢复探针（页面按 pending / 会话状态传入）。 */
  enabled?: boolean;
}): ReviewJobParts {
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [tone, setTone] = useState<MessageTone>("info");
  const [introOpen, setIntroOpen] = useState(false);
  const [position, setPosition] = useState<{
    left: number;
    top: number;
    width: number;
  } | null>(null);

  const anchorRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const ackRef = useRef<HTMLButtonElement>(null);
  const timerRef = useRef<number | null>(null);
  const attemptsRef = useRef(0);
  const mountedRef = useRef(true);
  const introChecked = useRef(false);
  // 同步防双击：两次点击可能在 React 批处理前同一 tick 到达。
  const startLock = useRef(false);
  // 作业身份（started_at）：恢复/轮询只认同一作业，防止过期响应覆盖终态。
  const trackedStartRef = useRef<string | null>(null);
  const terminalStartRef = useRef<string | null>(null);
  const onSessionStartRef = useRef(onSessionStart);

  const busy = phase === "running";
  const showButton = pending != null && pending > 0;

  useEffect(() => {
    onSessionStartRef.current = onSessionStart;
  }, [onSessionStart]);

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

  // 首次出现立即弹说明；仅在用户点击「我知道了」后才持久化。
  useEffect(() => {
    if (!showButton) return;
    if (introChecked.current) return;
    introChecked.current = true;
    if (!readIntroSeen()) setIntroOpen(true);
  }, [showButton]);

  const closeIntro = useCallback((persist: boolean) => {
    if (persist) writeIntroSeen();
    setIntroOpen(false);
    anchorRef.current?.focus();
  }, []);

  useLayoutEffect(() => {
    if (!introOpen) {
      setPosition(null);
      return;
    }
    const update = () => {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      const width = Math.min(300, window.innerWidth - 16);
      const height = popRef.current?.offsetHeight ?? 176;
      // 优先放在「回测」按钮右上方：底边贴按钮上沿，从按钮左缘向右展开。
      let top = rect.top - height - 6;
      if (top < 8) top = rect.bottom + 6; // 上方空间不足 → 退回下方
      const left = Math.min(
        Math.max(8, rect.left),
        Math.max(8, window.innerWidth - width - 8)
      );
      setPosition({ left, top, width });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [introOpen]);

  useEffect(() => {
    if (introOpen) ackRef.current?.focus();
  }, [introOpen]);

  // 点击外部 / Escape 关闭说明，不记录已读。
  useEffect(() => {
    if (!introOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (popRef.current?.contains(target)) return;
      if (anchorRef.current?.contains(target)) return;
      closeIntro(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeIntro(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [introOpen, closeIntro]);

  const finishSuccess = useCallback(
    async (payload: ReviewJobPayload) => {
      stopPolling();
      startLock.current = false;
      terminalStartRef.current = payload.last_run?.started_at ?? null;
      setPhase("succeeded");
      setTone("info");
      setMessage("任务已结束，正在刷新列表与全量覆盖…");
      let result: ReviewRefreshResult;
      try {
        result = await onFinished();
      } catch (exc) {
        if (!mountedRef.current) return;
        setTone("warn");
        setMessage(
          `任务已结束，但列表刷新失败：${messageOf(exc)}；` +
            "已保留上次有效数据，请重试。"
        );
        return;
      }
      if (!mountedRef.current) return;
      if (result.stale) {
        setTone("info");
        setMessage("任务执行结束；列表已由较新的查询刷新。");
        return;
      }
      if (!result.refreshed) {
        setTone("warn");
        setMessage(
          `任务已结束，但列表刷新失败：${result.error ?? "未知错误"}；` +
            "已保留上次有效数据，请重试。"
        );
        return;
      }
      const composed = composeSummaryText(
        payload.summary,
        result.remaining ?? null
      );
      setTone(composed.tone);
      setMessage(composed.text);
    },
    [onFinished, stopPolling]
  );

  const applyTerminal = useCallback(
    (payload: ReviewJobPayload) => {
      stopPolling();
      startLock.current = false;
      terminalStartRef.current = payload.last_run?.started_at ?? null;
      if (payload.state === "succeeded") {
        setPhase("succeeded");
        const composed = composeSummaryText(payload.summary, null);
        setTone(composed.tone);
        setMessage(composed.text);
        return;
      }
      if (payload.state === "failed") {
        setPhase("failed");
        setTone("danger");
        const summary = payload.summary;
        const details: string[] = [];
        if (summary) {
          details.push(
            `processed=${summary.processed ?? "—"}，failed=${summary.failed ?? "—"}`
          );
        }
        const tail = lastLine(payload.last_run?.output_tail);
        if (tail) details.push(tail);
        const code = payload.last_run?.exit_code;
        setMessage(
          `任务执行失败${code != null ? `（退出码 ${code}）` : ""}` +
            `${details.length ? `：${details.join("；")}` : ""}。`
        );
        return;
      }
      setPhase("idle");
      setTone("warn");
      setMessage("未获取到任务结果，请刷新确认。");
    },
    [stopPolling]
  );

  const pollOnceRef = useRef<() => void>(() => {});

  const schedulePoll = useCallback(() => {
    if (timerRef.current != null) return;
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      pollOnceRef.current();
    }, POLL_INTERVAL_MS);
  }, []);

  const pollOnce = useCallback(async () => {
    if (!mountedRef.current) return;
    attemptsRef.current += 1;
    if (attemptsRef.current > MAX_POLLS) {
      // 只停止前端等待，不宣称后端任务已取消。
      setTone("warn");
      setMessage("前端已停止等待，任务可能仍在运行；刷新页面可恢复状态。");
      return;
    }
    let payload: ReviewJobPayload;
    try {
      payload = await fetchReviewJobStatus();
    } catch (exc) {
      if (!mountedRef.current) return;
      setTone("warn");
      setMessage(
        `状态读取失败（${messageOf(exc)}），正在重试…任务不会被取消。`
      );
      schedulePoll();
      return;
    }
    if (!mountedRef.current) return;
    const start = payload.last_run?.started_at ?? null;

    // 终态守卫：同一作业的过期 running 响应不得覆盖终态；其他作业也不覆盖。
    if (terminalStartRef.current != null) {
      if (start === terminalStartRef.current) return;
      return;
    }
    if (trackedStartRef.current && start && start !== trackedStartRef.current) {
      stopPolling();
      startLock.current = false;
      setPhase("idle");
      setTone("warn");
      setMessage("回测任务状态已更新，请刷新查看结果。");
      return;
    }
    if (payload.state === "running") {
      if (start) trackedStartRef.current = start;
      setTone("info");
      setMessage("正在执行增量回测（补算成熟待回测与缺失严格结果）…");
      schedulePoll();
      return;
    }
    stopPolling();
    if (payload.state === "succeeded") {
      await finishSuccess(payload);
      return;
    }
    applyTerminal(payload);
  }, [applyTerminal, finishSuccess, schedulePoll, stopPolling]);

  useEffect(() => {
    pollOnceRef.current = () => {
      void pollOnce();
    };
  }, [pollOnce]);

  // 挂载只读恢复：运行中 → 挂接并禁用按钮（绝不重复 POST）；最近完成 → 展示结果。
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    fetchReviewJobStatus()
      .then((payload) => {
        if (!active || !mountedRef.current) return;
        const last = payload.last_run;
        const start = last?.started_at ?? null;
        if (payload.state === "running" && start) {
          onSessionStartRef.current?.();
          trackedStartRef.current = start;
          startLock.current = true;
          setPhase("running");
          setTone("info");
          setMessage("已有回测任务在运行，正在等待完成…");
          attemptsRef.current = 0;
          schedulePoll();
          return;
        }
        if (
          (payload.state === "succeeded" || payload.state === "failed") &&
          last?.finished_at
        ) {
          const finished = Date.parse(last.finished_at);
          if (
            Number.isFinite(finished) &&
            Date.now() - finished <= RESTORE_WINDOW_MS
          ) {
            applyTerminal(payload);
          }
        }
      })
      .catch(() => {
        // 只读探针失败：保持 idle，用户点击时会自动处理 409 并挂接。
      });
    return () => {
      active = false;
    };
    // 入口启用时执行一次；回调通过 ref 读取最新值。
  }, [applyTerminal, enabled, schedulePoll]);

  const start = useCallback(async () => {
    if (busy || startLock.current || !showButton) return;
    startLock.current = true;
    // 新作业：清掉上一个作业的身份/终态守卫，允许重新挂接与轮询。
    terminalStartRef.current = null;
    trackedStartRef.current = null;
    onSessionStartRef.current?.();
    setIntroOpen(false);
    setPhase("running");
    setTone("info");
    setMessage("正在启动增量回测…");
    try {
      const payload = await startIncrementalReview();
      if (!mountedRef.current) return;
      const startAt = payload.last_run?.started_at ?? null;
      if (startAt) trackedStartRef.current = startAt;
      if (payload.state === "running") {
        setTone("info");
        setMessage("正在执行增量回测（补算成熟待回测与缺失严格结果）…");
        attemptsRef.current = 0;
        schedulePoll();
        return;
      }
      if (payload.state === "succeeded") {
        await finishSuccess(payload);
        return;
      }
      applyTerminal(payload);
    } catch (exc) {
      if (!mountedRef.current) return;
      if (exc instanceof ReviewJobHttpError && exc.status === 409) {
        // 已有任务在跑：不重复起任务，转入同一状态的轮询。
        setTone("info");
        setMessage("已有回测任务在运行，正在等待完成…");
        attemptsRef.current = 0;
        schedulePoll();
        return;
      }
      startLock.current = false;
      setPhase("failed");
      setTone("danger");
      setMessage(`回测启动失败：${messageOf(exc)}`);
    }
  }, [applyTerminal, busy, finishSuccess, schedulePoll, showButton]);

  const toneClass =
    tone === "danger"
      ? "text-danger-text"
      : tone === "warn"
        ? "text-warning"
        : "text-ink-2";

  const action = showButton ? (
    <button
      ref={anchorRef}
      type="button"
      onClick={start}
      disabled={busy}
      aria-label="对全量待处理池执行增量回测"
      aria-haspopup="dialog"
      aria-expanded={introOpen}
      aria-controls={introOpen ? "review-intro-popover" : undefined}
      className="btn btn-primary btn-sm"
    >
      {busy ? "回测中…" : "回测"}
    </button>
  ) : null;

  const status =
    showButton || message ? (
      <div className="mt-1 grid gap-1">
        {showButton ? (
          <div className="text-caption text-ink-3">
            {busy ? "运行中，勿重复点击" : "增量补算，任务针对全量待处理池"}
          </div>
        ) : null}
        {message ? (
          <div
            role="status"
            aria-live="polite"
            className={`text-caption ${toneClass}`}
          >
            {message}
          </div>
        ) : null}
      </div>
    ) : null;

  const overlay = introOpen ? (
    <div
      ref={popRef}
      id="review-intro-popover"
      role="dialog"
      aria-modal="false"
      aria-labelledby="review-intro-title"
      aria-describedby="review-intro-desc"
      style={
        position
          ? {
              position: "fixed",
              left: position.left,
              top: position.top,
              width: position.width,
            }
          : {
              position: "fixed",
              left: 8,
              top: 8,
              width: 300,
              visibility: "hidden",
            }
      }
      className="panel z-50 p-3 text-caption text-ink-2"
    >
      <p
        id="review-intro-title"
        className="text-body font-semibold text-ink"
      >
        增量回测说明
      </p>
      <p id="review-intro-desc" className="mt-1">
        点击「回测」只补算可回测的新标的与缺失结果，不覆盖已完成结果；
        未成熟记录会保留，约 6 个交易日后才能补算。任务始终针对
        <strong className="text-ink">全量待处理池</strong>
        ，不受当前筛选影响。
      </p>
      <div className="mt-2 flex justify-end">
        <button
          ref={ackRef}
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => closeIntro(true)}
        >
          我知道了
        </button>
      </div>
    </div>
  ) : null;

  return { action, status, overlay };
}
