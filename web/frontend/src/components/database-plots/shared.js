// Shared color tokens so all chart components use the same phenotype semantics.
export const CLASSIFICATION_COLORS = {
  count: '#0f766e',
  resistant: '#c2410c',
  intermediate: '#b7791f',
  susceptible: '#6b7280',
  unknown: '#c3ccd6',
};

// Default palette for pie slices; order matters for stable legend color mapping.
export const PIE_COLORS = ['#27978e', '#5c79ca', '#be6e4e', '#8d5ddf', '#d1a034', '#467a87', '#64748b'];

// Human-readable legend labels for stacked bars and summary plots.
export const CLASSIFICATION_LABELS = {
  count: 'Mutations',
  resistant: 'Resistant',
  intermediate: 'Intermediate',
  susceptible: 'Susceptible',
  unknown: 'Unknown',
};

export function chartLabelStyle() {
  // Small axis label helper keeps font settings consistent across charts.
  return {
    fontSize: 12,
    fill: '#4c6072',
  };
}