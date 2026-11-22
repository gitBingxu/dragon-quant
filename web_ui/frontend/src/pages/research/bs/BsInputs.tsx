import type { ReactNode } from "react";
import { defaultDateRange } from "../../../lib/format";
import type { BsFormState, BsMode, BsNotice } from "./types";

function readFile(
  event: React.ChangeEvent<HTMLInputElement>,
  apply: (text: string) => void
): void {
  const file = event.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => apply(String(reader.result || ""));
  reader.readAsText(file);
}

function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="grid gap-1">
      <label className="text-caption font-medium text-ink-2" htmlFor={id}>
        {label}
      </label>
      {children}
      {hint ? <p className="text-caption text-ink-3">{hint}</p> : null}
    </div>
  );
}

/** 结构化输入区：账户/策略/研究规范 JSON、冻结增强 ID、时间与母集。 */
export function BsInputs({
  form,
  setForm,
  busy,
  notice,
  onAdvice,
  onReview,
  onExperiment,
  onReset,
}: {
  form: BsFormState;
  setForm: (patch: Partial<BsFormState>) => void;
  busy: BsMode | null;
  notice: BsNotice | null;
  onAdvice: () => void;
  onReview: () => void;
  onExperiment: () => void;
  onReset: () => void;
}): ReactNode {
  const range = defaultDateRange();
  return (
    <div className="grid gap-3">
      <div className="grid gap-3 lg:grid-cols-2">
        <Field
          id="bs-account"
          label="账户 JSON（现金 / 持仓批次 / 可卖量 / 预留 / 恢复检查点）"
          hint="完整持仓与批次由后端 parser 校验；缺字段会返回受限原因，不猜值。"
        >
          <textarea
            id="bs-account"
            className="textarea textarea-bordered h-40 w-full font-mono text-caption"
            value={form.accountText}
            placeholder='{"schema_version":1,"currency":"CNY","shares_unit":"share",...}'
            onChange={(event) => setForm({ accountText: event.target.value })}
          />
          <input
            type="file"
            accept="application/json,.json"
            aria-label="上传账户 JSON 文件"
            className="text-caption"
            onChange={(event) => readFile(event, (text) => setForm({ accountText: text }))}
          />
        </Field>
        <Field
          id="bs-policy"
          label="BS 策略配置 JSON（留空 = 只读观察；风险参数不自动填充）"
          hint="不支持教学/推荐默认值；需显式提供 rho、预算、失效与场景规格。"
        >
          <textarea
            id="bs-policy"
            className="textarea textarea-bordered h-40 w-full font-mono text-caption"
            value={form.policyText}
            placeholder='{"schema_version":1,"preference":{"rho":...},"budgets":{...},...}'
            onChange={(event) => setForm({ policyText: event.target.value })}
          />
          <input
            type="file"
            accept="application/json,.json"
            aria-label="上传策略配置 JSON 文件"
            className="text-caption"
            onChange={(event) => readFile(event, (text) => setForm({ policyText: text }))}
          />
        </Field>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Field
          id="bs-study"
          label="研究规范 JSON（可选：有限命名 variant / 阈值 / 评价截止）"
          hint="留空则运行默认「BS vs 无新增风险」对照；不会替你生成风险偏好。"
        >
          <textarea
            id="bs-study"
            className="textarea textarea-bordered h-28 w-full font-mono text-caption"
            value={form.studyText}
            placeholder='{"schema_version":1,"study_id":"...","variants":[...]}'
            onChange={(event) => setForm({ studyText: event.target.value })}
          />
          <input
            type="file"
            accept="application/json,.json"
            aria-label="上传研究规范 JSON 文件"
            className="text-caption"
            onChange={(event) => readFile(event, (text) => setForm({ studyText: text }))}
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            id="bs-enhanced-id"
            label="增强证据：本地已存实验 ID（可选）"
            hint="只认本地已存 ID；资源/母集/证据不可比时显示受限原因。"
          >
            <input
              id="bs-enhanced-id"
              className="input num"
              inputMode="numeric"
              value={form.enhancedId}
              onChange={(event) => setForm({ enhancedId: event.target.value })}
            />
          </Field>
          <label className="flex items-end gap-2 text-caption">
            <input
              type="checkbox"
              className="checkbox checkbox-sm"
              checked={form.newAttempt}
              onChange={(event) => setForm({ newAttempt: event.target.checked })}
            />
            新建研究尝试（new_attempt，不覆盖旧结果）
          </label>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-2 text-caption">
        <Field id="bs-cutoff" label="cutoff（带时区）">
          <input
            id="bs-cutoff"
            className="input input-bordered input-sm w-64"
            value={form.cutoff}
            onChange={(event) => setForm({ cutoff: event.target.value })}
          />
        </Field>
        <Field id="bs-from" label="起始">
          <input
            id="bs-from"
            type="date"
            className="input input-bordered input-sm w-36"
            value={form.dateFrom}
            onChange={(event) => setForm({ dateFrom: event.target.value })}
          />
        </Field>
        <Field id="bs-to" label="结束">
          <input
            id="bs-to"
            type="date"
            className="input input-bordered input-sm w-36"
            value={form.dateTo}
            onChange={(event) => setForm({ dateTo: event.target.value })}
          />
        </Field>
        <Field id="bs-cohort" label="母集">
          <select
            id="bs-cohort"
            className="select select-bordered select-sm"
            value={form.cohort}
            onChange={(event) => setForm({ cohort: event.target.value as "rolling" | "fixed" })}
          >
            <option value="rolling">滚动 last5 / 严格 T-1</option>
            <option value="fixed">固定期初 cohort</option>
          </select>
        </Field>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn btn-sm btn-primary"
          aria-busy={busy === "advice"}
          disabled={busy !== null}
          onClick={onAdvice}
        >
          {busy === "advice" ? "评估中…" : "只读建议"}
        </button>
        <button
          type="button"
          className="btn btn-sm btn-outline"
          aria-busy={busy === "review"}
          disabled={busy !== null}
          onClick={onReview}
        >
          {busy === "review" ? "模拟回放中…" : "显式模拟回放"}
        </button>
        <button
          type="button"
          className="btn btn-sm btn-outline"
          aria-busy={busy === "experiment"}
          disabled={busy !== null}
          onClick={onExperiment}
        >
          {busy === "experiment" ? "对照实验中…" : "运行冻结对照"}
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onReset}>
          清空 / 重置输入
        </button>
        <span className="text-caption text-ink-3">
          默认区间 {range.dateFrom} ~ {range.dateTo}（可改）；只读 GET 不创建数据。
        </span>
      </div>

      {notice ? (
        <div
          className={
            notice.tone === "error"
              ? "alert-danger rounded-lg px-3 py-2 text-caption"
              : notice.tone === "warning"
              ? "rounded-lg border border-warning-line bg-warning-soft px-3 py-2 text-caption text-warning"
              : "rounded-lg border border-hairline bg-base-200/40 px-3 py-2 text-caption text-ink-2"
          }
          role="status"
          aria-live="polite"
        >
          {notice.text}
        </div>
      ) : null}
    </div>
  );
}
