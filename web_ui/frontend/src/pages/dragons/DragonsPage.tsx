import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchDragons } from "../../api/dragons";
import type {
  Dragon,
  DragonFilters,
  ReviewCaliber,
  Summary,
} from "../../api/dragons";
import { fetchReviewJobStatus } from "../../api/review";
import { PageHeader } from "../../components/PageHeader";
import { IconAlert } from "../../components/icons-extra";
import { effectiveCaliber, resolveSortField } from "../../lib/caliber";
import { SummaryCards } from "./SummaryCards";
import { RecentDragons } from "./RecentDragons";
import { FilterBar } from "./FilterBar";
import { DragonTable } from "./DragonTable";
import { DragonDetailDrawer } from "./DragonDetailDrawer";
import {
  useIncrementalReview,
  type ReviewRefreshResult,
} from "./ReviewJobControl";

/** 前端固定 v2；页面不再读取 ?source query（旧 v1 深链按 v2 渲染）。 */
const DEFAULT_FILTERS: DragonFilters = {
  review_caliber: "strict",
  // 默认展示全部派生状态：待回测不能被默认筛选隐藏成 0（D4/R6）。
  // 列表默认按入选日倒序：最新入选的龙头排在最前。
  sort_by: "trade_date",
  sort_dir: "desc",
};

interface SortState {
  by: string;
  dir: "asc" | "desc";
}

type DragonResponse = Awaited<ReturnType<typeof fetchDragons>>;

type LoadOutcome =
  | { kind: "ok"; resp: DragonResponse }
  | { kind: "stale" }
  | { kind: "error"; error: string };

export function DragonsPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [coverage, setCoverage] = useState<Summary | null>(null);
  const [filters, setFilters] = useState<DragonFilters>(DEFAULT_FILTERS);
  const [rawData, setRawData] = useState<Dragon[]>([]);
  const [sort, setSort] = useState<SortState>({ by: "trade_date", dir: "desc" });
  const [responseCaliber, setResponseCaliber] = useState<ReviewCaliber>("strict");
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ date: string; code: string } | null>(null);
  // 本次页面会话内是否启动过回测：成功后 pending 归零也要保留结果反馈。
  const [reviewSessionActive, setReviewSessionActive] = useState(false);

  // 单调递增的请求序号：只接受最后一次查询的响应，防止慢的旧请求覆盖新结果。
  const requestSeq = useRef(0);
  const initialised = useRef(false);
  // 最近一次真正提交的筛选：回测作业完成后按它刷新，保留用户当前筛选。
  const appliedFilters = useRef<DragonFilters>(DEFAULT_FILTERS);

  const load = useCallback(async (next: DragonFilters): Promise<LoadOutcome> => {
    const seq = ++requestSeq.current;
    appliedFilters.current = next;
    try {
      const resp = await fetchDragons(next);
      if (seq !== requestSeq.current) return { kind: "stale" };
      setRawData(resp.data || []);
      setSummary(resp.summary);
      setCoverage(resp.coverage ?? null);
      setResponseCaliber(effectiveCaliber(next.review_caliber));
      setSort({ by: next.sort_by, dir: next.sort_dir as "asc" | "desc" });
      setError(null);
      return { kind: "ok", resp };
    } catch (exc) {
      if (seq !== requestSeq.current) return { kind: "stale" };
      // 刷新失败保留最近一次有效数据（不制造伪空态），只显示错误。
      setError(exc instanceof Error ? exc.message : String(exc));
      return {
        kind: "error",
        error: exc instanceof Error ? exc.message : String(exc),
      };
    }
  }, []);

  useEffect(() => {
    if (initialised.current) return;
    initialised.current = true;
    load(DEFAULT_FILTERS);
  }, [load]);

  // 只读探针：运行中及最近完成均保留状态区，pending 归零刷新也不丢结果。
  useEffect(() => {
    let active = true;
    fetchReviewJobStatus()
      .then((payload) => {
        const finished = Date.parse(payload.last_run?.finished_at ?? "");
        const recentTerminal =
          (payload.state === "succeeded" || payload.state === "failed") &&
          Number.isFinite(finished) &&
          Date.now() - finished >= 0 &&
          Date.now() - finished <= 10 * 60 * 1000;
        if (active && (payload.state === "running" || recentTerminal)) {
          setReviewSessionActive(true);
        }
      })
      .catch(() => {
        // 状态读取失败不影响页面；点击回测会通过 409 自动挂接。
      });
    return () => {
      active = false;
    };
  }, []);

  const onChange = useCallback((patch: Partial<DragonFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch }));
  }, []);

  const onSearch = useCallback(() => {
    load(filters);
  }, [filters, load]);

  const onReset = useCallback(() => {
    const next: DragonFilters = { ...DEFAULT_FILTERS, status: "" };
    setFilters(next);
    load(next);
  }, [load]);

  const onSort = useCallback((by: string) => {
    setSort((prev) => ({
      by,
      dir: prev.by === by && prev.dir === "desc" ? "asc" : "desc",
    }));
  }, []);

  const sortedData = useMemo(() => {
    const { by, dir } = sort;
    // 逻辑排序键按已加载数据的口径取物理字段，避免「显示宽松值、按严格值排」。
    const field = resolveSortField(responseCaliber, by);
    const arr = [...rawData];
    arr.sort((a, b) => {
      let va: string | number = -Infinity;
      let vb: string | number = -Infinity;
      const ra = a[field];
      const rb = b[field];
      if (ra != null) va = typeof ra === "string" ? ra.toLowerCase() : (ra as number);
      if (rb != null) vb = typeof rb === "string" ? rb.toLowerCase() : (rb as number);
      if (va < vb) return dir === "asc" ? -1 : 1;
      if (va > vb) return dir === "asc" ? 1 : -1;
      // 同日多行按综合分倒序兜底，避免翻页时行序抖动。
      if (by === "trade_date") {
        const sa = a.composite_score ?? -Infinity;
        const sb = b.composite_score ?? -Infinity;
        if (sa !== sb) return sa < sb ? 1 : -1;
      }
      return 0;
    });
    return arr;
  }, [rawData, sort, responseCaliber]);

  // 待回测计数未知（加载中 / 失败）时为 null：入口不出现。
  const pendingCount = summary
    ? (summary.derived_pending ?? summary.pending ?? 0)
    : null;

  const onReviewFinished = useCallback(async (): Promise<ReviewRefreshResult> => {
    const outcome = await load(appliedFilters.current);
    if (outcome.kind === "stale") {
      return { refreshed: false, stale: true };
    }
    if (outcome.kind === "error") {
      return { refreshed: false, error: outcome.error };
    }
    const cov = outcome.resp.coverage;
    return {
      refreshed: true,
      remaining: cov ? (cov.derived_pending ?? cov.pending ?? null) : null,
    };
  }, [load]);

  // 待回测入口：按钮进「待回测」标签行，状态文案留在卡片底部。
  // 本次会话启动过作业时即使 pending 归零也保持挂接与结果反馈。
  const review = useIncrementalReview({
    pending: pendingCount,
    onFinished: onReviewFinished,
    onSessionStart: () => setReviewSessionActive(true),
    enabled:
      reviewSessionActive || (pendingCount != null && pendingCount > 0),
  });

  return (
    <>
      <PageHeader
        eyebrow="OVERVIEW · 龙头回测"
        title="龙头总览"
      />

      {error ? (
        <div className="alert-danger mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
          <IconAlert size={15} />
          加载失败：{error}
        </div>
      ) : null}

      <div className="grid gap-3">
        <SummaryCards
          summary={summary}
          coverage={coverage}
          caliber={responseCaliber}
          pendingHeader={review.action}
          pendingFooter={
            <>
              {review.status}
              {review.overlay}
            </>
          }
        />
        <RecentDragons
          onSelect={(date, code) => setSelected({ date, code })}
        />
        <FilterBar
          filters={filters}
          count={sortedData.length}
          onChange={onChange}
          onSearch={onSearch}
          onReset={onReset}
        />
        <DragonTable
          data={sortedData}
          caliber={responseCaliber}
          sort={sort}
          onSort={onSort}
          onRowClick={(row) => setSelected({ date: row.trade_date, code: row.code })}
        />
      </div>

      <DragonDetailDrawer
        selected={selected}
        caliber={responseCaliber}
        onClose={() => setSelected(null)}
      />
    </>
  );
}
