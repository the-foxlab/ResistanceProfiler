import Plotly from 'plotly.js-dist-min';
import { useContext, useEffect, useRef, useState } from 'react';

import { ThemeContext } from '../../hooks/useTheme';

export const STATIC_PLOT_CONFIG = {
  responsive: true,
  displayModeBar: false,
  scrollZoom: false,
};

/**
 * Manage a Plotly chart container's lifecycle, error capture, theme subscription,
 * and debounced ResizeObserver handling.
 */
export function usePlotlyChart({ render, enabled = true, deps = [], resizeMode = 'react' }) {
  const containerRef = useRef(null);
  const activeElRef = useRef(null);
  const isMountedRef = useRef(true);
  const renderSeqRef = useRef(0);
  const renderRef = useRef(render);
  renderRef.current = render;
  const pendingRenderAfterErrorRef = useRef(false);
  const [plotError, setPlotError] = useState('');
  const theme = useContext(ThemeContext);

  const reportError = (err, seq = renderSeqRef.current) => {
    console.error(err);
    if (isMountedRef.current && seq === renderSeqRef.current) {
      setPlotError(String(err?.message || err));
    }
  };

  const runRender = (el) => {
    const seq = ++renderSeqRef.current;
    if (activeElRef.current && activeElRef.current !== el) {
      Plotly.purge(activeElRef.current);
      activeElRef.current = null;
    }
    try {
      const result = renderRef.current(el, theme);
      activeElRef.current = el;
      if (result && typeof result.then === 'function') {
        Promise.resolve(result).catch((err) => reportError(err, seq));
      }
    } catch (err) {
      reportError(err, seq);
    }
  };

  // Declared before render effects so StrictMode remounts restore isMountedRef before runRender executes.
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      renderSeqRef.current += 1;
      if (activeElRef.current) {
        Plotly.purge(activeElRef.current);
        activeElRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    setPlotError('');
    const el = containerRef.current;
    if (!el || !enabled) {
      renderSeqRef.current += 1;
      if (activeElRef.current) {
        Plotly.purge(activeElRef.current);
        activeElRef.current = null;
      }
      pendingRenderAfterErrorRef.current = Boolean(enabled && !el);
      return;
    }

    pendingRenderAfterErrorRef.current = false;
    runRender(el);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, theme, ...deps]);

  useEffect(() => {
    if (!pendingRenderAfterErrorRef.current || plotError || !enabled) {
      return;
    }
    const el = containerRef.current;
    if (!el) {
      return;
    }
    pendingRenderAfterErrorRef.current = false;
    runRender(el);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plotError, enabled, theme]);

  useEffect(() => {
    const targetEl = containerRef.current;
    if (!enabled || plotError || !targetEl || typeof ResizeObserver === 'undefined') {
      return undefined;
    }

    let timeoutId = null;
    let lastWidth = targetEl.clientWidth;
    let lastHeight = targetEl.clientHeight;

    const observer = new ResizeObserver((entries) => {
      const entry = entries?.[entries.length - 1];
      const rect = entry?.contentRect;
      if (rect && (rect.width <= 0 || rect.height <= 0)) {
        return;
      }
      const width = rect ? rect.width : targetEl.clientWidth;
      const height = rect ? rect.height : targetEl.clientHeight;

      if (width > 0 && height > 0 && width === lastWidth && height === lastHeight) {
        return;
      }
      if (width > 0 && height > 0) {
        lastWidth = width;
        lastHeight = height;
      }

      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        const el = containerRef.current;
        if (!el) {
          return;
        }
        const seq = ++renderSeqRef.current;
        try {
          let result;
          if (resizeMode === 'rerender') {
            result = renderRef.current(el, theme);
          } else if (typeof Plotly.Plots?.resize === 'function') {
            result = Plotly.Plots.resize(el);
          }
          if (result && typeof result.then === 'function') {
            Promise.resolve(result).catch((err) => reportError(err, seq));
          }
        } catch (err) {
          reportError(err, seq);
        }
      }, 300);
    });

    observer.observe(targetEl);
    return () => {
      observer.disconnect();
      clearTimeout(timeoutId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, theme, plotError, resizeMode, ...deps]);

  return { containerRef, plotError, theme };
}
