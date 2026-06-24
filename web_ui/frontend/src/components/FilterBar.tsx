import { useDisclosure } from "@mantine/hooks";
import {
  Button,
  Collapse,
  Group,
  Paper,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { DatePickerInput } from "@mantine/dates";
import dayjs from "dayjs";
import {
  IconChevronDown,
  IconChevronUp,
  IconSearch,
} from "@tabler/icons-react";
import type { DragonFilters } from "../api";

interface Props {
  filters: DragonFilters;
  count: number;
  onChange: (patch: Partial<DragonFilters>) => void;
  onSearch: () => void;
}

const STATUS_OPTIONS = [
  { value: "completed", label: "已完成" },
  { value: "", label: "全部状态" },
  { value: "pending", label: "待回测" },
  { value: "no_entry", label: "无介入日" },
  { value: "error", label: "错误" },
];

const SOURCE_OPTIONS = [
  { value: "v1", label: "v1 四维" },
  { value: "v2", label: "v2 五维" },
];

export function FilterBar({ filters, count, onChange, onSearch }: Props) {
  const [advOpen, advHandlers] = useDisclosure(false);

  const onEnter = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") onSearch();
  };

  // label 在左、控件在右的横向字段包装
  const Field = ({
    label,
    children,
  }: {
    label: string;
    children: React.ReactNode;
  }) => (
    <Group gap={6} wrap="nowrap" align="center">
      <Text size="xs" fw={600} c="blue.4" style={{ whiteSpace: "nowrap" }}>
        {label}
      </Text>
      {children}
    </Group>
  );

  const textInput = (
    key: keyof DragonFilters,
    label: string,
    placeholder: string,
    w = 110
  ) => (
    <Field label={label}>
      <TextInput
        placeholder={placeholder}
        w={w}
        size="xs"
        value={(filters[key] as string) ?? ""}
        onChange={(e) => onChange({ [key]: e.currentTarget.value })}
        onKeyDown={onEnter}
      />
    </Field>
  );

  // 日期字段：与后端约定 YYYY-MM-DD 字符串
  const dateInput = (
    key: "date_from" | "date_to",
    label: string,
    placeholder: string
  ) => {
    const raw = filters[key];
    const value = raw ? dayjs(raw).toDate() : null;
    return (
      <Field label={label}>
        <DatePickerInput
          placeholder={placeholder}
          w={150}
          size="xs"
          clearable
          valueFormat="YYYY-MM-DD"
          value={value}
          onChange={(d) =>
            onChange({ [key]: d ? dayjs(d).format("YYYY-MM-DD") : "" })
          }
        />
      </Field>
    );
  };

  return (
    <Paper withBorder radius="md" p="sm" mb="md">
      <Stack gap="sm">
        {/* 可见区：常用筛选 */}
        <Group gap="md" align="center" justify="flex-start" wrap="wrap">
          <Field label="体系">
            <Select
              w={120}
              size="xs"
              data={SOURCE_OPTIONS}
              value={filters.source ?? "v1"}
              onChange={(v) => onChange({ source: (v === "v2" ? "v2" : "v1") })}
              allowDeselect={false}
            />
          </Field>
          {dateInput("date_from", "入选起", "选择日期")}
          {dateInput("date_to", "入选止", "选择日期")}
          {textInput("return_min", "收益≥%", "下限", 90)}
          {textInput("return_max", "收益≤%", "上限", 90)}
          {textInput("drawdown_min", "回撤≥%", "下限", 90)}
          {textInput("drawdown_max", "回撤≤%", "上限", 90)}
          <Button
            size="xs"
            leftSection={<IconSearch size={14} />}
            onClick={onSearch}
          >
            查询
          </Button>
          <Button
            size="xs"
            variant="subtle"
            color="gray"
            rightSection={
              advOpen ? (
                <IconChevronUp size={14} />
              ) : (
                <IconChevronDown size={14} />
              )
            }
            onClick={advHandlers.toggle}
          >
            {advOpen ? "收起" : "更多筛选"}
          </Button>
        </Group>

        {/* 折叠区：不常用筛选 */}
        <Collapse in={advOpen}>
          <Group gap="md" align="center" wrap="wrap">
            {textInput("code", "代码", "如 600519", 100)}
            {textInput("name", "名称", "如 贵州茅台", 110)}
            <Field label="状态">
              <Select
                w={120}
                size="xs"
                data={STATUS_OPTIONS}
                value={filters.status ?? "completed"}
                onChange={(v) => onChange({ status: v ?? "" })}
                allowDeselect={false}
              />
            </Field>
            {textInput("score_min", "综合分≥", "下限", 90)}
            {textInput("score_max", "综合分≤", "上限", 90)}
            {textInput("version_min", "版本≥", "如 0.2", 90)}
            {textInput("version_max", "版本≤", "如 0.2.4", 90)}
          </Group>
        </Collapse>

        {/* 计数 */}
        <Group gap="xs" align="center">
          <Text size="sm" c="dimmed" ml="auto">
            共 <Text span fw={700} c="bright">{count}</Text> 条
          </Text>
        </Group>
      </Stack>
    </Paper>
  );
}
