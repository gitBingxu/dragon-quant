// 研究入口 API（只读 scan 候选质量；账户实验见 api/account.ts）。

export interface ScanQualityWindow {
  entry_rule: string;
  exit_rule: string;
  hold_days: number[];
  cost_bps: number;
}

export interface ScanQualityGroup {
  samples: number;
  valid: number;
  stocks: number;
  days: number;
  mean_net_return_pct: number | null;
  median_net_return_pct: number | null;
  date_equal_mean_return_pct: number | null;
  win_rate: number | null;
  mean_max_opportunity_pct: number | null;
  missed_opportunity_rate: number | null;
  by_horizon?: Record<string, {
    valid: number;
    mean_net_return_pct: number | null;
    win_rate: number | null;
  }>;
}

export interface ScanQuality {
  status: "ok" | "insufficient_data";
  reason: string | null;
  evaluation_version: string;
  universe: string;
  sample_unit: string;
  date_from: string;
  date_to: string;
  as_of: string | null;
  window: ScanQualityWindow;
  dedupe_rule: string;
  coverage: Record<string, unknown>;
  baseline: ScanQualityGroup;
  groups: Record<string, Record<string, ScanQualityGroup>>;
  diffs_vs_baseline: Record<string, number | null>;
  spread: Record<string, unknown> | null;
  coverage_gap: string | null;
  warnings: string[];
  read_errors?: string[];
  caveats: string[];
}

export async function fetchScanQuality(
  dateFrom: string,
  dateTo: string
): Promise<ScanQuality> {
  const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
  const res = await fetch("/api/research/scan-quality?" + params.toString());
  if (!res.ok) {
    let message = `扫描研究接口返回 ${res.status}`;
    try {
      const body = await res.json();
      const detail = body?.detail ?? body?.error;
      if (detail) message = String(detail);
    } catch {
      // keep the status-based Chinese fallback
    }
    throw new Error(message);
  }
  return res.json();
}
