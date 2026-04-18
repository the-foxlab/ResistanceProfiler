import { PIE_COLORS } from './shared';

function _isPopulated(value) {
  return value !== null && value !== undefined && String(value).trim() !== '';
}

function _displayValue(value, fallback = 'n/a') {
  return _isPopulated(value) ? String(value) : fallback;
}

function _classificationText(rule) {
  const clinicalPhenotype = _isPopulated(rule.clinical_phenotype)
    ? String(rule.clinical_phenotype).trim()
    : '';
  const phenotype = _isPopulated(rule.phenotype)
    ? String(rule.phenotype).trim()
    : '';

  if (clinicalPhenotype && clinicalPhenotype.toLowerCase() !== 'unknown') {
    return clinicalPhenotype;
  }
  if (phenotype && phenotype.toLowerCase() !== 'unknown') {
    return phenotype;
  }
  if (clinicalPhenotype) {
    return clinicalPhenotype;
  }
  if (phenotype) {
    return phenotype;
  }
  return '';
}

function _hasUsableAnnotation(value) {
  if (!_isPopulated(value)) {
    return false;
  }
  return String(value).trim().toLowerCase() !== 'unknown';
}

function _resolvePhenotypeMode(rules, requestedMode) {
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
  const value = mode === 'clinical' ? rule.clinical_phenotype : rule.phenotype;
  if (!_hasUsableAnnotation(value)) {
    return '';
  }
  return String(value).trim();
}

function _classificationTone(label) {
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
    title: 'Database Entries',
    total: rules.length,
    centerLabel: 'entries',
    slices: _limitPieSlices(ordered),
  };
}

function _buildDoiPerDrugPie(rules) {
  const counts = new Map();

  rules.forEach((rule) => {
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
    title: 'Unique Publications',
    total,
    centerLabel: 'publications',
    slices: _limitPieSlices(ordered),
  };
}

function _buildMutationsPerGenePie(rules) {
  const counts = new Map();

  rules.forEach((rule) => {
    const geneName = _displayValue(rule.gene, 'Unknown gene');
    counts.set(geneName, (counts.get(geneName) || 0) + 1);
  });

  if (counts.size === 0) {
    return null;
  }

  const ordered = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  return {
    key: 'mutations-per-gene',
    title: 'Mutations / Gene',
    total: rules.length,
    centerLabel: 'mutations',
    slices: _limitPieSlices(ordered),
  };
}

function _buildEntriesPerOrganismPie(rules, plotMeta) {
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
    title: 'Entries / Organism',
    total: rules.length,
    centerLabel: 'entries',
    slices: _limitPieSlices(ordered),
  };
}

function _buildSummaryPies(rules, plotMeta) {
  const pies = [
    _buildRulesPerDrugPie(rules),
    _buildDoiPerDrugPie(rules),
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
  const genes = Array.isArray(plotMeta?.genes) ? plotMeta.genes : [];
  const lookup = new Map();
  genes.forEach((gene) => {
    const referenceName = _displayValue(gene.reference_name, 'Unknown reference');
    const geneName = _displayValue(gene.gene_name, 'Unknown gene');
    const aaLength = Number(gene.aa_length);
    if (!Number.isFinite(aaLength) || aaLength <= 0) {
      return;
    }
    lookup.set(`${referenceName}::${geneName}`, aaLength);
  });
  return lookup;
}

function _buildGenePositionSections(rules, plotMeta, phenotypeMode, binSize) {
  const groupMap = new Map();
  const referenceLookup = _buildReferenceLookup(plotMeta);
  const geneLengthLookup = _buildGeneLengthLookup(plotMeta);

  rules.forEach((rule) => {
    const positionValue = Number(rule.position);
    if (!Number.isFinite(positionValue)) {
      return;
    }

    const referenceName = _displayValue(rule.reference_name, 'Unknown reference');
    const geneName = _displayValue(rule.gene, 'Unknown gene');
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
  plotMeta,
  requestedPhenotypeMode = 'auto',
  requestedBinSize = 10
) {
  const parsedBinSize = Number(requestedBinSize);
  const binSize = Number.isFinite(parsedBinSize) ? Math.min(100, Math.max(1, Math.floor(parsedBinSize))) : 10;
  const phenotypeMode = _resolvePhenotypeMode(rules, requestedPhenotypeMode);
  const summaryTile = _buildSummaryPies(rules, plotMeta);
  const detailSections = _buildGenePositionSections(rules, plotMeta, phenotypeMode.activeMode, binSize);

  return {
    summaryTile,
    detailSections,
    phenotypeMode,
    binSize,
  };
}