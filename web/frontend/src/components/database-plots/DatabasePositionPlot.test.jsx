import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import Plotly from 'plotly.js-dist-min';

import { DatabasePositionPlot } from './DatabasePositionPlot';
import { ThemeContext } from '../../hooks/useTheme';

vi.mock('plotly.js-dist-min', () => ({
  default: {
    react: vi.fn(),
    purge: vi.fn(),
    Plots: { resize: vi.fn() },
  },
}));

const samplePlot = {
  kind: 'positions',
  key: 'ref1::UL23',
  title: 'UL23',
  subtitle: 'AA length 30 · 2 mutation(s) · 10-aa bins',
  footer: '2 phenotype annotation(s)',
  xAxisLabel: 'Amino-acid bin end (10 aa)',
  tones: ['rank1', 'rank5'],
  toneLabels: { rank1: 'sensitive', rank5: 'resistant' },
  positions: [
    { label: '10', rangeStart: 1, rangeEnd: 10, rank1: 1, rank5: 0 },
    { label: '20', rangeStart: 11, rangeEnd: 20, rank1: 0, rank5: 1 },
  ],
};

describe('DatabasePositionPlot', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders stacked bar traces and legend labels from toneLabels', () => {
    const { rerender } = render(
      <ThemeContext.Provider value="light">
        <DatabasePositionPlot plot={samplePlot} />
      </ThemeContext.Provider>,
    );

    expect(screen.getByText('UL23')).toBeInTheDocument();
    expect(screen.getByText('sensitive')).toBeInTheDocument();
    expect(screen.getByText('resistant')).toBeInTheDocument();

    const [, traces, layout] = Plotly.react.mock.calls.at(-1);
    expect(traces).toHaveLength(2);
    expect(traces[0].name).toBe('sensitive');
    expect(traces[0].y).toEqual([1, 0]);
    expect(traces[1].name).toBe('resistant');
    expect(traces[1].y).toEqual([0, 1]);
    expect(layout.barmode).toBe('stack');
    expect(layout.xaxis.dtick).toBe(1);

    rerender(
      <ThemeContext.Provider value="dark">
        <DatabasePositionPlot plot={samplePlot} />
      </ThemeContext.Provider>,
    );
    expect(Plotly.purge).not.toHaveBeenCalled();
    expect(Plotly.react).toHaveBeenCalledTimes(2);
  });

  it('diffs plot updates in-place with Plotly.react and purges the container element on unmount', () => {
    const { container, rerender, unmount } = render(
      <ThemeContext.Provider value="light">
        <DatabasePositionPlot plot={samplePlot} />
      </ThemeContext.Provider>,
    );
    const chartEl = Plotly.react.mock.calls[0][0];
    expect(chartEl).toBeInstanceOf(HTMLElement);
    expect(container.contains(chartEl)).toBe(true);

    const updatedPlot = {
      ...samplePlot,
      positions: [
        ...samplePlot.positions,
        { label: '30', rangeStart: 21, rangeEnd: 30, rank1: 2, rank5: 0 },
      ],
    };
    rerender(
      <ThemeContext.Provider value="light">
        <DatabasePositionPlot plot={updatedPlot} />
      </ThemeContext.Provider>,
    );

    expect(Plotly.purge).not.toHaveBeenCalled();
    expect(Plotly.react).toHaveBeenCalledTimes(2);

    unmount();
    expect(Plotly.purge).toHaveBeenCalledTimes(1);
    expect(Plotly.purge).toHaveBeenCalledWith(chartEl);
  });

  it('switches to nticks when there are more than 24 position bins', () => {
    const manyPositions = Array.from({ length: 25 }, (_, i) => ({
      label: String((i + 1) * 10),
      rangeStart: i * 10 + 1,
      rangeEnd: (i + 1) * 10,
      rank1: 1,
    }));
    render(<DatabasePositionPlot plot={{ ...samplePlot, tones: ['rank1'], positions: manyPositions }} />);

    const layout = Plotly.react.mock.calls.at(-1)[2];
    expect(layout.xaxis.nticks).toBe(25);
    expect(layout.xaxis.dtick).toBeUndefined();
  });

  it('applies dark-mode baseLayout, axisTitleFont, and classification colors via ThemeContext without DOM mutation', () => {
    document.documentElement.setAttribute('data-theme', 'light');
    const mixedPlot = {
      ...samplePlot,
      tones: ['rank1', 'contradictory', 'unknown'],
      toneLabels: { rank1: 'sensitive', contradictory: 'contradictory', unknown: 'other' },
      positions: [
        { label: '10', rangeStart: 1, rangeEnd: 10, rank1: 1, contradictory: 2, unknown: 1 },
      ],
    };

    const { container, rerender } = render(
      <ThemeContext.Provider value="light">
        <DatabasePositionPlot plot={mixedPlot} />
      </ThemeContext.Provider>,
    );
    const lightCall = Plotly.react.mock.calls.at(-1);
    expect(lightCall[1].map((t) => t.marker.color)).toEqual(['#27ae60', '#64748b', '#c3ccd6']);
    expect(lightCall[2].xaxis.title.font.color).toBe('#71717a');

    rerender(
      <ThemeContext.Provider value="dark">
        <DatabasePositionPlot plot={mixedPlot} />
      </ThemeContext.Provider>,
    );
    const darkCall = Plotly.react.mock.calls.at(-1);
    expect(darkCall[1].map((t) => t.marker.color)).toEqual(['#4ade80', '#94a3b8', '#cbd5e1']);
    expect(darkCall[2].font.color).toBe('#d4d4d8');
    expect(darkCall[2].xaxis.title.font.color).toBe('#d4d4d8');
    expect(darkCall[2].yaxis.title.font.color).toBe('#d4d4d8');
    expect(darkCall[2].hoverlabel.bgcolor).toBe('#27272a');

    const dots = container.querySelectorAll('.database-legend-dot');
    expect(dots[0]).toHaveStyle({ backgroundColor: '#4ade80' });
    expect(dots[1]).toHaveStyle({ backgroundColor: '#94a3b8' });
    expect(dots[2]).toHaveStyle({ backgroundColor: '#cbd5e1' });
    document.documentElement.removeAttribute('data-theme');
  });

  it('renders plotError using var(--color-error)', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    Plotly.react.mockImplementationOnce(() => {
      throw new Error('pos boom');
    });

    render(
      <ThemeContext.Provider value="dark">
        <DatabasePositionPlot plot={samplePlot} />
      </ThemeContext.Provider>,
    );

    const errEl = screen.getByText(/Chart error: pos boom/);
    expect(errEl).toHaveStyle({ color: 'var(--color-error)' });
    errSpy.mockRestore();
  });
});
