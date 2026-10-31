import { useEffect, useState } from "react";
import { createAccountRun, type AccountRun } from "../../api/account";
import { Modal } from "../../components/Modal";
import { IconAlert } from "../../components/icons-extra";
import { defaultDateRange, defaultRunName } from "../../lib/format";

export function CreateRunModal({
  open,
  source,
  onClose,
  onCreated,
}: {
  open: boolean;
  source: "v1" | "v2";
  onClose: () => void;
  onCreated: (run: AccountRun) => void;
}) {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [name, setName] = useState("");
  const [initialCash, setInitialCash] = useState("100000");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const range = defaultDateRange();
    setDateFrom(range.dateFrom);
    setDateTo(range.dateTo);
    setName(defaultRunName(range.dateFrom, range.dateTo));
    setInitialCash("100000");
    setError(null);
  }, [open]);

  const submit = async () => {
    const cleanName = name.trim() || defaultRunName(dateFrom, dateTo);
    if (!dateFrom || !dateTo) {
      setError("请选择完整日期范围");
      return;
    }
    if (dateFrom > dateTo) {
      setError("开始日期不能晚于结束日期");
      return;
    }
    const cash = Number(initialCash);
    if (!Number.isFinite(cash) || cash <= 0) {
      setError("初始资金必须大于 0");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const resp = await createAccountRun({
        source,
        name: cleanName,
        date_from: dateFrom,
        date_to: dateTo,
        initial_cash: cash,
      });
      onCreated(resp.data);
      onClose();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "生成失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="生成回测记录"
      description={`数据来源：${source === "v2" ? "五维识别" : "v1 历史"} · 生成后自动选中`}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={submitting}>
            {submitting ? "生成中…" : "确认生成"}
          </button>
        </>
      }
    >
      <div className="grid gap-3.5">
        {error ? (
          <div className="flex items-center gap-2 rounded-lg border border-critical/35 bg-critical-soft px-3 py-2 text-[12.5px] text-[#ff9093]">
            <IconAlert size={14} />
            {error}
          </div>
        ) : null}
        <label className="field">
          <span className="field-label">记录名称</span>
          <input
            className="input"
            value={name}
            placeholder={defaultRunName(dateFrom, dateTo)}
            onChange={(event) => setName(event.currentTarget.value)}
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="field">
            <span className="field-label">开始日期</span>
            <input
              className="input"
              type="date"
              value={dateFrom}
              onChange={(event) => {
                const next = event.currentTarget.value;
                setDateFrom(next);
                if (!name.trim() || name === defaultRunName(dateFrom, dateTo)) {
                  setName(defaultRunName(next, dateTo));
                }
              }}
            />
          </label>
          <label className="field">
            <span className="field-label">结束日期</span>
            <input
              className="input"
              type="date"
              value={dateTo}
              onChange={(event) => {
                const next = event.currentTarget.value;
                setDateTo(next);
                if (!name.trim() || name === defaultRunName(dateFrom, dateTo)) {
                  setName(defaultRunName(dateFrom, next));
                }
              }}
            />
          </label>
        </div>
        <label className="field">
          <span className="field-label">初始资金</span>
          <input
            className="input num"
            type="number"
            min={1}
            step={10000}
            value={initialCash}
            onChange={(event) => setInitialCash(event.currentTarget.value)}
          />
        </label>
      </div>
    </Modal>
  );
}
