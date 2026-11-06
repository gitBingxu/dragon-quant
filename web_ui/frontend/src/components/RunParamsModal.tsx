// 自定义运行参数弹窗：字段、默认值与范围全部来自 `/api/runs/{job}/spec`。
//
// 提交前先做前端校验（与后端同规则），失败字段就地提示；后端 400/409 的
// 文案显示在弹窗顶部，不静默吞掉。

import { useEffect, useState } from "react";
import type { RunSpec } from "../api/runs";
import { IconAlert } from "./icons-extra";
import { Modal } from "./Modal";
import { Select, TextField } from "./Select";
import { Switch } from "./Switch";
import {
  defaultValues,
  isParamActive,
  validateRunValues,
  type RunValues,
} from "../lib/runParams";

export function RunParamsModal({
  open,
  spec,
  submitting,
  error,
  onClose,
  onSubmit,
}: {
  open: boolean;
  spec: RunSpec | null;
  submitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (values: Record<string, unknown>) => void;
}) {
  const [values, setValues] = useState<RunValues>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open || !spec) return;
    setValues(defaultValues(spec));
    setErrors({});
  }, [open, spec]);

  if (!spec) return null;

  const update = (name: string, value: string | number | boolean) => {
    setValues((current) => ({ ...current, [name]: value }));
  };

  const submit = () => {
    const result = validateRunValues(spec, values);
    setErrors(result.errors);
    if (Object.keys(result.errors).length > 0) return;
    onSubmit(result.clean);
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={spec.custom_label}
      description={`仅可在交易日 ${spec.window.start}–${spec.window.end} 之间执行；参数会先做合法性校验。`}
      width={480}
      footer={
        <>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onClose}
            disabled={submitting}
          >
            取消
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={submit}
            disabled={submitting}
          >
            {submitting ? "提交中…" : "确认执行"}
          </button>
        </>
      }
    >
      <div className="grid gap-3.5">
        {error ? (
          <div className="alert-danger flex items-center gap-2 rounded-lg px-3 py-2 text-body">
            <IconAlert size={14} />
            {error}
          </div>
        ) : null}

        {spec.params.map((param) =>
          isParamActive(param, values) ? (
            <div key={param.name}>
              {param.kind === "enum" ? (
                <Select
                  label={param.label}
                  value={String(values[param.name] ?? "")}
                  options={param.choices ?? []}
                  onChange={(next) => update(param.name, next)}
                />
              ) : param.kind === "bool" ? (
                <div className="field">
                  <span className="field-label">{param.label}</span>
                  <Switch
                    checked={Boolean(values[param.name])}
                    label={param.help ?? "启用"}
                    onChange={(next) => update(param.name, next)}
                  />
                </div>
              ) : (
                <TextField
                  label={param.label}
                  type="number"
                  min={param.minimum}
                  max={param.maximum}
                  value={String(values[param.name] ?? "")}
                  onChange={(next) => update(param.name, next)}
                />
              )}
              {param.kind !== "bool" ? (
                <div className="mt-1 text-caption text-ink-3">
                  {param.help}
                  {param.minimum != null && param.maximum != null
                    ? `${param.help ? "；" : ""}取值范围 ${param.minimum}–${param.maximum}`
                    : ""}
                </div>
              ) : null}
              {errors[param.name] ? (
                <div className="mt-1 text-caption text-danger-text">
                  {errors[param.name]}
                </div>
              ) : null}
            </div>
          ) : null
        )}
      </div>
    </Modal>
  );
}
