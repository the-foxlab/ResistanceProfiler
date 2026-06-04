/**
 * Pie chart builders for the database summary row.
 */

import { PIE_COLORS } from './shared';
import { buildDrugAliasLookup } from '../../utils';
import {
  displayValue,
  formatDrugNameWithAlias,
  extractDoiTokens,
  limitPieSlices,
} from './shared-helpers';

export function buildRulesPerDrugPie(rules, plotMeta) {
  // Counts raw rule rows per drug (not unique mutations).
  const aliasLookup = buildDrugAliasLookup(plotMeta);
  const counts = new Map();

  rules.forEach((rule) => {
    const drugName = displayValue(rule.drug, 'Unspecified drug');
    const key = drugName.trim().toLowerCase();
    const entry = counts.get(key) || {
      label: formatDrugNameWithAlias(drugName, aliasLookup),
      count: 0,
    };
    entry.count += 1;
    counts.set(key, entry);
  });

  if (counts.size === 0) {
    return null;
  }

  const ordered = Array.from(counts.values())
    .map((entry) => [entry.label, entry.count])
    .sort((a, b) => b[1] - a[1]);
  return {
    key: 'rules-per-drug',
    title: 'Mutations per Drug',
    total: counts.size,
    centerLabel: 'drugs',
    slices: limitPieSlices(ordered),
  };
}

export function buildDoiPerDrugPie(rules, formulaRules, plotMeta) {
  // Counts unique publication identifiers per drug across single and formula rules.
  const aliasLookup = buildDrugAliasLookup(plotMeta);
  const counts = new Map();

  const allRows = [
    ...(Array.isArray(rules) ? rules : []),
    ...(Array.isArray(formulaRules) ? formulaRules : []),
  ];

  allRows.forEach((rule) => {
    const drugName = displayValue(rule.drug, 'Unspecified drug');
    const key = drugName.trim().toLowerCase();
    const entry = counts.get(key) || {
      label: formatDrugNameWithAlias(drugName, aliasLookup),
      doiSet: new Set(),
    };
    extractDoiTokens(rule.publication).forEach((doi) => entry.doiSet.add(doi));
    counts.set(key, entry);
  });

  const ordered = Array.from(counts.values())
    .map((entry) => [entry.label, entry.doiSet.size])
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);

  if (ordered.length === 0) {
    return null;
  }

  const total = ordered.reduce((sum, [, count]) => sum + count, 0);
  return {
    key: 'dois-per-drug',
    title: 'Publications per Drug',
    total,
    centerLabel: 'publications',
    slices: limitPieSlices(ordered),
  };
}

export function buildMutationsPerGenePie(rules) {
  // Counts rule rows grouped by gene.
  const counts = new Map();

  rules.forEach((rule) => {
    const geneName = displayValue(rule.feature, 'Unknown gene');
    counts.set(geneName, (counts.get(geneName) || 0) + 1);
  });

  if (counts.size === 0) {
    return null;
  }

  const ordered = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  return {
    key: 'mutations-per-gene',
    title: 'Mutations per Gene',
    total: counts.size,
    centerLabel: 'features',
    slices: limitPieSlices(ordered),
  };
}

export function buildEntriesPerOrganismPie(rules, plotMeta) {
  // Map rule reference -> organism using metadata from the backend plot payload.
  const references = Array.isArray(plotMeta?.references) ? plotMeta.references : [];
  const organismByReference = new Map();

  references.forEach((reference) => {
    const referenceName = displayValue(reference.reference_name, 'Unknown reference');
    const organism = displayValue(reference.reference_organism, 'Unknown organism');
    organismByReference.set(referenceName, organism);
  });

  const counts = new Map();
  rules.forEach((rule) => {
    const referenceName = displayValue(rule.reference_name, 'Unknown reference');
    const organism = organismByReference.get(referenceName) || 'Unknown organism';
    counts.set(organism, (counts.get(organism) || 0) + 1);
  });

  if (counts.size === 0) {
    return null;
  }

  const ordered = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  return {
    key: 'entries-per-organism',
    title: 'Mutations per Organism',
    total: counts.size,
    centerLabel: 'organisms',
    slices: limitPieSlices(ordered),
  };
}

export function buildRuleTypePie(rules, formulaRules) {
  // Show how many rows are single-rule entries vs combination (formula) entries.
  const singleCount = Array.isArray(rules) ? rules.length : 0;
  const combinationCount = Array.isArray(formulaRules) ? formulaRules.length : 0;

  const slices = [];
  if (singleCount > 0) {
    slices.push({ label: 'Single', count: singleCount, color: PIE_COLORS[0] });
  }
  if (combinationCount > 0) {
    slices.push({ label: 'Combinatorial', count: combinationCount, color: PIE_COLORS[1] });
  }

  if (slices.length === 0) {
    return null;
  }

  return {
    key: 'mutations-per-rule-type',
    title: 'Mutations per Rule Type',
    total: slices.length,
    centerLabel: 'types',
    slices,
  };
}

export function buildSummaryPies(rules, formulaRules, plotMeta) {
  // Compose all headline pies shown in the summary row.
  const pies = [
    buildRuleTypePie(rules, formulaRules),
    buildRulesPerDrugPie(rules, plotMeta),
    buildDoiPerDrugPie(rules, formulaRules, plotMeta),
    buildMutationsPerGenePie(rules),
    buildEntriesPerOrganismPie(rules, plotMeta),
  ].filter(Boolean);

  if (pies.length === 0) {
    return null;
  }

  return {
    key: 'database-summary-pies',
    pies,
  };
}
