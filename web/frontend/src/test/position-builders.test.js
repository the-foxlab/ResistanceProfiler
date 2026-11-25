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

  it('includes contradictory and unknown annotations in stacked bars and toneLabels when ranked rules exist', () => {
    const rules = [
      { reference_name: 'ref1', feature: 'GeneA', position: 0, mutation: 'M1', phenotype: 'sensitive' },
      { reference_name: 'ref1', feature: 'GeneA', position: 1, mutation: 'M2', phenotype: 'contradictory' },
      { reference_name: 'ref1', feature: 'GeneA', position: 2, mutation: 'M3', phenotype: 'something else' },
      { reference_name: 'ref1', feature: 'GeneA', position: 12, mutation: 'M4', phenotype: 'conflicting' },
    ];
    const sections = buildGenePositionSections(rules, plotMeta, 'phenotype', 10);
    const plot = sections[0].plots[0];

    expect(plot.hasTypedClassification).toBe(true);
    expect(plot.tones).toEqual(['rank1', 'contradictory', 'unknown']);
    expect(plot.toneLabels).toEqual({
      rank1: 'sensitive',
      contradictory: 'conflicting / contradictory',
      unknown: 'something else',
    });

    const bin1 = plot.positions[0];
    expect(bin1.count).toBe(3);
    expect(bin1.rank1).toBe(1);
    expect(bin1.contradictory).toBe(1);
    expect(bin1.unknown).toBe(1);

    const bin2 = plot.positions[1];
    expect(bin2.count).toBe(1);
    expect(bin2.contradictory).toBe(1);
    expect(bin2.tone).toBe('contradictory');

    const stackedSum = plot.positions.reduce(
      (sum, bin) => sum + plot.tones.reduce((acc, tone) => acc + (bin[tone] || 0), 0),
      0,
    );
    expect(stackedSum).toBe(4);
    expect(plot.subtitle).toContain('4 mutation(s)');
    expect(plot.footer).toBe('4 phenotype annotation(s)');
  });

  it('renders contradictory tone bars and toneLabels even when no rank1..rank5 rules exist', () => {
    const rules = [
      { reference_name: 'ref1', feature: 'GeneA', position: 0, mutation: 'M1', phenotype: 'contradictory' },
    ];
    const sections = buildGenePositionSections(rules, plotMeta, 'phenotype', 10);
    const plot = sections[0].plots[0];

    expect(plot.hasTypedClassification).toBe(true);
    expect(plot.tones).toEqual(['contradictory']);
    expect(plot.toneLabels).toEqual({ contradictory: 'contradictory' });
    expect(plot.positions[0].contradictory).toBe(1);
    expect(plot.positions[0].tone).toBe('contradictory');
  });

  it('conserves counts when the same (position, mutation) has multiple rules with different tones', () => {
    const rules = [
      { reference_name: 'ref1', feature: 'GeneA', position: 0, mutation: 'S315T', phenotype: 'sensitive' },
      { reference_name: 'ref1', feature: 'GeneA', position: 0, mutation: 'S315T', phenotype: 'resistant' },
    ];
    const sections = buildGenePositionSections(rules, plotMeta, 'phenotype', 10);
    const plot = sections[0].plots[0];

    expect(plot.subtitle).toContain('1 mutation(s)');
    expect(plot.footer).toBe('2 phenotype annotation(s)');
    expect(plot.tones).toEqual(['rank5']);
    expect(plot.toneLabels).toEqual({ rank5: 'resistant' });

    const bin0 = plot.positions[0];
    expect(bin0.count).toBe(1);
    expect(bin0.rank5).toBe(1);
    expect(bin0.rank1).toBe(0);
    const binStacked = plot.tones.reduce((sum, tone) => sum + (bin0[tone] || 0), 0);
    expect(binStacked).toBe(bin0.count);
    const totalCount = plot.positions.reduce((sum, bin) => sum + bin.count, 0);
    expect(totalCount).toBe(1);
  });

  it('keeps identical mutation strings at different positions in the same bin distinct', () => {
    const rules = [
      { reference_name: 'ref1', feature: 'GeneA', position: 0, mutation: 'del', phenotype: 'resistant' },
      { reference_name: 'ref1', feature: 'GeneA', position: 2, mutation: 'del', phenotype: 'resistant' },
    ];
    const sections = buildGenePositionSections(rules, plotMeta, 'phenotype', 10);
    const plot = sections[0].plots[0];

    expect(plot.subtitle).toContain('2 mutation(s)');
    const bin0 = plot.positions[0];
    expect(bin0.count).toBe(2);
    expect(bin0.rank5).toBe(2);
    expect(plot.tones.reduce((sum, tone) => sum + (bin0[tone] || 0), 0)).toBe(bin0.count);
    expect(plot.positions.reduce((sum, bin) => sum + bin.count, 0)).toBe(2);
  });

  it('ignores negative positions so bin totals match subtitle mutation counts', () => {
    const rules = [
      { reference_name: 'ref1', feature: 'GeneA', position: -1, mutation: 'upstream', phenotype: 'resistant' },
      { reference_name: 'ref1', feature: 'GeneA', position: 3, mutation: 'S315T', phenotype: 'resistant' },
    ];
    const sections = buildGenePositionSections(rules, plotMeta, 'phenotype', 10);
    const plot = sections[0].plots[0];

    expect(plot.subtitle).toContain('1 mutation(s)');
    expect(plot.footer).toBe('1 phenotype annotation(s)');
    expect(plot.positions.reduce((sum, bin) => sum + bin.count, 0)).toBe(1);
  });
});
