import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

interface ThemeState {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  /** Changes the theme and saves it for the signed-in user when a saver is registered (AppShell). */
  setPreference: (preference: ThemePreference) => void;
  /** Applies a preference loaded from the server without saving it again. */
  applyPreference: (preference: ThemePreference) => void;
  registerSaver: (saver: ((preference: ThemePreference) => void) | null) => void;
}

const ThemeContext = createContext<ThemeState>({
  preference: "system",
  resolved: "light",
  setPreference: () => undefined,
  applyPreference: () => undefined,
  registerSaver: () => undefined,
});

const DARK_QUERY = "(prefers-color-scheme: dark)";

function systemTheme(): ResolvedTheme {
  return typeof window !== "undefined" && window.matchMedia?.(DARK_QUERY).matches ? "dark" : "light";
}

/**
 * Light, dark or system theme (US-134). The resolved theme is set as data-theme on <html>, which switches the token
 * variables (tokens.css) and the ECharts theme. The preference is remembered per user on the
 * server (PUT /api/v1/me/preferences), not in browser storage.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPref] = useState<ThemePreference>("system");
  const [system, setSystem] = useState<ResolvedTheme>(systemTheme);
  const saver = useRef<((p: ThemePreference) => void) | null>(null);

  useEffect(() => {
    const mq = window.matchMedia?.(DARK_QUERY);
    if (!mq) return;
    const onChange = () => setSystem(mq.matches ? "dark" : "light");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const resolved = preference === "system" ? system : preference;
  useEffect(() => {
    document.documentElement.dataset.theme = resolved;
  }, [resolved]);

  const setPreference = useCallback((p: ThemePreference) => {
    setPref(p);
    saver.current?.(p);
  }, []);
  const registerSaver = useCallback((s: ((p: ThemePreference) => void) | null) => {
    saver.current = s;
  }, []);

  const value = useMemo(
    () => ({ preference, resolved, setPreference, applyPreference: setPref, registerSaver }),
    [preference, resolved, setPreference, registerSaver],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  return useContext(ThemeContext);
}

/**
 * Renders children in a fixed theme without changing the page (the component catalogue shows both themes side by
 * side). Sets data-theme on a wrapper, so token variables switch for that subtree, and tells charts which theme to use.
 */
export function ThemeScope({ theme, className, style, children }: { theme: ResolvedTheme; className?: string; style?: CSSProperties; children: ReactNode }) {
  const parent = useContext(ThemeContext);
  const value = useMemo(() => ({ ...parent, resolved: theme }), [parent, theme]);
  return (
    <ThemeContext.Provider value={value}>
      <div data-theme={theme} className={className} style={style}>
        {children}
      </div>
    </ThemeContext.Provider>
  );
}
