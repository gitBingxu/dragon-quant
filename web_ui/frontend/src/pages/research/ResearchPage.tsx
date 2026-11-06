import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  fetchScanQuality,
  type ScanQuality,
  type ScanQualityGroup,
} from "../../api/research";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { StatCard } from "../../components/StatCard";
import { TextField } from "../../components/Select";
import { fmtPct, pnlClass, pnlStatTone } from "../../lib/format";
import { labelOf } from "../../lib/labels";
import { useSearchParams } from "../../router";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

/** 比率（0.5）→ 百分数（50.0%）；null 一律「—」，不填 0。 */
function pctRate(value: number | null): string {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function coverageCount(coverage: Record<string, unknown>, key: string): number {
  const value = Number(coverage[key] ?? 0);
  return Number.isFinite(value) ? value : 0;
}

/** 分组 key 是后端枚举，展示层翻成中文；未知 key 原样回落。 */
const GATE_LABELS: Record<string, string> = {
  passed: "通过",
  rejected: "否决",
};

const FIRST_REPEAT_LABELS: Record<string, string> = {
  first: "首次",
  repeat: "重复",
  unknown: "未知",
};

const UNKNOWN_LABELS: Record<string, string> = { unknown: "未知" };

interface GroupRow {
  name: string;
  group: ScanQualityGroup;
}

function groupColumns(
  labels: Record<string, string>
): Column<GroupRow>[] {
  return [
    {
      key: "name",
      header: "分组",
      render: (row) => (
        <span className="font-medium text-ink">
          {labelOf(labels, row.name, row.name)}
        </span>
      ),
    },
    {
      key: "valid",
      header: "有效",
      align: "right",
      render: (row) => <span className="num text-ink">{row.group.valid}</span>,
    },
    {
      key: "stocks",
      header: "股票",
      align: "right",
      render: (row) => <span className="num text-ink-2">{row.group.stocks}</span>,
    },
    {
      key: "days",
      header: "日期",
      align: "right",
      render: (row) => <span className="num text-ink-2">{row.group.days}</span>,
    },
    {
      key: "mean",
      header: "净收益均值",
      align: "right",
      render: (row) => (
        <span className={`num font-medium ${pnlClass(row.group.mean_net_return_pct)}`}>
          {fmtPct(row.group.mean_net_return_pct)}
        </span>
      ),
    },
    {
      key: "date_equal",
      header: "日期等权",
      align: "right",
      render: (row) => (
        <span className={`num ${pnlClass(row.group.date_equal_mean_return_pct)}`}>
          {fmtPct(row.group.date_equal_mean_return_pct)}
        </span>
      ),
    },
    {
      key: "win_rate",
      header: "胜率",
      align: "right",
      render: (row) => (
        <span className="num text-ink">{pctRate(row.group.win_rate)}</span>
      ),
    },
    {
      key: "missed",
      header: "错失机会",
      align: "right",
      render: (row) => (
        <span className="num text-ink-2">
          {pctRate(row.group.missed_opportunity_rate)}
        </span>
      ),
    },
  ];
}

function GroupTable({
  title,
  groups,
  labels,
}: {
  title: string;
  groups: Record<string, ScanQualityGroup>;
  labels: Record<string, string>;
}) {
  const rows: GroupRow[] = Object.entries(groups).map(([name, group]) => ({
    name,
    group,
  }));
  if (!rows.length) {
    return (
      <Section title={title}>
        <p className="text-body text-ink-3">
          暂无可用分组：该维度有效样本不足，缺数据不补造。
        </p>
      </Section>
    );
  }
  return (
    <Section
      title={title}
      className="overflow-hidden"
      bodyClassName="p-0"
    >
      <DataTable
        columns={groupColumns(labels)}
        rows={rows}
        rowKey={(row) => row.name}
        minWidth={620}
      />
    </Section>
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

function Meta({
  label,
  value,
}: {
  label: string;
  value: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <div className="text-caption font-semibold tracking-[0.08em] text-ink-3">
        {label}
      </div>
      <div className="mt-1 text-body text-ink">{value}</div>
    </div>
  );
}

function MetaPanel({ data }: { data: ScanQuality }) {
  return (
    <div className="panel px-4 py-3.5 sm:px-[18px]">
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
        <Meta
          label="状态"
          value={
            <Badge tone={data.status === "ok" ? "normal" : "warning"}>
              {data.status === "ok" ? "正常" : "证据不足"}
            </Badge>
          }
        />
        <Meta
          label="区间"
          value={
            <span className="num">
              {data.date_from} ~ {data.date_to}
            </span>
          }
        />
        <Meta
          label="版本"
          value={<span className="num">{data.evaluation_version}</span>}
        />
        <Meta label="截至" value={<span className="num">{data.as_of ?? "—"}</span>} />
        <Meta
          label="成本"
          value={<span className="num">{data.window.cost_bps} bp</span>}
        />
      </div>

      <div className="mt-3 border-t border-hairline pt-2.5 text-caption">
        <div className="font-semibold tracking-[0.08em] text-ink-3">窗口口径</div>
        <div className="mt-1 break-words text-body text-ink-2">
          {data.window.entry_rule} → {data.window.exit_rule}
        </div>
      </div>

      {data.coverage_gap ? (
        <p className="mt-2 text-caption text-ink-3">覆盖缺口：{data.coverage_gap}</p>
      ) : null}

      {data.warnings.length ? (
        <div className="mt-2 text-caption">
          <div className="font-semibold tracking-[0.08em] text-ink-3">提示</div>
          <ul className="mt-1 grid gap-0.5 text-ink-3">
            {data.warnings.map((item, index) => (
              <li key={`${item}-${index}`}>· {item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.read_errors?.length ? (
        <div className="mt-2 text-caption">
          <div className="font-semibold tracking-[0.08em] text-warning">
            部分读取异常
          </div>
          <ul className="mt-1 grid gap-0.5 text-warning">
            {data.read_errors.map((item, index) => (
              <li key={`${item}-${index}`}>· {item}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function Overview({ data }: { data: ScanQuality }) {
  const passedDiff = data.diffs_vs_baseline?.passed ?? null;
  const rejectedDiff = data.diffs_vs_baseline?.rejected ?? null;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      <StatCard
        label="原始贡献"
        value={coverageCount(data.coverage, "raw_contributions")}
        sub="去重前逐日贡献"
      />
      <StatCard
        label="去重后候选"
        value={coverageCount(data.coverage, "canonical_candidates")}
        sub="按 per_day_code 去重"
      />
      <StatCard
        label="成熟样本"
        value={coverageCount(data.coverage, "mature_samples")}
        sub="已进入收益窗口"
      />
      <StatCard
        label="通过组差值"
        value={fmtPct(passedDiff)}
        sub="相对基线的净收益均值差"
        tone={pnlStatTone(passedDiff)}
      />
      <StatCard
        label="否决组差值"
        value={fmtPct(rejectedDiff)}
        sub="相对基线的净收益均值差"
        tone={pnlStatTone(rejectedDiff)}
      />
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

      <div className="panel mb-3 px-4 py-3.5 sm:px-[18px]">
        <div className="flex flex-wrap items-end gap-3">
          <TextField
            label="起始日期"
            type="date"
            value={dateFrom}
            onChange={setDateFrom}
            width={168}
          />
          <TextField
            label="结束日期"
            type="date"
            value={dateTo}
            onChange={setDateTo}
            width={168}
          />
          <button
            type="button"
            className="btn btn-primary"
            onClick={load}
            disabled={loading}
          >
            {loading ? "查询中…" : "查询"}
          </button>
        </div>
      </div>

      {error ? (
        <div className="alert-danger mb-3 rounded-lg px-3.5 py-2.5 text-body">
          加载失败：{error}
        </div>
      ) : null}

      {data ? (
        <div className="grid gap-3">
          <StatusNotice data={data} />
          <Overview data={data} />
          <MetaPanel data={data} />

          <div className="grid items-start gap-3 2xl:grid-cols-2">
            <GroupTable
              title="通过 / 否决"
              groups={data.groups.by_gate || {}}
              labels={GATE_LABELS}
            />
            <GroupTable
              title="分数区间"
              groups={data.groups.by_score_band || {}}
              labels={UNKNOWN_LABELS}
            />
            <GroupTable
              title="rank 区间"
              groups={data.groups.by_rank_band || {}}
              labels={UNKNOWN_LABELS}
            />
            <GroupTable
              title="首次 / 重复入榜"
              groups={data.groups.by_first_repeat || {}}
              labels={FIRST_REPEAT_LABELS}
            />
          </div>

          {data.dedupe_rule || data.caveats.length ? (
            <Section title="口径与限制">
              <ul className="grid gap-1 text-caption text-ink-2">
                {data.dedupe_rule ? <li>· 去重规则：{data.dedupe_rule}</li> : null}
                {data.caveats.map((item) => (
                  <li key={item}>· {item}</li>
                ))}
              </ul>
            </Section>
          ) : null}
        </div>
      ) : loading ? (
        <div className="panel px-4 py-12 text-center text-body text-ink-3">
          正在读取扫描质量…
        </div>
      ) : (
        <div className="panel">
          <EmptyState
            title="选择区间后查询"
            description="默认展示近 60 天；结果只描述历史样本，不构成策略有效性结论。"
          />
        </div>
      )}
    </>
  );
}
