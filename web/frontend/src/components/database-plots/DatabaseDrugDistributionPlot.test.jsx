import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';

import Plotly from 'plotly.js-dist-min';
import { DatabaseDrugDistributionPlot } from './DatabaseDrugDistributionPlot';

vi.mock('plotly.js-dist-min', () => ({
  default: {
    react: vi.fn(),
    purge: vi.fn(),
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
});
