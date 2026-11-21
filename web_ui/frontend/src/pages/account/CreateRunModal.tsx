import { useEffect, useRef, useState } from "react";
import {
  createAccountRun,
  type AccountRun,
  type CreateAccountRunInput,
} from "../../api/account";
import { fetchStrategies, type StrategyItem } from "../../api/strategies";
import { Modal } from "../../components/Modal";
import { Select } from "../../components/Select";
import { IconAlert } from "../../components/icons-extra";
import { defaultDateRange, defaultRunName } from "../../lib/format";

/**
 * 「重新回测」预填值：来自一条缺少冻结策略身份的历史记录。
 *
 * 只复用同一个表单/接口，不新增回测引擎；旧记录保持不变，新记录按**当前**
 * 策略版本全量重跑并重新冻结身份。
 */
export interface CreateRunSeed {
  /** 原记录 id（仅用于文案：旧记录保持不变）。 */
  originRunId: number;
  dateFrom: string;
  dateTo: string;
  initialCash: number;
  /**
   * 可预选的 canonical 策略 ID：仅当历史策略可确定为已知内建时给出；
   * null = 未知/自定义历史策略，必须由用户显式选择（不猜）。
   */
  strategyId: string | null;
  /** 原记录交易参数（StrategyConfig 形状），按既有 strategy_params 合同回传。 */
  strategyParams: Record<string, unknown>;
}

function laterDate(a: string, b: string): string {
  if (!a) return b;
  if (!b) return a;
  return a >= b ? a : b;
}

function seedRange(seed: CreateRunSeed | null): { dateFrom: string; dateTo: string } {
  const range = defaultDateRange();
  if (!seed) return range;
  // 终点用「当前默认终点 vs 原终点」较晚者：绝不早于原记录的结束日。
  return {
    dateFrom: seed.dateFrom || range.dateFrom,
    dateTo: laterDate(range.dateTo, seed.dateTo),
  };
}

export function CreateRunModal({
  open,
  onClose,
  onCreated,
  seed = null,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (run: AccountRun) => void;
  /** 重新回测预填；普通新建传 null（关闭再打开会重置为默认值）。 */
  seed?: CreateRunSeed | null;
}) {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [name, setName] = useState("");
  const [initialCash, setInitialCash] = useState("100000");
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [bsDefault, setBsDefault] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 本次打开生效的 seed：异步 catalog 只按它判断，绝不覆盖 seed / 用户后续编辑。
  const seedRef = useRef<CreateRunSeed | null>(null);
  const strategyTouchedRef = useRef(false);
  const generationRef = useRef(0);

  useEffect(() => {
    if (!open) return;
    const active = seed ?? null;
    const generation = ++generationRef.current;
    seedRef.current = active;
    strategyTouchedRef.current = false;
    const range = seedRange(active);
    setDateFrom(range.dateFrom);
    setDateTo(range.dateTo);
    // 名称保持「起 ~ 止」默认形式，编辑日期时同步；「重新回测」语义在标题/说明里。
    setName(defaultRunName(range.dateFrom, range.dateTo));
    setInitialCash(active ? String(active.initialCash) : "100000");
    setError(null);
    setBsDefault(false);
    setStrategies([]);
    setStrategyId(active?.strategyId ?? "");
    void fetchStrategies()
      .then((payload) => {
        if (generation !== generationRef.current) return;
        const list = payload.strategies.filter(
          (item) => item.valid && item.id !== "builtin:bs_account_decision"
        );
        setStrategies(list);
        if (active) {
          // 历史策略在当前目录不可用（例如用户策略已删除）→ 必须显式重选。
          if (active.strategyId && !list.some((item) => item.id === active.strategyId)) {
            setStrategyId("");
          }
          return;
        }
        const isBs = payload.current === "builtin:bs_account_decision";
        setBsDefault(isBs);
        if (!strategyTouchedRef.current) {
          setStrategyId(isBs ? "" : payload.current);
        }
      })
      .catch(() => {
        if (generation !== generationRef.current) return;
        setStrategies([]);
        if (active) return;
        setBsDefault(false);
        if (!strategyTouchedRef.current) setStrategyId("");
      });
  }, [open, seed]);

  const submit = async () => {
    const seeded = seedRef.current;
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

    if (seeded && !strategyId) {
      setError(
        "历史记录的策略无法对应到当前可用版本，请显式选择本次要运行的策略后再重跑。"
      );
      return;
    }
    if (bsDefault && !strategyId) {
      setError("当前保存选择是 BS 账户决策，旧 entry/exit 表单不能默默改跑其它策略；请改用专用流程。");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const params = seeded?.strategyParams;
      const payload: CreateAccountRunInput = {
        name: cleanName,
        date_from: dateFrom,
        date_to: dateTo,
        initial_cash: cash,
        ...(strategyId ? { strategy_id: strategyId } : {}),
        // 交易参数沿用既有 strategy_params 合同（服务端照常校验/拒绝绕过开关）。
        ...(params && Object.keys(params).length ? { strategy_params: params } : {}),
      };
      const resp = await createAccountRun(payload);
      onCreated(resp.data);
      onClose();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "生成失败");
    } finally {
      setSubmitting(false);
    }
  };

  // 渲染用 seed 直接来自 prop（effect 再把表单状态重置到它）；关闭后不再生效。
  const seeded = open ? seed ?? null : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={seeded ? "重新回测（生成新记录）" : "生成回测记录"}
      description={
        seeded
          ? "按当前策略版本全量重跑；原记录保持不变"
          : "数据来源：五维识别 · 生成后自动选中"
      }
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={submitting}>
            {submitting ? "生成中…" : seeded ? "确认重新回测" : "确认生成"}
          </button>
        </>
      }
    >
      <div className="grid gap-3.5">
        {error ? (
          <div className="alert-danger flex items-center gap-2 rounded-lg px-3 py-2 text-body">
            <IconAlert size={14} />
            {error}
          </div>
        ) : null}
        {seeded ? (
          <div className="rounded-lg border border-hairline px-3 py-2 text-caption text-ink-2">
            原记录 #{seeded.originRunId}（{seeded.dateFrom} ~ {seeded.dateTo}）缺少可证明的策略版本，
            无法增量续算。本次将按当前策略版本从起点全量重跑并生成{" "}
            <b>新记录</b>，原记录保持不变。旧记录的交易参数（止损/止盈/费用等）已预填。
          </div>
        ) : null}
        {seeded && !strategyId ? (
          <div className="rounded-lg border border-warning-line bg-warning-soft px-3 py-2 text-caption text-warning">
            原记录的策略无法确定为当前内建版本：请在上方显式选择本次要运行的策略，
            系统不会替你猜测历史策略。
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
        {bsDefault ? (
          <div className="rounded-lg border border-warning-line bg-warning-soft px-3 py-2 text-caption text-warning">
            当前保存的策略是 <b>BS 账户决策</b>：本表单只运行旧 entry/exit 策略，不会替它提交。
            请前往 <a className="underline" href="/research?bs=policy">BS 账户决策统一流程</a>
            显式选择策略/账户；或在上方主动选择其它策略再运行。
          </div>
        ) : null}
        <Select
          label="策略"
          value={strategyId}
          options={
            strategies.length > 0
              ? [
                  ...(seeded && !strategyId
                    ? [{ value: "", label: "请选择策略（必选）" }]
                    : []),
                  ...strategies.map((item) => ({
                    value: item.id,
                    label: `${item.label}（${item.source === "builtin" ? "内建" : "用户"}）`,
                  })),
                ]
              : [
                  {
                    value: seeded?.strategyId ?? "",
                    label: seeded
                      ? seeded.strategyId
                        ? seeded.strategyId
                        : "请选择策略（必选；目录读取失败）"
                      : "沿用保存选择 / 内建默认",
                  },
                ]
          }
          onChange={(value) => {
            strategyTouchedRef.current = true;
            setStrategyId(value);
          }}
        />
        <p className="text-caption text-ink-3">
          省略选择时消费「策略」页保存的同一状态；此处的显式选择只影响本次回测。
        </p>
      </div>
    </Modal>
  );
}
