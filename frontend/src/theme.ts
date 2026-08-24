export type ThemeMode = "dark" | "light" | "system";

const STORAGE_KEY = "novelforge.theme";

export function getStoredTheme(): ThemeMode {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === "light" || value === "dark" || value === "system") return value;
  } catch {
    /* ignore */
  }
  return "system";
}

export function resolveTheme(mode: ThemeMode): "dark" | "light" {
  if (mode === "system") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return mode;
}

export function applyTheme(mode: ThemeMode): ThemeMode {
  const effective = resolveTheme(mode);
  const root = document.documentElement;
  root.dataset.theme = effective;
  root.dataset.themeMode = mode;
  root.classList.toggle("dark", effective === "dark");
  root.classList.toggle("light", effective === "light");
  root.style.colorScheme = effective;
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* ignore */
  }
  return mode;
}

export function toggleTheme(current: ThemeMode): ThemeMode {
  // button toggle: system falls back to the effective light/dark
  const effective = resolveTheme(current);
  return applyTheme(effective === "dark" ? "light" : "dark");
}

/** Listen to OS theme changes; only re-applies when mode is "system". */
export function initSystemThemeListener(): () => void {
  const mql = window.matchMedia("(prefers-color-scheme: dark)");
  const handler = () => {
    if (getStoredTheme() === "system") applyTheme("system");
  };
  mql.addEventListener("change", handler);
  return () => mql.removeEventListener("change", handler);
}

export type { ThemeMode as ThemeModeT };
