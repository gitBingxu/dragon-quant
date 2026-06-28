import { useMemo, useState } from "react";
import {
  Badge,
  Group,
  Pagination,
  Paper,
  Select,
  Table,
  Text,
} from "@mantine/core";
import { IconChevronDown, IconChevronUp } from "@tabler/icons-react";
import type { Dragon } from "../api";
import { fmtNum, fmtPct, pnlColor, statusMeta } from "../utils";

interface SortState {
  by: keyof Dragon;
  dir: "asc" | "desc";
}

interface Col {
  key: keyof Dragon;
  label: string;
  sortable?: boolean;
}

const COLUMNS: Col[] = [
  { key: "source", label: "体系", sortable: true },
  { key: "code", label: "代码", sortable: true },
  { key: "name", label: "名称", sortable: true },
  { key: "trade_date", label: "入选日", sortable: true },
  { key: "buy_date", label: "买入日", sortable: true },
  { key: "rank", label: "排名", sortable: true },
  { key: "composite_score", label: "综合分", sortable: true },
  { key: "is_true_dragon", label: "真龙" },
  { key: "board_count", label: "连板", sortable: true },
  { key: "concepts", label: "概念" },
  { key: "buy_price", label: "买入价", sortable: true },
  { key: "max_return_5d", label: "最大收益%", sortable: true },
  { key: "max_drawdown_5d", label: "最大回撤%", sortable: true },
  { key: "max_return_hold_days", label: "持有天", sortable: true },
  { key: "review_status", label: "状态", sortable: true },
];

const PAGE_SIZES = ["10", "25", "50", "100"];

export function DragonTable({
  data,
  sort,
  onSort,
}: {
  data: Dragon[];
  sort: SortState;
  onSort: (by: keyof Dragon) => void;
}) {
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);

  const totalPages = Math.max(1, Math.ceil(data.length / pageSize));
  const curPage = Math.min(page, totalPages);

  const pageData = useMemo(() => {
    const start = (curPage - 1) * pageSize;
    return data.slice(start, start + pageSize);
  }, [data, curPage, pageSize]);

  const renderHeader = (col: Col) => {
    const active = sort.by === col.key;
    return (
      <Table.Th
        key={col.key}
        style={{ cursor: col.sortable ? "pointer" : "default", whiteSpace: "nowrap" }}
        onClick={col.sortable ? () => onSort(col.key) : undefined}
        c={active ? "blue" : undefined}
      >
        <Group gap={2} wrap="nowrap">
          {col.label}
          {active &&
            (sort.dir === "asc" ? (
              <IconChevronUp size={12} />
            ) : (
              <IconChevronDown size={12} />
            ))}
        </Group>
      </Table.Th>
    );
  };

  const renderRow = (r: Dragon) => {
    const st = statusMeta(r.review_status);
    return (
      <Table.Tr key={`${r.source}-${r.trade_date}-${r.code}`}>
        <Table.Td>
          <Badge color={r.source === "v2" ? "grape" : "blue"} variant="light" radius="sm">
            {r.source === "v2" ? "v2 五维" : "v1 四维"}
          </Badge>
        </Table.Td>
        <Table.Td>{r.code}</Table.Td>
        <Table.Td>{r.name}</Table.Td>
        <Table.Td>{r.trade_date}</Table.Td>
        <Table.Td>{r.buy_date || "—"}</Table.Td>
        <Table.Td>{r.rank ?? "—"}</Table.Td>
        <Table.Td fw={600}>{fmtNum(r.composite_score, 1)}</Table.Td>
        <Table.Td>
          {r.is_true_dragon == null ? (
            "—"
          ) : r.is_true_dragon ? (
            <Badge color="red" variant="light" radius="sm">
              🐉真龙
            </Badge>
          ) : (
            <Badge color="gray" variant="light" radius="sm">
              ✗
            </Badge>
          )}
        </Table.Td>
        <Table.Td>{r.board_count ?? "—"}</Table.Td>
        <Table.Td>{(r.concepts || []).slice(0, 3).join(" · ")}</Table.Td>
        <Table.Td>{fmtNum(r.buy_price)}</Table.Td>
        <Table.Td c={pnlColor(r.max_return_5d)}>{fmtPct(r.max_return_5d)}</Table.Td>
        <Table.Td c={pnlColor(r.max_drawdown_5d)}>{fmtPct(r.max_drawdown_5d)}</Table.Td>
        <Table.Td>{r.max_return_hold_days ?? "—"}</Table.Td>
        <Table.Td>
          <Badge color={st.color} variant="light" radius="sm">
            {st.label}
          </Badge>
        </Table.Td>
      </Table.Tr>
    );
  };

  return (
    <Paper withBorder radius="md" style={{ overflow: "hidden" }}>
      <Table.ScrollContainer minWidth={1100}>
        <Table highlightOnHover stickyHeader verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>{COLUMNS.map(renderHeader)}</Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {pageData.length > 0 ? (
              pageData.map(renderRow)
            ) : (
              <Table.Tr>
                <Table.Td colSpan={COLUMNS.length}>
                  <Text ta="center" c="dimmed" py={40}>
                    暂无数据
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>

      <Group justify="flex-end" gap="md" p="sm">
        <Text size="sm" c="dimmed">
          共 <Text span fw={700} c="bright">{data.length}</Text> 条
        </Text>
        <Select
          size="xs"
          w={110}
          data={PAGE_SIZES.map((v) => ({ value: v, label: `${v} 条/页` }))}
          value={String(pageSize)}
          onChange={(v) => {
            setPageSize(Number(v) || 25);
            setPage(1);
          }}
          allowDeselect={false}
        />
        <Pagination
          size="sm"
          total={totalPages}
          value={curPage}
          onChange={setPage}
        />
      </Group>
    </Paper>
  );
}
