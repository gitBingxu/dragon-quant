import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Container, Group, Text, Title } from "@mantine/core";
import {
  fetchDragons,
  fetchSummary,
  type Dragon,
  type DragonFilters,
  type Summary,
} from "./api";
import { SummaryCards } from "./components/SummaryCards";
import { FilterBar } from "./components/FilterBar";
import { DragonTable } from "./components/DragonTable";

const DEFAULT_FILTERS: DragonFilters = {
  source: "v1",
  status: "completed",
  sort_by: "composite_score",
  sort_dir: "desc",
};

function initialFilters(): DragonFilters {
  const params = new URLSearchParams(window.location.search);
  const source = params.get("source") === "v2" ? "v2" : "v1";
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

  const debounceRef = useRef<number | undefined>(undefined);

  const load = useCallback(async (f: DragonFilters) => {
    try {
      const resp = await fetchDragons(f);
      setRawData(resp.data || []);
      fetchSummary(f.source ?? "v1").then(setSummary).catch(() => setSummary(null));
      setSort({ by: f.sort_by as keyof Dragon, dir: f.sort_dir as "asc" | "desc" });
    } catch {
      setRawData([]);
    }
  }, []);

  // 初始加载
  useEffect(() => {
    load(filters);
  }, [load]);

  // 筛选变更 → debounce 300ms 触发查询
  const onChange = useCallback(
    (patch: Partial<DragonFilters>) => {
      setFilters((prev) => {
        const next = { ...prev, ...patch };
        window.clearTimeout(debounceRef.current);
        debounceRef.current = window.setTimeout(() => load(next), 300);
        return next;
      });
    },
    [load]
  );

  const onSearch = useCallback(() => {
    window.clearTimeout(debounceRef.current);
    load(filters);
  }, [filters, load]);

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
      />
      <DragonTable data={sortedData} sort={sort} onSort={onSort} />
    </Container>
  );
}
