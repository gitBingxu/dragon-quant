import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Container, Group, Text, Title } from "@mantine/core";
import {
  fetchDragons,
  type Dragon,
  type DragonFilters,
  type Summary,
} from "./api";
import { SummaryCards } from "./components/SummaryCards";
import { FilterBar } from "./components/FilterBar";
import { DragonTable } from "./components/DragonTable";

const DEFAULT_FILTERS: DragonFilters = {
  source: "v2",
  status: "completed",
  sort_by: "composite_score",
  sort_dir: "desc",
};

function initialFilters(): DragonFilters {
  const params = new URLSearchParams(window.location.search);
  const source = params.get("source") === "v1" ? "v1" : "v2";
  return { ...DEFAULT_FILTERS, source };
}

export function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [filters, setFilters] = useState<DragonFilters>(() => initialFilters());
  const [rawData, setRawData] = useState<Dragon[]>([]);
  const [sort, setSort] = useState<{ by: keyof Dragon; dir: "asc" | "desc" }>({
    by: "composite_score",
    dir: "desc",
  });

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
      setSort({ by: f.sort_by as keyof Dragon, dir: f.sort_dir as "asc" | "desc" });
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

  // 重置：保留当前体系（source），清空其余 12 项筛选并立即恢复全量。
  const onReset = useCallback(() => {
    const next: DragonFilters = {
      ...DEFAULT_FILTERS,
      source: filters.source ?? "v2",
      status: "",
    };
    setFilters(next);
    load(next);
  }, [filters.source, load]);

  // 表头点击 → 客户端排序（对齐旧版行为）
  const onSort = useCallback((by: keyof Dragon) => {
    setSort((prev) => ({
      by,
      dir: prev.by === by && prev.dir === "desc" ? "asc" : "desc",
    }));
  }, []);

  const sortedData = useMemo(() => {
    const { by, dir } = sort;
    const arr = [...rawData];
    arr.sort((a, b) => {
      let va: string | number = -Infinity;
      let vb: string | number = -Infinity;
      const ra = a[by];
      const rb = b[by];
      if (ra != null) va = typeof ra === "string" ? ra.toLowerCase() : (ra as number);
      if (rb != null) vb = typeof rb === "string" ? rb.toLowerCase() : (rb as number);
      if (va < vb) return dir === "asc" ? -1 : 1;
      if (va > vb) return dir === "asc" ? 1 : -1;
      return 0;
    });
    return arr;
  }, [rawData, sort]);

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

      <SummaryCards summary={summary} />
      <FilterBar
        filters={filters}
        count={sortedData.length}
        onChange={onChange}
        onSearch={onSearch}
        onReset={onReset}
      />
      <DragonTable data={sortedData} sort={sort} onSort={onSort} />
    </Container>
  );
}
