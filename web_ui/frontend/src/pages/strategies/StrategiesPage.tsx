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
import {
  buildDraft,
  validateDraft,
  type ParamDraft,
  type ParamErrors,
  type ParamValue,
} from "../../lib/strategyParams";
import { ParamField } from "./ParamField";

function msg(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

function sourceTone(item: StrategyItem) {
  return item.source === "builtin" ? "normal" : "brand";
}

function seedDraft(item: StrategyItem | undefined): ParamDraft {
  if (!item) return {};
  return buildDraft(item.parameters ?? {}, item.effective_parameters ?? {});
}

export function StrategiesPage() {
  const [data, setData] = useState<StrategyList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [draft, setDraft] = useState<ParamDraft>({});
  const [fieldErrors, setFieldErrors] = useState<ParamErrors>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const payload = await fetchStrategies();
      setData(payload);
      setSelected(payload.current);
      const current = payload.strategies.find((item) => item.id === payload.current);
      setDraft(seedDraft(current));
      setFieldErrors({});
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
  const parameterEntries = useMemo(
    () => Object.entries(selectedItem?.parameters ?? {}),
    [selectedItem],
  );
  const readOnly = !data?.workspace || data?.revision == null;
  const invalid = Boolean(selectedItem && !selectedItem.valid);

  const onSelect = (id: string) => {
    const item = data?.strategies.find((entry) => entry.id === id);
    setSelected(id);
    setDraft(seedDraft(item));
    setFieldErrors({});
    setError(null);
    setNotice(null);
  };

  const setValue = (name: string, value: ParamValue) => {
    setDraft((prev) => ({ ...prev, [name]: value }));
    setFieldErrors((prev) => {
      if (!(name in prev)) return prev;
      const next = { ...prev };
      delete next[name];
      return next;
    });
  };

  const save = async () => {
    if (!data || data.revision == null) {
      setError("未配置 workspace，无法保存策略选择；请先运行 workspace init");
      return;
    }
    if (!selectedItem) return;
    const { values, errors } = validateDraft(selectedItem.parameters ?? {}, draft);
    setFieldErrors(errors);
    if (!values) return;
    setSaving(true);
    setNotice(null);
    setError(null);
    try {
      const result = await saveStrategySelection(selected, values, data.revision);
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
        description="选择策略决定买卖逻辑；右侧只调整该策略开放的阈值与选项。保存只影响未来新开仓，已有持仓继续使用开仓时冻结的配置。"
      />

      {error ? (
        <div className="panel mb-4 border-danger-line bg-danger-soft p-4 text-body text-danger-text">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="panel mb-4 border-positive-line bg-positive-soft p-4 text-body text-positive-text">
          {notice}
        </div>
      ) : null}

      {!data ? (
        <div className="panel p-6 text-body text-ink-2">加载中…</div>
      ) : data.strategies.length === 0 ? (
        <EmptyState title="暂无策略" description="运行 dragon-quant workspace init 初始化策略工作区。" />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Section
            title="选择策略"
            description={`点击卡片切换右侧参数；当前保存的选择：${data.current}${
              data.workspace ? "" : " · 未配置 workspace"
            }`}
          >
            <div className="space-y-3">
              {data.strategies.map((item) => {
                const active = selected === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    aria-pressed={active}
                    onClick={() => onSelect(item.id)}
                    className={`block w-full rounded-md border p-3 text-left transition-colors ${
                      active
                        ? "border-brand bg-brand-soft"
                        : "border-hairline bg-raise hover:border-hairline-2"
                    }`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-ink">{item.label}</span>
                      <Badge tone={sourceTone(item)}>
                        {item.source === "builtin" ? "内建" : "用户"}
                      </Badge>
                      {active ? <Badge tone="brand">已选</Badge> : null}
                      {item.current ? <Badge tone="up">当前</Badge> : null}
                      {!item.valid ? <Badge tone="danger">校验失败</Badge> : null}
                    </div>
                    <div className="mt-1 font-mono text-caption text-ink-3">{item.id}</div>
                    {item.description ? (
                      <div className="mt-1 text-caption text-ink-2">{item.description}</div>
                    ) : null}
                    {item.errors.length > 0 ? (
                      <ul className="mt-1 text-caption text-danger-text">
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
                  </button>
                );
              })}
            </div>
          </Section>

          <Section
            title={selectedItem ? `${selectedItem.label} · 参数` : "策略参数"}
            description="参数只调整该策略开放的阈值与选项，不改变策略逻辑。"
          >
            <div className="space-y-4">
              {readOnly ? (
                <div className="rounded-md border border-warning-line bg-warning-soft p-3 text-caption text-ink-2">
                  未配置 workspace：参数可浏览，但保存已禁用。请先运行
                  <code className="mx-1">dragon-quant workspace init &lt;path&gt;</code>
                  初始化工作区。
                </div>
              ) : null}
              {invalid ? (
                <div className="rounded-md border border-danger-line bg-danger-soft p-3 text-caption text-danger-text">
                  该策略存在校验错误，保存已禁用；请先修复 descriptor 后刷新。
                </div>
              ) : null}

              {!selectedItem ? (
                <div className="text-body text-ink-2">请选择左侧策略。</div>
              ) : parameterEntries.length === 0 ? (
                <EmptyState
                  title="该策略未开放可调参数"
                  description="仍可保存，以把该策略设为新开仓使用的策略。"
                />
              ) : (
                <div className="grid gap-4 sm:grid-cols-2">
                  {parameterEntries.map(([name, spec]) => (
                    <ParamField
                      key={name}
                      name={name}
                      spec={spec}
                      value={draft[name]}
                      error={fieldErrors[name]}
                      onChange={(value) => setValue(name, value)}
                    />
                  ))}
                </div>
              )}

              <div className="flex flex-col gap-3 border-t border-hairline pt-4 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-caption text-ink-3">
                  保存后用于未来新开仓；已有持仓继续使用开仓时冻结的配置。
                </p>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => void save()}
                  disabled={saving || !selectedItem || readOnly || invalid}
                >
                  {saving ? "保存中…" : "保存"}
                </button>
              </div>

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
