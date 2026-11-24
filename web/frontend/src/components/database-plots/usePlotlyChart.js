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
 * and debounced window-resize handling.
 */
export function usePlotlyChart({ render, enabled = true, deps = [], resizeMode = 'react' }) {
  const containerRef = useRef(null);
  const [plotError, setPlotError] = useState('');
  const theme = useContext(ThemeContext);

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }
    if (!enabled) {
      Plotly.purge(containerRef.current);
      return;
    }

    setPlotError('');
    try {
      render(containerRef.current, theme);
    } catch (err) {
      setPlotError(String(err.message || err));
    }

    return () => {
      if (containerRef.current) {
        Plotly.purge(containerRef.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, theme, ...deps]);

  useEffect(() => {
    if (!enabled || !containerRef.current) {
      return;
    }

    let timeoutId = null;
    const handleResize = () => {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        const el = containerRef.current;
        if (!el) {
          return;
        }
        try {
          if (resizeMode === 'rerender') {
            render(el, theme);
          } else {
            Plotly.react(el, el.data, el.layout, el._config);
          }
        } catch (_) {
          // ignore re-render errors on resize
        }
      }, 300);
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      clearTimeout(timeoutId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, theme, ...deps]);

  return { containerRef, plotError, theme };
}
