/**
 * IC50 and score distribution chart builders.
 */

import { PIE_COLORS } from './shared';
import { isPopulated, buildDrugAliasLookup } from '../../utils';
import {
  displayValue,
  formatDrugNameWithAlias,
  buildDrugGroupLookup,
} from './shared-helpers';

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
    if (aNumeric !== bNumeric) {
      return aNumeric - bNumeric;
    }
    return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
  }

  if (aIsNumeric !== bIsNumeric) {
    return aIsNumeric ? -1 : 1;
  }

  return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
}

export function buildLogTicks(minValue, maxValue) {
  const safeMin = Math.max(minValue, 1e-6);
  const safeMax = Math.max(maxValue, safeMin);
  const logMin = Math.log10(safeMin);
  const logMax = Math.log10(safeMax);
  const minExponent = Math.floor(logMin) - 1;
  const maxExponent = Math.ceil(logMax) + 1;
  const tickEntries = [];

  for (let exponent = minExponent; exponent <= maxExponent; exponent += 1) {
    const decadeScale = 10 ** exponent;

    // Major ticks at powers of ten.
    const majorValue = 1 * decadeScale;
    if (majorValue >= safeMin && majorValue <= safeMax) {
      tickEntries.push({
        value: Math.log10(majorValue),
        isMajor: true,
      });
    }

    // Minor ticks at 2..9 within each decade.
    for (let multiplier = 2; multiplier <= 9; multiplier += 1) {
      const tickValue = multiplier * decadeScale;
      if (tickValue >= safeMin && tickValue <= safeMax) {
        tickEntries.push({
          value: Math.log10(tickValue),
          isMajor: false,
        });
      }
    }
  }

  const roundedEntries = tickEntries
    .map((entry) => ({
      value: Math.round(entry.value * 1000000) / 1000000,
      isMajor: entry.isMajor,
    }))
    .sort((a, b) => a.value - b.value);

  const merged = [];
  roundedEntries.forEach((entry) => {
    const existing = merged.find((item) => item.value === entry.value);
    if (existing) {
      existing.isMajor = existing.isMajor || entry.isMajor;
      return;
    }
    merged.push(entry);
  });

  return {
    ticks: merged.map((entry) => entry.value),
    majorTicks: merged.filter((entry) => entry.isMajor).map((entry) => entry.value),
  };
}

export function buildIc50DistributionSections(rules, formulaRules, plotMeta) {
  // Build one strip-plot per organism with one y-lane per drug and log-scaled IC50 on x.
  const references = Array.isArray(plotMeta?.references) ? plotMeta.references : [];
  const organismByReference = new Map();
  const drugAliasLookup = buildDrugAliasLookup(plotMeta);
  const allRules = [
    ...(Array.isArray(rules) ? rules : []),
    ...(Array.isArray(formulaRules) ? formulaRules : []),
  ];

  references.forEach((reference) => {
    const referenceName = displayValue(reference.reference_name, 'Unknown reference');
    const organism = displayValue(reference.reference_organism, 'Unknown organism');
    organismByReference.set(referenceName, organism);
  });

  const groups = new Map();
  allRules.forEach((rule) => {
    const referenceName = displayValue(rule.reference_name, 'Unknown reference');
    const organism = organismByReference.get(referenceName) || 'Unknown organism';
    if (!groups.has(organism)) {
      groups.set(organism, {
        organism,
        byDrug: new Map(),
        skippedNonPositive: 0,
      });
    }

    const group = groups.get(organism);
    const drug = displayValue(rule.drug, 'Unspecified drug');
    const displayDrug = formatDrugNameWithAlias(drug, drugAliasLookup);
    if (!group.byDrug.has(drug)) {
      group.byDrug.set(drug, {
        ic50Values: [],
        foldValues: [],
      });
    }

    const drugBucket = group.byDrug.get(drug);
    const ic50Value = parseNumericMeasurement(rule.ic50);
    if (ic50Value !== null) {
      if (ic50Value > 0) {
        drugBucket.ic50Values.push(ic50Value);
      } else {
        group.skippedNonPositive += 1;
      }
    }

    const foldValue = parseNumericMeasurement(rule.fold_ic50);
    if (foldValue !== null) {
      if (foldValue > 0) {
        drugBucket.foldValues.push(foldValue);
      } else {
        group.skippedNonPositive += 1;
      }
    }
  });

  const plots = Array.from(groups.values())
    .map((group) => {
      const drugEntries = Array.from(group.byDrug.entries())
        .map(([drug, bucket]) => {
          const useIc50 = bucket.ic50Values.length > 0;
          const values = useIc50 ? bucket.ic50Values : bucket.foldValues;
          return {
            drug,
            displayDrug: formatDrugNameWithAlias(drug, drugAliasLookup),
            values,
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
      let minValue = Number.POSITIVE_INFINITY;
      let maxValue = Number.NEGATIVE_INFINITY;

      drugEntries.forEach((entry, laneIdx) => {
        const lane = laneIdx + 1;
        laneLabels[String(lane)] = entry.displayDrug;
        metricLabels.add(entry.metricLabel);

        entry.values.forEach((value, valueIdx) => {
          // Small deterministic jitter to reduce overplotting while keeping lane readability.
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
          minValue = Math.min(minValue, value);
          maxValue = Math.max(maxValue, value);
        });
      });

      if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) {
        return null;
      }

      if (minValue === maxValue) {
        minValue = minValue / 2;
        maxValue = maxValue * 2;
      }

      const minLog = Math.log10(minValue);
      const maxLog = Math.log10(maxValue);
      const xDomain = minLog === maxLog
        ? [minLog - 0.3, maxLog + 0.3]
        : [minLog, maxLog];
      const tickConfig = buildLogTicks(minValue, maxValue);
      const metricLabelList = Array.from(metricLabels).map((label) => label.replace('IC50', 'IC₅₀'));
      const axisMetricLabel = metricLabelList.length === 1
        ? metricLabelList[0]
        : metricLabelList.includes('IC₅₀')
          ? 'IC₅₀ / Fold IC₅₀'
          : metricLabelList.join(' / ');
      const subtitleMetricHint = metricLabelList.length === 1
        ? metricLabelList[0]
        : 'IC₅₀ preferred, Fold IC₅₀ fallback';
      const xAxisUnitLabel = metricLabelList.length === 1
        ? (metricLabelList[0] === 'IC₅₀'
          ? 'IC₅₀ (µM, log scale)'
          : `${metricLabelList[0]} (log scale)`)
        : 'IC₅₀ (µM, log scale) / Fold IC₅₀ (log scale)';

      return {
        kind: 'ic50-distribution',
        key: `ic50::${group.organism}`,
        title: group.organism,
        subtitle: `Log-scaled ${subtitleMetricHint} distribution across ${drugEntries.length} drug(s)`,
        footer: group.skippedNonPositive > 0
          ? `${group.skippedNonPositive} non-positive value(s) were ignored for log-scale plotting`
          : 'Each point is one rule-level measurement',
        xScale: 'log',
        xTickMode: 'ic50-log',
        xAxisLabel: xAxisUnitLabel,
        yAxisLabel: 'Drug',
        xDomain,
        xTicks: tickConfig.ticks,
        majorTicks: tickConfig.majorTicks,
        yDomain: [0.5, drugEntries.length + 0.5],
        yTicks: drugEntries.map((_, idx) => idx + 1),
        laneLabels,
        points,
      };
    })
    .filter(Boolean)
    .sort((a, b) => a.title.localeCompare(b.title));

  if (plots.length === 0) {
    return [];
  }

  return [
    {
      sectionKey: 'ic50-distribution',
      sectionHeading: 'IC50 distribution',
      layout: 'grid',
      plots,
    },
  ];
}

export function buildScoreDistributionSections(rules, formulaRules, plotMeta) {
  // Build one count-per-score bar chart per drug within each organism.
  const references = Array.isArray(plotMeta?.references) ? plotMeta.references : [];
  const organismByReference = new Map();
  const drugAliasLookup = buildDrugAliasLookup(plotMeta);
  const drugGroupLookup = buildDrugGroupLookup(plotMeta);
  const hasDrugGroups = drugGroupLookup.size > 0;
  const allRules = [
    ...(Array.isArray(rules) ? rules : []),
    ...(Array.isArray(formulaRules) ? formulaRules : []),
  ];
  const allScoreLabels = new Set();

  references.forEach((reference) => {
    const referenceName = displayValue(reference.reference_name, 'Unknown reference');
    const organism = displayValue(reference.reference_organism, 'Unknown organism');
    organismByReference.set(referenceName, organism);
  });

  const groups = new Map();
  allRules.forEach((rule) => {
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
        groupName: scoreGroupName,
        groupKey: plotKey,
        organism,
        drugName,
        displayDrugName: formatDrugNameWithAlias(drugName, drugAliasLookup),
        byScore: new Map(),
      });
    }

    const group = sectionPlots.get(plotKey);
    group.byScore.set(scoreLabel, (group.byScore.get(scoreLabel) || 0) + 1);
  });

  const orderedScoreLabels = Array.from(allScoreLabels.values())
    .sort((a, b) => scoreCategorySort(a, b));

  const sectionEntries = Array.from(groups.entries())
    .map(([sectionKey, sectionPlots]) => {
      const plots = Array.from(sectionPlots.values())
        .map((group) => {
          const scoreEntries = orderedScoreLabels.map((scoreLabel) => ({
            scoreLabel,
            count: group.byScore.get(scoreLabel) || 0,
          }));

          if (scoreEntries.length === 0) {
            return null;
          }

          const totalRules = scoreEntries.reduce((sum, entry) => sum + entry.count, 0);

          return {
            kind: 'score-counts',
            key: `score::${sectionKey}::${group.groupKey}`,
            title: group.displayDrugName,
            subtitle: `${group.organism} - ${totalRules} rule(s) grouped by score`,
            organismSortKey: group.organism,
            footer: 'Rule counts grouped by score value',
            xAxisLabel: 'Score',
            yAxisLabel: 'Rule count',
            bars: scoreEntries.map((entry) => ({
              scoreLabel: entry.scoreLabel,
              count: entry.count,
              color: '#6d8194',
            })),
          };
        })
        .filter(Boolean)
        .sort((a, b) => {
          const organismOrder = a.organismSortKey.localeCompare(b.organismSortKey, undefined, {
            numeric: true,
            sensitivity: 'base',
          });
          if (organismOrder !== 0) {
            return organismOrder;
          }
          return a.title.localeCompare(b.title, undefined, {
            numeric: true,
            sensitivity: 'base',
          });
        });

      return { sectionKey, plots };
    })
    .filter((section) => section.plots.length > 0);

  if (sectionEntries.length === 0) {
    return [];
  }

  if (!hasDrugGroups) {
    return [
      {
        sectionKey: 'score-distribution',
        sectionHeading: 'Score counts',
        layout: 'score-grid',
        plots: sectionEntries[0].plots,
      },
    ];
  }

  return sectionEntries
    .map((section) => ({
      sectionKey: `score-distribution::${section.sectionKey}`,
      sectionHeading: section.sectionKey,
      layout: 'score-grid',
      plots: section.plots,
    }))
    .sort((a, b) => {
      if (a.sectionHeading === 'Ungrouped') {
        return 1;
      }
      if (b.sectionHeading === 'Ungrouped') {
        return -1;
      }
      return a.sectionHeading.localeCompare(b.sectionHeading, undefined, {
        numeric: true,
        sensitivity: 'base',
      });
    });
}
