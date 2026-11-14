export function Switch({
  checked,
  onChange,
  label,
  ariaLabel,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  /** 可选：为开关提供独立于可见文案的无障碍名称。 */
  ariaLabel?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      onClick={() => onChange(!checked)}
      className="inline-flex items-center gap-2.5 text-body text-ink-2 transition-colors hover:text-ink"
    >
      <span className="switch" data-on={checked ? "true" : "false"} />
      <span>{label}</span>
    </button>
  );
}
