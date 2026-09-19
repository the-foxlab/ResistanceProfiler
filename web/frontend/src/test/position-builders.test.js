import { describe, it, expect } from 'vitest';
import { buildGenePositionSections } from '../components/database-plots/position-builders';

function makePlotMeta(features = []) {
  return {
    references: [
      { reference_name: 'ref1', reference_display_name: 'Reference 1' },
    ],
    features,
  };
}

describe('buildGenePositionSections toneLabels', () => {
  const plotMeta = makePlotMeta([
    { reference_name: 'ref1', feature_name: 'GeneA', aa_length: 100 },
  ]);

  it('derives legend labels from the annotations observed in the database', () => {
    const rules = [
      { reference_name: 'ref1', feature: 'GeneA', position: 0, mutation: 'M1', phenotype: 'sensitive' },
      { reference_name: 'ref1', feature: 'GeneA', position: 5, mutation: 'M2', phenotype: 'resistant' },
    ];
    const sections = buildGenePositionSections(rules, plotMeta, 'phenotype', 10);
    const plot = sections[0].plots[0];
    expect(plot.toneLabels).toEqual({
      rank1: 'sensitive',
      rank5: 'resistant',
    });
  });

  it('joins multiple observed labels sharing a tone, most common first', () => {
    const rules = [
      { reference_name: 'ref1', feature: 'GeneA', position: 0, mutation: 'M1', phenotype: 'sensitive' },
      { reference_name: 'ref1', feature: 'GeneA', position: 1, mutation: 'M2', phenotype: 'sensitive' },
      { reference_name: 'ref1', feature: 'GeneA', position: 2, mutation: 'M3', phenotype: 'susceptible' },
      { reference_name: 'ref1', feature: 'GeneA', position: 3, mutation: 'M4', phenotype: 'high-level resistance' },
    ];
    const sections = buildGenePositionSections(rules, plotMeta, 'phenotype', 10);
    const plot = sections[0].plots[0];
    expect(plot.toneLabels.rank1).toBe('sensitive / susceptible');
    expect(plot.toneLabels.rank5).toBe('high-level resistance');
  });

  it('shows database wording, not canonical rank labels', () => {
    const rules = [
      { reference_name: 'ref1', feature: 'GeneA', position: 0, mutation: 'M1', phenotype: 'low-level resistance' },
      { reference_name: 'ref1', feature: 'GeneA', position: 2, mutation: 'M2', phenotype: 'intermediate' },
    ];
    const sections = buildGenePositionSections(rules, plotMeta, 'phenotype', 10);
    const plot = sections[0].plots[0];
    expect(plot.toneLabels.rank3).toBe('low-level resistance');
    expect(plot.toneLabels.rank4).toBe('intermediate');
    expect(plot.toneLabels.rank1).toBeUndefined();
    expect(plot.toneLabels.rank5).toBeUndefined();
  });

  it('respects the requested annotation mode', () => {
    const rules = [
      { reference_name: 'ref1', feature: 'GeneA', position: 0, mutation: 'M1', phenotype: 'sensitive', clinical_phenotype: 'resistant' },
    ];
    const sections = buildGenePositionSections(rules, plotMeta, 'clinical', 10);
    const plot = sections[0].plots[0];
    expect(plot.toneLabels).toEqual({ rank5: 'resistant' });
  });

  it('omits toneLabels for count-only plots', () => {
    const rules = [
      { reference_name: 'ref1', feature: 'GeneA', position: 0, mutation: 'M1', phenotype: 'something else' },
    ];
    const sections = buildGenePositionSections(rules, plotMeta, 'phenotype', 10);
    const plot = sections[0].plots[0];
    expect(plot.tones).toEqual(['count']);
    expect(plot.toneLabels).toEqual({});
  });

  it('breaks label-count ties alphabetically for deterministic legends', () => {
    const rules = [
      { reference_name: 'ref1', feature: 'GeneA', position: 0, mutation: 'M1', phenotype: 'susceptible' },
      { reference_name: 'ref1', feature: 'GeneA', position: 1, mutation: 'M2', phenotype: 'sensitive' },
    ];
    const sections = buildGenePositionSections(rules, plotMeta, 'phenotype', 10);
    const plot = sections[0].plots[0];
    expect(plot.toneLabels.rank1).toBe('sensitive / susceptible');
  });
});
