import { useContext } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { ThemeContext, useTheme } from './useTheme';

function createMatchMediaStub(initialMatches = false) {
  let matches = initialMatches;
  const listeners = new Set();
  const mq = {
    media: '(prefers-color-scheme: dark)',
    get matches() {
      return matches;
    },
    addEventListener: vi.fn((event, handler) => {
      if (event === 'change') listeners.add(handler);
    }),
    removeEventListener: vi.fn((event, handler) => {
      if (event === 'change') listeners.delete(handler);
    }),
    dispatchChange(nextMatches) {
      matches = nextMatches;
      for (const handler of [...listeners]) {
        handler({ matches: nextMatches, media: mq.media });
      }
    },
  };
  return mq;
}

describe('useTheme', () => {
  const origMatchMedia = window.matchMedia;

  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.matchMedia = origMatchMedia;
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  it('reads stored preferences, falls back to system on invalid values, and syncs data-theme', () => {
    const mq = createMatchMediaStub(true);
    window.matchMedia = vi.fn(() => mq);

    localStorage.setItem('respro-theme', 'dark');
    const darkHook = renderHook(() => useTheme());
    expect(darkHook.result.current.preference).toBe('dark');
    expect(darkHook.result.current.resolved).toBe('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    darkHook.unmount();

    localStorage.setItem('respro-theme', 'light');
    const lightHook = renderHook(() => useTheme());
    expect(lightHook.result.current.preference).toBe('light');
    expect(lightHook.result.current.resolved).toBe('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    lightHook.unmount();

    localStorage.setItem('respro-theme', 'sepia');
    const invalidHook = renderHook(() => useTheme());
    expect(invalidHook.result.current.preference).toBe('system');
    expect(invalidHook.result.current.resolved).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    invalidHook.unmount();

    localStorage.removeItem('respro-theme');
    mq.dispatchChange(false);
    const missingHook = renderHook(() => useTheme());
    expect(missingHook.result.current.preference).toBe('system');
    expect(missingHook.result.current.resolved).toBe('light');
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('handles localStorage.getItem SecurityError cleanly and resolves from matchMedia', () => {
    const mq = createMatchMediaStub(true);
    window.matchMedia = vi.fn(() => mq);
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Blocked', 'SecurityError');
    });

    const { result } = renderHook(() => useTheme());
    expect(result.current.preference).toBe('system');
    expect(result.current.resolved).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('persists light/dark choices and removes storage key on system', () => {
    const mq = createMatchMediaStub(false);
    window.matchMedia = vi.fn(() => mq);
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem');
    const removeItemSpy = vi.spyOn(Storage.prototype, 'removeItem');

    const { result } = renderHook(() => useTheme());

    act(() => {
      result.current.setTheme('dark');
    });
    expect(result.current.preference).toBe('dark');
    expect(result.current.resolved).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(setItemSpy).toHaveBeenCalledWith('respro-theme', 'dark');

    act(() => {
      result.current.setTheme('light');
    });
    expect(result.current.preference).toBe('light');
    expect(result.current.resolved).toBe('light');
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(setItemSpy).toHaveBeenCalledWith('respro-theme', 'light');

    act(() => {
      result.current.setTheme('system');
    });
    expect(result.current.preference).toBe('system');
    expect(result.current.resolved).toBe('light');
    expect(removeItemSpy).toHaveBeenCalledWith('respro-theme');
  });

  it('does not allow OS matchMedia changes to override an explicit session-only choice when localStorage throws', () => {
    const mq = createMatchMediaStub(false);
    window.matchMedia = vi.fn(() => mq);
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage blocked');
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('storage blocked');
    });

    const { result } = renderHook(() => useTheme());
    expect(result.current.preference).toBe('system');
    expect(result.current.resolved).toBe('light');

    // Explicit dark override despite storage errors
    act(() => {
      result.current.setTheme('dark');
    });
    expect(result.current.resolved).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');

    // OS matchMedia changes must NOT overwrite explicit dark choice
    act(() => {
      mq.dispatchChange(false);
    });
    expect(result.current.resolved).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');

    // Explicit light override despite OS dark preference
    act(() => {
      result.current.setTheme('light');
    });
    act(() => {
      mq.dispatchChange(true);
    });
    expect(result.current.resolved).toBe('light');
    expect(document.documentElement.dataset.theme).toBe('light');

    // Switching back to system resumes tracking OS matchMedia changes
    act(() => {
      result.current.setTheme('system');
    });
    expect(result.current.resolved).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');

    act(() => {
      mq.dispatchChange(false);
    });
    expect(result.current.resolved).toBe('light');
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('removes the exact matchMedia listener on unmount and handles missing matchMedia safely', () => {
    const mq = createMatchMediaStub(false);
    window.matchMedia = vi.fn(() => mq);

    const { unmount } = renderHook(() => useTheme());
    expect(mq.addEventListener).toHaveBeenCalledTimes(1);
    const registeredHandler = mq.addEventListener.mock.calls[0][1];

    unmount();
    expect(mq.removeEventListener).toHaveBeenCalledWith('change', registeredHandler);

    window.matchMedia = undefined;
    const missingHook = renderHook(() => useTheme());
    expect(missingHook.result.current.resolved).toBe('light');
    missingHook.unmount();

    window.matchMedia = vi.fn(() => undefined);
    const undefinedMqHook = renderHook(() => useTheme());
    expect(undefinedMqHook.result.current.resolved).toBe('light');
    undefinedMqHook.unmount();

    // matchMedia object lacking addEventListener does not throw on mount/unmount.
    window.matchMedia = vi.fn(() => ({ matches: true, media: '(prefers-color-scheme: dark)' }));
    const legacyMqHook = renderHook(() => useTheme());
    expect(legacyMqHook.result.current.resolved).toBe('dark');
    expect(() => legacyMqHook.unmount()).not.toThrow();

    // setTheme('unknown') normalizes to 'system' and ThemeContext defaults to 'light'.
    act(() => {
      legacyMqHook.result.current.setTheme('unknown');
    });
    expect(legacyMqHook.result.current.preference).toBe('system');
    const ctxHook = renderHook(() => useContext(ThemeContext));
    expect(ctxHook.result.current).toBe('light');
  });
});
