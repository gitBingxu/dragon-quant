// 自定义运行参数：默认值 / 类型收敛 / 前端校验。
//
// 规则与后端 `run_specs.validate_params` 对齐（服务端仍是最终权威）；默认值
// 与范围全部由 `/api/runs/{job}/spec` 下发，前端不写死第二份数字。

import type { RunParamSpec, RunSpec } from "../api/runs";

export type RunValue = string | number | boolean;
export type RunValues = Record<string, RunValue>;

export function defaultValues(spec: RunSpec): RunValues {
  const values: RunValues = {};
  for (const param of spec.params) {
    values[param.name] = normalizeDefault(param);
  }
  return values;
}

function normalizeDefault(param: RunParamSpec): RunValue {
  if (param.kind === "bool") return Boolean(param.default);
  if (param.kind === "int") {
    const value = Number(param.default);
    return Number.isFinite(value) ? value : 0;
  }
  return String(param.default ?? "");
}

export function isParamActive(param: RunParamSpec, values: RunValues): boolean {
  if (!param.depends_on) return true;
  return String(values[param.depends_on.param] ?? "") === param.depends_on.value;
}

function parseIntInput(text: string): number | null {
  const trimmed = text.trim();
  if (!/^[+-]?\d+$/.test(trimmed)) return null;
  return Number(trimmed);
}

export interface RunValidationResult {
  errors: Record<string, string>;
  clean: Record<string, unknown>;
}

/** 逐字段校验并收敛为后端可接受的值；不生效的字段回填默认值。 */
export function validateRunValues(
  spec: RunSpec,
  values: RunValues
): RunValidationResult {
  const errors: Record<string, string> = {};
  const clean: Record<string, unknown> = {};

  for (const param of spec.params) {
    if (!isParamActive(param, values)) {
      clean[param.name] = normalizeDefault(param);
      continue;
    }
    const raw = values[param.name];

    if (param.kind === "int") {
      const value =
        typeof raw === "number" ? raw : parseIntInput(String(raw ?? ""));
      if (value == null || !Number.isInteger(value)) {
        errors[param.name] = `${param.label}必须是整数`;
        continue;
      }
      if (param.minimum != null && value < param.minimum) {
        errors[param.name] = `${param.label}不能小于 ${param.minimum}`;
        continue;
      }
      if (param.maximum != null && value > param.maximum) {
        errors[param.name] = `${param.label}不能大于 ${param.maximum}`;
        continue;
      }
      clean[param.name] = value;
      continue;
    }

    if (param.kind === "bool") {
      clean[param.name] = Boolean(raw);
      continue;
    }

    const text = String(raw ?? "");
    const allowed = (param.choices ?? []).map((choice) => choice.value);
    if (!allowed.includes(text)) {
      errors[param.name] = `请选择${param.label}`;
      continue;
    }
    clean[param.name] = text;
  }

  return { errors, clean };
}
