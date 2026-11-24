import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import Plotly from 'plotly.js-dist-min';

import { DatabasePositionPlot } from './DatabasePositionPlot';
import { ThemeContext } from '../../hooks/useTheme';

vi.mock('plotly.js-dist-min', () => ({
  default: {
    react: vi.fn(),
    purge: vi.fn(),
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
    expect(Plotly.purge).toHaveBeenCalled();
    expect(Plotly.react).toHaveBeenCalledTimes(2);
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
});
