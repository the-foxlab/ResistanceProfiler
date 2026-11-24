import { createContext, useCallback, useEffect, useState } from 'react';

/**
 * Theme preference ('light' | 'dark' | 'system') synced to `<html data-theme="...">`.
 * A blocking script in index.html sets the initial attribute before first paint.
 */
const STORAGE_KEY = 'respro-theme';
export const ThemeContext = createContext('light');

function readStoredTheme() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' || value === 'system' ? value : 'system';
  } catch {
    return 'system';
  }
}

function resolveTheme(preference) {
  if (preference !== 'system') return preference;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(preference) {
  const resolved = resolveTheme(preference);
  document.documentElement.setAttribute('data-theme', resolved);
  return resolved;
}

export function useTheme() {
  const [preference, setPreference] = useState(readStoredTheme);
  const [resolved, setResolved] = useState(() => resolveTheme(preference));

  useEffect(() => {
    setResolved(applyTheme(preference));
    try {
      if (preference === 'system') {
        localStorage.removeItem(STORAGE_KEY);
      } else {
        localStorage.setItem(STORAGE_KEY, preference);
      }
    } catch {
      // Ignore storage failures; session-local choice still applies.
    }
  }, [preference]);

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

  const setTheme = useCallback((next) => setPreference(next), []);
  return { preference, resolved, setTheme };
}
