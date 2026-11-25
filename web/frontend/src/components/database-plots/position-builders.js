/**
 * Gene-position and reference chart builders.
 */

import { RANK_TONES } from './shared';
import {
  displayValue,
  getRuleAnnotationByMode,
  classificationTone,
  dominantTone,
} from './shared-helpers';

const ALL_TONES = [...RANK_TONES, 'contradictory', 'unknown'];

function buildReferenceLookup(plotMeta) {
  const references = Array.isArray(plotMeta?.references) ? plotMeta.references : [];
  const lookup = new Map();
  references.forEach((reference) => {
    const referenceName = displayValue(reference.reference_name, 'Unknown reference');
    lookup.set(referenceName, {
      referenceName,
      referenceHeading: displayValue(reference.reference_display_name, referenceName),
    });
  });
  return lookup;
}

function buildGeneLengthLookup(plotMeta) {
  const features = Array.isArray(plotMeta?.features) ? plotMeta.features : [];
  const lookup = new Map();
  features.forEach((feature) => {
    const referenceName = displayValue(feature.reference_name, 'Unknown reference');
    const geneName = displayValue(feature.feature_name, 'Unknown gene');
    const aaLength = Number(feature.aa_length);
    if (Number.isFinite(aaLength) && aaLength > 0) {
      lookup.set(`${referenceName}::${geneName}`, aaLength);
    }
  });
  return lookup;
}

function collectGeneRuleGroups(rules, plotMeta, phenotypeMode) {
  const groupMap = new Map();
  const referenceLookup = buildReferenceLookup(plotMeta);
  const geneLengthLookup = buildGeneLengthLookup(plotMeta);

  rules.forEach((rule) => {
    const positionValue = Number(rule.position);
    if (!Number.isFinite(positionValue) || positionValue < 0) {
      return;
    }

    const referenceName = displayValue(rule.reference_name, 'Unknown reference');
    const geneName = displayValue(rule.feature, 'Unknown gene');
    const groupKey = `${referenceName}::${geneName}`;
    if (!groupMap.has(groupKey)) {
      const referenceMeta = referenceLookup.get(referenceName);
      groupMap.set(groupKey, {
        key: groupKey,
        referenceName,
        referenceHeading: referenceMeta ? referenceMeta.referenceHeading : referenceName,
        geneName,
        aaLength: geneLengthLookup.get(groupKey) || 0,
        positions: new Map(),
        mutationTokens: new Set(),
        classifiedRules: 0,
        toneLabels: new Map(),
      });
    }

    const group = groupMap.get(groupKey);
    const aaPosition = positionValue + 1;
    const classification = getRuleAnnotationByMode(rule, phenotypeMode);
    if (!classification) {
      return;
    }

    const tone = classificationTone(classification);
    const mutationToken = displayValue(rule.mutation, 'unknown mutation');
    const mutationKey = `${aaPosition}:${mutationToken}`;
    if (!group.positions.has(aaPosition)) {
      group.positions.set(aaPosition, {
        position: aaPosition,
        mutationTones: new Map(),
      });
    }

    const bucket = group.positions.get(aaPosition);
    if (!bucket.mutationTones.has(mutationKey)) {
      bucket.mutationTones.set(mutationKey, new Map());
    }
    const perMutationTones = bucket.mutationTones.get(mutationKey);
    perMutationTones.set(tone, (perMutationTones.get(tone) || 0) + 1);

    // Track observed database wording per tone so legends match DB terminology.
    if (!group.toneLabels.has(tone)) {
      group.toneLabels.set(tone, new Map());
    }
    const labelCounts = group.toneLabels.get(tone);
    labelCounts.set(classification, (labelCounts.get(classification) || 0) + 1);
    group.mutationTokens.add(mutationKey);
    group.classifiedRules += 1;
    group.aaLength = Math.max(group.aaLength, aaPosition);
  });

  return groupMap;
}

function deriveToneLabels(tones, groupToneLabels) {
  const toneLabels = {};
  tones.forEach((tone) => {
    if (tone === 'count') {
      return;
    }
    const labelCounts = groupToneLabels.get(tone);
    if (!labelCounts) {
      return;
    }
    toneLabels[tone] = Array.from(labelCounts.entries())
      .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0]))
      .map(([label]) => label)
      .join(' / ');
  });
  return toneLabels;
}

function buildGenePositionPlot(group, phenotypeMode, binSize) {
  const aaLength = Math.max(group.aaLength, 1);
  const binCount = Math.ceil(aaLength / binSize);
  // Pre-create empty bins so unmutated regions remain visible along the gene axis.
  const bins = Array.from({ length: binCount }, (_, index) => {
    const binStart = index * binSize + 1;
    const binEnd = Math.min(aaLength, binStart + binSize - 1);
    return {
      binStart,
      binEnd,
      label: String(binEnd),
      mutationSet: new Set(),
      toneMutationSets: new Map(),
    };
  });

  group.positions.forEach((entry) => {
    const binIndex = Math.floor((entry.position - 1) / binSize);
    if (binIndex < 0 || binIndex >= bins.length) {
      return;
    }
    const bin = bins[binIndex];
    // Assign each unique `${aaPosition}:${mutationToken}` one canonical tone so stacked bars sum to bin.count.
    entry.mutationTones.forEach((perMutationTones, mutationKey) => {
      bin.mutationSet.add(mutationKey);
      const canonicalTone = dominantTone(perMutationTones);
      if (!bin.toneMutationSets.has(canonicalTone)) {
        bin.toneMutationSets.set(canonicalTone, new Set());
      }
      bin.toneMutationSets.get(canonicalTone).add(mutationKey);
    });
  });

  const binnedPositions = bins.map((entry) => {
    const toneCounts = new Map();
    const toneFields = {};
    ALL_TONES.forEach((tone) => {
      const count = entry.toneMutationSets.get(tone)?.size || 0;
      toneCounts.set(tone, count);
      toneFields[tone] = count;
    });

    return {
      position: entry.binStart,
      label: entry.label,
      rangeStart: entry.binStart,
      rangeEnd: entry.binEnd,
      count: entry.mutationSet.size,
      tone: dominantTone(toneCounts),
      ...toneFields,
    };
  });

  if (!binnedPositions.some((entry) => entry.count > 0)) {
    return null;
  }

  const activeRankTones = RANK_TONES.filter((tone) => binnedPositions.some((item) => item[tone] > 0));
  const hasContradictory = binnedPositions.some((item) => item.contradictory > 0);
  const hasUnknown = binnedPositions.some((item) => item.unknown > 0);
  const hasTyped = activeRankTones.length > 0 || hasContradictory;
  const tones = hasTyped
    ? [
        ...activeRankTones,
        ...(hasContradictory ? ['contradictory'] : []),
        ...(hasUnknown ? ['unknown'] : []),
      ]
    : ['count'];

  return {
    kind: 'positions',
    key: group.key,
    referenceName: group.referenceName,
    referenceHeading: group.referenceHeading,
    title: group.geneName,
    subtitle: `AA length ${aaLength} · ${group.mutationTokens.size} mutation(s) · ${binSize}-aa bins`,
    footer: group.classifiedRules > 0
      ? `${group.classifiedRules} ${phenotypeMode} annotation(s)`
      : `No ${phenotypeMode} annotation available`,
    xAxisLabel: `Amino-acid bin end (${binSize} aa)`,
    tones,
    toneLabels: deriveToneLabels(tones, group.toneLabels),
    hasTypedClassification: hasTyped,
    positions: binnedPositions,
  };
}

export function buildGenePositionSections(rules, plotMeta, phenotypeMode, binSize) {
  const groupMap = collectGeneRuleGroups(rules, plotMeta, phenotypeMode);
  const plots = Array.from(groupMap.values(), (group) => buildGenePositionPlot(group, phenotypeMode, binSize))
    .filter(Boolean)
    .sort((a, b) => a.referenceHeading.localeCompare(b.referenceHeading) || a.title.localeCompare(b.title));

  const sectionMap = new Map();
  plots.forEach((plot) => {
    const sectionKey = plot.referenceName;
    if (!sectionMap.has(sectionKey)) {
      sectionMap.set(sectionKey, {
        referenceKey: sectionKey,
        referenceHeading: plot.referenceHeading,
        plots: [],
      });
    }
    sectionMap.get(sectionKey).plots.push(plot);
  });

  return Array.from(sectionMap.values()).sort((a, b) => a.referenceHeading.localeCompare(b.referenceHeading));
}
