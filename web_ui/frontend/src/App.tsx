import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Container, Group, Text, Title } from "@mantine/core";
import {
  fetchDragons,
  type Dragon,
  type DragonFilters,
  type ReviewCaliber,
  type Summary,
} from "./api";
import { effectiveCaliber, resolveSortField } from "./caliber";
import { SummaryCards } from "./components/SummaryCards";
import { FilterBar } from "./components/FilterBar";
import { DragonTable } from "./components/DragonTable";

const DEFAULT_FILTERS: DragonFilters = {
  source: "v2",
  review_caliber: "strict",
  status: "completed",
  sort_by: "composite_score",
  sort_dir: "desc",
};

function initialFilters(): DragonFilters {
  const params = new URLSearchParams(window.location.search);
  const source = params.get("source") === "v1" ? "v1" : "v2";
  return {
    ...DEFAULT_FILTERS,
    source,
    review_caliber: source === "v1" ? "loose" : "strict",
  };
}

interface SortState {
  by: string;
  dir: "asc" | "desc";
}

export function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [filters, setFilters] = useState<DragonFilters>(() => initialFilters());
  const [rawData, setRawData] = useState<Dragon[]>([]);
  const [sort, setSort] = useState<SortState>({
    by: "composite_score",
    dir: "desc",
  });
  // 已加载数据所属的口径：表格/卡片用它渲染，保证与 data/summary 同源。
  const [responseCaliber, setResponseCaliber] =
    useState<ReviewCaliber>("strict");

  // 单调递增的请求序号：只接受最后一次查询的响应，防止慢的旧请求覆盖新结果。
  const requestSeq = useRef(0);

  const load = useCallback(async (f: DragonFilters) => {
    const seq = ++requestSeq.current;
    try {
      const resp = await fetchDragons(f);
      if (seq !== requestSeq.current) return;
      setRawData(resp.data || []);
      // 卡片统计与列表来自同一响应（服务端基于完整筛选结果计算）。
      setSummary(resp.summary);
      setResponseCaliber(effectiveCaliber(f.source ?? "v2", f.review_caliber));
      setSort({ by: f.sort_by, dir: f.sort_dir as "asc" | "desc" });
    } catch {
      if (seq !== requestSeq.current) return;
      setRawData([]);
      setSummary(null);
    }
  }, []);

  // 初始加载
  useEffect(() => {
    load(filters);
  }, [load]);

  // 修改筛选条件只更新表单状态；点击「查询」/ 回车才真正请求。
  const onChange = useCallback((patch: Partial<DragonFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch }));
  }, []);

  const onSearch = useCallback(() => {
    load(filters);
  }, [filters, load]);

  // 重置：保留当前体系（source），口径回到该体系默认，清空其余筛选并恢复全量。
  const onReset = useCallback(() => {
    const source = filters.source ?? "v2";
    const next: DragonFilters = {
      ...DEFAULT_FILTERS,
      source,
      review_caliber: source === "v1" ? "loose" : "strict",
      status: "",
    };
    setFilters(next);
    load(next);
  }, [filters.source, load]);

  // 表头点击 → 客户端排序（对齐旧版行为）
  const onSort = useCallback((by: string) => {
    setSort((prev) => ({
      by,
      dir: prev.by === by && prev.dir === "desc" ? "asc" : "desc",
    }));
  }, []);

  const sortedData = useMemo(() => {
    const { by, dir } = sort;
    // 逻辑排序键按**已加载数据**的口径取物理字段，避免「显示宽松值、按严格值排」。
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
    <Container size="100%" px="xl" py="lg">
      <Title order={1} mb="lg">
        <Group gap={8} component="span">
          <span style={{ fontSize: 26 }}>🐉</span>
          <Text span inherit>
            龙头回测面板
          </Text>
        </Group>
      </Title>

      <SummaryCards summary={summary} caliber={responseCaliber} />
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
      />
    </Container>
  );
}
