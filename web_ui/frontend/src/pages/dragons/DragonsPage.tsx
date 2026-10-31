import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchDragons,
  type Dragon,
  type DragonFilters,
  type ReviewCaliber,
  type Summary,
} from "../../api/dragons";
import { PageHeader } from "../../components/PageHeader";
import { IconAlert } from "../../components/icons-extra";
import { effectiveCaliber, resolveSortField } from "../../lib/caliber";
import { SummaryCards } from "./SummaryCards";
import { RecentDragons } from "./RecentDragons";
import { FilterBar } from "./FilterBar";
import { DragonTable } from "./DragonTable";
import { DragonDetailDrawer } from "./DragonDetailDrawer";

/** 前端固定 v2；页面不再读取 ?source query（旧 v1 深链按 v2 渲染）。 */
const DEFAULT_FILTERS: DragonFilters = {
  review_caliber: "strict",
  status: "completed",
  sort_by: "composite_score",
  sort_dir: "desc",
};

interface SortState {
  by: string;
  dir: "asc" | "desc";
}

export function DragonsPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [filters, setFilters] = useState<DragonFilters>(DEFAULT_FILTERS);
  const [rawData, setRawData] = useState<Dragon[]>([]);
  const [sort, setSort] = useState<SortState>({ by: "composite_score", dir: "desc" });
  const [responseCaliber, setResponseCaliber] = useState<ReviewCaliber>("strict");
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ date: string; code: string } | null>(null);

  // 单调递增的请求序号：只接受最后一次查询的响应，防止慢的旧请求覆盖新结果。
  const requestSeq = useRef(0);
  const initialised = useRef(false);

  const load = useCallback(async (next: DragonFilters) => {
    const seq = ++requestSeq.current;
    try {
      const resp = await fetchDragons(next);
      if (seq !== requestSeq.current) return;
      setRawData(resp.data || []);
      setSummary(resp.summary);
      setResponseCaliber(effectiveCaliber(next.review_caliber));
      setSort({ by: next.sort_by, dir: next.sort_dir as "asc" | "desc" });
      setError(null);
    } catch (exc) {
      if (seq !== requestSeq.current) return;
      setRawData([]);
      setSummary(null);
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    if (initialised.current) return;
    initialised.current = true;
    load(DEFAULT_FILTERS);
  }, [load]);

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
      return 0;
    });
    return arr;
  }, [rawData, sort, responseCaliber]);

  return (
    <>
      <PageHeader
        eyebrow="OVERVIEW · 龙头回测"
        title="龙头总览"
        description="五维「识别真龙」评分结果与双口径回测。红涨绿跌；严格口径为入选次一交易日开盘价买入，宽松口径为最低价买入。"
      />

      {error ? (
        <div className="alert-critical mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-[12.5px]">
          <IconAlert size={15} />
          加载失败：{error}
        </div>
      ) : null}

      <div className="grid gap-3">
        <SummaryCards summary={summary} caliber={responseCaliber} />
        <RecentDragons
          caliber={responseCaliber}
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
