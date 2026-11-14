// 单个参数控件：按 descriptor 的 type/enum 分派到数字输入、开关、下拉或文本框。
// 元数据缺失时回退到参数键名，保证老 descriptor 仍可编辑。

import type { StrategyParameterSpec } from "../../api/strategies";
import { Select } from "../../components/Select";
import { Switch } from "../../components/Switch";
import {
  enumOptions,
  paramKind,
  paramLabel,
  rangeHint,
  type ParamValue,
} from "../../lib/strategyParams";

export function ParamField({
  name,
  spec,
  value,
  error,
  onChange,
}: {
  name: string;
  spec: StrategyParameterSpec;
  value: ParamValue | undefined;
  error?: string;
  onChange: (value: ParamValue) => void;
}) {
  const kind = paramKind(spec);
  const label = paramLabel(name, spec);
  const id = `param-${name}`;
  const errorId = `${id}-error`;
  const hint = rangeHint(spec);

  const head = (
    <>
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {spec.help ? <span className="text-caption text-ink-3">{spec.help}</span> : null}
      {hint ? <span className="text-caption text-ink-3">{hint}</span> : null}
    </>
  );
  const errorLine = error ? (
    <span id={errorId} className="text-caption text-danger">
      {error}
    </span>
  ) : (
    <span id={errorId} className="text-caption text-danger" aria-live="polite" />
  );

  if (kind === "bool") {
    return (
      <div className="field">
        <span className="field-label">{label}</span>
        {spec.help ? <span className="text-caption text-ink-3">{spec.help}</span> : null}
        <Switch
          checked={value === true}
          onChange={onChange}
          label={value === true ? "开启" : "关闭"}
          ariaLabel={label}
        />
        {errorLine}
      </div>
    );
  }

  if (kind === "enum") {
    return (
      <div className="field">
        {head}
        <Select
          id={id}
          value={typeof value === "string" ? value : ""}
          options={enumOptions(spec)}
          onChange={onChange}
        />
        {spec.unit ? (
          <span className="text-caption text-ink-3">单位：{spec.unit}</span>
        ) : null}
        {errorLine}
      </div>
    );
  }

  if (kind === "number" || kind === "int") {
    return (
      <div className="field">
        {head}
        <div className="flex items-center gap-2">
          <input
            id={id}
            className="input"
            type="number"
            inputMode={kind === "int" ? "numeric" : "decimal"}
            step={spec.step ?? (kind === "int" ? 1 : "any")}
            min={spec.min}
            max={spec.max}
            value={typeof value === "string" ? value : ""}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? errorId : undefined}
            onChange={(event) => onChange(event.currentTarget.value)}
          />
          {spec.unit ? (
            <span className="whitespace-nowrap text-caption text-ink-3">{spec.unit}</span>
          ) : null}
        </div>
        {errorLine}
      </div>
    );
  }

  return (
    <div className="field">
      {head}
      <div className="flex items-center gap-2">
        <input
          id={id}
          className="input"
          type="text"
          value={typeof value === "string" ? value : ""}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          onChange={(event) => onChange(event.currentTarget.value)}
        />
        {spec.unit ? (
          <span className="whitespace-nowrap text-caption text-ink-3">{spec.unit}</span>
        ) : null}
      </div>
      {errorLine}
    </div>
  );
}
