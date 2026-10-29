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
  IconRefresh,
  IconSearch,
} from "@tabler/icons-react";
import type { DragonFilters, ReviewCaliber } from "../api";
import { effectiveCaliber } from "../caliber";

interface Props {
  filters: DragonFilters;
  count: number;
  onChange: (patch: Partial<DragonFilters>) => void;
  onSearch: () => void;
  onReset: () => void;
}

// 完整状态集（严格口径可达全部）；宽松模式隐藏仅严格可达的两项。
const STATUS_OPTIONS_STRICT = [
  { value: "completed", label: "已完成" },
  { value: "", label: "全部状态" },
  { value: "pending", label: "待回测" },
  { value: "pending_recompute", label: "待重算" },
  { value: "cannot_evaluate", label: "无法评估" },
  { value: "no_entry", label: "无介入" },
  { value: "error", label: "失败" },
];

const STRICT_ONLY_STATUSES = new Set(["pending_recompute", "cannot_evaluate"]);

const STATUS_OPTIONS_LOOSE = STATUS_OPTIONS_STRICT.filter(
  (option) => !STRICT_ONLY_STATUSES.has(option.value)
);

const CALIBER_OPTIONS: { value: ReviewCaliber; label: string }[] = [
  { value: "strict", label: "严格" },
  { value: "loose", label: "宽松" },
];

const SOURCE_OPTIONS = [
  { value: "v2", label: "五维识别" },
  { value: "v1", label: "v1 历史" },
];

export function FilterBar({ filters, count, onChange, onSearch, onReset }: Props) {
  const [advOpen, advHandlers] = useDisclosure(false);

  const source = filters.source ?? "v2";
  // 生效口径：v1 恒宽松，v2 缺省严格（与服务端 resolve_caliber 同规则）。
  const caliber = effectiveCaliber(source, filters.review_caliber);
  const statusOptions =
    caliber === "loose" ? STATUS_OPTIONS_LOOSE : STATUS_OPTIONS_STRICT;

  // 切体系时同步口径；生效口径变为宽松时同 patch 清理严格专有状态。
  const onSourceChange = (value: string | null) => {
    const nextSource = value === "v1" ? "v1" : "v2";
    const patch: Partial<DragonFilters> = {
      source: nextSource,
      review_caliber: nextSource === "v1" ? "loose" : "strict",
    };
    if (
      nextSource === "v1" &&
      STRICT_ONLY_STATUSES.has(filters.status ?? "")
    ) {
      patch.status = "";
    }
    onChange(patch);
  };

  const onCaliberChange = (value: string | null) => {
    const nextCaliber: ReviewCaliber = value === "loose" ? "loose" : "strict";
    const patch: Partial<DragonFilters> = { review_caliber: nextCaliber };
    // 清空只改表单、不自动请求，保证状态 Select 的 value 始终在选项内。
    if (
      nextCaliber === "loose" &&
      STRICT_ONLY_STATUSES.has(filters.status ?? "")
    ) {
      patch.status = "";
    }
    onChange(patch);
  };

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
              value={source}
              onChange={onSourceChange}
              allowDeselect={false}
            />
          </Field>
          <Field label="口径">
            <Select
              w={110}
              size="xs"
              data={CALIBER_OPTIONS}
              value={caliber}
              onChange={onCaliberChange}
              disabled={source === "v1"}
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
            variant="light"
            color="gray"
            leftSection={<IconRefresh size={14} />}
            onClick={onReset}
          >
            重置
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
                data={statusOptions}
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

        {/* v1 历史口径提示 */}
        {source === "v1" && (
          <Text size="xs" c="dimmed">
            v1 历史数据固定旧口径（首个非一字板日最低价买入），口径不可切换，不提供严格结果。
          </Text>
        )}

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
