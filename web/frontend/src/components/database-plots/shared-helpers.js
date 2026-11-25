/**
 * Shared helpers used across multiple chart-domain modules.
 */

import { PIE_COLORS } from './shared';
import { isPopulated, labelToRank } from '../../utils';

export function displayValue(value, fallback = 'n/a') {
  return isPopulated(value) ? String(value) : fallback;
}

export function hasUsableAnnotation(value) {
  if (!isPopulated(value)) {
    return false;
  }
  return String(value).trim().toLowerCase() !== 'unknown';
}

export function combineRules(rules, formulaRules) {
  return [
    ...(Array.isArray(rules) ? rules : []),
    ...(Array.isArray(formulaRules) ? formulaRules : []),
  ];
}

export function buildOrganismByReference(plotMeta) {
  const references = Array.isArray(plotMeta?.references) ? plotMeta.references : [];
  const lookup = new Map();
  references.forEach((reference) => {
    const referenceName = displayValue(reference.reference_name, 'Unknown reference');
    const organism = displayValue(reference.reference_organism, 'Unknown organism');
    lookup.set(referenceName, organism);
  });
  return lookup;
}

export function resolvePhenotypeMode(rules, requestedMode) {
  const hasPhenotype = rules.some((rule) => hasUsableAnnotation(rule.phenotype));
  const hasClinical = rules.some((rule) => hasUsableAnnotation(rule.clinical_phenotype));

  let activeMode = 'phenotype';
  if (requestedMode === 'clinical' && hasClinical) {
    activeMode = 'clinical';
  } else if (requestedMode === 'phenotype' && hasPhenotype) {
    activeMode = 'phenotype';
  } else if (!hasPhenotype && hasClinical) {
    activeMode = 'clinical';
  }

  return { requestedMode, activeMode, hasPhenotype, hasClinical };
}

export function getRuleAnnotationByMode(rule, mode) {
  // Ignore "unknown" annotations so bars represent interpretable classifications only.
  const value = mode === 'clinical' ? rule.clinical_phenotype : rule.phenotype;
  if (!hasUsableAnnotation(value)) {
    return '';
  }
  return String(value).trim();
}

export function classificationTone(label) {
  // Normalize free-text phenotypes into the 5-rank severity vocabulary mirroring
  // `respro/db/phenotype_ranks.py` (see `labelToRank` in utils.js).
  const rank = labelToRank(label);
  if (rank === null || rank === 0) {
    return 'unknown';
  }
  if (rank < 0) {
    return 'contradictory';
  }
  return `rank${rank}`;
}

export function limitPieSlices(entries) {
  // Limit legend size to 6 slices and aggregate the tail into "Other".
  const visible = entries.slice(0, 6).map(([label, count], index) => ({
    label,
    count,
    color: PIE_COLORS[index % PIE_COLORS.length],
  }));

  if (entries.length > 6) {
    const otherCount = entries.slice(6).reduce((sum, [, count]) => sum + count, 0);
    visible.push({
      label: 'Other',
      count: otherCount,
      color: PIE_COLORS[visible.length % PIE_COLORS.length],
    });
  }

  return visible;
}

const TONE_PRIORITY = {
  rank5: 8,
  rank4: 7,
  rank3: 6,
  rank2: 5,
  rank1: 4,
  contradictory: 2,
  unknown: 1,
};

export function dominantTone(toneCounts) {
  // Higher rank = more severe, so ties resolve toward the stronger class.
  const entries = Array.from(toneCounts.entries());
  if (entries.length === 0) {
    return 'unknown';
  }
  entries.sort((a, b) => (b[1] - a[1]) || ((TONE_PRIORITY[b[0]] || 0) - (TONE_PRIORITY[a[0]] || 0)));
  return entries[0][0];
}

export function extractDoiTokens(value) {
  if (!isPopulated(value)) {
    return [];
  }
  return String(value)
    .split(/[;,\n]+/)
    .map((token) => token.trim())
    .filter(Boolean);
}

export function buildDrugGroupLookup(plotMeta) {
  const groups = plotMeta?.drug_groups || {};
  const lookup = new Map();
  Object.entries(groups).forEach(([drugName, groupName]) => {
    const canonical = String(drugName || '').trim().toLowerCase();
    const displayGroup = String(groupName || '').trim();
    if (canonical && displayGroup) {
      lookup.set(canonical, displayGroup);
    }
  });
  return lookup;
}

export function formatDrugNameWithAlias(name, aliasLookup) {
  const drugName = displayValue(name, 'Unspecified drug');
  const alias = aliasLookup.get(drugName.trim().toLowerCase()) || '';
  return alias ? `${drugName} (${alias})` : drugName;
}
