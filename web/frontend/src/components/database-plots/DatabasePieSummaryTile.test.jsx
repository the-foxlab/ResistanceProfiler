import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';

import Plotly from 'plotly.js-dist-min';
import { ThemeContext } from '../../hooks/useTheme';
import { baseLayout } from './shared';
import { DatabasePieSummaryRow } from './DatabasePieSummaryTile';

vi.mock('plotly.js-dist-min', () => ({
  default: {
    react: vi.fn(),
    purge: vi.fn(),
  },
}));

const tile = {
  pies: [{
    key: 'rules-per-drug',
    title: 'Mutations per Drug',
    total: 4,
    centerLabel: 'drugs',
    slices: [
      { label: 'Drug A', count: 3, color: '#000000' },
      { label: 'Drug B', count: 1, color: '#000000' },
    ],
  }],
};

describe('database plot theme styling', () => {
  beforeEach(() => {
    document.documentElement.setAttribute('data-theme', 'light');
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    document.documentElement.removeAttribute('data-theme');
  });

  it('sets legible axis tick colors for light and dark themes', () => {
    expect(baseLayout().xaxis.tickfont.color).toBe('#52525b');
    expect(baseLayout().yaxis.tickfont.color).toBe('#52525b');

    document.documentElement.setAttribute('data-theme', 'dark');
    expect(baseLayout().xaxis.tickfont.color).toBe('#d4d4d8');
    expect(baseLayout().yaxis.tickfont.color).toBe('#d4d4d8');
  });

  it('recolors donut slices and center labels when the theme changes', () => {
    const renderInTheme = (theme) => (
      <ThemeContext.Provider value={theme}>
        <DatabasePieSummaryRow tile={tile} />
      </ThemeContext.Provider>
    );
    const { rerender } = render(renderInTheme('light'));

    const lightCall = Plotly.react.mock.calls.at(-1);
    expect(lightCall[1][0].marker.colors).toEqual(['#0f766e', '#2563eb']);
    expect(lightCall[2].annotations.map((annotation) => annotation.font.color))
      .toEqual(['#18181b', '#71717a']);

    document.documentElement.setAttribute('data-theme', 'dark');
    rerender(renderInTheme('dark'));

    const darkCall = Plotly.react.mock.calls.at(-1);
    expect(darkCall[1][0].marker.colors).toEqual(['#5eead4', '#93c5fd']);
    expect(darkCall[1][0].marker.line.color).toBe('#1c1c1f');
    expect(darkCall[2].annotations.map((annotation) => annotation.font.color))
      .toEqual(['#f4f4f5', '#d4d4d8']);
  });
});
