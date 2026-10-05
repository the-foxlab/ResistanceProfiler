// Shared color tokens so all chart components use the same phenotype semantics.
// Ranks 1-5 mirror the severity ladder in `respro/db/phenotype_ranks.py`
// (1 susceptible → 5 resistant). Sentinels: unknown (grey), contradictory (slate).
export const CLASSIFICATION_COLORS = {
  count: '#0f766e',
  rank1: '#27ae60',
  rank2: '#f1c40f',
  rank3: '#f39c12',
  rank4: '#e67e22',
  rank5: '#e74c3c',
  contradictory: '#64748b',
  unknown: '#c3ccd6',
};

// Default palette for pie slices; order matters for stable legend color mapping.
// Desaturated, colour-blind friendly tones anchored on the brand teal.
export const PIE_COLORS = ['#0f766e', '#4a6fa5', '#b0714f', '#7c5cbf', '#b8912a', '#3d7d8c', '#71717a'];

// Human-readable legend labels for stacked bars and summary plots.
export const CLASSIFICATION_LABELS = {
  count: 'Mutations',
  rank1: 'Susceptible',
  rank2: 'Potential low-level resistance',
  rank3: 'Low-level resistance',
  rank4: 'Intermediate',
  rank5: 'Resistant',
  contradictory: 'Contradictory',
  unknown: 'Unknown',
};

export function chartLabelStyle() {
  // Small axis label helper keeps font settings consistent across charts.
  return {
    fontSize: 12,
    fill: '#4c6072',
  };
}

// Plotly base typography — Geist, matching the app shell (fonts.css).
const PLOTLY_FONT_FAMILY = "'Geist', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif";

// Neutral ink tones for chart text, mirroring the CSS token palette
// (--ink/--muted-strong/--muted) so charts and shell read as one surface.
const INK = '#18181b';
const INK_SOFT = '#52525b';
const INK_MUTED = '#71717a';

// Hairline grid: matches --line (#e4e4e7) but slightly darker so it stays
// visible against the white plot background at 1px.
const GRID = '#e8e8ea';

/**
 * Shared Plotly layout baseline. Every chart spreads this first and overrides
 * per-chart specifics on top, so typography, grid and background styling stay
 * consistent across the dashboard without duplicating values.
 */
export function baseLayout() {
  return {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: {
      family: PLOTLY_FONT_FAMILY,
      size: 12,
      color: INK_SOFT,
    },
    // Default axis treatment: hairline dotted grid on both axes; components
    // override showgrid/gridcolor per axis where a cleaner look is wanted.
    xaxis: { gridcolor: GRID, gridwidth: 1, griddash: 'dot', zeroline: false },
    yaxis: { gridcolor: GRID, gridwidth: 1, griddash: 'dot', zeroline: false },
    hoverlabel: {
      font: { family: PLOTLY_FONT_FAMILY, size: 12, color: INK },
      bgcolor: '#ffffff',
      bordercolor: '#d4d4d8',
    },
  };
}

/**
 * Standard axis title font, replacing the previously duplicated inline
 * `{ size: 12, color: '#4c6072' }` objects in each chart component.
 */
export function axisTitleFont() {
  return { size: 12, color: INK_MUTED };
}