export function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="inline-flex items-center gap-2.5 text-[12.5px] text-ink-2 transition-colors hover:text-ink"
    >
      <span className="switch" data-on={checked ? "true" : "false"} />
      <span>{label}</span>
    </button>
  );
}
