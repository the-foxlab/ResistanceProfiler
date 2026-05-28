import { PIE_COLORS } from './shared';

function _isPopulated(value) {
  return value !== null && value !== undefined && String(value).trim() !== '';
}

function _displayValue(value, fallback = 'n/a') {
  return _isPopulated(value) ? String(value) : fallback;
}

function _hasUsableAnnotation(value) {
  if (!_isPopulated(value)) {
    return false;
  }
  return String(value).trim().toLowerCase() !== 'unknown';
}

function _resolvePhenotypeMode(rules, requestedMode) {
  // Determine which annotation namespace can be displayed meaningfully.
  const hasPhenotype = rules.some((rule) => _hasUsableAnnotation(rule.phenotype));
  const hasClinical = rules.some((rule) => _hasUsableAnnotation(rule.clinical_phenotype));

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

function _getRuleAnnotationByMode(rule, mode) {
  // Ignore "unknown" annotations so bars represent interpretable classifications only.
  const value = mode === 'clinical' ? rule.clinical_phenotype : rule.phenotype;
  if (!_hasUsableAnnotation(value)) {
    return '';
  }
  return String(value).trim();
}

function _classificationTone(label) {
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

function _hasTypedClassification(toneCounts) {
  return ['resistant', 'intermediate', 'susceptible'].some((tone) => (toneCounts.get(tone) || 0) > 0);
}

function _limitPieSlices(entries) {
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

function _dominantTone(toneCounts) {
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

function _extractDoiTokens(value) {
  if (!_isPopulated(value)) {
    return [];
  }

  return String(value)
    .split(/[;,\n]+/)
    .map((token) => token.trim())
    .filter(Boolean);
}

function _buildRulesPerDrugPie(rules) {
  // Counts raw rule rows per drug (not unique mutations).
  const counts = new Map();

  rules.forEach((rule) => {
    const drugName = _displayValue(rule.drug, 'Unspecified drug');
    counts.set(drugName, (counts.get(drugName) || 0) + 1);
  });

  if (counts.size === 0) {
    return null;
  }

  const ordered = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  return {
    key: 'rules-per-drug',
    title: 'Mutations per Drug',
    total: counts.size,
    centerLabel: 'drugs',
    slices: _limitPieSlices(ordered),
  };
}

function _buildDoiPerDrugPie(rules, formulaRules) {
  // Counts unique publication identifiers per drug across single and formula rules.
  const counts = new Map();

  const allRows = [
    ...(Array.isArray(rules) ? rules : []),
    ...(Array.isArray(formulaRules) ? formulaRules : []),
  ];

  allRows.forEach((rule) => {
    const drugName = _displayValue(rule.drug, 'Unspecified drug');
    const doiSet = counts.get(drugName) || new Set();
    _extractDoiTokens(rule.publication).forEach((doi) => doiSet.add(doi));
    counts.set(drugName, doiSet);
  });

  const ordered = Array.from(counts.entries())
    .map(([label, doiSet]) => [label, doiSet.size])
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
    slices: _limitPieSlices(ordered),
  };
}

function _buildMutationsPerGenePie(rules) {
  // Counts rule rows grouped by gene.
  const counts = new Map();

  rules.forEach((rule) => {
    const geneName = _displayValue(rule.feature, 'Unknown gene');
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
    slices: _limitPieSlices(ordered),
  };
}

function _buildEntriesPerOrganismPie(rules, plotMeta) {
  // Map rule reference -> organism using metadata from the backend plot payload.
  const references = Array.isArray(plotMeta?.references) ? plotMeta.references : [];
  const organismByReference = new Map();

  references.forEach((reference) => {
    const referenceName = _displayValue(reference.reference_name, 'Unknown reference');
    const organism = _displayValue(reference.reference_organism, 'Unknown organism');
    organismByReference.set(referenceName, organism);
  });

  const counts = new Map();
  rules.forEach((rule) => {
    const referenceName = _displayValue(rule.reference_name, 'Unknown reference');
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
    slices: _limitPieSlices(ordered),
  };
}

function _buildRuleTypePie(rules, formulaRules) {
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

function _buildSummaryPies(rules, formulaRules, plotMeta) {
  // Compose all headline pies shown in the summary row.
  const pies = [
    _buildRuleTypePie(rules, formulaRules),
    _buildRulesPerDrugPie(rules),
    _buildDoiPerDrugPie(rules, formulaRules),
    _buildMutationsPerGenePie(rules),
    _buildEntriesPerOrganismPie(rules, plotMeta),
  ].filter(Boolean);

  if (pies.length === 0) {
    return null;
  }

  return {
    key: 'database-summary-pies',
    pies,
  };
}

function _buildReferenceLookup(plotMeta) {
  // Provides stable display labels for section headers.
  const references = Array.isArray(plotMeta?.references) ? plotMeta.references : [];
  const lookup = new Map();
  references.forEach((reference) => {
    const referenceName = _displayValue(reference.reference_name, 'Unknown reference');
    lookup.set(referenceName, {
      referenceName,
      referenceHeading: _displayValue(reference.reference_display_name, referenceName),
    });
  });
  return lookup;
}

function _buildGeneLengthLookup(plotMeta) {
  // Amino-acid lengths are used to build bins with consistent x-axis semantics.
  const features = Array.isArray(plotMeta?.features) ? plotMeta.features : [];
  const lookup = new Map();
  features.forEach((feature) => {
    const referenceName = _displayValue(feature.reference_name, 'Unknown reference');
    const geneName = _displayValue(feature.feature_name, 'Unknown gene');
    const aaLength = Number(feature.aa_length);
    if (!Number.isFinite(aaLength) || aaLength <= 0) {
      return;
    }
    lookup.set(`${referenceName}::${geneName}`, aaLength);
  });
  return lookup;
}

function _parseNumericMeasurement(value) {
  if (!_isPopulated(value)) {
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

function _scoreCategorySort(a, b) {
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

function _buildLogTicks(minValue, maxValue) {
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

function _buildIc50DistributionSections(rules, formulaRules, plotMeta) {
  // Build one strip-plot per organism with one y-lane per drug and log-scaled IC50 on x.
  const references = Array.isArray(plotMeta?.references) ? plotMeta.references : [];
  const organismByReference = new Map();
  const allRules = [
    ...(Array.isArray(rules) ? rules : []),
    ...(Array.isArray(formulaRules) ? formulaRules : []),
  ];

  references.forEach((reference) => {
    const referenceName = _displayValue(reference.reference_name, 'Unknown reference');
    const organism = _displayValue(reference.reference_organism, 'Unknown organism');
    organismByReference.set(referenceName, organism);
  });

  const groups = new Map();
  allRules.forEach((rule) => {
    const referenceName = _displayValue(rule.reference_name, 'Unknown reference');
    const organism = organismByReference.get(referenceName) || 'Unknown organism';
    if (!groups.has(organism)) {
      groups.set(organism, {
        organism,
        byDrug: new Map(),
        skippedNonPositive: 0,
      });
    }

    const group = groups.get(organism);
    const drug = _displayValue(rule.drug, 'Unspecified drug');
    if (!group.byDrug.has(drug)) {
      group.byDrug.set(drug, {
        ic50Values: [],
        foldValues: [],
      });
    }

    const drugBucket = group.byDrug.get(drug);
    const ic50Value = _parseNumericMeasurement(rule.ic50);
    if (ic50Value !== null) {
      if (ic50Value > 0) {
        drugBucket.ic50Values.push(ic50Value);
      } else {
        group.skippedNonPositive += 1;
      }
    }

    const foldValue = _parseNumericMeasurement(rule.fold_ic50);
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
        laneLabels[String(lane)] = entry.drug;
        metricLabels.add(entry.metricLabel);

        entry.values.forEach((value, valueIdx) => {
          // Small deterministic jitter to reduce overplotting while keeping lane readability.
          const jitter = ((valueIdx % 7) - 3) / 14;
          points.push({
            x: Math.log10(value),
            y: lane + jitter,
            lane,
            drug: entry.drug,
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
      const tickConfig = _buildLogTicks(minValue, maxValue);
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
      layout: 'single-column',
      plots,
    },
  ];
}

function _buildScoreDistributionSections(rules, formulaRules, plotMeta) {
  // Build one count-per-score bar chart per drug within each organism.
  const references = Array.isArray(plotMeta?.references) ? plotMeta.references : [];
  const organismByReference = new Map();
  const allRules = [
    ...(Array.isArray(rules) ? rules : []),
    ...(Array.isArray(formulaRules) ? formulaRules : []),
  ];

  references.forEach((reference) => {
    const referenceName = _displayValue(reference.reference_name, 'Unknown reference');
    const organism = _displayValue(reference.reference_organism, 'Unknown organism');
    organismByReference.set(referenceName, organism);
  });

  const groups = new Map();
  allRules.forEach((rule) => {
    if (!_isPopulated(rule.score)) {
      return;
    }
    const scoreLabel = String(rule.score).trim();
    if (!scoreLabel) {
      return;
    }

    const referenceName = _displayValue(rule.reference_name, 'Unknown reference');
    const organism = organismByReference.get(referenceName) || 'Unknown organism';
    const drugName = _displayValue(rule.drug, 'Unspecified drug');
    const groupKey = `${organism}::${drugName}`;

    if (!groups.has(groupKey)) {
      groups.set(groupKey, {
        groupKey,
        organism,
        drugName,
        byScore: new Map(),
      });
    }

    const group = groups.get(groupKey);
    group.byScore.set(scoreLabel, (group.byScore.get(scoreLabel) || 0) + 1);
  });

  const plots = Array.from(groups.values())
    .map((group) => {
      const scoreEntries = Array.from(group.byScore.entries())
        .map(([scoreLabel, count]) => ({
          scoreLabel,
          count,
        }))
        .sort((a, b) => _scoreCategorySort(a.scoreLabel, b.scoreLabel));

      if (scoreEntries.length === 0) {
        return null;
      }

      const totalRules = scoreEntries.reduce((sum, entry) => sum + entry.count, 0);

      return {
        kind: 'score-counts',
        key: `score::${group.groupKey}`,
        title: group.drugName,
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

  if (plots.length === 0) {
    return [];
  }

  return [
    {
      sectionKey: 'score-distribution',
      sectionHeading: 'Score counts',
      layout: 'score-grid',
      plots,
    },
  ];
}

function _buildGenePositionSections(rules, plotMeta, phenotypeMode, binSize) {
  // Build per-reference/per-gene datasets for stacked mutation-position bars.
  const groupMap = new Map();
  const referenceLookup = _buildReferenceLookup(plotMeta);
  const geneLengthLookup = _buildGeneLengthLookup(plotMeta);

  rules.forEach((rule) => {
    const positionValue = Number(rule.position);
    if (!Number.isFinite(positionValue)) {
      return;
    }

    const referenceName = _displayValue(rule.reference_name, 'Unknown reference');
    const geneName = _displayValue(rule.feature, 'Unknown gene');
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
      });
    }

    const group = groupMap.get(groupKey);
    const aaPosition = positionValue + 1;
    const classification = _getRuleAnnotationByMode(rule, phenotypeMode);
    if (!classification) {
      return;
    }
    const tone = _classificationTone(classification);
    const mutationToken = _displayValue(rule.mutation, 'unknown mutation');

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
      ['resistant', 'intermediate', 'susceptible'].forEach((tone) => {
        const toneSet = entry.toneMutationSets.get(tone);
        toneCounts.set(tone, toneSet ? toneSet.size : 0);
      });

      return {
        position: entry.binStart,
        label: entry.label,
        rangeStart: entry.binStart,
        rangeEnd: entry.binEnd,
        count: entry.mutationSet.size,
        tone: _dominantTone(toneCounts),
        resistant: toneCounts.get('resistant') || 0,
        intermediate: toneCounts.get('intermediate') || 0,
        susceptible: toneCounts.get('susceptible') || 0,
      };
    });

    const hasAnyAnnotatedMutation = binnedPositions.some((entry) => entry.count > 0);
    if (!hasAnyAnnotatedMutation) {
      return null;
    }

    const hasTypedClassification = binnedPositions.some((entry) => _hasTypedClassification(
      new Map([
        ['resistant', entry.resistant],
        ['intermediate', entry.intermediate],
        ['susceptible', entry.susceptible],
      ])
    ));

    const tones = hasTypedClassification
      ? ['resistant', 'intermediate', 'susceptible'].filter((tone) => binnedPositions.some((item) => item[tone] > 0))
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
      hasTypedClassification,
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

export function buildDatabasePlots(
  rules,
  formulaRules,
  plotMeta,
  requestedPhenotypeMode = 'auto',
  requestedBinSize = 10
) {
  // Clamp bin size to a safe UI range and return fully prepared chart sections.
  const parsedBinSize = Number(requestedBinSize);
  const binSize = Number.isFinite(parsedBinSize) ? Math.min(100, Math.max(1, Math.floor(parsedBinSize))) : 10;
  const phenotypeMode = _resolvePhenotypeMode(rules, requestedPhenotypeMode);
  const summaryTile = _buildSummaryPies(rules, formulaRules, plotMeta);
  const ic50Sections = _buildIc50DistributionSections(rules, formulaRules, plotMeta);
  const scoreSections = _buildScoreDistributionSections(rules, formulaRules, plotMeta);
  const detailSections = _buildGenePositionSections(rules, plotMeta, phenotypeMode.activeMode, binSize);

  return {
    summaryTile,
    ic50Sections: [...ic50Sections, ...scoreSections],
    detailSections,
    phenotypeMode,
    binSize,
  };
}