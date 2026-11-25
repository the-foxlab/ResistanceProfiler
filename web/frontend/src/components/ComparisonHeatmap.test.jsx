import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import Plotly from 'plotly.js-dist-min';
import { ComparisonHeatmap } from './ComparisonHeatmap';
import { ThemeContext } from '../hooks/useTheme';

vi.mock('plotly.js-dist-min', () => ({
  default: {
    react: vi.fn(),
    purge: vi.fn(),
    Plots: { resize: vi.fn() },
  },
}));

const data = {
  samples: ['sample-1'],
  mutation_labels: ['mutation-1'],
  features: ['geneA'],
  feature_map: [0],
  db_hit_map: [true],
  consequences: ['missense_variant'],
  matrix: [[{ allele_freq: 0.5 }]],
};

describe('comparison heatmap', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    document.documentElement.removeAttribute('data-theme');
  });

  it('updates every axis text color when the theme changes', () => {
    const renderInTheme = (theme) => (
      <ThemeContext.Provider value={theme}>
        <ComparisonHeatmap data={data} isBusy={false} />
      </ThemeContext.Provider>
    );
    const { rerender } = render(renderInTheme('light'));

    const expectAxisColor = (color) => {
      const layout = Plotly.react.mock.calls.at(-1)[2];
      for (const axisName of ['xaxis', 'yaxis', 'yaxis2', 'yaxis3', 'yaxis4']) {
        expect(layout[axisName].tickfont.color).toBe(color);
      }
    };

    expectAxisColor('#18181b');
    rerender(renderInTheme('dark'));
    expectAxisColor('#f4f4f5');
  });

  it('recovers from plotError when valid data is provided subsequently', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      Plotly.react.mockImplementationOnce(() => {
        throw new Error('render boom');
      });

      const { rerender } = render(
        <ThemeContext.Provider value="light">
          <ComparisonHeatmap data={data} isBusy={false} />
        </ThemeContext.Provider>,
      );

      expect(screen.getByText(/Failed to render heatmap: render boom/)).toBeInTheDocument();
      expect(errSpy).toHaveBeenCalledTimes(1);

      const nextData = {
        ...data,
        samples: ['sample-2'],
      };
      rerender(
        <ThemeContext.Provider value="light">
          <ComparisonHeatmap data={nextData} isBusy={false} />
        </ThemeContext.Provider>,
      );

      expect(screen.queryByText(/Failed to render heatmap/)).toBeNull();
      expect(Plotly.react).toHaveBeenCalledTimes(2);
      const recoveredEl = Plotly.react.mock.calls[1][0];
      expect(recoveredEl).toBeInstanceOf(HTMLElement);
      expect(document.body.contains(recoveredEl)).toBe(true);
    } finally {
      errSpy.mockRestore();
    }
  });

  it('re-attaches the ResizeObserver after recovering from plotError', () => {
    vi.useFakeTimers();
    const originalResizeObserver = globalThis.ResizeObserver;
    const observers = [];
    class MockResizeObserver {
      constructor(callback) {
        this.callback = callback;
        observers.push(this);
      }

      observe() {}

      disconnect() {}

      trigger(entries) {
        this.callback(entries, this);
      }
    }
    vi.stubGlobal('ResizeObserver', MockResizeObserver);
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    try {
      Plotly.react.mockImplementationOnce(() => {
        throw new Error('render boom');
      });

      const { rerender } = render(
        <ThemeContext.Provider value="light">
          <ComparisonHeatmap data={data} isBusy={false} />
        </ThemeContext.Provider>,
      );

      expect(screen.getByText(/Failed to render heatmap: render boom/)).toBeInTheDocument();
      expect(Plotly.react).toHaveBeenCalledTimes(1);

      const nextData = {
        ...data,
        samples: ['sample-2'],
      };
      rerender(
        <ThemeContext.Provider value="light">
          <ComparisonHeatmap data={nextData} isBusy={false} />
        </ThemeContext.Provider>,
      );

      expect(screen.queryByText(/Failed to render heatmap/)).toBeNull();
      expect(Plotly.react).toHaveBeenCalledTimes(2);
      const recoveredEl = Plotly.react.mock.calls[1][0];
      expect(document.body.contains(recoveredEl)).toBe(true);

      // Redundant window resize events no longer trigger rerenders.
      window.dispatchEvent(new Event('resize'));
      vi.advanceTimersByTime(350);
      expect(Plotly.react).toHaveBeenCalledTimes(2);

      // ResizeObserver triggers rerender on recovered container.
      observers.at(-1).trigger([{ contentRect: { width: 800, height: 400 } }]);
      vi.advanceTimersByTime(350);

      expect(Plotly.react).toHaveBeenCalledTimes(3);
      expect(Plotly.react.mock.calls[2][0]).toBe(recoveredEl);
    } finally {
      errSpy.mockRestore();
      vi.useRealTimers();
      if (originalResizeObserver === undefined) {
        delete globalThis.ResizeObserver;
      } else {
        globalThis.ResizeObserver = originalResizeObserver;
      }
    }
  });

  it('purges when isBusy becomes true and re-renders when isBusy returns to false with the same data', () => {
    const { rerender } = render(
      <ThemeContext.Provider value="light">
        <ComparisonHeatmap data={data} isBusy={false} />
      </ThemeContext.Provider>,
    );
    const firstEl = Plotly.react.mock.calls[0][0];
    expect(Plotly.react).toHaveBeenCalledTimes(1);
    expect(Plotly.purge).not.toHaveBeenCalled();

    rerender(
      <ThemeContext.Provider value="light">
        <ComparisonHeatmap data={data} isBusy />
      </ThemeContext.Provider>,
    );
    expect(screen.getByText(/Loading comparison data/)).toBeInTheDocument();
    expect(Plotly.purge).toHaveBeenCalledTimes(1);
    expect(Plotly.purge).toHaveBeenCalledWith(firstEl);

    rerender(
      <ThemeContext.Provider value="light">
        <ComparisonHeatmap data={data} isBusy={false} />
      </ThemeContext.Provider>,
    );
    expect(Plotly.react).toHaveBeenCalledTimes(2);
    const secondEl = Plotly.react.mock.calls[1][0];
    expect(document.body.contains(secondEl)).toBe(true);
  });

  it('purges the rendered container element on unmount', () => {
    const { unmount } = render(
      <ThemeContext.Provider value="light">
        <ComparisonHeatmap data={data} isBusy={false} />
      </ThemeContext.Provider>,
    );
    const el = Plotly.react.mock.calls[0][0];
    expect(Plotly.purge).not.toHaveBeenCalled();

    unmount();
    expect(Plotly.purge).toHaveBeenCalledTimes(1);
    expect(Plotly.purge).toHaveBeenCalledWith(el);
  });

  it('renders a distinct unknown-AF trace and legend item only when unknown-AF cells exist', () => {
    const { rerender } = render(
      <ThemeContext.Provider value="light">
        <ComparisonHeatmap data={data} isBusy={false} />
      </ThemeContext.Provider>,
    );
    expect(screen.queryByText(/Detected \(unknown AF\)/)).toBeNull();
    const regularTraces = Plotly.react.mock.calls.at(-1)[1];
    expect(regularTraces.some((t) => String(t.hovertemplate || '').includes('Detected (unknown AF)'))).toBe(false);

    const unknownAfData = {
      ...data,
      matrix: [[{ allele_freq: null, coverage_gap: false }]],
    };
    rerender(
      <ThemeContext.Provider value="light">
        <ComparisonHeatmap data={unknownAfData} isBusy={false} />
      </ThemeContext.Provider>,
    );

    expect(screen.getByText(/Detected \(unknown AF\)/)).toBeInTheDocument();
    const unknownTraces = Plotly.react.mock.calls.at(-1)[1];
    const unknownTrace = unknownTraces.find((t) => String(t.hovertemplate || '').includes('Detected (unknown AF)'));
    expect(unknownTrace).toBeDefined();
    expect(unknownTrace.z).toEqual([[1]]);
    expect(unknownTrace.yaxis).toBe('y');
  });

  it('uses dark-mode dbHitTrace, stop_gained consequence, coverage-gap swatch, and baseLayout(theme)', () => {
    document.documentElement.setAttribute('data-theme', 'light');
    const themedData = {
      ...data,
      consequences: ['stop_gained'],
    };

    const { container, rerender } = render(
      <ThemeContext.Provider value="light">
        <ComparisonHeatmap data={themedData} isBusy={false} />
      </ThemeContext.Provider>,
    );

    const lightTraces = Plotly.react.mock.calls.at(-1)[1];
    const lightDbHitTrace = lightTraces.find((t) => t.yaxis === 'y3');
    const lightConseqTrace = lightTraces.find((t) => t.yaxis === 'y4');
    expect(lightDbHitTrace.colorscale).toEqual([[0, '#f4f4f5'], [1, '#dc2626']]);
    expect(lightConseqTrace.colorscale).toEqual([[0, '#18181b'], [1, '#18181b']]);
    expect(container.querySelector('.comparison-cell-swatch')).toHaveStyle({ backgroundColor: '#a1a1aa' });
    expect(container.querySelector('.comparison-consequence-swatch')).toHaveStyle({ backgroundColor: '#18181b' });

    // Switch ThemeContext to dark without mutating <html data-theme>.
    rerender(
      <ThemeContext.Provider value="dark">
        <ComparisonHeatmap data={themedData} isBusy={false} />
      </ThemeContext.Provider>,
    );

    const [, darkTraces, darkLayout] = Plotly.react.mock.calls.at(-1);
    const darkDbHitTrace = darkTraces.find((t) => t.yaxis === 'y3');
    const darkConseqTrace = darkTraces.find((t) => t.yaxis === 'y4');
    expect(darkDbHitTrace.colorscale).toEqual([[0, '#27272a'], [1, '#dc2626']]);
    expect(darkConseqTrace.colorscale).toEqual([[0, '#f4f4f5'], [1, '#f4f4f5']]);
    expect(darkLayout.font.color).toBe('#d4d4d8');
    expect(darkLayout.hoverlabel.bgcolor).toBe('#27272a');
    expect(container.querySelector('.comparison-cell-swatch')).toHaveStyle({ backgroundColor: '#71717a' });
    expect(container.querySelector('.comparison-consequence-swatch')).toHaveStyle({ backgroundColor: '#f4f4f5' });
  });

  it('returns null when data is null and purges when matrix is empty', () => {
    const { container, rerender } = render(
      <ThemeContext.Provider value="light">
        <ComparisonHeatmap data={null} isBusy={false} />
      </ThemeContext.Provider>,
    );
    expect(container.firstChild).toBeNull();
    expect(Plotly.react).not.toHaveBeenCalled();

    rerender(
      <ThemeContext.Provider value="light">
        <ComparisonHeatmap data={{ ...data, matrix: [] }} isBusy={false} />
      </ThemeContext.Provider>,
    );
    expect(Plotly.purge).toHaveBeenCalledTimes(1);
    expect(Plotly.react).not.toHaveBeenCalled();
  });
});
