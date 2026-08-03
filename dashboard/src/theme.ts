import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

/** Shared with the pre-paint script in index.html. Both must agree or dark flashes light. */
const KEY = "nexusgtm-theme";

/**
 * The console's theme, as a stored choice rather than an inherited one.
 *
 * Light is the default for every reader on every machine -- the OS `prefers-color-scheme`
 * signal is deliberately not consulted, so a dark-desktop reader still opens on the light
 * console and gets dark only by asking. That is the opposite of `usePrefersReducedMotion`
 * in components.tsx, which tracks a live media query; this is state the reader owns, so it
 * is plain useState over localStorage and not useSyncExternalStore.
 *
 * The attribute on <html> is what the stylesheet reads. index.html sets it before first
 * paint from the same key; this effect keeps it true for the rest of the session.
 */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(read);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    // Storage is a nicety, not a dependency -- Safari's hardened privacy mode throws on
    // read and write alike, and the console must still render and still toggle.
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* not persisted; the session still themes correctly */
    }
  }, [theme]);

  const toggle = useCallback(
    () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    [],
  );

  return [theme, toggle];
}

/** Anything that is not exactly "dark" -- absent, corrupt, unreadable -- means light. */
function read(): Theme {
  try {
    return localStorage.getItem(KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}
