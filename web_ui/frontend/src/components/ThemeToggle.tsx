import { IconMonitor, IconMoon, IconSun } from "./icons";
import { useTheme } from "../theme/ThemeProvider";
import type { ThemePreference } from "../theme/theme";

const OPTIONS: ReadonlyArray<{
  value: ThemePreference;
  label: string;
  short: string;
  Icon: typeof IconSun;
}> = [
  { value: "system", label: "跟随系统", short: "系统", Icon: IconMonitor },
  { value: "light", label: "浅色", short: "浅色", Icon: IconSun },
  { value: "dark", label: "深色", short: "深色", Icon: IconMoon },
];

export function ThemeToggle() {
  const { preference, setPreference } = useTheme();
  return (
    <div className="seg" role="group" aria-label="主题模式">
      {OPTIONS.map(({ value, label, short, Icon }) => {
        const active = preference === value;
        return (
          <button
            key={value}
            type="button"
            className="seg-item"
            data-active={active ? "true" : "false"}
            aria-pressed={active}
            aria-label={label}
            title={label}
            onClick={() => setPreference(value)}
          >
            <Icon size={14} />
            <span className="hidden sm:inline">{short}</span>
          </button>
        );
      })}
    </div>
  );
}
