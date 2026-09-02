import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { useMobileClass } from '../hooks/useMobileClass';

// Wrapper that exercises the hook; render it in each test.
function Probe() {
  useMobileClass();
  return null;
}

describe('useMobileClass', () => {
  afterEach(() => {
    cleanup();
    document.body.classList.remove('is-mobile');
  });

  it('adds is-mobile to <body> when viewport is <= mobile breakpoint', () => {
    // jsdom does not implement layout, so matchMedia always returns the
    // matches value we configure here.
    window.matchMedia = (query) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    });
    render(<Probe />);
    expect(document.body.classList.contains('is-mobile')).toBe(true);
  });

  it('does NOT add is-mobile when viewport is above the breakpoint', () => {
    window.matchMedia = (query) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    });
    render(<Probe />);
    expect(document.body.classList.contains('is-mobile')).toBe(false);
  });
});
