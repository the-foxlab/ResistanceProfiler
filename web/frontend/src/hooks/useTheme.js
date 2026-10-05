import { useCallback, useEffect, useState } from 'react';

/**
 * Theme state for the webapp: 'light' | 'dark' | 'system'.
 *
 * - 'system' follows prefers-color-scheme live (the default, so the app
 *   adapts automatically to the user's OS preference).
 * - 'light'/'dark' are explicit overrides persisted in localStorage.
 *
 * The resolved theme is applied as `data-theme="light"|"dark"` on
 * <html>. The CSS defines dark tokens under
 * `:root[data-theme="dark"]` and mirrors the system preference under
 * `@media (prefers-color-scheme: dark)` for :root[data-theme="system"]
 * (see styles.css). Setting the attribute in JS (rather than relying on
 * the media query alone) keeps the resolved state queryable for Plotly
 * re-theming and lets the toggle show the current mode unambiguously.
 *
 * A blocking script in index.html applies the same resolution before the
 * first paint to avoid a light flash on dark systems; this hook only
 * keeps React state in sync with it afterwards.
 */

const STORAGE_KEY = 'respro-theme';
export const THEME_STORAGE_KEY = STORAGE_KEY;

function readStoredTheme() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' || value === 'system' ? value : 'system';
  } catch {
    // localStorage can throw (private mode, disabled storage); fall back to system.
    return 'system';
  }
}

function resolveSystemTheme() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(preference) {
  const resolved = preference === 'system' ? resolveSystemTheme() : preference;
  document.documentElement.setAttribute('data-theme', resolved);
  return resolved;
}

export function useTheme() {
  const [preference, setPreference] = useState(readStoredTheme);
  // Track the resolved theme ('light'|'dark') so consumers (Plotly colors,
  // toggle label) re-render when the system preference flips while in
  // 'system' mode.
  const [resolved, setResolved] = useState(() => applyTheme(readStoredTheme()));

  useEffect(() => {
    applyTheme(preference);
    setResolved(preference === 'system' ? resolveSystemTheme() : preference);
    try {
      if (preference === 'system') {
        localStorage.removeItem(STORAGE_KEY);
      } else {
        localStorage.setItem(STORAGE_KEY, preference);
      }
    } catch {
      // Ignore persistence failures; the session-local choice still applies.
    }
  }, [preference]);

  // Live-follow the OS preference while in 'system' mode.
  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => {
      if (readStoredTheme() === 'system') {
        setResolved(applyTheme('system'));
      }
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const setTheme = useCallback((next) => {
    setPreference(next);
  }, []);

  return { preference, resolved, setTheme };
}
