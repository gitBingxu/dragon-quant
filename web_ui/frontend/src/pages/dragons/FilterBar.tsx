import { useState } from "react";
import type { DragonFilters, ReviewCaliber } from "../../api/dragons";
import { Section } from "../../components/Section";
import { Select, TextField } from "../../components/Select";
import {
  IconChevronDown,
  IconChevronUp,
  IconRefresh,
  IconSearch,
} from "../../components/icons";
import { IconSliders } from "../../components/icons-extra";
import { effectiveCaliber } from "../../lib/caliber";

interface Props {
  filters: DragonFilters;
  count: number;
  onChange: (patch: Partial<DragonFilters>) => void;
  onSearch: () => void;
  onReset: () => void;
}

const STATUS_OPTIONS_STRICT = [
  { value: "completed", label: "已完成" },
  { value: "", label: "全部状态" },
  { value: "pending", label: "待回测" },
  { value: "pending_recompute", label: "待重算" },
  { value: "cannot_evaluate", label: "无法评估" },
  { value: "no_entry", label: "无介入" },
  { value: "error", label: "失败" },
];

export const STRICT_ONLY_STATUSES = new Set(["pending_recompute", "cannot_evaluate"]);

const STATUS_OPTIONS_LOOSE = STATUS_OPTIONS_STRICT.filter(
  (option) => !STRICT_ONLY_STATUSES.has(option.value)
);

const CALIBER_OPTIONS = [
  { value: "strict", label: "严格口径" },
  { value: "loose", label: "宽松口径" },
];

export function FilterBar({ filters, count, onChange, onSearch, onReset }: Props) {
  const [advOpen, setAdvOpen] = useState(false);
  const source = filters.source ?? "v2";
  const caliber = effectiveCaliber(source, filters.review_caliber);
  const statusOptions = caliber === "loose" ? STATUS_OPTIONS_LOOSE : STATUS_OPTIONS_STRICT;

  const onCaliberChange = (value: string) => {
    const nextCaliber: ReviewCaliber = value === "loose" ? "loose" : "strict";
    const patch: Partial<DragonFilters> = { review_caliber: nextCaliber };
    if (nextCaliber === "loose" && STRICT_ONLY_STATUSES.has(filters.status ?? "")) {
      patch.status = "";
    }
    onChange(patch);
  };

  return (
    <Section
      title="筛选"
      description="修改条件后点击查询；回车亦触发"
      right={
        <span className="text-[12px] text-ink-2">
          共 <span className="num text-ink">{count}</span> 条
        </span>
      }
      bodyClassName="px-4 py-3.5 sm:px-[18px]"
    >
      <div className="grid grid-cols-2 gap-x-3 gap-y-3 md:grid-cols-4 xl:grid-cols-7">
        <Select
          label="口径"
          value={caliber}
          options={CALIBER_OPTIONS}
          onChange={onCaliberChange}
          disabled={source === "v1"}
        />
        <TextField
          label="入选起"
          type="date"
          value={(filters.date_from as string) ?? ""}
          onChange={(value) => onChange({ date_from: value })}
        />
        <TextField
          label="入选止"
          type="date"
          value={(filters.date_to as string) ?? ""}
          onChange={(value) => onChange({ date_to: value })}
        />
        <TextField
          label="收益 ≥ %"
          value={(filters.return_min as string) ?? ""}
          placeholder="下限"
          onChange={(value) => onChange({ return_min: value })}
          onEnter={onSearch}
        />
        <TextField
          label="收益 ≤ %"
          value={(filters.return_max as string) ?? ""}
          placeholder="上限"
          onChange={(value) => onChange({ return_max: value })}
          onEnter={onSearch}
        />
        <TextField
          label="回撤 ≥ %"
          value={(filters.drawdown_min as string) ?? ""}
          placeholder="下限"
          onChange={(value) => onChange({ drawdown_min: value })}
          onEnter={onSearch}
        />
        <TextField
          label="回撤 ≤ %"
          value={(filters.drawdown_max as string) ?? ""}
          placeholder="上限"
          onChange={(value) => onChange({ drawdown_max: value })}
          onEnter={onSearch}
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className="btn btn-primary" onClick={onSearch}>
          <IconSearch size={14} />
          查询
        </button>
        <button type="button" className="btn btn-ghost" onClick={onReset}>
          <IconRefresh size={14} />
          重置
        </button>
        <button
          type="button"
          className="btn btn-subtle"
          onClick={() => setAdvOpen((prev) => !prev)}
        >
          <IconSliders size={14} />
          更多筛选
          {advOpen ? <IconChevronUp size={13} /> : <IconChevronDown size={13} />}
        </button>
        {source === "v1" ? (
          <span className="ml-auto text-[11.5px] text-ink-2">
            v1 历史固定旧口径（首个非一字板日最低价买入），不可切换。
          </span>
        ) : null}
      </div>

      {advOpen ? (
        <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-3 border-t border-hairline pt-3 md:grid-cols-4 xl:grid-cols-7">
          <TextField
            label="代码"
            value={(filters.code as string) ?? ""}
            placeholder="600519"
            onChange={(value) => onChange({ code: value })}
            onEnter={onSearch}
          />
          <TextField
            label="名称"
            value={(filters.name as string) ?? ""}
            placeholder="贵州茅台"
            onChange={(value) => onChange({ name: value })}
            onEnter={onSearch}
          />
          <Select
            label="状态"
            value={filters.status ?? ""}
            options={statusOptions}
            onChange={(value) => onChange({ status: value })}
          />
          <TextField
            label="综合分 ≥"
            value={(filters.score_min as string) ?? ""}
            placeholder="下限"
            onChange={(value) => onChange({ score_min: value })}
            onEnter={onSearch}
          />
          <TextField
            label="综合分 ≤"
            value={(filters.score_max as string) ?? ""}
            placeholder="上限"
            onChange={(value) => onChange({ score_max: value })}
            onEnter={onSearch}
          />
          <TextField
            label="版本 ≥"
            value={(filters.version_min as string) ?? ""}
            placeholder="0.2"
            onChange={(value) => onChange({ version_min: value })}
            onEnter={onSearch}
          />
          <TextField
            label="版本 ≤"
            value={(filters.version_max as string) ?? ""}
            placeholder="0.2.4"
            onChange={(value) => onChange({ version_max: value })}
            onEnter={onSearch}
          />
        </div>
      ) : null}
    </Section>
  );
}
