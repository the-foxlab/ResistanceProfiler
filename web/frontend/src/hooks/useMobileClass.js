import { useEffect } from 'react';

// Mobile detection hook: toggles a `is-mobile` class on <body> whenever the
// viewport crosses --mobile-breakpoint (820px). Purely an additive opt-in
// signal for any JS that needs to know the layout mode; the responsive CSS
// itself is driven by @media queries and does NOT depend on this class.
export function useMobileClass() {
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined;
    const breakpoint =
      getComputedStyle(document.documentElement)
        .getPropertyValue('--mobile-breakpoint')
        .trim() || '820px';
    const mq = window.matchMedia(`(max-width: ${breakpoint})`);
    const apply = (e) => {
      document.body.classList.toggle('is-mobile', e.matches);
    };
    apply(mq);
    // MediaQueryList extends EventTarget, so addEventListener/removeEventListener
    // are the standard (and non-deprecated) API. The legacy addListener fallback
    // for Safari < 14 is intentionally omitted — that browser is no longer
    // supported by React 18 or any current evergreen browser.
    mq.addEventListener('change', apply);
    return () => mq.removeEventListener('change', apply);
  }, []);
}
