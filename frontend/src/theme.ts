export type ThemeMode = "dark" | "light";

const STORAGE_KEY = "novelforge.theme";

export function getStoredTheme(): ThemeMode {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === "light" || value === "dark") return value;
  } catch {
    /* ignore */
  }
  return "dark";
}

export function applyTheme(mode: ThemeMode): ThemeMode {
  const root = document.documentElement;
  root.dataset.theme = mode;
  root.classList.toggle("dark", mode === "dark");
  root.classList.toggle("light", mode === "light");
  root.style.colorScheme = mode;
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* ignore */
  }
  return mode;
}

export function toggleTheme(current: ThemeMode): ThemeMode {
  return applyTheme(current === "dark" ? "light" : "dark");
}
