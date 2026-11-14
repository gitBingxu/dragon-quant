import type { FactorSpecView } from "../../api/factors";

/** 归属筛选：全部 / 系统内建 / 当前 Workspace。 */
export type OriginFilter = "all" | "builtin" | "workspace";

export interface FactorFilterState {
  /** 文本搜索：不区分大小写，匹配 name / label / description。 */
  query: string;
  origin: OriginFilter;
  /** 数据来源；空串表示全部。 */
  source: string;
}

export const EMPTY_FILTERS: FactorFilterState = {
  query: "",
  origin: "all",
  source: "",
};

/** 纯函数：单因子是否满足全部筛选条件（组合为 AND）。 */
export function matchesFilters(
  factor: FactorSpecView,
  filters: FactorFilterState
): boolean {
  if (filters.origin !== "all" && factor.origin !== filters.origin) {
    return false;
  }
  if (filters.source && factor.source !== filters.source) {
    return false;
  }
  const needle = filters.query.trim().toLowerCase();
  if (!needle) return true;
  return [factor.name, factor.label, factor.description]
    .filter((value): value is string => typeof value === "string")
    .some((value) => value.toLowerCase().includes(needle));
}

/** 纯函数：过滤注册表（保持输入顺序，排序由调用方负责）。 */
export function filterFactors(
  factors: FactorSpecView[],
  filters: FactorFilterState
): FactorSpecView[] {
  return factors.filter((factor) => matchesFilters(factor, filters));
}

/** 当前 catalog 中去重排序的数据来源选项。 */
export function sourceOptions(factors: FactorSpecView[]): string[] {
  const unique = new Set<string>();
  for (const factor of factors) {
    if (factor.source) unique.add(factor.source);
  }
  return [...unique].sort((left, right) => left.localeCompare(right));
}

/** 任一筛选生效时「清空筛选」才可用。 */
export function isFilterActive(filters: FactorFilterState): boolean {
  return (
    filters.query.trim() !== "" ||
    filters.origin !== "all" ||
    filters.source !== ""
  );
}

/** 分页 resetKey：筛选条件变化后回到第 1 页。 */
export function filterResetKey(filters: FactorFilterState): string {
  return `${filters.query.trim().toLowerCase()}|${filters.origin}|${filters.source}`;
}
