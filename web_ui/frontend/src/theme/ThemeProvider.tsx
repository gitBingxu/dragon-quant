import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  applyResolvedTheme,
  readStoredPreference,
  resolveTheme,
  systemPrefersLight,
  writeStoredPreference,
  type ResolvedTheme,
  type ThemePreference,
} from "./theme";

interface ThemeContextValue {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (next: ThemePreference) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function createLightQuery(): MediaQueryList | null {
  try {
    if (typeof window.matchMedia !== "function") return null;
    return window.matchMedia("(prefers-color-scheme: light)");
  } catch {
    return null;
  }
}

/** Provider 只依赖 theme/theme.ts：不 import router / api，主题切换不可能导航或请求。 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(() => readStoredPreference());
  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    resolveTheme(preference, systemPrefersLight())
  );

  useEffect(() => {
    const mq = createLightQuery();
    const apply = () => {
      const next = resolveTheme(preference, mq ? mq.matches : null);
      setResolved(next);
      applyResolvedTheme(next);
    };
    // 立即执行一次：保证 React 首帧与 head 内联脚本结果一致，避免二次跳变。
    apply();
    if (preference !== "system" || !mq) return;

    const onChange = () => apply();
    // 旧 Safari 没有 addEventListener/removeEventListener。
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    }
    mq.addListener(onChange);
    return () => mq.removeListener(onChange);
  }, [preference]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    writeStoredPreference(next);
  }, []);

  const value = useMemo(
    () => ({ preference, resolved, setPreference }),
    [preference, resolved, setPreference]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme 必须在 ThemeProvider 内使用");
  return value;
}
