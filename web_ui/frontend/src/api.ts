// 后端 API 类型定义与 fetch 封装。字段对齐 dragon_quant/storage/db.py。

export interface Dragon {
  source: "v1" | "v2";
  trade_date: string;
  code: string;
  name: string;
  scan_id: string;
  rank: number | null;
  composite_score: number | null;
  board_count: number | null;
  open_px: number | null;
  close_px: number | null;
  high_px: number | null;
  low_px: number | null;
  pct: number | null;
  turnover_rate: number | null;
  amount: number | null;
  market_cap: number | null;
  concepts: string[];
  report_text: string;
  is_true_dragon: boolean | null;
  buy_date: string | null;
  buy_price: number | null;
  max_return_5d: number | null;
  max_drawdown_5d: number | null;
  max_return_hold_days: number | null;
  review_status: string | null;
  version: string;
}

export interface Summary {
  source: "v1" | "v2";
  total: number;
  completed: number;
  pending: number;
  avg_return: number | null;
  win_rate: number | null;
  best_stock_code: string | null;
  best_stock_name: string | null;
  best_return: number | null;
}

export interface DragonFilters {
  source?: "v1" | "v2";
  code?: string;
  name?: string;
  date_from?: string;
  date_to?: string;
  score_min?: string;
  score_max?: string;
  return_min?: string;
  return_max?: string;
  drawdown_min?: string;
  drawdown_max?: string;
  version_min?: string;
  version_max?: string;
  status?: string;
  sort_by: string;
  sort_dir: string;
}

export async function fetchSummary(source: "v1" | "v2" = "v1"): Promise<Summary> {
  const params = new URLSearchParams({ source });
  const res = await fetch("/api/summary?" + params.toString());
  if (!res.ok) throw new Error(`summary ${res.status}`);
  return res.json();
}

export async function fetchDragons(
  filters: DragonFilters
): Promise<{ data: Dragon[]; count: number }> {
  const params = new URLSearchParams();
  const keys: (keyof DragonFilters)[] = [
    "source",
    "code",
    "name",
    "date_from",
    "date_to",
    "score_min",
    "score_max",
    "return_min",
    "return_max",
    "drawdown_min",
    "drawdown_max",
    "version_min",
    "version_max",
    "status",
    "sort_by",
    "sort_dir",
  ];
  for (const k of keys) {
    const v = filters[k];
    if (v != null && String(v).trim() !== "") params.set(k, String(v).trim());
  }
  const res = await fetch("/api/dragons?" + params.toString());
  if (!res.ok) throw new Error(`dragons ${res.status}`);
  return res.json();
}
