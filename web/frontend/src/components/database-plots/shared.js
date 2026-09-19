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
export const PIE_COLORS = ['#27978e', '#5c79ca', '#be6e4e', '#8d5ddf', '#d1a034', '#467a87', '#64748b'];

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