import { useCallback, useEffect, useRef, useState } from "react";
import {
  createAccountExperiment,
  fetchAccountExperiments,
  type AccountExperiment,
} from "../../api/account";
import { IconAlert } from "../../components/icons-extra";
import { Badge } from "../../components/Badge";
import { CollapsibleSection } from "../../components/CollapsibleSection";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { Section } from "../../components/Section";
import { fmtPct, pnlClass } from "../../lib/format";
import { labelOf } from "../../lib/labels";
import { fetchBsOverview } from "../../api/bs";

type VariantRow = {
  name: string;
  label?: string;
  status?: string;
  reason?: string;
  total_return?: number | null;
  closed_count?: number | null;
  max_drawdown?: number | null;
  diff_vs_baseline?: number | null;
};

const STATUS_LABELS: Record<string, string> = {
  ok: "正常",
  insufficient_data: "数据不足",
  insufficient_evidence: "证据不足",
};

function statusTone(status?: string): "normal" | "warning" | "muted" {
  if (status === "ok") return "normal";
  if (status === "insufficient_data" || status === "insufficient_evidence") {
    return "warning";
  }
  return "muted";
}

const VARIANT_COLUMNS: Column<VariantRow>[] = [
  {
    key: "variant",
    header: "变体",
    render: (row) => (
      <span className="font-medium text-ink">{row.label || row.name}</span>
    ),
  },
  {
    key: "status",
    header: "状态",
    render: (row) => (
      <Badge tone={statusTone(row.status)}>
        {labelOf(STATUS_LABELS, row.status, row.status ?? "—")}
      </Badge>
    ),
  },
  {
    key: "return",
    header: "收益",
    align: "right",
    render: (row) => (
      <span className={`num font-medium ${pnlClass(row.total_return)}`}>
        {fmtPct(row.total_return ?? null)}
      </span>
    ),
  },
  {
    key: "drawdown",
    header: "回撤",
    align: "right",
    render: (row) => (
      <span className={`num ${pnlClass(row.max_drawdown)}`}>
        {fmtPct(row.max_drawdown ?? null)}
      </span>
    ),
  },
  {
    key: "closed",
    header: "已平仓",
    align: "right",
    render: (row) => <span className="num text-ink-2">{row.closed_count ?? "—"}</span>,
  },
  {
    key: "diff",
    header: "相对基线",
    align: "right",
    render: (row) => (
      <span className={`num ${pnlClass(row.diff_vs_baseline)}`}>
        {fmtPct(row.diff_vs_baseline ?? null)}
      </span>
    ),
  },
  {
    key: "reason",
    header: "说明",
    render: (row) => <span className="text-ink-3">{row.reason ?? "—"}</span>,
  },
];

function pickMetrics(entry: Record<string, unknown>): VariantRow {
  const test = (entry.test || entry.all || {}) as Record<string, unknown>;
  return {
    name: String(entry.name ?? ""),
    label: entry.label as string | undefined,
    status: entry.status as string | undefined,
    reason: entry.reason as string | undefined,
    total_return: (test.total_return ?? null) as number | null,
    closed_count: (test.closed_count ?? null) as number | null,
    max_drawdown: (test.max_drawdown ?? null) as number | null,
    diff_vs_baseline: (entry.diff_vs_baseline_test
      ?? entry.diff_vs_baseline_validation ?? null) as number | null,
  };
}

export function ExperimentPanel({ runId }: { runId: number }) {
  const [history, setHistory] = useState<AccountExperiment[]>([]);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<AccountExperiment | null>(null);
  const [sellRules, setSellRules] = useState<string[]>([]);
  const [availableRules, setAvailableRules] = useState<
    Array<{ name: string; label: string }>
  >([]);
  // 跨 run 请求守卫：旧 run 的响应不得覆盖新 run 的组件状态。
  const requestSeq = useRef(0);

  const loadHistory = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    try {
      const resp = await fetchAccountExperiments(runId);
      if (seq !== requestSeq.current) return;
      const rows = resp.data || [];
      setHistory(rows);
      setActive(rows[0] ?? null);
    } catch (exc) {
      if (seq !== requestSeq.current) return;
      setHistory([]);
      setActive(null);
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    setHistory([]);
    setActive(null);
    setSellRules([]);
    // 查看路径只读已保存报告，绝不隐式重算。
    loadHistory();
  }, [loadHistory]);

  useEffect(() => {
    let activeRequest = true;
    fetchBsOverview()
      .then((overview) => {
        if (!activeRequest) return;
        setAvailableRules(
          (overview.strategy?.sell_rules ?? []).map((item) => ({
            name: item.name,
            label: item.label,
          }))
        );
      })
      .catch(() => {
        if (activeRequest) setAvailableRules([]);
      });
    return () => {
      activeRequest = false;
    };
  }, []);

  const run = useCallback(async () => {
    setConfirming(false);
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    try {
      await createAccountExperiment(runId, sellRules);
      if (seq !== requestSeq.current) return;
      await loadHistory();
    } catch (exc) {
      if (seq !== requestSeq.current) return;
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [runId, sellRules, loadHistory]);

  const report = (active?.report || {}) as Record<string, unknown>;
  const variants = (report.variants || {}) as Record<string, Record<string, unknown>>;
  const rows = Object.entries(variants).map(([name, entry]) => ({
    ...pickMetrics(entry),
    name,
  }));
  const period = (report.period || {}) as Record<string, unknown>;
  const upstream = (report.upstream || {}) as Record<string, unknown>;

  return (
    <Section
      title="账户对照实验"
      description="基于当前批次的冻结期间/资金/策略与上游候选母集做下游消融；本地行情、显式触发、只展示可比证据，不自动推广参数。"
      right={
        <>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={loadHistory}
            disabled={loading}
          >
            {loading ? "加载中…" : "刷新已存报告"}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setConfirming(true)}
            disabled={loading}
          >
            运行对照实验
          </button>
        </>
      }
    >

      {confirming ? (
        <div
          role="alertdialog"
          aria-label="确认运行对照实验"
          className="mt-3 rounded-lg border border-hairline bg-hover px-3.5 py-3 text-body"
        >
          <div className="flex items-center gap-2">
            <IconAlert size={15} />
            将基于 run #{runId} 的冻结区间在本机运行多组变体（可能耗时），不会联网补数据。
          </div>
          <div className="mt-2 grid gap-2">
            {availableRules.length ? (
              <fieldset className="text-caption text-ink-2">
                <legend className="mb-1">单条卖出规则消融（可多选）</legend>
                <div className="flex flex-wrap gap-x-3 gap-y-1">
                  {availableRules.map((rule) => (
                    <label key={rule.name} className="flex items-center gap-1">
                      <input
                        type="checkbox"
                        checked={sellRules.includes(rule.name)}
                        onChange={(event) => {
                          setSellRules((current) =>
                            event.currentTarget.checked
                              ? [...current, rule.name]
                              : current.filter((name) => name !== rule.name)
                          );
                        }}
                      />
                      {rule.label}
                    </label>
                  ))}
                </div>
              </fieldset>
            ) : null}
            <div className="flex gap-2">
            <button type="button" className="btn btn-primary" onClick={run}>
              确认运行
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setConfirming(false)}
            >
              取消
            </button>
            </div>
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="alert-danger mt-3 rounded-lg px-3.5 py-2.5 text-body">
          {error}
        </div>
      ) : null}

      {!active ? (
        <EmptyState
          title="暂无已保存的对照实验"
          description="点击「运行对照实验」显式生成；仅查看不会重算。"
        />
      ) : (
        <div className="grid gap-3">
          <div className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
            <Badge tone="outline">报告 #{active.id}</Badge>
            <Badge tone="outline">版本 {active.experiment_version}</Badge>
            <Badge tone={statusTone(active.status ?? undefined)}>
              {labelOf(STATUS_LABELS, active.status, active.status ?? "—")}
            </Badge>
            <span>
              区间{" "}
              <span className="num text-ink">
                {String(period.date_from ?? "—")} ~ {String(period.date_to ?? "—")}
              </span>
            </span>
            <span>
              上游母集{" "}
              <span className="num text-ink-2">
                {String(upstream.candidate_universe_hash ?? "—")}
              </span>
            </span>
          </div>

          <div className="overflow-hidden rounded-lg border border-hairline">
            <DataTable
              columns={VARIANT_COLUMNS}
              rows={rows}
              rowKey={(row) => row.name}
              minWidth={760}
              empty="报告中暂无可比变体。"
            />
          </div>

          <p className="text-caption text-ink-3">
            不可比/证据不足的变体按原状态返回；不给虚假显著性，也不自动改变默认策略。
          </p>

          {history.length > 1 ? (
            <CollapsibleSection title={`历史报告（${history.length}）`}>
              <ul className="grid gap-1 text-caption">
                {history.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className="underline-offset-2 hover:underline"
                      onClick={() => setActive(item)}
                    >
                      #{item.id} · {item.created_at} · {item.status ?? "—"}
                    </button>
                  </li>
                ))}
              </ul>
            </CollapsibleSection>
          ) : null}
        </div>
      )}
    </Section>
  );
}
