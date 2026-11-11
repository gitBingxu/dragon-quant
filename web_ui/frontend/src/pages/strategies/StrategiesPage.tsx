import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchStrategies,
  saveStrategySelection,
  type StrategyItem,
  type StrategyList,
} from "../../api/strategies";
import { ApiError } from "../../api/client";
import { Badge } from "../../components/Badge";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { Select, TextField } from "../../components/Select";

function msg(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

function sourceTone(item: StrategyItem) {
  return item.source === "builtin" ? "normal" : "brand";
}

export function StrategiesPage() {
  const [data, setData] = useState<StrategyList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [paramsText, setParamsText] = useState<string>("{}");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const payload = await fetchStrategies();
      setData(payload);
      setSelected(payload.current);
      const current = payload.strategies.find((item) => item.id === payload.current);
      setParamsText(JSON.stringify(current?.effective_parameters ?? {}, null, 2));
      setError(null);
    } catch (exc) {
      setError(msg(exc));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const selectedItem = useMemo(
    () => data?.strategies.find((item) => item.id === selected) ?? null,
    [data, selected],
  );

  const onSelect = (value: string) => {
    setSelected(value);
    const item = data?.strategies.find((entry) => entry.id === value);
    setParamsText(JSON.stringify(item?.effective_parameters ?? {}, null, 2));
    setNotice(null);
  };

  const save = async () => {
    if (!data || data.revision == null) {
      setError("未配置 workspace，无法保存策略选择；请先运行 workspace init");
      return;
    }
    let parameters: Record<string, unknown> | null = null;
    try {
      parameters = JSON.parse(paramsText || "{}");
    } catch {
      setError("参数必须是合法 JSON");
      return;
    }
    setSaving(true);
    setNotice(null);
    try {
      const result = await saveStrategySelection(selected, parameters, data.revision);
      setNotice(`已保存：${result.current}（revision ${result.revision}）`);
      await load();
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 409) {
        setError("配置已被其他操作修改，请刷新后重试");
      } else {
        setError(msg(exc));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="策略"
        title="内建与用户策略"
        description="在此选择用于新开仓的策略；单次回测可在账户页显式覆盖。切换只影响新开仓，旧持仓按开仓时冻结的版本退出。"
      />

      {error ? (
        <div className="panel mb-4 border-danger/40 p-4 text-body text-danger">{error}</div>
      ) : null}
      {notice ? (
        <div className="panel mb-4 border-positive/40 p-4 text-body text-positive">{notice}</div>
      ) : null}

      {!data ? (
        <div className="panel p-6 text-body text-ink-2">加载中…</div>
      ) : data.strategies.length === 0 ? (
        <EmptyState title="暂无策略" description="运行 dragon-quant workspace init 初始化策略工作区。" />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Section
            title="策略清单"
            description={`当前：${data.current}${data.workspace ? ` · workspace ${data.workspace}` : " · 未配置 workspace"}`}
          >
            <div className="space-y-3">
              {data.strategies.map((item) => (
                <label
                  key={item.id}
                  className="block cursor-pointer rounded-md border border-line p-3 hover:border-brand/50"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      type="radio"
                      name="strategy"
                      value={item.id}
                      checked={selected === item.id}
                      onChange={() => onSelect(item.id)}
                    />
                    <span className="font-medium text-ink">{item.label}</span>
                    <Badge tone={sourceTone(item)}>{item.source === "builtin" ? "内建" : "用户"}</Badge>
                    {item.current ? <Badge tone="up">当前</Badge> : null}
                    {!item.valid ? <Badge tone="danger">校验失败</Badge> : null}
                  </div>
                  <div className="mt-1 font-mono text-caption text-ink-3">{item.id}</div>
                  {item.description ? (
                    <div className="mt-1 text-caption text-ink-2">{item.description}</div>
                  ) : null}
                  {item.errors.length > 0 ? (
                    <ul className="mt-1 text-caption text-danger">
                      {item.errors.map((line) => (
                        <li key={line}>⚠️ {line}</li>
                      ))}
                    </ul>
                  ) : null}
                  {item.required_factors.length > 0 ? (
                    <div className="mt-1 text-caption text-ink-3">
                      必要因子：{item.required_factors.join("、")}
                    </div>
                  ) : null}
                </label>
              ))}
            </div>
          </Section>

          <Section title="配置" description="参数覆盖只影响保存后的新开仓；历史回测使用冻结值。">
            <div className="space-y-3">
              <Select
                label="策略"
                value={selected}
                options={data.strategies.map((item) => ({
                  value: item.id,
                  label: `${item.label}（${item.id}）`,
                }))}
                onChange={onSelect}
              />
              <TextField
                label="参数（JSON）"
                value={paramsText}
                onChange={setParamsText}
                placeholder="{}"
              />
              {selectedItem && !selectedItem.valid ? (
                <div className="text-body text-danger">
                  该策略存在校验错误，保存后执行会失败；请先修复。
                </div>
              ) : null}
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void save()}
                disabled={saving || !selected}
              >
                {saving ? "保存中…" : "用于新开仓并保存"}
              </button>
              <p className="text-caption text-ink-3">
                CLI 等价命令：<code>dragon-quant strategy use {selected || "<id>"}</code>。
                Web 与 CLI 共读同一保存状态。
              </p>
            </div>
          </Section>
        </div>
      )}
    </>
  );
}
