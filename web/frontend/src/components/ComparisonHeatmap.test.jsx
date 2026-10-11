import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';

import Plotly from 'plotly.js-dist-min';
import { ComparisonHeatmap } from './ComparisonHeatmap';
import { ThemeContext } from '../hooks/useTheme';

vi.mock('plotly.js-dist-min', () => ({
  default: {
    react: vi.fn(),
    purge: vi.fn(),
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

describe('comparison heatmap axis colors', () => {
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
});
