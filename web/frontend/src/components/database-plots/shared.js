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

// Plotly base typography — Geist, matching the app shell (styles/typography.css).
const PLOTLY_FONT_FAMILY = "'Geist', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif";

// Neutral ink tones for chart text, mirroring the CSS token palette
// (--ink/--muted-strong/--muted) so charts and shell read as one surface.
// Light and dark values are kept in sync with the token blocks in
// styles/global.css (:root and :root[data-theme='dark']).
const INK = { light: '#18181b', dark: '#f4f4f5' };
const INK_SOFT = { light: '#52525b', dark: '#d4d4d8' };

// Axis titles need more contrast than generic muted text: in dark mode the
// muted tone (#a1a1aa) reads as darkish grey on the dark surface, so titles
// use the soft ink value instead. Light mode keeps the muted tone.
const AXIS_TITLE = { light: '#71717a', dark: '#d4d4d8' };

// Hairline grid: matches --line but slightly darker so it stays
// visible against the plot background at 1px.
const GRID = { light: '#e8e8ea', dark: '#2e2e33' };

/**
 * Read the currently resolved theme from <html data-theme>. The attribute is
 * set by hooks/useTheme.js (and the pre-paint script in index.html), so this
 * works before React mounts and stays live across theme switches.
 */
export function currentTheme() {
  if (typeof document === 'undefined') return 'light';
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

function pick(colors) {
  return colors[currentTheme()];
}

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
      color: pick(INK_SOFT),
    },
    // Default axis treatment: hairline dotted grid on both axes; components
    // override showgrid/gridcolor per axis where a cleaner look is wanted.
    xaxis: { gridcolor: pick(GRID), gridwidth: 1, griddash: 'dot', zeroline: false },
    yaxis: { gridcolor: pick(GRID), gridwidth: 1, griddash: 'dot', zeroline: false },
    hoverlabel: {
      font: { family: PLOTLY_FONT_FAMILY, size: 12, color: pick(INK) },
      bgcolor: currentTheme() === 'dark' ? '#27272a' : '#ffffff',
      bordercolor: currentTheme() === 'dark' ? '#3f3f46' : '#d4d4d8',
    },
  };
}

/**
 * Standard axis title font, replacing the previously duplicated inline
 * `{ size: 12, color: '#4c6072' }` objects in each chart component. Uses
 * AXIS_TITLE rather than INK_MUTED so titles stay readable in dark mode.
 */
export function axisTitleFont() {
  return { size: 12, color: pick(AXIS_TITLE) };
}