import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  fetchDragonDetail,
  fetchDragonPerformance,
  type DragonDetail,
  type DragonPerformance,
} from "../../api/dragons";
import type { ReviewCaliber } from "../../api/dragons";
import { Badge } from "../../components/Badge";
import { CollapsibleSection } from "../../components/CollapsibleSection";
import { Drawer } from "../../components/Drawer";
import { IconAlert } from "../../components/icons-extra";
import { IconRefresh } from "../../components/icons";
import { fmtNum, fmtPct, pnlClass } from "../../lib/format";
import { DimBars } from "./DimBars";

export function DragonDetailDrawer({
  selected,
  caliber,
  onClose,
}: {
  selected: { date: string; code: string } | null;
  caliber: ReviewCaliber;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<DragonDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [performance, setPerformance] = useState<DragonPerformance | null>(null);
  const [perfError, setPerfError] = useState<string | null>(null);
  const [perfLoading, setPerfLoading] = useState(false);
  const [perfReload, setPerfReload] = useState(0);

  const selectedDate = selected?.date;
  const selectedCode = selected?.code;

  useEffect(() => {
    if (!selectedDate || !selectedCode) return;
    let active = true;
    setLoading(true);
    setDetail(null);
    setError(null);
    fetchDragonDetail(selectedDate, selectedCode, caliber)
      .then((payload) => {
        if (active) setDetail(payload);
      })
      .catch((exc: unknown) => {
        if (active) setError(exc instanceof Error ? exc.message : String(exc));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedDate, selectedCode, caliber]);

  // 绩效是独立请求：只依赖选中标的，切换口径不重复拉取。
  useEffect(() => {
    if (!selectedDate || !selectedCode) return;
    let active = true;
    setPerfLoading(true);
    setPerformance(null);
    setPerfError(null);
    fetchDragonPerformance(selectedDate, selectedCode)
      .then((payload) => {
        if (active) setPerformance(payload);
      })
      .catch((exc: unknown) => {
        if (active) setPerfError(exc instanceof Error ? exc.message : String(exc));
      })
      .finally(() => {
        if (active) setPerfLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedDate, selectedCode, perfReload]);

  return (
    <Drawer
      open={selected !== null}
      onClose={onClose}
      title={
        detail ? (
          <span className="flex items-center gap-2">
            <span className="num text-body text-ink-2">{detail.code}</span>
            <span>{detail.name}</span>
          </span>
        ) : (
          "龙头明细"
        )
      }
      subtitle={
        detail
          ? `${detail.trade_date} · 排名 ${detail.rank ?? "—"} · 综合分 ${fmtNum(detail.composite_score, 1)}`
          : selected
            ? `${selected.date} · ${selected.code}`
            : undefined
      }
    >
      {error ? (
        <div className="alert-danger mb-4 flex items-center gap-2 rounded-lg px-3 py-2 text-body">
          <IconAlert size={15} />
          {error}
        </div>
      ) : null}
      {loading ? <div className="py-10 text-center text-body text-ink-2">正在加载…</div> : null}
      {detail ? (
        <DetailBody
          detail={detail}
          performance={performance}
          perfError={perfError}
          perfLoading={perfLoading}
          onRetryPerformance={() => setPerfReload((value) => value + 1)}
        />
      ) : null}
    </Drawer>
  );
}

function DetailBody({
  detail,
  performance,
  perfError,
  perfLoading,
  onRetryPerformance,
}: {
  detail: DragonDetail;
  performance: DragonPerformance | null;
  perfError: string | null;
  perfLoading: boolean;
  onRetryPerformance: () => void;
}) {
  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={detail.is_true_dragon ? "brand" : "neutral"} dot>
          {detail.is_true_dragon ? "真龙" : "非真龙"}
        </Badge>
        <Badge tone="outline">{detail.caliber === "strict" ? "严格口径" : "宽松口径"}</Badge>
        {detail.is_true_dragon === false && detail.reject_reason ? (
          <Badge tone="warning">已否决</Badge>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2.5 rounded-lg border border-hairline bg-raise px-3.5 py-3 sm:grid-cols-4">
        <Meta label="入选日" value={detail.trade_date} mono />
        <Meta label="排名" value={detail.rank ?? "—"} mono />
        <Meta label="连板" value={detail.board_count ?? "—"} mono />
        <Meta label="综合分" value={fmtNum(detail.composite_score, 1)} mono />
      </div>

      <PerformanceCard
        performance={performance}
        error={perfError}
        loading={perfLoading}
        onRetry={onRetryPerformance}
      />

      {detail.concepts.length ? (
        <div>
          <h3 className="mb-2 text-caption font-semibold uppercase tracking-[0.09em] text-ink-2">
            概念
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {detail.concepts.map((concept) => (
              <Badge key={concept} tone="neutral">
                {concept}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}

      <div>
        <div className="mb-2.5 flex items-baseline justify-between">
          <h3 className="text-caption font-semibold uppercase tracking-[0.09em] text-ink-2">
            五维评分
          </h3>
          <span className="font-mono text-caption text-ink-3">
            dims · {detail.dims_source}
          </span>
        </div>
        <DimBars dims={detail.dims} />
      </div>

      {detail.reject_reason ? (
        <div className="rounded-lg border border-warning-line bg-warning-soft px-3.5 py-2.5">
          <div className="mb-0.5 text-caption font-semibold uppercase tracking-[0.08em] text-warning">
            否决原因
          </div>
          <div className="text-body text-ink">{detail.reject_reason}</div>
        </div>
      ) : null}

      {detail.report_text ? (
        <CollapsibleSection title="扫描报告原文" defaultOpen>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap font-mono text-body leading-relaxed text-ink-2">
            {detail.report_text}
          </pre>
        </CollapsibleSection>
      ) : null}
    </div>
  );
}

function PerformanceCard({
  performance,
  error,
  loading,
  onRetry,
}: {
  performance: DragonPerformance | null;
  error: string | null;
  loading: boolean;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-lg border border-hairline bg-raise px-3.5 py-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-caption font-semibold uppercase tracking-[0.09em] text-ink-2">
          入选后表现
        </h3>
        <Badge tone="outline">宽松口径</Badge>
        <span className="text-caption text-ink-3">至最新日 K</span>
      </div>

      {loading ? (
        <div className="py-2 text-body text-ink-2">正在计算入选后表现…</div>
      ) : error ? (
        <PerfError message={error} onRetry={onRetry} />
      ) : performance ? (
        <PerformanceBody performance={performance} onRetry={onRetry} />
      ) : null}
    </div>
  );
}

function PerfError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-body text-warning">
      <span>绩效加载失败：{message}</span>
      <button type="button" className="btn btn-ghost" onClick={onRetry}>
        <IconRefresh size={13} />
        重试
      </button>
    </div>
  );
}

function PerformanceBody({
  performance,
  onRetry,
}: {
  performance: DragonPerformance;
  onRetry: () => void;
}) {
  if (performance.status === "error") {
    return <PerfError message={performance.error ?? "未知错误"} onRetry={onRetry} />;
  }

  if (performance.status === "no_entry") {
    return (
      <div className="text-body text-ink-2">
        入选后无可介入日（连续一字板 / 停牌 / 尚无后续交易日）。
      </div>
    );
  }

  const bought = (
    <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
      <Meta label="买入日" value={performance.buy_date ?? "—"} mono />
      <Meta label="买入价" value={fmtNum(performance.buy_price)} mono />
      <Meta label="观测截止" value={performance.as_of} mono />
    </div>
  );

  if (performance.status === "empty_window") {
    return (
      <div className="grid gap-2">
        {bought}
        <div className="text-body text-ink-2">已买入，暂无后续日 K。</div>
      </div>
    );
  }

  return (
    <div className="grid gap-2">
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
        <Meta label="买入日" value={performance.buy_date ?? "—"} mono />
        <Meta label="买入价" value={fmtNum(performance.buy_price)} mono />
        <Meta label="最新日 K" value={performance.latest_bar_date ?? "—"} mono />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-md border border-hairline px-2.5 py-2">
          <div className="text-caption uppercase tracking-[0.08em] text-ink-3">
            最大收益（至最新日 K）
          </div>
          <div className={`num mt-0.5 text-title ${pnlClass(performance.max_return_since_entry)}`}>
            {fmtPct(performance.max_return_since_entry)}
          </div>
          <div className="text-caption text-ink-3">峰值日 {performance.peak_date ?? "—"}</div>
        </div>
        <div className="rounded-md border border-hairline px-2.5 py-2">
          <div className="text-caption uppercase tracking-[0.08em] text-ink-3">
            最大回撤（买入日 → 峰值日）
          </div>
          <div className={`num mt-0.5 text-title ${pnlClass(performance.max_drawdown_since_entry)}`}>
            {fmtPct(performance.max_drawdown_since_entry)}
          </div>
          <div className="text-caption text-ink-3">峰值后的新低不计入</div>
        </div>
      </div>
      <div className="font-mono text-caption text-ink-3">
        窗口 {performance.bars_in_window ?? "—"} 根 · 买入后 {performance.bars_after_entry ?? "—"} 根
      </div>
    </div>
  );
}

function Meta({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div>
      <div className="text-caption uppercase tracking-[0.08em] text-ink-3">{label}</div>
      <div className={`mt-0.5 text-body text-ink ${mono ? "num" : ""}`}>{value}</div>
    </div>
  );
}
