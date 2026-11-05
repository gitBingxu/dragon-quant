import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchBsBuys,
  fetchBsJobStatus,
  fetchBsOverview,
  fetchBsSells,
  postBsRebuild,
  type BsBuyRow,
  type BsBuySummary,
  type BsJobStatus,
  type BsOverview,
  type BsSellRow,
  type BsSellSummary,
} from "../../api/bs";
import { ApiError } from "../../api/client";
import { Badge, type BadgeTone } from "../../components/Badge";
import type { Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { Modal } from "../../components/Modal";
import { PageHeader } from "../../components/PageHeader";
import { PaginatedDataTable } from "../../components/PaginatedDataTable";
import { Section } from "../../components/Section";
import { Select, TextField } from "../../components/Select";
import { StatCard } from "../../components/StatCard";
import { IconAlert } from "../../components/icons-extra";
import { fmtNum, fmtPct, pnlClass } from "../../lib/format";
import { StrategyCard } from "./StrategyCard";

const QUALITY_LABELS: Record<string, string> = {
  strict_5min: "严格5分钟",
  daily_fallback: "日K兜底",
  mixed: "混合",
};

function qualityLabel(value: string | null): string {
  if (!value) return "—";
  return QUALITY_LABELS[value] ?? value;
}

function fmtRate(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(2)}%`;
}

function msg(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

function resultBadge(row: BsBuyRow) {
  if (row.buy_window_partial) return <Badge tone="muted">窗口不足</Badge>;
  if (row.strict_win === true) return <Badge tone="up">胜</Badge>;
  if (row.strict_win === false) return <Badge tone="down">负</Badge>;
  return <Badge tone="muted">—</Badge>;
}

const VERDICT_TONE: Record<string, BadgeTone> = {
  flew: "warning",
  good: "positive",
  neutral: "muted",
  partial: "muted",
};

const BUY_COLUMNS: Column<BsBuyRow>[] = [
  {
    key: "trade_date",
    header: "入选日",
    sortable: true,
    render: (row) => <span className="num text-ink-2">{row.trade_date}</span>,
  },
  {
    key: "stock",
    header: "代码 / 名称",
    render: (row) => (
      <span className="text-body text-ink">
        <span className="num text-ink-3">{row.code}</span> {row.name}
      </span>
    ),
  },
  {
    key: "rule",
    header: "买点",
    render: (row) => (
      <span className="text-body text-ink">{row.buy_rule_label ?? row.buy_rule_code ?? "—"}</span>
    ),
  },
  {
    key: "buy_date",
    header: "信号日",
    sortable: true,
    render: (row) => <span className="num text-ink-2">{row.buy_date ?? "—"}</span>,
  },
  {
    key: "loose_price",
    header: "宽松价",
    align: "right",
    render: (row) => <span className="num">{fmtNum(row.buy_loose_price)}</span>,
  },
  {
    key: "strict_price",
    header: "严格价",
    align: "right",
    render: (row) => <span className="num">{fmtNum(row.buy_strict_price)}</span>,
  },
  {
    key: "loose_max_return_5d",
    header: "宽松 5 日",
    align: "right",
    sortable: true,
    render: (row) => (
      <span className="num">
        <span className={pnlClass(row.buy_loose_max_return_5d)}>
          {fmtPct(row.buy_loose_max_return_5d)}
        </span>
        <span className="text-ink-3"> / </span>
        <span className={pnlClass(row.buy_loose_max_drawdown_5d)}>
          {fmtPct(row.buy_loose_max_drawdown_5d)}
        </span>
      </span>
    ),
  },
  {
    key: "strict_max_return_5d",
    header: "严格 5 日",
    align: "right",
    sortable: true,
    render: (row) => (
      <span className="num">
        <span className={pnlClass(row.buy_strict_max_return_5d)}>
          {fmtPct(row.buy_strict_max_return_5d)}
        </span>
        <span className="text-ink-3"> / </span>
        <span className={pnlClass(row.buy_strict_max_drawdown_5d)}>
          {fmtPct(row.buy_strict_max_drawdown_5d)}
        </span>
      </span>
    ),
  },
  {
    key: "result",
    header: "结果",
    align: "center",
    render: (row) => resultBadge(row),
  },
  {
    key: "quality",
    header: "数据质量",
    render: (row) => (
      <span className="text-caption text-ink-2">
        {qualityLabel(row.buy_data_quality ?? row.data_quality)}
      </span>
    ),
  },
];

const SELL_COLUMNS: Column<BsSellRow>[] = [
  {
    key: "trade_date",
    header: "入选日",
    sortable: true,
    render: (row) => <span className="num text-ink-2">{row.trade_date}</span>,
  },
  {
    key: "stock",
    header: "代码 / 名称",
    render: (row) => (
      <span className="text-body text-ink">
        <span className="num text-ink-3">{row.code}</span> {row.name}
      </span>
    ),
  },
  {
    key: "rule",
    header: "卖出规则",
    render: (row) => (
      <span className="text-body text-ink">{row.sell_rule_label ?? row.sell_rule_code ?? "—"}</span>
    ),
  },
  {
    key: "sell_date",
    header: "卖出日",
    sortable: true,
    render: (row) => <span className="num text-ink-2">{row.sell_date ?? "—"}</span>,
  },
  {
    key: "sell_price",
    header: "卖出价",
    align: "right",
    render: (row) => <span className="num">{fmtNum(row.sell_price)}</span>,
  },
  {
    key: "sell_max_return_5d",
    header: "卖后 5 日",
    align: "right",
    sortable: true,
    render: (row) => (
      <span className="num">
        <span className={pnlClass(row.sell_max_return_5d)}>
          {fmtPct(row.sell_max_return_5d)}
        </span>
        <span className="text-ink-3"> / </span>
        <span className={pnlClass(row.sell_max_drawdown_5d)}>
          {fmtPct(row.sell_max_drawdown_5d)}
        </span>
      </span>
    ),
  },
  {
    key: "verdict",
    header: "判定",
    align: "center",
    render: (row) => (
      <Badge tone={VERDICT_TONE[row.verdict] ?? "muted"}>{row.verdict_label}</Badge>
    ),
  },
  {
    key: "half",
    header: "半仓腿",
    render: (row) => (
      <span className="num text-ink-2">
        {row.half_exit
          ? `${row.half_exit.date} @ ${fmtNum(row.half_exit.price)} × ${row.half_exit.qty ?? "—"}`
          : "—"}
      </span>
    ),
  },
  {
    key: "quality",
    header: "数据质量",
    render: (row) => (
      <span className="text-caption text-ink-2">
        {qualityLabel(row.sell_data_quality ?? row.data_quality)}
      </span>
    ),
  },
];

function RebuildButton({
  running,
  submitting,
  onClick,
}: {
  running: boolean;
  submitting: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="btn btn-primary"
      disabled={running || submitting}
      onClick={onClick}
    >
      {running ? "生成中…" : "重新计算买卖点"}
    </button>
  );
}

export function BsPage() {
  const [overview, setOverview] = useState<BsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [job, setJob] = useState<BsJobStatus | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [postError, setPostError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const wasRunning = useRef(false);
  const pollFailures = useRef(0);
  const initialStatusHandled = useRef(false);

  const jobState = job?.state ?? "idle";
  const running = jobState === "running";

  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [includePartial, setIncludePartial] = useState(true);
  const [buyRule, setBuyRule] = useState("");
  const [sellRule, setSellRule] = useState("");
  const [buySort, setBuySort] = useState<{ by: string; dir: "asc" | "desc" }>({
    by: "buy_date",
    dir: "desc",
  });
  const [sellSort, setSellSort] = useState<{ by: string; dir: "asc" | "desc" }>({
    by: "sell_date",
    dir: "desc",
  });

  const [buys, setBuys] = useState<BsBuyRow[]>([]);
  const [buySummary, setBuySummary] = useState<BsBuySummary | null>(null);
  const [sells, setSells] = useState<BsSellRow[]>([]);
  const [sellSummary, setSellSummary] = useState<BsSellSummary | null>(null);
  const [tableError, setTableError] = useState<string | null>(null);
  const requestSeq = useRef(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchBsOverview()
      .then((payload) => {
        if (!active) return;
        setOverview(payload);
        setError(null);
      })
      .catch((exc: unknown) => {
        if (!active) return;
        setOverview(null);
        setError(msg(exc));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reloadToken]);

  useEffect(() => {
    let active = true;
    fetchBsJobStatus()
      .then((payload) => {
        if (!active) return;
        setJob(payload);
        setJobError(null);
        // 首屏恰逢任务完成：overview 可能先读到旧数据而 status 已是 succeeded，
        // 此时本页从未观察到 running，不会触发 running→succeeded 的重拉，
        // 因此补一次（仅首屏一次，不影响后续轮询）。
        if (!initialStatusHandled.current) {
          initialStatusHandled.current = true;
          if (payload.state === "succeeded") {
            setReloadToken((value) => value + 1);
          }
        }
      })
      .catch((exc: unknown) => {
        if (active) setJobError(msg(exc));
      });
    return () => {
      active = false;
    };
  }, []);

  // running 时 ~3s 轮询；成功后触发一次数据重拉，连续 5 次失败停止轮询。
  useEffect(() => {
    if (!running) {
      if (wasRunning.current) {
        wasRunning.current = false;
        if (jobState === "succeeded") setReloadToken((value) => value + 1);
      }
      return;
    }
    wasRunning.current = true;
    pollFailures.current = 0;
    let active = true;
    const timer = window.setInterval(() => {
      fetchBsJobStatus()
        .then((payload) => {
          if (!active) return;
          pollFailures.current = 0;
          setJob(payload);
          setJobError(null);
        })
        .catch((exc: unknown) => {
          if (!active) return;
          pollFailures.current += 1;
          if (pollFailures.current >= 5) {
            window.clearInterval(timer);
            setJobError(msg(exc));
            setJob((prev) => (prev ? { ...prev, state: "idle" } : prev));
          }
        });
    }, 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [running, jobState]);

  const handleRebuild = useCallback(async () => {
    setConfirmOpen(false);
    setSubmitting(true);
    setPostError(null);
    try {
      const payload = await postBsRebuild();
      setJob(payload);
      setJobError(null);
    } catch (exc: unknown) {
      if (exc instanceof ApiError && exc.status === 409) {
        // 幂等：已在运行，直接进入 running 横幅并开始轮询。
        setJob((prev) =>
          prev
            ? { ...prev, state: "running" }
            : { state: "running", autogen_enabled: false, last_run: null }
        );
        setJobError(null);
      } else {
        setPostError(msg(exc));
      }
    } finally {
      setSubmitting(false);
    }
  }, []);

  const baseFilters = useMemo(
    () => ({
      date_from: dateFrom,
      date_to: dateTo,
      code,
      name,
      partial: includePartial ? "include" : "exclude",
    }),
    [dateFrom, dateTo, code, name, includePartial]
  );

  const loadBuys = useCallback(async () => {
    return fetchBsBuys({
      ...baseFilters,
      rule: buyRule,
      sort_by: buySort.by,
      sort_dir: buySort.dir,
    });
  }, [baseFilters, buyRule, buySort]);

  const loadSells = useCallback(async () => {
    return fetchBsSells({
      ...baseFilters,
      rule: sellRule,
      sort_by: sellSort.by,
      sort_dir: sellSort.dir,
    });
  }, [baseFilters, sellRule, sellSort]);

  useEffect(() => {
    if (!overview?.run) {
      requestSeq.current += 1;
      setBuys([]);
      setSells([]);
      setBuySummary(null);
      setSellSummary(null);
      return;
    }
    let active = true;
    const seq = ++requestSeq.current;
    setTableError(null);
    Promise.all([loadBuys(), loadSells()])
      .then(([buyPayload, sellPayload]) => {
        if (!active || seq !== requestSeq.current) return;
        setBuys(buyPayload.data);
        setBuySummary(buyPayload.summary);
        setSells(sellPayload.data);
        setSellSummary(sellPayload.summary);
      })
      .catch((exc: unknown) => {
        if (!active || seq !== requestSeq.current) return;
        setTableError(msg(exc));
      });
    return () => {
      active = false;
    };
  }, [overview?.run, loadBuys, loadSells, reloadToken]);

  const toggleSort = (
    current: { by: string; dir: "asc" | "desc" },
    setter: (next: { by: string; dir: "asc" | "desc" }) => void,
    key: string
  ) => {
    setter(
      current.by === key
        ? { by: key, dir: current.dir === "asc" ? "desc" : "asc" }
        : { by: key, dir: "desc" }
    );
  };

  const summary = overview?.summary;
  const strategy = overview?.strategy;

  return (
    <>
      <PageHeader
        eyebrow="BS · 策略回放"
        title="BS策略"
        description="假想买卖点回放：每只龙头入选后逐日评估四类买点与十条卖出规则，统计买后/卖后 5 日表现，为策略优化提供样本。查看面板默认只读；需要重算时点击「重新计算买卖点」显式运行。"
      />

      {running ? (
        <div
          role="status"
          aria-live="polite"
          className="mb-3 flex items-center gap-2 rounded-lg border border-hairline bg-raise px-3.5 py-2.5 text-body text-ink-2"
        >
          <IconAlert size={15} />
          数据生成中…（{job?.last_run?.mode === "force" ? "全量重置" : "增量生成"}）
          {job?.last_run?.started_at ? ` · 开始于 ${job.last_run.started_at}` : ""}
        </div>
      ) : null}

      {jobState === "failed" && job?.last_run ? (
        <div className="alert-danger mb-3 rounded-lg px-3.5 py-2.5 text-body">
          <div className="flex items-center gap-2">
            <IconAlert size={15} />
            BS 数据生成失败
            {job.last_run.exit_code != null ? `（exit ${job.last_run.exit_code}）` : ""}
          </div>
          {job.last_run.output_tail ? (
            <details className="mt-2">
              <summary className="cursor-pointer text-caption text-ink-2">
                查看输出摘要
              </summary>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words text-caption text-ink-2">
                {job.last_run.output_tail}
              </pre>
            </details>
          ) : null}
        </div>
      ) : null}

      {jobError ? (
        <div className="alert-danger mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
          <IconAlert size={15} />
          状态读取失败：{jobError}
        </div>
      ) : null}

      {postError ? (
        <div className="alert-danger mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
          <IconAlert size={15} />
          重置失败：{postError}
        </div>
      ) : null}

      {error ? (
        <div className="alert-danger mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
          <IconAlert size={15} />
          加载失败：{error}
        </div>
      ) : null}

      {tableError ? (
        <div className="alert-danger mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
          <IconAlert size={15} />
          列表加载失败：{tableError}
        </div>
      ) : null}

      {!overview ? (
        <Section>
          <EmptyState
            title={
              loading
                ? "正在读取回放结果…"
                : running
                  ? "数据生成中…"
                  : "暂无 BS 回放数据"
            }
            description={
              loading
                ? "读取最近一次 bs-eval 批次与全量样本。"
                : "查看面板不会隐式生成；需要时点击「重新计算买卖点」，或在终端显式执行 python -m dragon_quant bs-eval。"
            }
            action={
              !loading ? (
                <RebuildButton
                  running={running}
                  submitting={submitting}
                  onClick={() => setConfirmOpen(true)}
                />
              ) : null
            }
          />
        </Section>
      ) : !overview.run ? (
        <Section>
          <EmptyState
            title={running ? "数据生成中…" : "暂无 BS 回放数据"}
            description="查看面板不会隐式生成/重算；需要时点击「重新计算买卖点」，或在终端显式执行 python -m dragon_quant bs-eval。"
            action={
              <RebuildButton
                running={running}
                submitting={submitting}
                onClick={() => setConfirmOpen(true)}
              />
            }
          />
        </Section>
      ) : (
        <div className="grid gap-3">
          {overview.stale_rows > 0 ? (
            <div className="flex items-center gap-2 rounded-lg border border-warning-line bg-warning-soft px-3.5 py-2.5 text-body text-warning-text">
              <IconAlert size={15} />
              有 {overview.stale_rows} 行来自旧策略版本，未计入当前统计；可点击「重新计算买卖点」
              全量重算，或在终端执行 python -m dragon_quant bs-eval --force。
            </div>
          ) : null}

          {summary ? (
            <Section
              title="汇总"
              description={
                overview.run
                  ? `最近一次运行 #${overview.run.id} · ${overview.run.started_at}；本块为固定分母的全局统计（当前策略版本全部样本），不随下方筛选变化。`
                  : "当前策略版本全部样本。"
              }
              right={
                <>
                  {overview.run ? (
                    <Badge tone="outline">
                      扫描 {overview.run.counters.rows_scanned} · 评估 {overview.run.counters.rows_evaluated}
                    </Badge>
                  ) : null}
                  <RebuildButton
                    running={running}
                    submitting={submitting}
                    onClick={() => setConfirmOpen(true)}
                  />
                </>
              }
            >
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard label="样本数" value={summary.rows_total} sub="当前策略版本全部行" />
                <StatCard
                  label="买入触发率"
                  value={fmtRate(summary.buy.trigger_rate)}
                  sub={`触发 ${summary.buy.triggered} / 可评估`}
                />
                <StatCard
                  label="严格口径胜率"
                  value={fmtRate(summary.buy.strict.win_rate)}
                  tone="up"
                  sub={`${summary.buy.strict.wins} / ${summary.buy.strict.samples} 样本`}
                />
                <StatCard
                  label="宽松口径胜率"
                  value={fmtRate(summary.buy.loose.win_rate)}
                  tone="up"
                  sub={`${summary.buy.loose.wins} / ${summary.buy.loose.samples} 样本`}
                />
                <StatCard
                  label="买入平均最大收益"
                  value={fmtPct(summary.buy.strict.avg_return)}
                  tone="up"
                  sub={`平均回撤 ${fmtPct(summary.buy.strict.avg_drawdown)}`}
                />
                <StatCard label="卖出样本数" value={summary.sell.samples} sub={`触发 ${summary.sell.triggered}`} />
                <StatCard
                  label="卖后错失上涨"
                  value={fmtRate(summary.sell.missed_upside_rate)}
                  tone="warning"
                  sub={`${summary.sell.missed_upside} / ${summary.sell.axes_denominator} 可评价`}
                />
                <StatCard
                  label="卖后避开下跌"
                  value={fmtRate(summary.sell.avoided_downside_rate)}
                  tone="positive"
                  sub={`${summary.sell.avoided_downside} / ${summary.sell.axes_denominator}；两轴可同时成立`}
                />
              </div>
            </Section>
          ) : null}

          <Section
            title="筛选"
            description="按入选日、代码/名称与规则过滤；排序走服务端白名单。"
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
              <TextField label="入选日起" type="date" value={dateFrom} onChange={setDateFrom} />
              <TextField label="入选日止" type="date" value={dateTo} onChange={setDateTo} />
              <TextField label="代码" value={code} onChange={setCode} placeholder="600001" />
              <TextField label="名称" value={name} onChange={setName} placeholder="龙头" />
              <Select
                label="买点"
                value={buyRule}
                onChange={setBuyRule}
                options={[
                  { value: "", label: "全部买点" },
                  ...(strategy?.buy_signals ?? []).map((item) => ({
                    value: item.code,
                    label: item.label,
                  })),
                ]}
              />
              <Select
                label="卖出规则"
                value={sellRule}
                onChange={setSellRule}
                options={[
                  { value: "", label: "全部规则" },
                  ...(strategy?.sell_rules ?? []).map((item) => ({
                    value: item.name,
                    label: item.label,
                  })),
                ]}
              />
            </div>
            <label className="mt-3 flex items-center gap-2 text-body text-ink-2">
              <input
                type="checkbox"
                checked={includePartial}
                onChange={(event) => setIncludePartial(event.currentTarget.checked)}
              />
              显示窗口不足样本（不计入胜率）
            </label>
          </Section>

          <Section
            title="买入信号 × 买后 5 日"
            description="本表汇总与当前筛选（含买点）同基集；宽松 = 信号日最低价，严格 = 引擎实际成交价。窗口不足行标记为「窗口不足」。"
            right={
              buySummary ? (
                <Badge tone="outline">
                  触发 {buySummary.triggered} · 严格胜率 {fmtRate(buySummary.win_rate)}
                </Badge>
              ) : null
            }
            bodyClassName="p-0"
            className="overflow-hidden"
          >
            {buys.length === 0 ? (
              <EmptyState title="暂无买入信号" description="当前筛选下没有触发买点的龙头。" />
            ) : (
              <PaginatedDataTable
                columns={BUY_COLUMNS}
                rows={buys}
                rowKey={(row) => `${row.trade_date}-${row.code}`}
                sort={buySort}
                onSort={(key) => toggleSort(buySort, setBuySort, key)}
                minWidth={960}
                resetKey={JSON.stringify({ baseFilters, buyRule, buySort })}
              />
            )}
          </Section>

          <Section
            title="卖出信号 × 卖后 5 日"
            description="送出口径 = 卖出日之后 5 根有效日K（不含卖出当日、不跳过次日）；上涨机会与下跌风险分两轴，半仓腿独立样本。"
            right={
              <Badge tone="outline">
                卖出 {sells.length} · 错失上涨 {sellSummary?.missed_upside ?? 0}
                {" · "}避开下跌 {sellSummary?.avoided_downside ?? 0}
              </Badge>
            }
            bodyClassName="p-0"
            className="overflow-hidden"
          >
            {sells.length === 0 ? (
              <EmptyState title="暂无卖出信号" description="当前筛选下没有已卖出的龙头。" />
            ) : (
              <PaginatedDataTable
                columns={SELL_COLUMNS}
                rows={sells}
                rowKey={(row) => `${row.trade_date}-${row.code}`}
                sort={sellSort}
                onSort={(key) => toggleSort(sellSort, setSellSort, key)}
                minWidth={960}
                resetKey={JSON.stringify({ baseFilters, sellRule, sellSort })}
              />
            )}
          </Section>

          {strategy ? (
            <details className="rounded-lg border border-hairline bg-panel p-4">
              <summary className="cursor-pointer text-title font-semibold text-ink">
                策略方法（四类买点 / 十条卖出规则）
              </summary>
              <div className="mt-3">
                <StrategyCard strategy={strategy} run={overview.run} />
              </div>
            </details>
          ) : null}
        </div>
      )}

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="重新计算买卖点？"
        description="范围：全部 dragons_v2 行；版本：新评测器（旧行将标记 stale）；副作用：写入 bs_runs / bs_signals 派生表并可能耗时数分钟。纯查看不会触发重算。"
        footer={
          <>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setConfirmOpen(false)}
              disabled={submitting}
            >
              取消
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleRebuild}
              disabled={submitting}
            >
              开始重新计算
            </button>
          </>
        }
      />
    </>
  );
}
