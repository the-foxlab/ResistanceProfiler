/**
 * Pure data preparation functions for the comparison heatmap.
 */

export const CONSEQUENCE_COLORS = {
  missense: '#d97706',
  synonymous: '#16a34a',
  stop_gained: '#18181b',
  stop_lost: '#6d28d9',
  start_lost: '#854d0e',
  frameshift: '#dc2626',
  insertion: '#0d9488',
  deletion: '#2563eb',
  splice_region: '#a1a1aa',
  unknown: '#d4d4d8',
};

export const DARK_CONSEQUENCE_COLORS = {
  missense: '#d97706',
  synonymous: '#16a34a',
  stop_gained: '#f4f4f5',
  stop_lost: '#a78bfa',
  start_lost: '#eab308',
  frameshift: '#dc2626',
  insertion: '#0d9488',
  deletion: '#60a5fa',
  splice_region: '#a1a1aa',
  unknown: '#64748b',
};

const FEATURE_BASE_COLORS = [
  '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
  '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac',
  '#86bcb6', '#8cd17d', '#b6992d', '#499894', '#eabe7a',
  '#d37295', '#a0cbe8', '#f1ce63', '#d4a6c8', '#9d7660',
];

export function consequenceColor(ctype, theme = 'light') {
  if (theme === 'dark') {
    return DARK_CONSEQUENCE_COLORS[ctype] || '#71717a';
  }
  return CONSEQUENCE_COLORS[ctype] || '#e4e4e7';
}

export function uniqueConsequenceTypes(consequences) {
  return [...new Set(consequences)].sort();
}

export function featureColorPalette(count) {
  if (count <= FEATURE_BASE_COLORS.length) {
    return FEATURE_BASE_COLORS.slice(0, count);
  }
  return Array.from({ length: count }, (_, i) => FEATURE_BASE_COLORS[i % FEATURE_BASE_COLORS.length]);
}

function uniqueXLabels(labels) {
  // Append zero-width spaces to duplicate labels so Plotly treats each column distinctly.
  const counts = {};
  for (const label of labels) {
    counts[label] = (counts[label] || 0) + 1;
  }
  const seen = {};
  return labels.map((label) => {
    seen[label] = (seen[label] || 0) + 1;
    return counts[label] > 1 ? `${label}${'\u200B'.repeat(seen[label] - 1)}` : label;
  });
}

function categoricalColorscale(colors) {
  if (colors.length === 1) {
    return [[0, colors[0]], [1, colors[0]]];
  }
  return colors.map((color, idx) => [idx / (colors.length - 1), color]);
}

export function classifyHeatmapCell(cell) {
  if (!cell) {
    return { gapZ: 1, mainZ: null, unknownAfZ: null, isUnknownAf: false };
  }
  if (typeof cell.coverage_gap === 'boolean') {
    if (cell.coverage_gap) {
      return { gapZ: 1, mainZ: null, unknownAfZ: null, isUnknownAf: false };
    }
    if (cell.allele_freq == null) {
      return { gapZ: null, mainZ: null, unknownAfZ: 1, isUnknownAf: true };
    }
    return { gapZ: null, mainZ: cell.allele_freq, unknownAfZ: null, isUnknownAf: false };
  }
  if (cell.allele_freq == null) {
    return { gapZ: 1, mainZ: null, unknownAfZ: null, isUnknownAf: false };
  }
  return { gapZ: null, mainZ: cell.allele_freq, unknownAfZ: null, isUnknownAf: false };
}

export function prepareHeatmapData(data, theme = 'light') {
  if (!data || !data.matrix || data.matrix.length === 0) {
    return null;
  }

  const {
    samples,
    mutation_labels,
    mutation_tick_labels,
    features,
    feature_map,
    feature_display_names,
    consequences,
    db_hit_map,
    matrix,
  } = data;

  const displayNames = feature_display_names || {};
  const hasFeatures = Boolean(features && features.length > 0);
  const hasDbHits = Boolean(db_hit_map && db_hit_map.length > 0);
  const hasConsequences = Boolean(consequences && consequences.length > 0);

  const xLabels = uniqueXLabels(mutation_tick_labels || mutation_labels);
  const maxSampleLen = Math.max(...samples.map((s) => s.length), 1);
  const leftMargin = Math.max(150, Math.min(300, maxSampleLen * 8));

  const classifiedMatrix = samples.map((_s, si) =>
    mutation_labels.map((_m, mi) => classifyHeatmapCell(matrix[si]?.[mi])),
  );
  const gapZ = classifiedMatrix.map((row) => row.map((cell) => cell.gapZ));
  const mainZ = classifiedMatrix.map((row) => row.map((cell) => cell.mainZ));
  const unknownAfZ = classifiedMatrix.map((row) => row.map((cell) => cell.unknownAfZ));
  const hasUnknownAf = classifiedMatrix.some((row) => row.some((cell) => cell.isUnknownAf));

  // Gap and main traces share identical [tickLabel, featureDisplayName] hover metadata.
  const cellCustomdata = samples.map(() =>
    mutation_labels.map((label, mi) => {
      const feature = features ? features[feature_map[mi]] : '';
      const featureDisplayName = displayNames[feature] || feature;
      const tickLabel = mutation_tick_labels ? mutation_tick_labels[mi] : label;
      return [tickLabel, featureDisplayName];
    }),
  );

  let featureColors = null;
  let featureZValues = null;
  let featureCustomdata = null;
  let featureColorscale = null;
  if (hasFeatures) {
    featureColors = featureColorPalette(Math.max(features.length, 1));
    featureZValues = [mutation_labels.map((_m, mi) => feature_map[mi])];
    featureCustomdata = [mutation_labels.map((label, mi) => {
      const feature = features[feature_map[mi]];
      const featureDisplayName = displayNames[feature] || feature;
      const tickLabel = mutation_tick_labels ? mutation_tick_labels[mi] : label;
      return [featureDisplayName, tickLabel];
    })];
    featureColorscale = categoricalColorscale(featureColors);
  }

  const dbHitZ = hasDbHits ? [db_hit_map.map((hit) => (hit ? 1 : 0))] : null;
  const dbHitCustomdata = hasDbHits
    ? [db_hit_map.map((hit) => [hit ? 'In database' : 'Not in database'])]
    : null;

  let consequenceTypes = null;
  let consequenceZValues = null;
  let consequenceColorscale = null;
  let consequenceCustomdata = null;
  if (hasConsequences) {
    consequenceTypes = uniqueConsequenceTypes(consequences);
    const consequenceIndex = Object.fromEntries(consequenceTypes.map((ctype, idx) => [ctype, idx]));
    consequenceZValues = [consequences.map((c) => consequenceIndex[c] || 0)];
    consequenceColorscale = categoricalColorscale(
      consequenceTypes.map((ctype) => consequenceColor(ctype, theme)),
    );
    consequenceCustomdata = [consequences.map((c) => [c])];
  }

  // Allocate vertical y-axis domains for the heatmap plus each active annotation strip.
  const annotationRows = (hasFeatures ? 1 : 0) + (hasDbHits ? 1 : 0) + (hasConsequences ? 1 : 0);
  const totalRows = samples.length + annotationRows;
  const gapBudget = annotationRows > 0 ? 0.04 : 0;
  const gapBetweenSections = gapBudget / Math.max(annotationRows, 1);
  const cellFraction = (1 - gapBudget) / totalRows;

  const heatmapDomainEnd = samples.length * cellFraction;
  let currentY = heatmapDomainEnd;
  const allocateDomain = (enabled) => {
    if (!enabled) return [0, 0];
    currentY += gapBetweenSections;
    const start = currentY;
    const end = currentY + cellFraction;
    currentY = end;
    return [start, end];
  };

  const [featureDomainStart, featureDomainEnd] = allocateDomain(hasFeatures);
  const [dbHitDomainStart, dbHitDomainEnd] = allocateDomain(hasDbHits);
  const [consequenceDomainStart, consequenceDomainEnd] = allocateDomain(hasConsequences);

  const targetCellHeight = Math.max(20, Math.min(32, 600 / totalRows));
  const height = Math.max(260, Math.round(totalRows * targetCellHeight + 160));

  return {
    samples,
    xLabels,
    leftMargin,
    hasFeatures,
    hasDbHits,
    hasConsequences,
    gapZ,
    gapCustomdata: cellCustomdata,
    mainZ,
    mainCustomdata: cellCustomdata,
    unknownAfZ,
    hasUnknownAf,
    featureColors,
    featureZValues,
    featureCustomdata,
    featureColorscale,
    dbHitZ,
    dbHitCustomdata,
    consequenceTypes,
    consequenceZValues,
    consequenceColorscale,
    consequenceCustomdata,
    heatmapDomainEnd,
    featureDomainStart,
    featureDomainEnd,
    dbHitDomainStart,
    dbHitDomainEnd,
    consequenceDomainStart,
    consequenceDomainEnd,
    totalRows,
    height,
  };
}
