// Review 口径（严格 / 宽松）到物理字段的映射。
//
// 服务端 /api/dragons 已按所选口径完成筛选、排序与派生状态；前端只在渲染
// 与客户端排序时把「逻辑字段」映射到行内对应字段，不写 if strict/loose 分支。

import type { Dragon, ReviewCaliber } from "../api/dragons";

/** 逻辑字段键：两种口径共用，渲染/排序时经 CALIBER_FIELD 取物理列。 */
export type CaliberField =
  | "buy_date"
  | "buy_price"
  | "max_return_5d"
  | "max_drawdown_5d"
  | "max_return_hold_days"
  | "status";

export const CALIBER_FIELD: Record<
  ReviewCaliber,
  Record<CaliberField, keyof Dragon>
> = {
  strict: {
    buy_date: "strict_buy_date",
    buy_price: "strict_buy_price",
    max_return_5d: "strict_max_return_5d",
    max_drawdown_5d: "strict_max_drawdown_5d",
    max_return_hold_days: "strict_max_return_hold_days",
    status: "derived_status",
  },
  loose: {
    buy_date: "buy_date",
    buy_price: "buy_price",
    max_return_5d: "max_return_5d",
    max_drawdown_5d: "max_drawdown_5d",
    max_return_hold_days: "max_return_hold_days",
    status: "derived_status",
  },
};

/** 前端固定 v2：仅显式 loose 才宽松，其余按严格口径。 */
export function effectiveCaliber(caliber?: ReviewCaliber): ReviewCaliber {
  return caliber === "loose" ? "loose" : "strict";
}

export function caliberField(
  caliber: ReviewCaliber,
  field: CaliberField
): keyof Dragon {
  return CALIBER_FIELD[caliber][field];
}

/** 逻辑排序键 → 物理字段；非口径字段（code/rank/…）原样返回。 */
export function resolveSortField(
  caliber: ReviewCaliber,
  key: string
): keyof Dragon {
  const mapped = CALIBER_FIELD[caliber] as Record<string, keyof Dragon>;
  return mapped[key] ?? (key as keyof Dragon);
}
