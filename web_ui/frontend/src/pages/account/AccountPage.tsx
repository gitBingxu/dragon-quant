import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createIncrementalAccountRun,
  deleteAccountRun,
  fetchAccountBenchmark,
  fetchAccountEvents,
  fetchAccountPositions,
  fetchAccountRuns,
  fetchAccountSnapshots,
  fetchAccountTrades,
  type AccountBenchmarkPoint,
  type AccountPosition,
  type AccountPositionStats,
  type AccountRun,
  type AccountSnapshot,
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

function clearDetail(
  setSnapshots: (value: AccountSnapshot[]) => void,
  setBenchmark: (value: AccountBenchmarkPoint[]) => void,
  setTrades: (value: AccountTrade[]) => void,
  setPositions: (value: AccountPosition[]) => void,
  setPositionStats: (value: AccountPositionStats | null) => void,
  setEvents: (value: AccountTimelineEvent[]) => void
) {
  setSnapshots([]);
  setBenchmark([]);
  setTrades([]);
  setPositions([]);
  setPositionStats(null);
  setEvents([]);
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
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<AccountRun | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [incrementing, setIncrementing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

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
    if (!runId) {
      clearDetail(
        setSnapshots,
        setBenchmark,
        setTrades,
        setPositions,
        setPositionStats,
        setEvents
      );
      return;
    }
    let active = true;
    setError(null);
    Promise.all([
      fetchAccountSnapshots(runId),
      fetchAccountBenchmark(runId),
      fetchAccountTrades(runId),
      fetchAccountPositions(runId),
      fetchAccountEvents(runId),
    ])
      .then(([snapshotResp, benchmarkResp, tradeResp, positionResp, eventResp]) => {
        if (!active) return;
        setSnapshots(snapshotResp.data || []);
        setBenchmark(benchmarkResp.data || []);
        setTrades(tradeResp.data || []);
        setPositions(positionResp.data || []);
        setPositionStats(positionResp.stats || null);
        setEvents(eventResp.data || []);
      })
      .catch((exc) => {
        if (!active) return;
        clearDetail(
          setSnapshots,
          setBenchmark,
          setTrades,
          setPositions,
          setPositionStats,
          setEvents
        );
        setError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      active = false;
    };
  }, [runId]);

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
        // 新记录已生成但列表刷新失败：至少让用户看到并选中它。
        setRuns([resp.data]);
        setRunId(resp.data.id);
      }
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
          <AccountStats run={selectedRun} latest={latest} />
          <EntryReasonStats stats={positionStats} />

          <div className="grid gap-3 lg:grid-cols-12">
            <div className="lg:col-span-8">
              <EquityCurve data={snapshots} benchmark={benchmark} />
            </div>
            <div className="lg:col-span-4 self-start">
              <CurrentPosition latest={latest} />
            </div>
          </div>

          <TradesTable trades={trades} />
          <PositionsTable positions={positions} />
          <AccountTimeline events={events} positions={positions} />
        </div>
      )}
    </>
  );
}
