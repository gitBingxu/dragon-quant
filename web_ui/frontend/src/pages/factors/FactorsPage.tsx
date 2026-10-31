import { useCallback, useEffect, useRef, useState } from "react";
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
import { navigate, useSearchParams } from "../../router";
import { RegistryTable } from "./RegistryTable";
import { DayValues } from "./DayValues";
import { BuildAudit } from "./BuildAudit";

const today = () => formatDateInput(new Date());

export function FactorsPage() {
  const search = useSearchParams();
  const urlDate = search.get("date") ?? "";
  const urlFactor = search.get("factor") ?? "";
  const urlCode = search.get("code") ?? "";

  const [factors, setFactors] = useState<FactorSpecView[]>([]);
  const [selected, setSelected] = useState(urlFactor);
  const [tradeDate, setTradeDate] = useState(urlDate || today());
  const [code, setCode] = useState(urlCode);
  const [day, setDay] = useState<FactorDayView | null>(null);
  const [runs, setRuns] = useState<FactorRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [querying, setQuerying] = useState(false);

  // 已发起的查询签名 + 本页写回 URL 的 search，用于避免同步时重复请求。
  const lastRequested = useRef("");
  const selfWritten = useRef("");
  const requestSeq = useRef(0);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  const runQuery = useCallback(
    async (date: string, name: string, codeText: string) => {
      const seq = ++requestSeq.current;
      lastRequested.current = `${date}|${name}|${codeText}`;
      setQuerying(true);
      setError(null);
      try {
        const params: { tradeDate: string; name?: string; code?: string } = {
          tradeDate: date,
        };
        if (name) params.name = name;
        if (codeText) params.code = codeText;
        const [values, runList] = await Promise.all([
          fetchFactorValues(params),
          fetchFactorRuns({ tradeDate: date, limit: 20 }),
        ]);
        if (seq !== requestSeq.current) return;
        setDay(values);
        setRuns(runList.data);
      } catch (exc) {
        if (seq !== requestSeq.current) return;
        setDay(null);
        setRuns([]);
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        if (seq === requestSeq.current) setQuerying(false);
      }
    },
    []
  );

  const writeUrl = useCallback(
    (date: string, name: string, codeText: string, replace: boolean) => {
      const params = new URLSearchParams(window.location.search);
      const put = (key: string, value: string) => {
        if (value) params.set(key, value);
        else params.delete(key);
      };
      put("date", date);
      put("factor", name);
      put("code", codeText);
      const query = params.toString();
      selfWritten.current = query ? `?${query}` : "";
      navigate(`${window.location.pathname}${query ? `?${query}` : ""}`, { replace });
    },
    []
  );

  useEffect(() => {
    fetchFactors()
      .then((payload) => {
        setFactors(payload.data);
        if (payload.data.length > 0) setSelected((prev) => prev || payload.data[0].name);
      })
      .catch((exc: unknown) => setError(exc instanceof Error ? exc.message : String(exc)));
  }, []);

  // URL → 状态：直链打开与浏览器后退/前进；本页写回 URL 时跳过，避免重复请求。
  useEffect(() => {
    const signature = `${urlDate}|${urlFactor}|${urlCode}`;
    const self = window.location.search === selfWritten.current;
    if (self) return;
    if (signature === "||") {
      setTradeDate(today());
      setCode("");
      setDay(null);
      setRuns([]);
      lastRequested.current = "";
      return;
    }
    if (signature === lastRequested.current) return;
    const nextDate = urlDate || today();
    const nextFactor = urlFactor || selectedRef.current;
    const nextCode = urlCode;
    setTradeDate(nextDate);
    setSelected(nextFactor);
    setCode(nextCode);
    void runQuery(nextDate, nextFactor, nextCode);
  }, [urlDate, urlFactor, urlCode, runQuery]);

  const handleDate = (value: string) => {
    setTradeDate(value);
  };

  const handleFactor = (value: string) => {
    setSelected(value);
  };

  const handleCode = (value: string) => {
    setCode(value);
  };

  const handleQuery = () => {
    const codeText = code.trim();
    writeUrl(tradeDate, selected, codeText, false);
    void runQuery(tradeDate, selected, codeText);
  };

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
          description="按交易日 + 因子（可选代码）读取 PIT 因子值与 build 审计；筛选条件写入 URL 可直链分享"
          bodyClassName="px-4 py-3.5 sm:px-[18px]"
        >
          <div className="grid grid-cols-2 items-end gap-3 md:grid-cols-4">
            <TextField
              label="交易日"
              type="date"
              value={tradeDate}
              onChange={handleDate}
              onEnter={handleQuery}
            />
            <Select
              label="因子"
              value={selected}
              options={
                factors.length
                  ? factors.map((factor) => ({ value: factor.name, label: factor.name }))
                  : [{ value: "", label: "无可用因子" }]
              }
              onChange={handleFactor}
            />
            <TextField
              label="代码（可选）"
              value={code}
              placeholder="600001"
              onChange={handleCode}
              onEnter={handleQuery}
            />
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleQuery}
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
          <RegistryTable
            factors={factors}
            selected={selected}
            onSelect={(name) => setSelected(name)}
          />
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
