import { useCallback, useEffect, useState } from "react";
import {
  fetchFactorRuns,
  fetchFactors,
  fetchFactorValues,
  type FactorDayView,
  type FactorRun,
  type FactorSpecView,
} from "../../api/factors";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { Select, TextField } from "../../components/Select";
import { EmptyState } from "../../components/EmptyState";
import { IconSearch } from "../../components/icons";
import { IconAlert, IconDatabase } from "../../components/icons-extra";
import { formatDateInput } from "../../lib/format";
import { RegistryTable } from "./RegistryTable";
import { DayValues } from "./DayValues";
import { BuildAudit } from "./BuildAudit";

export function FactorsPage() {
  const [factors, setFactors] = useState<FactorSpecView[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [tradeDate, setTradeDate] = useState(() => formatDateInput(new Date()));
  const [code, setCode] = useState("");
  const [day, setDay] = useState<FactorDayView | null>(null);
  const [runs, setRuns] = useState<FactorRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [querying, setQuerying] = useState(false);

  useEffect(() => {
    fetchFactors()
      .then((payload) => {
        setFactors(payload.data);
        if (payload.data.length > 0) setSelected((prev) => prev || payload.data[0].name);
      })
      .catch((exc: unknown) => setError(exc instanceof Error ? exc.message : String(exc)));
  }, []);

  const query = useCallback(async () => {
    setQuerying(true);
    setError(null);
    try {
      const params: { tradeDate: string; name?: string; code?: string } = { tradeDate };
      if (selected) params.name = selected;
      if (code.trim()) params.code = code.trim();
      const [values, runList] = await Promise.all([
        fetchFactorValues(params),
        fetchFactorRuns({ tradeDate, limit: 20 }),
      ]);
      setDay(values);
      setRuns(runList.data);
    } catch (exc) {
      setDay(null);
      setRuns([]);
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setQuerying(false);
    }
  }, [tradeDate, selected, code]);

  return (
    <>
      <PageHeader
        eyebrow="FACTORS · 因子层"
        title="因子视图"
        description="代码注册表为唯一真值，DB 版本为最新快照；缺失 = 无行，resolved_versions 永远显式。"
        actions={
          <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-2">
            <IconDatabase size={14} />
            <span className="num text-ink">{factors.length}</span> 个因子
          </span>
        }
      />

      {error ? (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-critical/35 bg-critical-soft px-3.5 py-2.5 text-[12.5px] text-[#ff9093]">
          <IconAlert size={15} />
          加载失败：{error}
        </div>
      ) : null}

      <div className="grid gap-3">
        <Section
          title="查询"
          description="按交易日 + 因子（可选代码）读取 PIT 因子值与 build 审计"
          bodyClassName="px-4 py-3.5 sm:px-[18px]"
        >
          <div className="grid grid-cols-2 items-end gap-3 md:grid-cols-4">
            <TextField
              label="交易日"
              type="date"
              value={tradeDate}
              onChange={setTradeDate}
              onEnter={query}
            />
            <Select
              label="因子"
              value={selected}
              options={
                factors.length
                  ? factors.map((factor) => ({ value: factor.name, label: factor.name }))
                  : [{ value: "", label: "无可用因子" }]
              }
              onChange={setSelected}
            />
            <TextField
              label="代码（可选）"
              value={code}
              placeholder="600001"
              onChange={setCode}
              onEnter={query}
            />
            <button
              type="button"
              className="btn btn-primary"
              onClick={query}
              disabled={querying}
            >
              <IconSearch size={14} />
              {querying ? "查询中…" : "查询"}
            </button>
          </div>
        </Section>

        <Section
          title="因子注册表"
          description="代码版本 vs DB 版本；点击行切换查询因子"
          bodyClassName="p-0"
        >
          <RegistryTable factors={factors} selected={selected} onSelect={setSelected} />
        </Section>

        {day ? (
          <Section
            title={`当日值 · ${day.trade_date}`}
            description={`build_id ${day.build_id ?? "—"} · coverage 来源 ${day.coverage_source}`}
            bodyClassName="px-4 py-3.5 sm:px-[18px]"
          >
            <DayValues day={day} />
          </Section>
        ) : (
          <Section title="当日值" description="选择日期后点击查询">
            <EmptyState
              title="尚未查询"
              description="选择交易日与因子后点击查询，展示 PIT 因子值、覆盖率与血缘 scan。"
            />
          </Section>
        )}

        <Section title="build 审计" description="最近 20 次构建" bodyClassName="p-0">
          <BuildAudit runs={runs} />
        </Section>
      </div>
    </>
  );
}
