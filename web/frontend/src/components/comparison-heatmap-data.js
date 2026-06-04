/**
 * Pure data preparation functions for the comparison heatmap.
 *
 * All functions are side-effect-free: no DOM access, no Plotly calls.
 * Same input always produces the same output.
 */

/** Predefined consequence-to-color mapping. */
export const CONSEQUENCE_COLORS = {
  missense: '#f39c12',
  synonymous: '#27ae60',
  stop_gained: '#1d1e1f',
  stop_loss: '#3e0c8d',
  start_loss: '#9e821d',
  frameshift: '#e74c3c',
  insertion: '#16a085',
  deletion: '#2980b9',
  splice_region: '#95a5a6',
  unknown: '#bdc3c7',
};

/**
 * Return the color for a consequence type.
 *
 * :param ctype: consequence type string
 * :return: hex color string
 */
export function consequenceColor(ctype) {
  return CONSEQUENCE_COLORS[ctype] || '#cccccc';
}

/**
 * Return unique consequence types in sorted order.
 *
 * :param consequences: array of consequence type strings (may contain duplicates)
 * :return: sorted array of unique consequence types
 */
export function uniqueConsequenceTypes(consequences) {
  const seen = new Set();
  const result = [];
  for (const c of consequences) {
    if (!seen.has(c)) {
      seen.add(c);
      result.push(c);
    }
  }
  result.sort();
  return result;
}

/**
 * Generate a distinct categorical color palette for features.
 *
 * :param count: number of colors needed
 * :return: array of hex color strings
 */
export function featureColorPalette(count) {
  const baseColors = [
    '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
    '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac',
    '#86bcb6', '#8cd17d', '#b6992d', '#499894', '#eabe7a',
    '#d37295', '#a0cbe8', '#f1ce63', '#d4a6c8', '#9d7660',
  ];
  if (count <= baseColors.length) {
    return baseColors.slice(0, count);
  }
  const result = [];
  for (let i = 0; i < count; i++) {
    result.push(baseColors[i % baseColors.length]);
  }
  return result;
}

/**
 * Make x-axis labels unique by appending invisible zero-width spaces.
 *
 * When the same label appears multiple times, append zero-width spaces
 * so Plotly treats them as distinct categories while displaying identically.
 *
 * :param labels: array of label strings
 * :return: array of unique display labels
 */
function _uniqueXLabels(labels) {
  const xLabels = [];
  const labelCounts = {};
  for (const label of labels) {
    labelCounts[label] = (labelCounts[label] || 0) + 1;
  }
  const labelSeen = {};
  for (const label of labels) {
    labelSeen[label] = (labelSeen[label] || 0) + 1;
    if (labelCounts[label] > 1) {
      xLabels.push(label + '\u200B'.repeat(labelSeen[label] - 1));
    } else {
      xLabels.push(label);
    }
  }
  return xLabels;
}

/**
 * Prepare all data needed to render the comparison heatmap.
 *
 * Pure function: no side effects, no DOM access, no Plotly calls.
 * Returns null if data is invalid or empty.
 *
 * :param data: CompareResponse object from the backend
 * :return: structured heatmap data object, or null if data is invalid
 */
export function prepareHeatmapData(data) {
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
  const hasFeatures = features && features.length > 0;
  const hasDbHits = db_hit_map && db_hit_map.length > 0;
  const hasConsequences = consequences && consequences.length > 0;

  // Unique x-axis labels
  const xLabels = _uniqueXLabels(mutation_tick_labels || mutation_labels);

  // Left margin auto-calculation for long sample names
  const maxSampleLen = Math.max(...samples.map((s) => s.length), 1);
  const leftMargin = Math.max(150, Math.min(300, maxSampleLen * 8));

  // Feature display names list
  const featureDisplayList = features
    ? features.map((f) => displayNames[f] || f)
    : [];

  // 1. Coverage gap z-matrix
  const gapZ = samples.map((_si, si) =>
    mutation_labels.map((_mi, mi) => {
      const af = matrix[si][mi].allele_freq;
      return (af === null || af === undefined) ? 1 : null;
    })
  );

  // Customdata for gap trace
  const gapCustomdata = samples.map((_si, si) =>
    mutation_labels.map((mi, miIdx) => {
      const feature = features ? features[feature_map[miIdx]] : '';
      const featureDisplayName = displayNames[feature] || feature;
      const tickLabel = mutation_tick_labels ? mutation_tick_labels[miIdx] : mi;
      return [tickLabel, featureDisplayName];
    })
  );

  // 2. Main allele frequency z-matrix
  const mainZ = samples.map((_si, si) =>
    mutation_labels.map((_mi, mi) => {
      const af = matrix[si][mi].allele_freq;
      return (af === null || af === undefined) ? null : af;
    })
  );

  // Customdata for main trace
  const mainCustomdata = samples.map((_si, si) =>
    mutation_labels.map((mi, miIdx) => {
      const feature = features ? features[feature_map[miIdx]] : '';
      const featureDisplayName = displayNames[feature] || feature;
      const tickLabel = mutation_tick_labels ? mutation_tick_labels[miIdx] : mi;
      return [tickLabel, featureDisplayName];
    })
  );

  // 3. Feature annotation data
  let featureColors = null;
  let featureZValues = null;
  let featureCustomdata = null;
  let featureColorscale = null;
  if (hasFeatures) {
    featureColors = featureColorPalette(Math.max(features.length, 1));
    featureZValues = [mutation_labels.map((_mi, mi) => feature_map[mi])];
    featureCustomdata = [mutation_labels.map((mi, miIdx) => {
      const feature = features[feature_map[miIdx]];
      const featureDisplayName = displayNames[feature] || feature;
      const tickLabel = mutation_tick_labels ? mutation_tick_labels[miIdx] : mi;
      return [featureDisplayName, tickLabel];
    })];
    featureColorscale = features.length === 1
      ? [[0, featureColors[0]], [1, featureColors[0]]]
      : featureColors.map((color, idx) => [idx / (features.length - 1), color]);
  }

  // 4. DB hit annotation data
  let dbHitZ = null;
  let dbHitCustomdata = null;
  if (hasDbHits) {
    dbHitZ = [db_hit_map.map((hit) => (hit ? 1 : 0))];
    dbHitCustomdata = [db_hit_map.map((hit) => [hit ? 'In database' : 'Not in database'])];
  }

  // 5. Consequence annotation data
  let consequenceTypes = null;
  let consequenceZValues = null;
  let consequenceColorscale = null;
  let consequenceCustomdata = null;
  if (hasConsequences) {
    consequenceTypes = uniqueConsequenceTypes(consequences);
    const consequenceIndex = {};
    consequenceTypes.forEach((ctype, idx) => { consequenceIndex[ctype] = idx; });

    consequenceZValues = [consequences.map((c) => consequenceIndex[c] || 0)];
    consequenceColorscale = consequenceTypes.length === 1
      ? [[0, consequenceColor(consequenceTypes[0])], [1, consequenceColor(consequenceTypes[0])]]
      : consequenceTypes.map((ctype, idx) => [idx / (consequenceTypes.length - 1), consequenceColor(ctype)]);
    consequenceCustomdata = [consequences.map((c) => [c])];
  }

  // 6. Layout domain calculations
  const annotationRows = (hasFeatures ? 1 : 0) + (hasDbHits ? 1 : 0) + (hasConsequences ? 1 : 0);
  const totalRows = samples.length + annotationRows;
  const gapBudget = annotationRows > 0 ? 0.04 : 0;
  const gapBetweenSections = gapBudget / Math.max(annotationRows, 1);
  const availableFraction = 1 - gapBudget;
  const cellFraction = availableFraction / totalRows;

  const heatmapDomainEnd = samples.length * cellFraction;
  let currentY = heatmapDomainEnd;

  let featureDomainStart = 0;
  let featureDomainEnd = 0;
  if (hasFeatures) {
    currentY += gapBetweenSections;
    featureDomainStart = currentY;
    featureDomainEnd = currentY + cellFraction;
    currentY = featureDomainEnd;
  }

  let dbHitDomainStart = 0;
  let dbHitDomainEnd = 0;
  if (hasDbHits) {
    currentY += gapBetweenSections;
    dbHitDomainStart = currentY;
    dbHitDomainEnd = currentY + cellFraction;
    currentY = dbHitDomainEnd;
  }

  let consequenceDomainStart = 0;
  let consequenceDomainEnd = 0;
  if (hasConsequences) {
    currentY += gapBetweenSections;
    consequenceDomainStart = currentY;
    consequenceDomainEnd = currentY + cellFraction;
    currentY = consequenceDomainEnd;
  }

  // Scale cell height: ~32px for small heatmaps, shrinking to 20px floor for larger ones
  const targetCellHeight = Math.max(20, Math.min(32, 600 / totalRows));
  const height = Math.max(260, Math.round(totalRows * targetCellHeight + 160));

  return {
    samples,
    xLabels,
    leftMargin,
    featureDisplayList,
    displayNames,
    hasFeatures,
    hasDbHits,
    hasConsequences,
    gapZ,
    gapCustomdata,
    mainZ,
    mainCustomdata,
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
