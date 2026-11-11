// 策略页 API：清单 / 当前选择 / 持久切换（与 CLI 共读同一保存状态）。

import { getJson, postJson } from "./client";

export type StrategyItem = {
  id: string;
  label: string;
  source: "builtin" | "user";
  sdk_version: number;
  description: string;
  entrypoint: string;
  parameters: Record<string, unknown>;
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
