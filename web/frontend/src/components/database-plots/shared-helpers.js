/**
 * Shared helpers used across multiple chart-domain modules.
 */

import { PIE_COLORS } from './shared';
import { isPopulated, buildDrugAliasLookup } from '../../utils';

export function displayValue(value, fallback = 'n/a') {
  return isPopulated(value) ? String(value) : fallback;
}

export function hasUsableAnnotation(value) {
  if (!isPopulated(value)) {
    return false;
  }
  return String(value).trim().toLowerCase() !== 'unknown';
}

export function resolvePhenotypeMode(rules, requestedMode) {
  // Determine which annotation namespace can be displayed meaningfully.
  const hasPhenotype = rules.some((rule) => hasUsableAnnotation(rule.phenotype));
  const hasClinical = rules.some((rule) => hasUsableAnnotation(rule.clinical_phenotype));

  if (requestedMode === 'clinical' && hasClinical) {
    return {
      requestedMode,
      activeMode: 'clinical',
      hasPhenotype,
      hasClinical,
    };
  }

  if (requestedMode === 'phenotype' && hasPhenotype) {
    return {
      requestedMode,
      activeMode: 'phenotype',
      hasPhenotype,
      hasClinical,
    };
  }

  if (hasPhenotype) {
    return {
      requestedMode,
      activeMode: 'phenotype',
      hasPhenotype,
      hasClinical,
    };
  }

  if (hasClinical) {
    return {
      requestedMode,
      activeMode: 'clinical',
      hasPhenotype,
      hasClinical,
    };
  }

  return {
    requestedMode,
    activeMode: 'phenotype',
    hasPhenotype,
    hasClinical,
  };
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
  // Normalize free-text phenotypes into a small color/legend vocabulary.
  const lowered = String(label || '').toLowerCase();
  if (!lowered) {
    return 'unknown';
  }
  if (
    lowered.includes('resist') ||
    lowered.includes('decreased susceptibility') ||
    lowered.includes('reduced susceptibility') ||
    lowered.includes('non-susceptible')
  ) {
    return 'resistant';
  }
  if (
    lowered.includes('intermediate') ||
    lowered.includes('partial') ||
    lowered.includes('reduced') ||
    lowered.includes('borderline')
  ) {
    return 'intermediate';
  }
  if (
    lowered.includes('susceptible') ||
    lowered.includes('sensitive') ||
    lowered.includes('wildtype')
  ) {
    return 'susceptible';
  }
  return 'unknown';
}

export function hasTypedClassification(toneCounts) {
  return ['resistant', 'intermediate', 'susceptible'].some((tone) => (toneCounts.get(tone) || 0) > 0);
}

export function limitPieSlices(entries) {
  // Keep pies readable by limiting legend size and aggregating the tail into "Other".
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

export function dominantTone(toneCounts) {
  // Used for a quick dominant-color hint when multiple tones occur in one bin.
  const priority = { resistant: 4, intermediate: 3, susceptible: 2, unknown: 1 };
  const entries = Array.from(toneCounts.entries());
  if (entries.length === 0) {
    return 'unknown';
  }
  entries.sort((a, b) => {
    if (b[1] !== a[1]) {
      return b[1] - a[1];
    }
    return priority[b[0]] - priority[a[0]];
  });
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
  if (!alias) {
    return drugName;
  }
  return `${drugName} (${alias})`;
}
