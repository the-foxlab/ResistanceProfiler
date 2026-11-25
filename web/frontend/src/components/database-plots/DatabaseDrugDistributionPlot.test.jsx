import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import Plotly from 'plotly.js-dist-min';
import { ThemeContext } from '../../hooks/useTheme';
import { DatabaseDrugDistributionPlot } from './DatabaseDrugDistributionPlot';

vi.mock('plotly.js-dist-min', () => ({
  default: {
    react: vi.fn(),
    purge: vi.fn(),
    Plots: { resize: vi.fn() },
  },
}));

const plot = {
  kind: 'ic50-distribution',
  title: 'Virus',
  subtitle: 'IC50 measurements',
  footer: 'Each point is one rule-level measurement',
  xAxisLabel: 'IC₅₀ (µM, log scale)',
  xDomain: [0, 6],
  xTicks: [0, 4, 5, 6],
  majorTicks: [0, 4, 5, 6],
  yDomain: [0.5, 1.5],
  yTicks: [1],
  laneLabels: { 1: 'Drug A' },
  points: [{ x: 6, y: 1, color: '#0f766e', drug: 'Drug A', value: 1_000_000 }],
};

describe('DatabaseDrugDistributionPlot', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('keeps large IC50 tick labels compact and separates the x-axis title', () => {
    render(<DatabaseDrugDistributionPlot plot={plot} />);

    const layout = Plotly.react.mock.calls.at(-1)[2];
    expect(layout.xaxis.ticktext).toEqual(['1', '1e4', '1e5', '1e6']);
    expect(layout.xaxis.automargin).toBe(true);
    expect(layout.xaxis.title.standoff).toBeGreaterThan(0);
    expect(layout.margin.b).toBeGreaterThan(40);
  });

  it('uses an automatic y-axis margin instead of reserving a wide fixed left gutter', () => {
    render(<DatabaseDrugDistributionPlot plot={plot} />);

    const layout = Plotly.react.mock.calls.at(-1)[2];
    expect(layout.margin.l).toBeLessThan(110);
    expect(layout.yaxis.automargin).toBe(true);
  });

  it('recolors IC50 markers and baseLayout/axisTitleFont when ThemeContext changes', () => {
    const multiLanePlot = {
      ...plot,
      points: [
        { x: 1, y: 1, lane: 1, color: '#0f766e', drug: 'Drug A', value: 10 },
        { x: 2, y: 2, color: '#2563eb', drug: 'Drug B', value: 100 },
      ],
    };

    const { rerender } = render(
      <ThemeContext.Provider value="light">
        <DatabaseDrugDistributionPlot plot={multiLanePlot} />
      </ThemeContext.Provider>,
    );

    const lightCall = Plotly.react.mock.calls.at(-1);
    expect(lightCall[1][0].marker.color).toEqual(['#0f766e', '#2563eb']);
    expect(lightCall[2].xaxis.title.font.color).toBe('#71717a');

    rerender(
      <ThemeContext.Provider value="dark">
        <DatabaseDrugDistributionPlot plot={multiLanePlot} />
      </ThemeContext.Provider>,
    );

    const darkCall = Plotly.react.mock.calls.at(-1);
    expect(darkCall[1][0].marker.color).toEqual(['#5eead4', '#93c5fd']);
    expect(darkCall[2].font.color).toBe('#d4d4d8');
    expect(darkCall[2].xaxis.title.font.color).toBe('#d4d4d8');
    expect(darkCall[2].hoverlabel.bgcolor).toBe('#27272a');
  });

  it('renders score-counts plots with theme-aware bar colors and var(--color-error) on error', () => {
    const scorePlot = {
      kind: 'score-counts',
      title: 'Drug A',
      subtitle: 'Virus - 3 rule(s) grouped by score',
      footer: 'Rule counts grouped by score value',
      xAxisLabel: 'Score',
      yAxisLabel: 'Rule count',
      bars: [
        { scoreLabel: '10', count: 1, color: '#0f766e' },
        { scoreLabel: '30', count: 2, color: '#0f766e' },
      ],
    };

    const { rerender } = render(
      <ThemeContext.Provider value="light">
        <DatabaseDrugDistributionPlot plot={scorePlot} />
      </ThemeContext.Provider>,
    );

    const lightCall = Plotly.react.mock.calls.at(-1);
    expect(lightCall[1][0].marker.color).toEqual(['#0f766e', '#0f766e']);

    rerender(
      <ThemeContext.Provider value="dark">
        <DatabaseDrugDistributionPlot plot={scorePlot} />
      </ThemeContext.Provider>,
    );

    const darkCall = Plotly.react.mock.calls.at(-1);
    expect(darkCall[1][0].marker.color).toEqual(['#2dd4bf', '#2dd4bf']);
    expect(darkCall[2].xaxis.title.font.color).toBe('#d4d4d8');
    expect(darkCall[2].yaxis.title.font.color).toBe('#d4d4d8');

    cleanup();
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    Plotly.react.mockImplementationOnce(() => {
      throw new Error('dist boom');
    });
    render(
      <ThemeContext.Provider value="dark">
        <DatabaseDrugDistributionPlot plot={scorePlot} />
      </ThemeContext.Provider>,
    );
    expect(screen.getByText(/Chart error: dist boom/)).toHaveStyle({ color: 'var(--color-error)' });
    errSpy.mockRestore();
  });
});
