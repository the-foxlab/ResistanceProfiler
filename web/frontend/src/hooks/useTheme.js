import { createContext, useCallback, useEffect, useRef, useState } from 'react';

/**
 * Theme preference ('light' | 'dark' | 'system') synced to `<html data-theme="...">`.
 * A blocking script in index.html sets the initial attribute before first paint.
 */
const STORAGE_KEY = 'respro-theme';
export const ThemeContext = createContext('light');

function normalizePreference(value) {
  return value === 'light' || value === 'dark' || value === 'system' ? value : 'system';
}

function readStoredTheme() {
  try {
    return normalizePreference(localStorage.getItem(STORAGE_KEY));
  } catch {
    return 'system';
  }
}

function resolveTheme(preference) {
  if (preference !== 'system') return preference;
  return window.matchMedia?.('(prefers-color-scheme: dark)')?.matches ? 'dark' : 'light';
}

function applyTheme(preference) {
  const resolved = resolveTheme(preference);
  document.documentElement.setAttribute('data-theme', resolved);
  return resolved;
}

export function useTheme() {
  const [preference, setPreference] = useState(readStoredTheme);
  const [resolved, setResolved] = useState(() => resolveTheme(preference));
  const preferenceRef = useRef(preference);

  useEffect(() => {
    preferenceRef.current = preference;
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
    if (typeof window.matchMedia !== 'function') return undefined;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    if (!mq || typeof mq.addEventListener !== 'function') return undefined;
    const onChange = () => {
      if (preferenceRef.current === 'system') {
        setResolved(applyTheme('system'));
      }
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const setTheme = useCallback((next) => {
    const normalized = normalizePreference(next);
    preferenceRef.current = normalized;
    setPreference(normalized);
  }, []);
  return { preference, resolved, setTheme };
}
