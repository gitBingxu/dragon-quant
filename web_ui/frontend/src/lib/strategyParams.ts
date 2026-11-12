// 策略参数表单的纯函数层：把 descriptor schema 映射成可编辑草稿，并在提交前
// 按与后端一致的规则做类型/范围/枚举校验。不含 React，便于审查与复用。

import type { StrategyParameterSpec } from "../api/strategies";

/** 表单草稿：布尔值保持 boolean，其余用字符串承载用户输入。 */
export type ParamValue = string | boolean;
export type ParamDraft = Record<string, ParamValue>;
export type ParamErrors = Record<string, string>;
export type ParamValues = Record<string, unknown>;

export type ParamKind = "number" | "int" | "bool" | "enum" | "string";

export function paramKind(spec: StrategyParameterSpec): ParamKind {
  if (spec.type === "bool") return "bool";
  if (spec.type === "int") return "int";
  if (spec.type === "number") return "number";
  if (Array.isArray(spec.enum)) return "enum";
  return "string";
}

/** 老 descriptor 没有 label 时以参数键名兜底。 */
export function paramLabel(name: string, spec: StrategyParameterSpec): string {
  const label = typeof spec.label === "string" ? spec.label.trim() : "";
  return label || name;
}

export function enumOptions(spec: StrategyParameterSpec): { value: string; label: string }[] {
  if (!Array.isArray(spec.enum)) return [];
  return spec.enum.map((value) => {
    const text = String(value);
    return { value: text, label: text };
  });
}

/** 用有效参数（schema 默认 + 当前保存值）初始化草稿；缺省为空输入。 */
export function buildDraft(
  specs: Record<string, StrategyParameterSpec>,
  effective: Record<string, unknown>,
): ParamDraft {
  const draft: ParamDraft = {};
  for (const [name, spec] of Object.entries(specs)) {
    const kind = paramKind(spec);
    const value = effective[name] !== undefined ? effective[name] : spec.default;
    if (kind === "bool") {
      draft[name] = value === true;
    } else if (value === undefined || value === null) {
      draft[name] = "";
    } else {
      draft[name] = String(value);
    }
  }
  return draft;
}

function numberInRange(spec: StrategyParameterSpec, value: number): string | null {
  if (typeof spec.min === "number" && value < spec.min) return `不能小于 ${spec.min}`;
  if (typeof spec.max === "number" && value > spec.max) return `不能大于 ${spec.max}`;
  return null;
}

/** 后端对任意 type 的 enum 都会校验；非字符串枚举也必须保持一致。 */
function outsideEnum(spec: StrategyParameterSpec, value: unknown): boolean {
  return Array.isArray(spec.enum) && !spec.enum.some((option) => option === value);
}

/**
 * 校验草稿并转成提交给后端的 JSON 值。
 * 校验失败返回 errors 且 values 为 null；调用方据此就地报错、不请求接口。
 */
export function validateDraft(
  specs: Record<string, StrategyParameterSpec>,
  draft: ParamDraft,
): { values: ParamValues | null; errors: ParamErrors } {
  const errors: ParamErrors = {};
  const values: ParamValues = {};

  for (const [name, spec] of Object.entries(specs)) {
    const kind = paramKind(spec);
    const raw = draft[name];

    if (kind === "bool") {
      if (outsideEnum(spec, raw === true)) {
        errors[name] = "必须是可选值之一";
        continue;
      }
      values[name] = raw === true;
      continue;
    }

    const text = typeof raw === "string" ? raw : "";

    if (kind === "number") {
      const trimmed = text.trim();
      if (!trimmed) {
        errors[name] = "请输入数值";
        continue;
      }
      const value = Number(trimmed);
      if (!Number.isFinite(value)) {
        errors[name] = "请输入有效数值";
        continue;
      }
      const range = numberInRange(spec, value);
      if (range) {
        errors[name] = range;
        continue;
      }
      if (outsideEnum(spec, value)) {
        errors[name] = "必须是可选值之一";
        continue;
      }
      values[name] = value;
      continue;
    }

    if (kind === "int") {
      const trimmed = text.trim();
      if (!trimmed) {
        errors[name] = "请输入整数";
        continue;
      }
      const value = Number(trimmed);
      if (!Number.isFinite(value) || !Number.isInteger(value)) {
        errors[name] = "必须是整数";
        continue;
      }
      const range = numberInRange(spec, value);
      if (range) {
        errors[name] = range;
        continue;
      }
      if (outsideEnum(spec, value)) {
        errors[name] = "必须是可选值之一";
        continue;
      }
      values[name] = value;
      continue;
    }

    if (kind === "enum") {
      const options = Array.isArray(spec.enum) ? spec.enum : [];
      const chosen = options.find((option) => String(option) === text);
      if (chosen === undefined) {
        errors[name] = options.length === 0 ? "该参数未提供可选项" : "请选择有效选项";
        continue;
      }
      values[name] = chosen;
      continue;
    }

    values[name] = text;
  }

  if (Object.keys(errors).length > 0) return { values: null, errors };
  return { values, errors };
}

/** 范围提示文案（元数据提供 min/max 时展示）。 */
export function rangeHint(spec: StrategyParameterSpec): string | null {
  if (typeof spec.min !== "number" && typeof spec.max !== "number") return null;
  const min = typeof spec.min === "number" ? spec.min : "-∞";
  const max = typeof spec.max === "number" ? spec.max : "+∞";
  const unit = spec.unit ? ` ${spec.unit}` : "";
  return `范围 ${min} ~ ${max}${unit}`;
}
