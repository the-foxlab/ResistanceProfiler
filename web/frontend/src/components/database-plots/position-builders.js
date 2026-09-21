/**
 * Gene-position and reference chart builders.
 */

import {
  displayValue,
  getRuleAnnotationByMode,
  classificationTone,
  dominantTone,
  hasTypedClassification,
} from './shared-helpers';

export function buildReferenceLookup(plotMeta) {
  // Provides stable display labels for section headers.
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

export function buildGeneLengthLookup(plotMeta) {
  // Amino-acid lengths are used to build bins with consistent x-axis semantics.
  const features = Array.isArray(plotMeta?.features) ? plotMeta.features : [];
  const lookup = new Map();
  features.forEach((feature) => {
    const referenceName = displayValue(feature.reference_name, 'Unknown reference');
    const geneName = displayValue(feature.feature_name, 'Unknown gene');
    const aaLength = Number(feature.aa_length);
    if (!Number.isFinite(aaLength) || aaLength <= 0) {
      return;
    }
    lookup.set(`${referenceName}::${geneName}`, aaLength);
  });
  return lookup;
}

export function buildGenePositionSections(rules, plotMeta, phenotypeMode, binSize) {
  // Build per-reference/per-gene datasets for stacked mutation-position bars.
  const groupMap = new Map();
  const referenceLookup = buildReferenceLookup(plotMeta);
  const geneLengthLookup = buildGeneLengthLookup(plotMeta);

  rules.forEach((rule) => {
    const positionValue = Number(rule.position);
    if (!Number.isFinite(positionValue)) {
      return;
    }

    const referenceName = displayValue(rule.reference_name, 'Unknown reference');
    const geneName = displayValue(rule.feature, 'Unknown gene');
    const groupKey = `${referenceName}::${geneName}`;
    if (!groupMap.has(groupKey)) {
      const referenceMeta = referenceLookup.get(referenceName);
      const aaLength = geneLengthLookup.get(groupKey) || 0;
      groupMap.set(groupKey, {
        key: groupKey,
        referenceName,
        referenceHeading: referenceMeta ? referenceMeta.referenceHeading : referenceName,
        geneName,
        aaLength,
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

    if (!group.positions.has(aaPosition)) {
      group.positions.set(aaPosition, {
        position: aaPosition,
        mutationSet: new Set(),
        toneMutationSets: new Map(),
      });
    }

    const bucket = group.positions.get(aaPosition);
    bucket.mutationSet.add(mutationToken);
    if (!bucket.toneMutationSets.has(tone)) {
      bucket.toneMutationSets.set(tone, new Set());
    }
    bucket.toneMutationSets.get(tone).add(mutationToken);
    // Remember the annotation labels actually observed for this tone so the
    // legend can show database wording (e.g. "sensitive") instead of the
    // canonical rank label.
    if (!group.toneLabels.has(tone)) {
      group.toneLabels.set(tone, new Map());
    }
    const labelCounts = group.toneLabels.get(tone);
    labelCounts.set(classification, (labelCounts.get(classification) || 0) + 1);
    group.mutationTokens.add(`${aaPosition}:${mutationToken}`);
    group.classifiedRules += 1;
    group.aaLength = Math.max(group.aaLength, aaPosition);
  });

  const groups = Array.from(groupMap.values()).map((group) => {
    const aaLength = Math.max(group.aaLength, 1);
    const binCount = Math.ceil(aaLength / binSize);
    // Pre-create bins so empty regions still exist on the axis for context.
    const positions = Array.from({ length: binCount }, (_, index) => {
      const binStart = index * binSize + 1;
      const binEnd = Math.min(aaLength, binStart + binSize - 1);
      return {
        index,
        binStart,
        binEnd,
        label: String(binEnd),
        mutationSet: new Set(),
        toneMutationSets: new Map(),
      };
    });

    Array.from(group.positions.values()).forEach((entry) => {
      const binIndex = Math.floor((entry.position - 1) / binSize);
      if (binIndex < 0 || binIndex >= positions.length) {
        return;
      }
      const bin = positions[binIndex];
      entry.mutationSet.forEach((token) => bin.mutationSet.add(token));
      entry.toneMutationSets.forEach((toneSet, tone) => {
        if (!bin.toneMutationSets.has(tone)) {
          bin.toneMutationSets.set(tone, new Set());
        }
        toneSet.forEach((token) => bin.toneMutationSets.get(tone).add(token));
      });
    });

    const binnedPositions = positions.map((entry) => {
      const toneCounts = new Map();
      ['rank1', 'rank2', 'rank3', 'rank4', 'rank5'].forEach((tone) => {
        const toneSet = entry.toneMutationSets.get(tone);
        toneCounts.set(tone, toneSet ? toneSet.size : 0);
      });

      return {
        position: entry.binStart,
        label: entry.label,
        rangeStart: entry.binStart,
        rangeEnd: entry.binEnd,
        count: entry.mutationSet.size,
        tone: dominantTone(toneCounts),
        rank1: toneCounts.get('rank1') || 0,
        rank2: toneCounts.get('rank2') || 0,
        rank3: toneCounts.get('rank3') || 0,
        rank4: toneCounts.get('rank4') || 0,
        rank5: toneCounts.get('rank5') || 0,
      };
    });

    const hasAnyAnnotatedMutation = binnedPositions.some((entry) => entry.count > 0);
    if (!hasAnyAnnotatedMutation) {
      return null;
    }

    const hasTyped = binnedPositions.some((entry) => hasTypedClassification(
      new Map([
        ['rank1', entry.rank1],
        ['rank2', entry.rank2],
        ['rank3', entry.rank3],
        ['rank4', entry.rank4],
        ['rank5', entry.rank5],
      ])
    ));

    const tones = hasTyped
      ? ['rank1', 'rank2', 'rank3', 'rank4', 'rank5'].filter((tone) => binnedPositions.some((item) => item[tone] > 0))
      : ['count'];

    // Derive legend labels from the annotations observed in the database.
    // Multiple distinct labels may share a tone (e.g. "sensitive" and
    // "susceptible" are both rank1); they are shown together, most common first.
    const toneLabels = {};
    tones.forEach((tone) => {
      if (tone === 'count') {
        return;
      }
      const labelCounts = group.toneLabels.get(tone);
      if (!labelCounts) {
        return;
      }
      toneLabels[tone] = Array.from(labelCounts.entries())
        .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0]))
        .map(([label]) => label)
        .join(' / ');
    });

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
      toneLabels,
      hasTypedClassification: hasTyped,
      positions: binnedPositions,
    };
  }).filter(Boolean);

  const sectionMap = new Map();
  groups
    .sort((a, b) => {
      if (a.referenceHeading !== b.referenceHeading) {
        return a.referenceHeading.localeCompare(b.referenceHeading);
      }
      return a.title.localeCompare(b.title);
    })
    .forEach((plot) => {
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
