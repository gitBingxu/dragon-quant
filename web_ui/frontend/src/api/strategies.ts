// 策略页 API：清单 / 当前选择 / 持久切换（与 CLI 共读同一保存状态）。

import { getJson, postJson } from "./client";

// 参数 schema 来自 descriptor；除 type/default/范围外，可选择提供展示元数据。
export type StrategyParameterSpec = {
  type?: "number" | "int" | "bool" | "string";
  default?: unknown;
  min?: number;
  max?: number;
  enum?: unknown[] | null;
  label?: string;
  help?: string;
  unit?: string;
  step?: number;
};

export type StrategyItem = {
  id: string;
  label: string;
  source: "builtin" | "user";
  sdk_version: number;
  description: string;
  entrypoint: string;
  parameters: Record<string, StrategyParameterSpec>;
  effective_parameters: Record<string, unknown>;
  required_factors: string[];
  data_requirements: string[];
  path: string | null;
  valid: boolean;
  errors: string[];
  current: boolean;
  source_fingerprint: string;
};

export type StrategyList = {
  workspace: string | null;
  workspace_id: string | null;
  revision: number | null;
  current: string;
  strategies: StrategyItem[];
};

export type StrategyCurrent = {
  workspace: string | null;
  workspace_id: string | null;
  revision: number | null;
  strategy_id: string;
  parameters: Record<string, unknown>;
};

export type SelectResult = {
  ok: boolean;
  workspace: string;
  workspace_id: string;
  current: string;
  parameters: Record<string, unknown>;
  revision: number;
};

export function fetchStrategies(): Promise<StrategyList> {
  return getJson<StrategyList>("/api/strategies");
}

export function fetchCurrentStrategy(): Promise<StrategyCurrent> {
  return getJson<StrategyCurrent>("/api/strategies/current");
}

export function saveStrategySelection(
  strategyId: string,
  parameters: Record<string, unknown> | null,
  expectedRevision: number | null,
): Promise<SelectResult> {
  return postJson<SelectResult>("/api/strategies/selection", {
    strategy_id: strategyId,
    parameters,
    expected_revision: expectedRevision,
  });
}
