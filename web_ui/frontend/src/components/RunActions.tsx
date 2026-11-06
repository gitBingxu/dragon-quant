// 页面级运行入口：快捷按钮 + 自定义按钮 + 运行状态 + 参数弹窗。
//
// 禁用用 `aria-disabled` 而不是 `disabled`：原生 disabled 按钮不触发 hover，
// 用户就看不到禁用原因；这里保持可 hover / 可聚焦，点击由守卫拦下，
// 原因同时写进 `title`（鼠标悬停）与状态行（读屏与可见文案）。

import type { RunJobName } from "../api/runs";
import { useRunJob } from "../lib/useRunJob";
import { HoverTip } from "./HoverTip";
import { RunParamsModal } from "./RunParamsModal";

export function RunActions({
  job,
  onCompleted,
}: {
  job: RunJobName;
  /** 任务成功后刷新页面数据；参数为服务端认定的交易日期（ISO）。 */
  onCompleted?: (tradeDate: string) => void | Promise<void>;
}) {
  const controller = useRunJob(job, onCompleted);
  const busy = controller.phase === "running";
  const disabled = !controller.canRun;
  const reason = controller.disabledReason;
  const reasonId = `${job}-run-disabled-reason`;

  const quickLabel = controller.spec?.quick_label ?? "运行";
  const customLabel = controller.spec?.custom_label ?? "自定义";
  const toneClass =
    controller.tone === "danger"
      ? "text-danger-text"
      : controller.tone === "warn"
        ? "text-warning"
        : "text-ink-2";

  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5">
      <HoverTip tip={disabled ? reason : null} labelId={`${reasonId}-quick`}>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          aria-disabled={disabled}
          aria-describedby={disabled ? `${reasonId}-quick` : undefined}
          onClick={disabled ? undefined : () => void controller.start({})}
        >
          {busy ? "执行中…" : quickLabel}
        </button>
      </HoverTip>
      <HoverTip tip={disabled ? reason : null} labelId={`${reasonId}-custom`}>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          aria-disabled={disabled}
          aria-describedby={disabled ? `${reasonId}-custom` : undefined}
          onClick={disabled ? undefined : controller.openCustom}
        >
          {customLabel}
        </button>
      </HoverTip>
      {controller.message ? (
        <span
          role="status"
          aria-live="polite"
          className={`max-w-[240px] truncate text-caption ${toneClass}`}
          title={controller.message}
        >
          {controller.message}
        </span>
      ) : null}

      <RunParamsModal
        open={controller.customOpen}
        spec={controller.spec}
        submitting={controller.submitting}
        error={controller.formError}
        onClose={controller.closeCustom}
        onSubmit={(values) => void controller.submitCustom(values)}
      />
    </div>
  );
}
