import { IconChevronDown } from "./icons";

export interface SelectOption {
  value: string;
  label: string;
}

export function Select({
  label,
  value,
  options,
  onChange,
  disabled = false,
  width,
  id,
}: {
  label?: string;
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  width?: number | string;
  id?: string;
}) {
  const select = (
    <div className="relative" style={width ? { width } : undefined}>
      <select
        id={id}
        className="select disabled:cursor-not-allowed disabled:opacity-50"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <IconChevronDown
        size={14}
        className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-3"
      />
    </div>
  );

  if (!label) return select;
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {select}
    </label>
  );
}

export function TextField({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  width,
  onEnter,
  min,
  step,
}: {
  label?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: "text" | "date" | "number";
  width?: number | string;
  onEnter?: () => void;
  min?: number;
  step?: number;
}) {
  const input = (
    <input
      className="input"
      type={type}
      value={value}
      placeholder={placeholder}
      min={min}
      step={step}
      onChange={(event) => onChange(event.currentTarget.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter" && onEnter) onEnter();
      }}
    />
  );
  const wrapped = width ? <div style={{ width }}>{input}</div> : input;
  if (!label) return wrapped;
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {wrapped}
    </label>
  );
}
