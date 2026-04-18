export const CLASSIFICATION_COLORS = {
  count: '#0f766e',
  resistant: '#c2410c',
  intermediate: '#b7791f',
  susceptible: '#6b7280',
  unknown: '#c3ccd6',
};

export const PIE_COLORS = ['#0f766e', '#1d4ed8', '#c2410c', '#7c3aed', '#ca8a04', '#0f4c5c', '#64748b'];

export const CLASSIFICATION_LABELS = {
  count: 'Mutations',
  resistant: 'Resistant',
  intermediate: 'Intermediate',
  susceptible: 'Susceptible',
  unknown: 'Unknown',
};

export function chartLabelStyle() {
  return {
    fontSize: 12,
    fill: '#4c6072',
  };
}