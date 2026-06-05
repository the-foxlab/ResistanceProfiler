/**
 * Orchestrator for database plot data preparation.
 *
 * Domain-specific builders are split into focused modules:
 *   - shared-helpers.js: shared formatting and lookup helpers
 *   - pie-builders.js: summary pie charts
 *   - ic50-builders.js: IC50 and score distribution sections
 *   - position-builders.js: gene-position stacked bar sections
 */

import { resolvePhenotypeMode } from './shared-helpers';
import { buildSummaryPies } from './pie-builders';
import { buildIc50DistributionSections, buildScoreDistributionSections } from './ic50-builders';
import { buildGenePositionSections } from './position-builders';

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
  const phenotypeMode = resolvePhenotypeMode(rules, requestedPhenotypeMode);
  const summaryTile = buildSummaryPies(rules, formulaRules, plotMeta);
  const ic50Sections = buildIc50DistributionSections(rules, formulaRules, plotMeta);
  const scoreSections = buildScoreDistributionSections(rules, formulaRules, plotMeta);
  const detailSections = buildGenePositionSections(rules, plotMeta, phenotypeMode.activeMode, binSize);

  return {
    summaryTile,
    ic50Sections: [...ic50Sections, ...scoreSections],
    detailSections,
    phenotypeMode,
    binSize,
  };
}