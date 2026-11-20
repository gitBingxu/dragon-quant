import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  fetchBsAdvice,
  fetchBsLedger,
  listBsExperiments,
  replayBsExperiment,
  resumeBsExperiment,
  runBsExperiment,
  runBsReview,
  showBsExperiment,
} from "../../api/bsResearch";
import { defaultDateRange, fmtMoney, nowIsoWithOffset } from "../../lib/format";
import { useSearchParams } from "../../router";
import { BsAdviceSummary } from "./bs/BsAdviceSummary";
import { BsAudit } from "./bs/BsAudit";
import { BsExperimentDetail } from "./bs/BsExperimentDetail";
import { BsExperimentList } from "./bs/BsExperimentList";
import { BsInputs } from "./bs/BsInputs";
import { BsLedgerView } from "./bs/BsLedgerView";
import type { BsFormState, BsMode, BsNotice } from "./bs/types";

function parseJson(text: string, label: string): Record<string, unknown> | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try {
    const value = JSON.parse(trimmed);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("必须是 JSON 对象");
    }
    return value as Record<string, unknown>;
  } catch (exc) {
    throw new Error(`${label} JSON 无法解析：${exc instanceof Error ? exc.message : String(exc)}`);
  }
}

function initialForm(): BsFormState {
  const range = defaultDateRange();
  return {
    accountText: "",
    policyText: "",
    studyText: "",
    enhancedId: "",
    newAttempt: false,
    cutoff: nowIsoWithOffset(),
    dateFrom: range.dateFrom,
    dateTo: range.dateTo,
    cohort: "rolling",
  };
}

/** BS 账户决策统一流程：结构化输入 → 建议/模拟/冻结实验 → 账本/列表详情。 */
export function BsPanel(): ReactNode {
  const search = useSearchParams();
  const [form, setFormState] = useState<BsFormState>(initialForm);
  const [busy, setBusy] = useState<BsMode | null>(null);
  const [notice, setNotice] = useState<BsNotice | null>(null);
  const [advice, setAdvice] = useState<Record<string, any> | null>(null);
  const [review, setReview] = useState<Record<string, any> | null>(null);
  const [ledger, setLedger] = useState<Record<string, any> | null>(null);
  const [ledgerCursor, setLedgerCursor] = useState<string | null>(null);
  const [ledgerId, setLedgerId] = useState("");
  const [list, setList] = useState<Record<string, any> | null>(null);
  const [listCursor, setListCursor] = useState<number | null>(null);
  const [detail, setDetail] = useState<Record<string, any> | null>(null);

  const setForm = (patch: Partial<BsFormState>) =>
    setFormState((prev) => ({ ...prev, ...patch }));

  const entry = search.get("bs");
  const entryHint = useMemo(() => {
    if (entry === "account") return "已从「账户」页进入 BS 统一流程：请显式选择策略与账户锚点。";
    if (entry === "policy") return "已从「策略配置」页进入 BS 统一流程：旧 entry/exit 表单不运行 BS。";
    return null;
  }, [entry]);

  const loadList = useCallback(async (cursor: number | null = null, append = false) => {
    const payload = await listBsExperiments(20, cursor);
    setList((prev) => (append && prev
      ? { ...payload.view, items: [...(prev.items || []), ...(payload.view.items || [])] }
      : payload.view));
    setListCursor(payload.meta?.next_cursor ?? null);
  }, []);

  useEffect(() => {
    // 只读列表：GET 不创建数据；空库也会给出说明。
    loadList(null).catch(() => setList({ kind: "experiment_list", items: [], page: {} }));
  }, [loadList]);

  const loadLedger = useCallback(async (id: string, cursor: string | null = null, append = false) => {
    if (!id) throw new Error("缺少 ledger_id");
    const payload = await fetchBsLedger(id, 100, cursor);
    setLedger((prev) => (append && prev
      ? { ...payload.view,
          events: [...(prev.events || []), ...(payload.view.events || [])] }
      : payload.view));
    setLedgerCursor(payload.view.page?.next_cursor ?? null);
    setLedgerId(id);
    return payload.view;
  }, []);

  const execute = async (mode: BsMode) => {
    setNotice(null);
    setBusy(mode);
    try {
      const account = parseJson(form.accountText, "账户");
      if (!account) throw new Error("请上传或粘贴账户 JSON");
      const policy = parseJson(form.policyText, "策略配置");
      const study = parseJson(form.studyText, "研究规范");
      if (mode === "advice") {
        const payload = await fetchBsAdvice({ account, policy, cutoff: form.cutoff });
        setAdvice(payload.view);
        return;
      }
      if (!policy) throw new Error("显式模拟/冻结对照需要策略配置 JSON");
      if (form.dateFrom > form.dateTo) throw new Error("开始日期不能晚于结束日期");
      if (mode === "review") {
        const payload = await runBsReview(account, policy, form.dateFrom,
          form.dateTo, form.cohort);
        setReview(payload.view);
        const ledgerView = await loadLedger(String(payload.data.ledger_id || ""));
        setNotice({ tone: "info", text: `模拟回放已写入账本 ${ledgerView.ledger_id}；未真实下单。` });
        await loadList(null);
        return;
      }
      const enhancedId = form.enhancedId.trim() ? Number(form.enhancedId) : null;
      if (enhancedId != null && (!Number.isInteger(enhancedId) || enhancedId <= 0)) {
        throw new Error("增强实验 ID 必须是正整数");
      }
      const payload = await runBsExperiment(account, policy, form.dateFrom,
        form.dateTo, form.cohort, {
          study, enhancedExperimentId: enhancedId, newAttempt: form.newAttempt,
        });
      setDetail(payload.view);
      setNotice({
        tone: "info",
        text: `已保存冻结实验 #${payload.data.id}；工程完成与效果合格分开展示。`,
      });
      await loadList(null);
    } catch (exc) {
      setNotice({ tone: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setBusy(null);
    }
  };

  const loadDetail = async (id: number) => {
    setBusy("experiment");
    setNotice(null);
    try {
      const payload = await showBsExperiment(id, { includeDetails: true });
      setDetail(payload.view);
    } catch (exc) {
      setNotice({ tone: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setBusy(null);
    }
  };

  const replayDetail = async (id: number) => {
    setBusy("experiment");
    try {
      const payload = await replayBsExperiment(id);
      setDetail(payload.view);
      setNotice({ tone: "info", text: "只读重放完成；未写入新结果。" });
    } catch (exc) {
      setNotice({ tone: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setBusy(null);
    }
  };

  const resumeDetail = async (id: number) => {
    setBusy("experiment");
    try {
      const payload = await resumeBsExperiment(id);
      setDetail(payload.view);
      setNotice({ tone: "info", text: "已按已存 ID 显式恢复并更新状态。" });
      await loadList(null);
    } catch (exc) {
      setNotice({ tone: "error", text: exc instanceof Error ? exc.message : String(exc) });
    } finally {
      setBusy(null);
    }
  };

  const reset = () => {
    setFormState(initialForm());
    setAdvice(null); setReview(null); setLedger(null); setDetail(null);
    setLedgerId(""); setLedgerCursor(null);
    setNotice({ tone: "info", text: "已清空本页输入与结果；不会写入 URL/localStorage/日志。" });
  };

  return (
    <section id="bs-panel" className="panel mb-3 px-4 py-3.5 sm:px-[18px]">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-section font-semibold">BS 账户决策（统一流程）</h2>
          <p className="text-caption text-base-content/60">
            结构化账户/策略/研究规范 → 只读建议 → 显式模拟 → 冻结实验 → 账本与详情。
            账户金额单位为元、数量为股、比例为百分比；缺证据显示「待证据」而不是 0。
          </p>
        </div>
        <span className="badge badge-ghost">builtin:bs_account_decision</span>
      </div>
      {entryHint ? (
        <p className="mb-2 rounded-lg border border-hairline bg-base-200/40 px-3 py-2 text-caption text-ink-2">
          {entryHint}
        </p>
      ) : null}

      <BsInputs
        form={form}
        setForm={setForm}
        busy={busy}
        notice={notice}
        onAdvice={() => execute("advice")}
        onReview={() => execute("review")}
        onExperiment={() => execute("experiment")}
        onReset={reset}
      />

      {advice ? (
        <div className="mt-3">
          <h3 className="mb-1 text-caption font-semibold tracking-[0.08em] text-ink-3">只读建议摘要</h3>
          <BsAdviceSummary view={advice} />
        </div>
      ) : null}

      {review ? (
        <div className="mt-3 rounded-lg border border-hairline p-3 text-caption">
          <div className="font-semibold text-ink-2">显式模拟结果</div>
          <div className="mt-1 flex flex-wrap gap-3">
            <span>状态：{review.status}</span>
            <span>账本：{review.ledger_id}</span>
            <span>事件：{review.summary?.events ?? "—"}</span>
            <span>成交：{review.summary?.fills ?? "—"}</span>
            <span>期末权益：{fmtMoney(review.summary?.equity)}</span>
            <span className="text-ink-3">模拟成交 ≠ 真实成交；未连接券商。</span>
          </div>
          <BsAudit label="模拟回放原始审计" value={review.audit} />
        </div>
      ) : null}

      {ledger ? (
        <div className="mt-3">
          <h3 className="mb-1 text-caption font-semibold tracking-[0.08em] text-ink-3">账本详情</h3>
          <BsLedgerView
            view={ledger}
            busy={busy !== null}
            onLoadMore={() => {
              if (ledgerCursor) {
                void loadLedger(ledgerId, ledgerCursor, true);
              }
            }}
          />
        </div>
      ) : null}

      {detail ? (
        <div className="mt-3">
          <BsExperimentDetail
            view={detail}
            busy={busy !== null}
            onReplay={replayDetail}
            onResume={resumeDetail}
            onClose={() => setDetail(null)}
          />
        </div>
      ) : null}

      <div className="mt-3">
        <h3 className="mb-1 text-caption font-semibold tracking-[0.08em] text-ink-3">已存实验列表</h3>
        <BsExperimentList
          view={list || { kind: "experiment_list", items: [], page: {} }}
          busy={busy !== null}
          onOpen={loadDetail}
          onLoadMore={() => {
            if (listCursor != null) void loadList(listCursor, true);
          }}
        />
      </div>
    </section>
  );
}
