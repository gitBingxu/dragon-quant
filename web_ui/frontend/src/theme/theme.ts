// 主题偏好契约：偏好 → 解析后的 light/dark → DOM。
//
// 策略模式：ThemePreference 是用户选择的策略，resolveTheme 是纯函数策略解析，
// applyResolvedTheme 是 DOM/CSS 的唯一输出端。业务页面、图表、表格不感知
// matchMedia / localStorage。
//
// ⚠ 存储键与合法值在 `web_ui/frontend/index.html` 的首屏内联脚本里各有一份
// （内联脚本无法 import TS）。两处必须同步；`test_web_frontend_theme.py`
// 的源码级双处契约测试会守护它们。改这里时同步改 index.html，反之亦然。

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

/** 与 index.html 内联脚本的 KEY 常量保持一致。 */
export const THEME_STORAGE_KEY = "dq.theme";

/** 与 index.html 内联脚本的 meta theme-color 取值保持一致。 */
export const THEME_META_COLORS: Record<ResolvedTheme, string> = {
  light: "#F4F6FB",
  dark: "#0B0E14",
};

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === "system" || value === "light" || value === "dark";
}

/** localStorage 的属性访问本身也可能抛错（隐私模式），必须一起包 try/catch。 */
function safeStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readStoredPreference(): ThemePreference {
  const storage = safeStorage();
  if (!storage) return "system";
  try {
    const stored = storage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

/** 写入失败静默：本次会话内切换仍然生效，只是不持久化。 */
export function writeStoredPreference(preference: ThemePreference): void {
  const storage = safeStorage();
  if (!storage) return;
  try {
    storage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // ignore quota / privacy-mode errors
  }
}

/** 无 matchMedia 或调用抛错 → null（system 回退 dark）。 */
export function systemPrefersLight(): boolean | null {
  try {
    if (typeof window.matchMedia !== "function") return null;
    return window.matchMedia("(prefers-color-scheme: light)").matches;
  } catch {
    return null;
  }
}

export function resolveTheme(
  preference: ThemePreference,
  systemPrefersLightValue: boolean | null
): ResolvedTheme {
  if (preference === "light") return "light";
  if (preference === "dark") return "dark";
  return systemPrefersLightValue === true ? "light" : "dark";
}

/** 同时维护 data-theme / colorScheme / meta theme-color，避免三者失配。 */
export function applyResolvedTheme(theme: ResolvedTheme): void {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", THEME_META_COLORS[theme]);
}
