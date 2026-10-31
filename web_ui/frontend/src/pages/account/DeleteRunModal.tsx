import type { AccountRun } from "../../api/account";
import { Modal } from "../../components/Modal";
import { IconTrash } from "../../components/icons";

export function DeleteRunModal({
  run,
  loading,
  onClose,
  onConfirm,
}: {
  run: AccountRun | null;
  loading: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const label = run
    ? run.display_name || `#${run.id} ${run.date_from} ~ ${run.date_to}`
    : "";
  return (
    <Modal
      open={run != null}
      onClose={onClose}
      title="删除回测记录"
      description="该操作不可恢复"
      width={440}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={loading}>
            取消
          </button>
          <button type="button" className="btn btn-danger" onClick={onConfirm} disabled={loading}>
            <IconTrash size={14} />
            {loading ? "删除中…" : "确认删除"}
          </button>
        </>
      }
    >
      <p className="text-[13px] leading-relaxed text-ink-2">
        确认删除记录「<span className="font-medium text-ink">{label}</span>」？
        该批次的交割单、持仓、快照和决策时间线将一并删除，且不可恢复。
      </p>
    </Modal>
  );
}
