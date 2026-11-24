/**
 * IC50 and score distribution chart builders.
 */

import { PIE_COLORS } from './shared';
import { isPopulated, buildDrugAliasLookup } from '../../utils';
import {
  buildOrganismByReference,
  combineRules,
  displayValue,
  formatDrugNameWithAlias,
  buildDrugGroupLookup,
} from './shared-helpers';

const NATURAL_COMPARE_OPTS = { numeric: true, sensitivity: 'base' };

export function parseNumericMeasurement(value) {
  if (!isPopulated(value)) {
    return null;
  }

  const normalized = String(value).trim();
  const direct = Number(normalized);
  if (Number.isFinite(direct)) {
    return direct;
  }

  const match = normalized.match(/[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?/);
  if (!match) {
    return null;
  }
  const parsed = Number(match[0]);
  return Number.isFinite(parsed) ? parsed : null;
}

export function scoreCategorySort(a, b) {
  const aNumeric = Number(a);
  const bNumeric = Number(b);
  const aIsNumeric = Number.isFinite(aNumeric);
  const bIsNumeric = Number.isFinite(bNumeric);

  if (aIsNumeric && bIsNumeric) {
    return aNumeric !== bNumeric
      ? aNumeric - bNumeric
      : a.localeCompare(b, undefined, NATURAL_COMPARE_OPTS);
  }
  if (aIsNumeric !== bIsNumeric) {
    return aIsNumeric ? -1 : 1;
  }
  return a.localeCompare(b, undefined, NATURAL_COMPARE_OPTS);
}

export function buildLogTicks(minValue, maxValue) {
  const safeMin = Math.max(minValue, 1e-6);
  const safeMax = Math.max(maxValue, safeMin);
  const minExponent = Math.floor(Math.log10(safeMin)) - 1;
  const maxExponent = Math.ceil(Math.log10(safeMax)) + 1;
  const ticksByValue = new Map();

  const recordTick = (rawValue, isMajor) => {
    if (rawValue < safeMin || rawValue > safeMax) {
      return;
    }
    const rounded = Math.round(Math.log10(rawValue) * 1e6) / 1e6;
    ticksByValue.set(rounded, Boolean(ticksByValue.get(rounded)) || isMajor);
  };

  for (let exponent = minExponent; exponent <= maxExponent; exponent += 1) {
    const decadeScale = 10 ** exponent;
    recordTick(decadeScale, true);
    for (let multiplier = 2; multiplier <= 9; multiplier += 1) {
      recordTick(multiplier * decadeScale, false);
    }
  }

  const sorted = Array.from(ticksByValue.entries()).sort((a, b) => a[0] - b[0]);
  return {
    ticks: sorted.map(([value]) => value),
    majorTicks: sorted.filter(([, isMajor]) => isMajor).map(([value]) => value),
  };
}

function collectIc50ByOrganism(allRules, organismByReference) {
  const groups = new Map();

  allRules.forEach((rule) => {
    const referenceName = displayValue(rule.reference_name, 'Unknown reference');
    const organism = organismByReference.get(referenceName) || 'Unknown organism';
    if (!groups.has(organism)) {
      groups.set(organism, { organism, byDrug: new Map(), skippedNonPositive: 0 });
    }

    const group = groups.get(organism);
    const drug = displayValue(rule.drug, 'Unspecified drug');
    if (!group.byDrug.has(drug)) {
      group.byDrug.set(drug, { ic50Values: [], foldValues: [] });
    }

    const bucket = group.byDrug.get(drug);
    for (const [field, target] of [['ic50', bucket.ic50Values], ['fold_ic50', bucket.foldValues]]) {
      const value = parseNumericMeasurement(rule[field]);
      if (value === null) continue;
      if (value > 0) {
        target.push(value);
      } else {
        group.skippedNonPositive += 1;
      }
    }
  });

  return groups;
}

function computeGlobalIc50Axis(groups) {
  let minVal = Number.POSITIVE_INFINITY;
  let maxVal = Number.NEGATIVE_INFINITY;

  for (const group of groups.values()) {
    for (const bucket of group.byDrug.values()) {
      const values = bucket.ic50Values.length > 0 ? bucket.ic50Values : bucket.foldValues;
      for (const value of values) {
        if (value > 0) {
          minVal = Math.min(minVal, value);
          maxVal = Math.max(maxVal, value);
        }
      }
    }
  }

  if (!Number.isFinite(minVal) || !Number.isFinite(maxVal)) {
    return null;
  }
  if (minVal === maxVal) {
    minVal /= 2;
    maxVal *= 2;
  }

  const tickConfig = buildLogTicks(minVal, maxVal);
  const minLog = Math.log10(minVal);
  const maxLog = Math.log10(maxVal);
  return {
    xDomain: minLog === maxLog ? [minLog - 0.3, maxLog + 0.3] : [minLog, maxLog],
    xTicks: tickConfig.ticks,
    majorTicks: tickConfig.majorTicks,
  };
}

function buildOrganismIc50Plot(group, drugAliasLookup, globalAxis) {
  const drugEntries = Array.from(group.byDrug.entries())
    .map(([drug, bucket]) => {
      const useIc50 = bucket.ic50Values.length > 0;
      return {
        drug,
        displayDrug: formatDrugNameWithAlias(drug, drugAliasLookup),
        values: useIc50 ? bucket.ic50Values : bucket.foldValues,
        metricLabel: useIc50 ? 'IC50' : 'Fold IC50',
      };
    })
    .filter((entry) => entry.values.length > 0)
    .sort((a, b) => a.drug.localeCompare(b.drug));

  if (drugEntries.length === 0) {
    return null;
  }

  const laneLabels = {};
  const points = [];
  const metricLabels = new Set();

  drugEntries.forEach((entry, laneIdx) => {
    const lane = laneIdx + 1;
    laneLabels[String(lane)] = entry.displayDrug;
    metricLabels.add(entry.metricLabel);

    entry.values.forEach((value, valueIdx) => {
      // Small deterministic jitter reduces overplotting while preserving lane readability.
      const jitter = ((valueIdx % 7) - 3) / 14;
      points.push({
        x: Math.log10(value),
        y: lane + jitter,
        lane,
        drug: entry.displayDrug,
        value,
        metricLabel: entry.metricLabel,
        color: PIE_COLORS[laneIdx % PIE_COLORS.length],
      });
    });
  });

  const metricLabelList = Array.from(metricLabels).map((label) => label.replace('IC50', 'IC₅₀'));
  const singleMetric = metricLabelList.length === 1 ? metricLabelList[0] : null;
  const subtitleMetricHint = singleMetric || 'IC₅₀ preferred, Fold IC₅₀ fallback';
  const xAxisUnitLabel = singleMetric
    ? (singleMetric === 'IC₅₀' ? 'IC₅₀ (µM, log scale)' : `${singleMetric} (log scale)`)
    : 'IC₅₀ (µM, log scale) / Fold IC₅₀ (log scale)';

  return {
    kind: 'ic50-distribution',
    key: `ic50::${group.organism}`,
    title: group.organism,
    subtitle: `Log-scaled ${subtitleMetricHint} distribution across ${drugEntries.length} drug(s)`,
    footer: group.skippedNonPositive > 0
      ? `${group.skippedNonPositive} non-positive value(s) were ignored for log-scale plotting`
      : 'Each point is one rule-level measurement',
    xAxisLabel: xAxisUnitLabel,
    yAxisLabel: 'Drug',
    ...globalAxis,
    yDomain: [0.5, drugEntries.length + 0.5],
    yTicks: drugEntries.map((_, idx) => idx + 1),
    laneLabels,
    points,
  };
}

export function buildIc50DistributionSections(rules, formulaRules, plotMeta) {
  // Build one strip-plot per organism with one y-lane per drug and log-scaled IC50 on x.
  const organismByReference = buildOrganismByReference(plotMeta);
  const drugAliasLookup = buildDrugAliasLookup(plotMeta);
  const groups = collectIc50ByOrganism(combineRules(rules, formulaRules), organismByReference);
  const globalAxis = computeGlobalIc50Axis(groups);

  if (!globalAxis) {
    return [];
  }

  const plots = Array.from(groups.values())
    .map((group) => buildOrganismIc50Plot(group, drugAliasLookup, globalAxis))
    .filter(Boolean)
    .sort((a, b) => a.title.localeCompare(b.title));

  if (plots.length === 0) {
    return [];
  }

  return [{
    sectionKey: 'ic50-distribution',
    sectionHeading: 'IC50 distribution',
    layout: 'grid',
    plots,
  }];
}

function buildScoreSectionPlots(sectionKey, sectionPlots, orderedScoreLabels) {
  if (orderedScoreLabels.length === 0) {
    return [];
  }
  return Array.from(sectionPlots.values())
    .map((group) => {
      const bars = orderedScoreLabels.map((scoreLabel) => ({
        scoreLabel,
        count: group.byScore.get(scoreLabel) || 0,
        color: '#0f766e',
      }));
      const totalRules = bars.reduce((sum, entry) => sum + entry.count, 0);

      return {
        kind: 'score-counts',
        key: `score::${sectionKey}::${group.groupKey}`,
        title: group.displayDrugName,
        subtitle: `${group.organism} - ${totalRules} rule(s) grouped by score`,
        organismSortKey: group.organism,
        footer: 'Rule counts grouped by score value',
        xAxisLabel: 'Score',
        yAxisLabel: 'Rule count',
        bars,
      };
    })
    .sort((a, b) => (
      a.organismSortKey.localeCompare(b.organismSortKey, undefined, NATURAL_COMPARE_OPTS)
      || a.title.localeCompare(b.title, undefined, NATURAL_COMPARE_OPTS)
    ));
}

export function buildScoreDistributionSections(rules, formulaRules, plotMeta) {
  // Build one count-per-score bar chart per drug within each organism.
  const organismByReference = buildOrganismByReference(plotMeta);
  const drugAliasLookup = buildDrugAliasLookup(plotMeta);
  const drugGroupLookup = buildDrugGroupLookup(plotMeta);
  const hasDrugGroups = drugGroupLookup.size > 0;
  const allScoreLabels = new Set();
  const groups = new Map();

  combineRules(rules, formulaRules).forEach((rule) => {
    if (!isPopulated(rule.score)) {
      return;
    }
    const scoreLabel = String(rule.score).trim();
    if (!scoreLabel) {
      return;
    }
    allScoreLabels.add(scoreLabel);

    const referenceName = displayValue(rule.reference_name, 'Unknown reference');
    const organism = organismByReference.get(referenceName) || 'Unknown organism';
    const drugName = displayValue(rule.drug, 'Unspecified drug');
    const scoreGroupName = hasDrugGroups
      ? (drugGroupLookup.get(drugName.trim().toLowerCase()) || 'Ungrouped')
      : 'Score counts';
    const sectionKey = hasDrugGroups ? scoreGroupName : 'score-distribution';
    const plotKey = `${organism}::${drugName}`;

    if (!groups.has(sectionKey)) {
      groups.set(sectionKey, new Map());
    }
    const sectionPlots = groups.get(sectionKey);
    if (!sectionPlots.has(plotKey)) {
      sectionPlots.set(plotKey, {
        groupKey: plotKey,
        organism,
        displayDrugName: formatDrugNameWithAlias(drugName, drugAliasLookup),
        byScore: new Map(),
      });
    }

    const group = sectionPlots.get(plotKey);
    group.byScore.set(scoreLabel, (group.byScore.get(scoreLabel) || 0) + 1);
  });

  const orderedScoreLabels = Array.from(allScoreLabels).sort(scoreCategorySort);
  const sectionEntries = Array.from(groups.entries())
    .map(([sectionKey, sectionPlots]) => ({
      sectionKey,
      plots: buildScoreSectionPlots(sectionKey, sectionPlots, orderedScoreLabels),
    }))
    .filter((section) => section.plots.length > 0);

  if (sectionEntries.length === 0) {
    return [];
  }

  if (!hasDrugGroups) {
    return [{
      sectionKey: 'score-distribution',
      sectionHeading: 'Score counts',
      layout: 'score-grid',
      plots: sectionEntries[0].plots,
    }];
  }

  return sectionEntries
    .map((section) => ({
      sectionKey: `score-distribution::${section.sectionKey}`,
      sectionHeading: section.sectionKey,
      layout: 'score-grid',
      plots: section.plots,
    }))
    .sort((a, b) => {
      if (a.sectionHeading === 'Ungrouped') return 1;
      if (b.sectionHeading === 'Ungrouped') return -1;
      return a.sectionHeading.localeCompare(b.sectionHeading, undefined, NATURAL_COMPARE_OPTS);
    });
}
