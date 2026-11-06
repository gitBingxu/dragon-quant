import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createIncrementalAccountRun,
  deleteAccountRun,
  fetchAccountBenchmark,
  fetchAccountEvents,
  fetchAccountPositions,
  fetchAccountRuns,
  fetchAccountSnapshots,
  fetchAccountSummary,
  fetchAccountTrades,
  type AccountBenchmarkPoint,
  type AccountPosition,
  type AccountPositionStats,
  type AccountRun,
  type AccountSnapshot,
  type AccountSummary,
  type AccountTimelineEvent,
  type AccountTrade,
} from "../../api/account";
import { PageHeader } from "../../components/PageHeader";
import { HintIcon } from "../../components/HintIcon";
import { IconPlus, IconRefresh } from "../../components/icons";
import { IconAlert } from "../../components/icons-extra";
import { AccountStats } from "./AccountStats";
import { EntryReasonStats } from "./EntryReasonStats";
import { EquityCurve } from "./EquityCurve";
import { CurrentPosition } from "./CurrentPosition";
import { TradesTable } from "./TradesTable";
import { PositionsTable } from "./PositionsTable";
import { AccountTimeline } from "./AccountTimeline";
import { RunPicker } from "./RunPicker";
import { CreateRunModal } from "./CreateRunModal";
import { DeleteRunModal } from "./DeleteRunModal";
import { EmptyRuns } from "./EmptyRuns";
import { ExperimentPanel } from "./ExperimentPanel";

function clearDetail(
  setSnapshots: (value: AccountSnapshot[]) => void,
  setBenchmark: (value: AccountBenchmarkPoint[]) => void,
  setTrades: (value: AccountTrade[]) => void,
  setPositions: (value: AccountPosition[]) => void,
  setPositionStats: (value: AccountPositionStats | null) => void,
  setEvents: (value: AccountTimelineEvent[]) => void,
  setSummary: (value: AccountSummary | null) => void
) {
  setSnapshots([]);
  setBenchmark([]);
  setTrades([]);
  setPositions([]);
  setPositionStats(null);
  setEvents([]);
  setSummary(null);
}

export function AccountPage() {
  const [runs, setRuns] = useState<AccountRun[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [snapshots, setSnapshots] = useState<AccountSnapshot[]>([]);
  const [benchmark, setBenchmark] = useState<AccountBenchmarkPoint[]>([]);
  const [trades, setTrades] = useState<AccountTrade[]>([]);
  const [positions, setPositions] = useState<AccountPosition[]>([]);
  const [positionStats, setPositionStats] = useState<AccountPositionStats | null>(null);
  const [events, setEvents] = useState<AccountTimelineEvent[]>([]);
  const [summary, setSummary] = useState<AccountSummary | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [auxError, setAuxError] = useState<string | null>(null);
  const [partialError, setPartialError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<AccountRun | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [incrementing, setIncrementing] = useState(false);
  // 增量是原地追加：runId 不变，必须显式触发详情重取（否则图表/表格保持旧数据）。
  const [detailRefresh, setDetailRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // 响应必须绑定发起时的 runId：切换 run 后旧响应不得覆盖新数据（D3）。
  const requestSeq = useRef(0);
  const [detailRunId, setDetailRunId] = useState<number | null>(null);

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === runId) ?? null,
    [runs, runId]
  );
  const latest = snapshots[snapshots.length - 1] ?? null;

  const loadRuns = useCallback(async (preferred: number | null = null) => {
    const resp = await fetchAccountRuns();
    const data = resp.data || [];
    setRuns(data);
    setRunId((current) => {
      if (preferred != null && data.some((run) => run.id === preferred)) return preferred;
      if (current != null && data.some((run) => run.id === current)) return current;
      return data[0]?.id ?? null;
    });
  }, []);

  useEffect(() => {
    setRunId(null);
    setError(null);
    loadRuns().catch((exc) => {
      setRuns([]);
      setRunId(null);
      setError(exc instanceof Error ? exc.message : String(exc));
    });
  }, [loadRuns]);

  useEffect(() => {
    const requestId = ++requestSeq.current;
    setDetailRunId(null);
    setError(null);
    setAuxError(null);
    setPartialError(null);
    // 切换 run 立即清空旧详情并进入明确加载态，绝不混用新旧批次。
    clearDetail(
      setSnapshots,
      setBenchmark,
      setTrades,
      setPositions,
      setPositionStats,
      setEvents,
      setSummary
    );
    if (!runId) {
      setDetailLoading(false);
      return;
    }
    setDetailLoading(true);
    const isCurrent = () =>
      requestSeq.current === requestId;
    // 核心数据逐项降级：单个统计端点失败（如旧 run 的 summary 500）不得
    // 清空已加载的快照 / 交割单；只有全部失败才显示整体错误。
    Promise.allSettled([
      fetchAccountSnapshots(runId),
      fetchAccountTrades(runId),
      fetchAccountPositions(runId),
      fetchAccountSummary(runId),
    ])
      .then(([snapshotResult, tradeResult, positionResult, summaryResult]) => {
        if (!isCurrent()) return;
        const failures: string[] = [];
        if (snapshotResult.status === "fulfilled") {
          setSnapshots(snapshotResult.value.data || []);
        } else {
          setSnapshots([]);
          failures.push("权益快照");
        }
        if (tradeResult.status === "fulfilled") {
          setTrades(tradeResult.value.data || []);
        } else {
          setTrades([]);
          failures.push("交割单");
        }
        if (positionResult.status === "fulfilled") {
          setPositions(positionResult.value.data || []);
          setPositionStats(positionResult.value.stats || null);
        } else {
          setPositions([]);
          setPositionStats(null);
          failures.push("持仓与买点统计");
        }
        if (summaryResult.status === "fulfilled") {
          setSummary(summaryResult.value.data || null);
        } else {
          setSummary(null);
          failures.push("收益统计");
        }
        setDetailRunId(runId);
        if (failures.length === 4) {
          setError(`批次详情读取失败：${failures.join("、")}`);
        } else if (failures.length) {
          setPartialError(
            `部分数据暂不可用：${failures.join("、")}（其余结果仍可用）`
          );
        }
        // 辅助数据独立降级：失败不清核心详情（D3）。
        Promise.allSettled([
          fetchAccountBenchmark(runId),
          fetchAccountEvents(runId),
        ]).then(([benchmarkResult, eventResult]) => {
          if (!isCurrent()) return;
          const auxFailures: string[] = [];
          if (benchmarkResult.status === "fulfilled") {
            setBenchmark(benchmarkResult.value.data || []);
          } else {
            setBenchmark([]);
            auxFailures.push("基准对比");
          }
          if (eventResult.status === "fulfilled") {
            setEvents(eventResult.value.data || []);
          } else {
            setEvents([]);
            auxFailures.push("决策时间线");
          }
          setAuxError(
            auxFailures.length
              ? `辅助数据暂不可用：${auxFailures.join("、")}`
              : null
          );
        });
      })
      .finally(() => {
        if (isCurrent()) setDetailLoading(false);
      });
  }, [runId, detailRefresh]);

  const detailMatchesRun = runId != null && detailRunId === runId;

  const selectRun = useCallback((id: number) => {
    setActionError(null);
    setRunId(id);
  }, []);

  const runIncremental = useCallback(async () => {
    if (!runId || incrementing) return;
    setIncrementing(true);
    setActionError(null);
    try {
      const resp = await createIncrementalAccountRun(runId);
      try {
        await loadRuns(resp.data.id);
      } catch {
        // 追加成功但列表刷新失败：至少让用户看到并选中原记录。
        setRuns([resp.data]);
        setRunId(resp.data.id);
      }
      // 同一 runId 不会触发详情 effect，显式刷新快照/交割单/统计。
      setDetailRefresh((n) => n + 1);
    } catch (exc) {
      setActionError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setIncrementing(false);
    }
  }, [runId, incrementing, loadRuns]);

  const confirmDelete = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteAccountRun(deleteTarget.id);
      setDeleteTarget(null);
      await loadRuns();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setDeleting(false);
    }
  }, [deleteTarget, loadRuns]);

  return (
    <>
      <PageHeader
        eyebrow="ACCOUNT · 账户回测"
        title="账户模拟交易"
        description="按策略逐日撮合：权益与回撤曲线、上证同期、最新持仓、交割单、买点统计、单票汇总与决策时间线。红涨绿跌。"
        actions={
          <>
            {runs.length > 0 ? (
              <RunPicker
                runs={runs}
                runId={runId}
                onSelect={selectRun}
                onDelete={setDeleteTarget}
              />
            ) : null}
            <button type="button" className="btn btn-primary" onClick={() => setCreateOpen(true)}>
              <IconPlus size={15} />
              生成回测记录
            </button>
            <span className="inline-flex items-center gap-1.5">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={runIncremental}
                disabled={!runId || incrementing}
              >
                <IconRefresh size={15} />
                {incrementing ? "增量回测中…" : "增量回测"}
              </button>
              <HintIcon label="增量回测说明">
                在当前回测记录与操盘策略的基础上对增量数据执行回测
              </HintIcon>
            </span>
          </>
        }
      />

      {error ? (
        <div className="alert-danger mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
          <IconAlert size={15} />
          加载失败：{error}
        </div>
      ) : null}

      {actionError ? (
        <div className="alert-danger mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
          <IconAlert size={15} />
          {actionError}
        </div>
      ) : null}

      {detailLoading ? (
        <div className="mb-3 rounded-lg border border-hairline px-3.5 py-2.5 text-body text-ink-2">
          正在加载批次详情…
        </div>
      ) : null}

      {partialError ? (
        <div className="mb-3 rounded-lg border border-warning-line bg-warning-soft px-3.5 py-2.5 text-caption text-warning">
          {partialError}
        </div>
      ) : null}

      {auxError ? (
        <div className="mb-3 rounded-lg border border-hairline px-3.5 py-2.5 text-caption text-ink-2">
          {auxError}（核心结果仍可用）
        </div>
      ) : null}

      <CreateRunModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(run) => {
          loadRuns(run.id).catch(() => {
            setRuns([run]);
            setRunId(run.id);
          });
        }}
      />

      <DeleteRunModal
        run={deleteTarget}
        loading={deleting}
        onClose={() => {
          if (!deleting) setDeleteTarget(null);
        }}
        onConfirm={confirmDelete}
      />

      {runs.length === 0 ? (
        <EmptyRuns onCreate={() => setCreateOpen(true)} />
      ) : (
        <div className="grid gap-3">
          <AccountStats
            run={selectedRun}
            latest={detailMatchesRun ? latest : null}
            summary={detailMatchesRun ? summary : null}
          />
          <EntryReasonStats stats={detailMatchesRun ? positionStats : null} />

          <div className="grid gap-3 lg:grid-cols-12">
            <div className="lg:col-span-8">
              <EquityCurve data={snapshots} benchmark={benchmark} />
            </div>
            <div className="lg:col-span-4 self-start">
              <CurrentPosition latest={latest} />
            </div>
          </div>

          <TradesTable trades={detailMatchesRun ? trades : []} />
          <PositionsTable positions={detailMatchesRun ? positions : []} />
          <AccountTimeline
            events={detailMatchesRun ? events : []}
            positions={detailMatchesRun ? positions : []}
          />
          {detailMatchesRun && runId != null ? (
            <ExperimentPanel runId={runId} />
          ) : null}
        </div>
      )}
    </>
  );
}
