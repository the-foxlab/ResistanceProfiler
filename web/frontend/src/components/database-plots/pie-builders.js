/**
 * Pie chart builders for the database summary row.
 */

import { PIE_COLORS } from './shared';
import { buildDrugAliasLookup } from '../../utils';
import {
  buildOrganismByReference,
  combineRules,
  displayValue,
  formatDrugNameWithAlias,
  extractDoiTokens,
  limitPieSlices,
} from './shared-helpers';

function buildCountPie({ key, title, centerLabel, entries, total }) {
  const ordered = entries
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);

  if (ordered.length === 0) {
    return null;
  }

  return {
    key,
    title,
    total: total ?? ordered.length,
    centerLabel,
    slices: limitPieSlices(ordered),
  };
}

function buildRulesPerDrugPie(rules, aliasLookup) {
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

  return buildCountPie({
    key: 'rules-per-drug',
    title: 'Mutations per Drug',
    centerLabel: 'drugs',
    entries: Array.from(counts.values(), (entry) => [entry.label, entry.count]),
  });
}

function buildDoiPerDrugPie(rules, formulaRules, aliasLookup) {
  const counts = new Map();
  combineRules(rules, formulaRules).forEach((rule) => {
    const drugName = displayValue(rule.drug, 'Unspecified drug');
    const key = drugName.trim().toLowerCase();
    const entry = counts.get(key) || {
      label: formatDrugNameWithAlias(drugName, aliasLookup),
      doiSet: new Set(),
    };
    extractDoiTokens(rule.publication).forEach((doi) => entry.doiSet.add(doi));
    counts.set(key, entry);
  });

  const entries = Array.from(counts.values(), (entry) => [entry.label, entry.doiSet.size]);
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  return buildCountPie({
    key: 'dois-per-drug',
    title: 'Publications per Drug',
    centerLabel: 'publications',
    entries,
    total,
  });
}

function buildMutationsPerGenePie(rules) {
  const counts = new Map();
  rules.forEach((rule) => {
    const geneName = displayValue(rule.feature, 'Unknown gene');
    counts.set(geneName, (counts.get(geneName) || 0) + 1);
  });

  return buildCountPie({
    key: 'mutations-per-gene',
    title: 'Mutations per Gene',
    centerLabel: 'features',
    entries: Array.from(counts.entries()),
  });
}

function buildEntriesPerOrganismPie(rules, plotMeta) {
  const organismByReference = buildOrganismByReference(plotMeta);
  const counts = new Map();
  rules.forEach((rule) => {
    const referenceName = displayValue(rule.reference_name, 'Unknown reference');
    const organism = organismByReference.get(referenceName) || 'Unknown organism';
    counts.set(organism, (counts.get(organism) || 0) + 1);
  });

  return buildCountPie({
    key: 'entries-per-organism',
    title: 'Mutations per Organism',
    centerLabel: 'organisms',
    entries: Array.from(counts.entries()),
  });
}

function buildRuleTypePie(rules, formulaRules) {
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
  const aliasLookup = buildDrugAliasLookup(plotMeta);
  const pies = [
    buildRuleTypePie(rules, formulaRules),
    buildRulesPerDrugPie(rules, aliasLookup),
    buildDoiPerDrugPie(rules, formulaRules, aliasLookup),
    buildMutationsPerGenePie(rules),
    buildEntriesPerOrganismPie(rules, plotMeta),
  ].filter(Boolean);

  return pies.length > 0 ? { key: 'database-summary-pies', pies } : null;
}
