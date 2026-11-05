import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchScanQuality,
  type ScanQuality,
  type ScanQualityGroup,
} from "../../api/research";
import { PageHeader } from "../../components/PageHeader";
import { useSearchParams } from "../../router";
import { fmtPct } from "../../lib/format";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

function GroupTable({ title, groups }: {
  title: string;
  groups: Record<string, ScanQualityGroup>;
}) {
  const rows = Object.entries(groups);
  if (!rows.length) {
    return (
      <div className="card p-4 text-caption text-ink-3">
        <h3 className="text-title text-ink">{title}</h3>
        <p className="mt-2">暂无可用分组（覆盖不足）。</p>
      </div>
    );
  }
  return (
    <div className="card p-4">
      <h3 className="text-title text-ink">{title}</h3>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[520px] border-collapse text-caption">
          <thead>
            <tr className="border-b border-hairline text-left text-ink-3">
              <th className="py-1.5 pr-3">分组</th>
              <th className="py-1.5 pr-3">有效</th>
              <th className="py-1.5 pr-3">股票</th>
              <th className="py-1.5 pr-3">日期</th>
              <th className="py-1.5 pr-3">净收益均值</th>
              <th className="py-1.5 pr-3">日期等权</th>
              <th className="py-1.5 pr-3">胜率</th>
              <th className="py-1.5">错失机会</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, group]) => (
              <tr key={name} className="border-b border-hairline/60">
                <td className="py-1.5 pr-3 text-ink">{name}</td>
                <td className="py-1.5 pr-3">{group.valid}</td>
                <td className="py-1.5 pr-3">{group.stocks}</td>
                <td className="py-1.5 pr-3">{group.days}</td>
                <td className="py-1.5 pr-3">{fmtPct(group.mean_net_return_pct)}</td>
                <td className="py-1.5 pr-3">{fmtPct(group.date_equal_mean_return_pct)}</td>
                <td className="py-1.5 pr-3">
                  {group.win_rate != null ? `${(group.win_rate * 100).toFixed(1)}%` : "—"}
                </td>
                <td className="py-1.5">
                  {group.missed_opportunity_rate != null
                    ? `${(group.missed_opportunity_rate * 100).toFixed(1)}%`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const STATUS_HINTS: Record<string, string> = {
  no_scan_in_range: "所选区间没有扫描记录（空区间不是错误，也没有补造样本）。",
  scan_read_error: "扫描日志读取失败，本次没有可用样本；可稍后重试。",
  no_mature_samples: "有扫描记录，但主窗口尚无成熟收益样本；未成熟样本保留，不填 0。",
  local_bars_truncated: "本地日 K 覆盖超出单次上限，已整体停止统计；请缩小日期区间后重试。",
};

function StatusNotice({ data }: { data: ScanQuality }) {
  if (data.status === "ok") return null;
  const reason = data.reason ?? "insufficient_data";
  return (
    <div className="rounded-lg border border-warning-line bg-warning-soft px-3.5 py-2.5 text-body text-warning">
      <span className="font-semibold">证据不足：</span>
      {STATUS_HINTS[reason] ?? `暂无可展示样本（${reason}）。`}
      {data.read_errors?.length ? (
        <ul className="mt-1 grid gap-0.5 font-mono text-caption">
          {data.read_errors.map((item, index) => (
            <li key={`${index}-${item}`}>· {item}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function ResearchPage() {
  const search = useSearchParams();
  const [dateFrom, setDateFrom] = useState(
    () => search.get("date_from") || daysAgo(60));
  const [dateTo, setDateTo] = useState(
    () => search.get("date_to") || today());
  const [data, setData] = useState<ScanQuality | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 单调递增请求序号：慢的旧响应不得覆盖新查询（乱序守卫）。
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchScanQuality(dateFrom, dateTo);
      if (seq !== requestSeq.current) return;
      setData(payload);
    } catch (exc) {
      if (seq !== requestSeq.current) return;
      setData(null);
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [dateFrom, dateTo]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <PageHeader
        eyebrow="RESEARCH · 扫描研究"
        title="扫描研究"
        description="只读原始扫描候选与本地行情：对比通过 / 否决、评分与 rank 区间、首次 / 重复入榜的事后净收益，用来反馈 scan 筛查质量；不改 dragons 去重与生产候选池。"
      />

      <div className="mb-3 flex flex-wrap items-end gap-2">
        <label className="text-caption text-ink-2">
          起始日期
          <input
            type="date"
            className="input ml-2"
            value={dateFrom}
            onChange={(event) => setDateFrom(event.target.value)}
          />
        </label>
        <label className="text-caption text-ink-2">
          结束日期
          <input
            type="date"
            className="input ml-2"
            value={dateTo}
            onChange={(event) => setDateTo(event.target.value)}
          />
        </label>
        <button type="button" className="btn btn-primary" onClick={load} disabled={loading}>
          {loading ? "加载中…" : "查询"}
        </button>
      </div>

      {error ? (
        <div className="alert-danger mb-3 rounded-lg px-3.5 py-2.5 text-body">
          加载失败：{error}
        </div>
      ) : null}

      {data ? (
        <div className="grid gap-3">
          <StatusNotice data={data} />
          <div className="card p-4 text-caption text-ink-2">
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span>状态：{data.status}</span>
              <span>区间：{data.date_from} ~ {data.date_to}</span>
              <span>版本：{data.evaluation_version}</span>
              <span>截至：{data.as_of ?? "—"}</span>
              <span>口径：{data.window.entry_rule} / {data.window.exit_rule}</span>
              <span>成本：{data.window.cost_bps} bp</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
              <span>原始贡献：{String((data.coverage.raw_contributions as number) ?? 0)}</span>
              <span>去重后：{String((data.coverage.canonical_candidates as number) ?? 0)}</span>
              <span>成熟样本：{String((data.coverage.mature_samples as number) ?? 0)}</span>
              <span>通过/否决差：{fmtPct(data.diffs_vs_baseline.passed ?? null)} / {fmtPct(data.diffs_vs_baseline.rejected ?? null)}</span>
            </div>
            {data.coverage_gap ? (
              <p className="mt-2 text-ink-3">覆盖缺口：{data.coverage_gap}</p>
            ) : null}
            {data.warnings.length ? (
              <p className="mt-2 text-ink-3">提示：{data.warnings.join("；")}</p>
            ) : null}
            {data.status === "ok" && data.read_errors?.length ? (
              <p className="mt-2 text-warning">
                部分读取异常：{data.read_errors.join("；")}
              </p>
            ) : null}
          </div>

          <GroupTable title="通过 / 否决" groups={data.groups.by_gate || {}} />
          <GroupTable title="分数区间" groups={data.groups.by_score_band || {}} />
          <GroupTable title="rank 区间" groups={data.groups.by_rank_band || {}} />
          <GroupTable title="首次 / 重复入榜" groups={data.groups.by_first_repeat || {}} />

          <div className="card p-4 text-caption text-ink-3">
            <h3 className="text-title text-ink">口径与限制</h3>
            <ul className="mt-2 list-disc pl-5">
              <li>去重规则：{data.dedupe_rule}</li>
              {data.caveats.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </>
  );
}
