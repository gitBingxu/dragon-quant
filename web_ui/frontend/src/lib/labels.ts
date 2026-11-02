// 中文展示映射（纯函数）。空值一律「—」/「数据不足」，不编造。

export const MAINLINE_STATE_LABELS: Record<string, string> = {
  new: "新进主线",
  strengthening: "走强",
  stable: "稳定",
  weakening: "走弱",
  unknown: "数据不足",
};

export const UNKNOWN_REASON_LABELS: Record<string, string> = {
  prev_snapshot_missing: "前日无快照",
  prev_snapshot_corrupt: "前日快照损坏",
  prev_mainlines_missing: "前日无主线",
  prev_mainline_score_missing: "前日主线缺分",
  mainline_score_missing: "当日主线缺分",
  mainline_prev_rank_missing: "前日无该主线排名",
  mainline_change_below_threshold: "变化未达阈值",
  board_members_missing: "缺成分名单",
  snapshot_missing: "当日无快照",
};

export const MARKET_STATE_LABELS: Record<string, string> = {
  strong: "强",
  neutral: "中性",
  weak: "弱",
  ice: "冰点",
  unknown: "数据不足",
};

export const FEEDBACK_STATE_LABELS: Record<string, string> = {
  confirmed: "确认",
  invalidated: "否决",
  unknown: "数据不足",
  pending: "待评估",
};

export const PLANNED_ACTION_LABELS: Record<string, string> = {
  execute_plan: "按计划执行",
  cancel_entry: "取消建仓",
  observe_only: "仅观察",
};

export const JUDGEMENT_LABELS: Record<string, string> = {
  correct_avoidance: "正确回避",
  missed_opportunity: "错过机会",
  data_gap: "数据不足",
};

export const GROUP_LABELS: Record<string, string> = {
  recommended: "推荐",
  not_recommended: "明确不推荐",
  data_insufficient: "数据不足",
  unknown: "数据不足",
};

/** 空仓验证结论（产品判定，不是策略有效性结论）。 */
export const VERIFICATION_CONCLUSION_LABELS: Record<string, string> = {
  correct: "验证正确",
  wrong: "验证错误",
  neutral: "中性",
  not_evaluable: "待评估",
};

export const PLAN_ADVICE_LABELS: Record<string, string> = {
  empty: "前日建议空仓",
  hold: "前日建议持仓",
};

export const VERIFICATION_REASON_LABELS: Record<string, string> = {
  current_snapshot_missing: "评估日无快照",
  no_prev_snapshot: "无前日快照",
  prev_position_missing: "前日快照缺仓位建议",
  temperature_missing: "温度缺分，无法比较",
  temperature_basis_mismatch: "两侧温度口径不同源",
  state_missing: "市场状态不可判",
  limit_counts_missing: "缺涨跌停家数",
};

export const PLAN_FOLLOWUP_REASON_LABELS: Record<string, string> = {
  snapshot_missing: "评估日无快照",
  no_plan: "无 T-1 计划",
  plan_snapshot_missing: "计划日快照缺失",
  plan_snapshot_corrupt: "计划日快照损坏",
  plan_snapshot_unsupported: "计划日快照版本过旧",
  plan_samples_missing: "计划日无候选样本",
  local_bars_missing: "本地日 K 缺失",
  insufficient_samples: "样本不足",
};

export const EXCLUSION_REASON_LABELS: Record<string, string> = {
  missing_bar: "缺本地日 K",
  suspended: "停牌",
  insufficient_horizon: "未来交易日未到",
  unfillable_one_word: "一字板未成交",
  illiquid: "成交额过低",
  no_entry_day: "无介入日",
  unmapped_label: "无法映射收益",
};

export const ASSET_CATEGORY_LABELS: Record<string, string> = {
  core: "核心",
  same_day_only: "仅当日",
  review_conditional: "复盘条件",
  auxiliary: "辅助",
  report: "元数据",
};

export const LEGAL_ABSENCE_LABELS: Record<string, string> = {
  same_day_only_history: "历史日不可补采",
  first_day_no_plan: "首日无计划",
  no_prev_plan: "无前日计划",
  review_assets_disabled: "复盘资产已关闭",
  not_attempted: "未采集",
  legal_empty: "合法空态",
};

export const COLLECTION_STATUS_LABELS: Record<string, string> = {
  ok: "已采+已评",
  asset_only: "仅原始资产",
  snapshot_only: "仅快照",
  missing: "无记录",
};

export const QUALITY_LABELS: Record<string, string> = {
  ok: "正常",
  degraded: "降级",
  unavailable: "数据不足",
  vetoed: "否决",
  unknown: "未知",
};

export const SCAN_STATUS_LABELS: Record<string, string> = {
  completed: "已完成",
  completed_with_errors: "已完成·有异常",
  logs_missing: "已完成·无日志",
};

export const EFFECT_QUALITY_LABELS: Record<string, string> = {
  ok: "可用",
  low_confidence: "样本不足",
  not_evaluable: "不可评估",
};

export function labelOf(
  map: Record<string, string>,
  value: string | null | undefined,
  fallback = "—"
): string {
  if (!value) return fallback;
  return map[value] ?? value;
}

export const ASSET_TYPE_LABELS: Record<string, string> = {
  dragons: "真龙榜",
  ths_limit_up_pool: "涨停池",
  ths_block_top: "板块榜",
  market_breadth: "市场宽度",
  fg_boards: "同花顺榜单",
  holdings: "持仓",
  fund_flow: "资金流",
  cninfo_announcements: "公告",
  macro_global: "宏观",
  stock_daily: "个股日 K",
  prev_plan: "前日计划",
  auction_feedback: "竞价反馈",
  t1_intraday: "次日盘中",
  collection_report: "采集记录",
};

export const ASSET_STATUS_LABELS: Record<string, string> = {
  ok: "正常",
  empty: "无数据",
  error: "采集异常",
  unavailable: "不可用",
  empty_suspicious: "数据可疑",
};

export const DIRECTION_LABELS: Record<string, string> = {
  bull: "看多",
  bear: "看空",
  neutral: "中性",
};

export const VALIDATION_LABELS: Record<string, string> = {
  ok: "通过",
  warn: "注意",
  fail: "未通过",
};

export const CHECKPOINT_LABELS: Record<string, string> = {
  T1_open: "次日开盘",
  T1_close: "次日收盘",
};

export const EFFECT_REASON_LABELS: Record<string, string> = {
  insufficient_samples: "样本不足",
  local_bars_missing: "本地日 K 缺失",
  no_snapshot: "无竞价快照",
  calendar_unavailable: "无本地交易日",
  no_snapshot_in_range: "窗口内无快照",
  // prep 单日效果 / 严格逐股
  snapshot_missing: "当日无复盘快照",
  review_candidates_missing: "无复盘候选",
  local_bars_truncated: "本地日 K 截断",
  next_trading_day_unavailable: "无下一交易日",
  next_trading_day_not_available: "下一交易日尚未就绪",
  window_not_started: "收益窗口尚未开始",
  missing_bar: "缺本地日 K",
  invalid_bar: "本地日 K 无效",
  mainline_missing: "无主线数据",
  board_asset_missing: "当日板块资产缺失",
  next_board_asset_missing: "次日板块资产缺失",
  board_code_missing: "板块代码缺失",
  change_missing: "涨跌幅缺失",
  ambiguous_board_name: "板块同名，无法匹配",
  next_board_missing: "次日无该板块",
  no_signal: "无有效信号",
};

export const CALENDAR_BASIS_LABELS: Record<string, string> = {
  bars_union: "本地交易日",
  index_calendar: "交易所日历",
  local_stock_daily_bars_union: "本地日 K 交易日并集",
  record_dates: "记录日",
};

/** 日历默认日期来源（后端 `default_date_source`）。 */
export const DEFAULT_DATE_SOURCE_LABELS: Record<string, string> = {
  local_bar_previous: "本地前一交易日",
  local_bar_latest: "本地最近交易日",
  record_date_fallback: "记录日回退",
  today_fallback: "今日回退",
};

/** 全历史准确率的样本级 / 日级排除原因。 */
export const ACCURACY_REASON_LABELS: Record<string, string> = {
  market_signal_missing: "当日市场信号缺失",
  candidate_data_insufficient: "候选数据不足",
  signal_missing: "竞价信号缺失",
  immature: "窗口未成熟",
  calendar_unavailable: "本地交易日缺失",
  missing_bar: "缺本地日 K",
  invalid_bar: "本地日 K 无效",
  snapshot_unsupported: "快照版本不支持",
  snapshot_corrupt: "快照损坏",
  review_candidates_missing: "无复盘候选",
  no_snapshots: "无历史快照",
  local_bars_missing: "本地日 K 缺失",
  local_bars_truncated: "本地日 K 截断",
};

/** 准确率逐股 / 单日效果的样本分组。 */
export const ACCURACY_GROUP_LABELS: Record<string, string> = {
  recommended: "推荐",
  not_recommended: "不推荐",
  data_insufficient: "数据不足",
};

/** prep 单日效果逐股状态。 */
export const EFFECT_STOCK_STATUS_LABELS: Record<string, string> = {
  completed: "已完成",
  cannot_evaluate: "无法评估",
  pending: "待评估",
};

/** prep 板块次日匹配方式。 */
export const EFFECT_MATCH_LABELS: Record<string, string> = {
  code: "代码匹配",
  name: "名称回退",
};

/** prep 准确率「是否建仓」日级计数。 */
export const DAY_KIND_LABELS: Record<string, string> = {
  build: "建仓日",
  no_build: "不建仓日",
  signal_missing: "信号缺失",
};

export const DATA_SOURCE_LABELS: Record<string, string> = {
  local_stock_daily: "本地日 K",
};

export function assetTypeLabel(assetType: string): string {
  return ASSET_TYPE_LABELS[assetType] ?? "其他资产";
}

export function reasonLabel(code: string | null | undefined): string {
  if (!code) return "—";
  return EFFECT_REASON_LABELS[code] ?? `其他原因（${code}）`;
}
