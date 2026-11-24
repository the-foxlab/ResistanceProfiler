import { describe, it, expect } from 'vitest';
import {
  parseNumericMeasurement,
  scoreCategorySort,
  buildLogTicks,
  buildIc50DistributionSections,
  buildScoreDistributionSections,
} from '../components/database-plots/ic50-builders';

describe('parseNumericMeasurement', () => {
  it('parses a direct number', () => {
    expect(parseNumericMeasurement(3.5)).toBe(3.5);
    expect(parseNumericMeasurement(0)).toBe(0);
    expect(parseNumericMeasurement(-1.2)).toBe(-1.2);
  });

  it('parses a string number', () => {
    expect(parseNumericMeasurement('10')).toBe(10);
    expect(parseNumericMeasurement('3.14')).toBeCloseTo(3.14);
  });

  it('parses a string with prefix and unit (e.g. ">10.5")', () => {
    expect(parseNumericMeasurement('>10.5')).toBeCloseTo(10.5);
    expect(parseNumericMeasurement('<0.01')).toBeCloseTo(0.01);
  });

  it('parses a string with trailing text', () => {
    expect(parseNumericMeasurement('5.2 µM')).toBeCloseTo(5.2);
  });

  it('returns null for non-numeric string', () => {
    expect(parseNumericMeasurement('not a number')).toBeNull();
    expect(parseNumericMeasurement('abc')).toBeNull();
  });

  it('returns null for null', () => {
    expect(parseNumericMeasurement(null)).toBeNull();
  });

  it('returns null for undefined', () => {
    expect(parseNumericMeasurement(undefined)).toBeNull();
  });

  it('returns null for whitespace-only string', () => {
    expect(parseNumericMeasurement('   ')).toBeNull();
  });

  it('parses scientific notation', () => {
    expect(parseNumericMeasurement('1e-3')).toBeCloseTo(0.001);
    expect(parseNumericMeasurement('2.5E4')).toBeCloseTo(25000);
  });

  it('parses string scientific notation with prefix', () => {
    expect(parseNumericMeasurement('>1e-6')).toBeCloseTo(1e-6);
  });
});

describe('scoreCategorySort', () => {
  it('sorts numeric categories in numeric order', () => {
    const values = ['3', '1', '2'];
    const sorted = values.sort(scoreCategorySort);
    expect(sorted).toEqual(['1', '2', '3']);
  });

  it('sorts string categories alphabetically', () => {
    const values = ['Zebra', 'Apple', 'Mango'];
    const sorted = values.sort(scoreCategorySort);
    expect(sorted).toEqual(['Apple', 'Mango', 'Zebra']);
  });

  it('places numeric categories before string categories', () => {
    const values = ['Beta', '2', 'Alpha', '1'];
    const sorted = values.sort(scoreCategorySort);
    expect(sorted.indexOf('1')).toBeLessThan(sorted.indexOf('Alpha'));
    expect(sorted.indexOf('2')).toBeLessThan(sorted.indexOf('Beta'));
  });

  it('handles same numeric value with different string representations', () => {
    const result = scoreCategorySort('1.0', '1.00');
    // Same numeric value, falls back to localeCompare
    expect(result).toBe('1.0'.localeCompare('1.00', undefined, { numeric: true, sensitivity: 'base' }));
  });
});

describe('buildLogTicks', () => {
  it('generates ticks for a normal range', () => {
    const result = buildLogTicks(0.001, 1000);
    expect(result.ticks.length).toBeGreaterThan(0);
    expect(result.majorTicks.length).toBeGreaterThan(0);
    // Major ticks should be a subset of all ticks
    expect(result.majorTicks.every((t) => result.ticks.includes(t))).toBe(true);
  });

  it('clamps very small values near 0 to 1e-6', () => {
    const result = buildLogTicks(0, 100);
    // Should not produce NaN or Infinity ticks
    expect(result.ticks.every((t) => Number.isFinite(t))).toBe(true);
    expect(result.majorTicks.every((t) => Number.isFinite(t))).toBe(true);
  });

  it('handles same min and max', () => {
    const result = buildLogTicks(10, 10);
    expect(result.ticks.length).toBeGreaterThan(0);
    expect(result.ticks.every((t) => Number.isFinite(t))).toBe(true);
  });

  it('handles negative min by clamping', () => {
    const result = buildLogTicks(-5, 100);
    expect(result.ticks.every((t) => Number.isFinite(t))).toBe(true);
    expect(result.majorTicks.every((t) => Number.isFinite(t))).toBe(true);
  });

  it('ticks are sorted in ascending order', () => {
    const result = buildLogTicks(0.01, 100);
    for (let i = 1; i < result.ticks.length; i += 1) {
      expect(result.ticks[i]).toBeGreaterThanOrEqual(result.ticks[i - 1]);
    }
  });

  it('returns object with ticks and majorTicks arrays', () => {
    const result = buildLogTicks(1, 100);
    expect(Array.isArray(result.ticks)).toBe(true);
    expect(Array.isArray(result.majorTicks)).toBe(true);
  });

  it('major ticks correspond to powers of ten within range', () => {
    const result = buildLogTicks(1, 1000);
    // 10^0=1, 10^1=10, 10^2=100, 10^3=1000 → log10 values: 0, 1, 2, 3
    expect(result.majorTicks).toContain(0);
    expect(result.majorTicks).toContain(1);
    expect(result.majorTicks).toContain(2);
    expect(result.majorTicks).toContain(3);
  });
});

describe('buildIc50DistributionSections', () => {
  const plotMeta = {
    references: [
      { reference_name: 'ref1', reference_organism: 'Virus A' },
      { reference_name: 'ref2', reference_organism: 'Virus B' },
    ],
    drug_aliases: { acyclovir: 'ACV' },
  };

  it('returns empty array when no positive IC50 or fold_ic50 values exist', () => {
    expect(buildIc50DistributionSections([{ reference_name: 'ref1', drug: 'Acyclovir', ic50: 0 }], [], plotMeta)).toEqual([]);
  });

  it('builds per-organism plots preferring IC50 over fold_ic50 and tracking skipped non-positive values', () => {
    const rules = [
      { reference_name: 'ref1', drug: 'Acyclovir', ic50: '10', fold_ic50: '2' },
      { reference_name: 'ref1', drug: 'Acyclovir', ic50: '-1' },
      { reference_name: 'ref1', drug: 'Foscarnet', fold_ic50: '5' },
    ];
    const formulaRules = [
      { reference_name: 'ref2', drug: 'Ganciclovir', ic50: '100' },
    ];
    const sections = buildIc50DistributionSections(rules, formulaRules, plotMeta);
    expect(sections).toHaveLength(1);
    expect(sections[0].sectionKey).toBe('ic50-distribution');
    expect(sections[0].plots.map((p) => p.title)).toEqual(['Virus A', 'Virus B']);

    const virusA = sections[0].plots[0];
    expect(virusA.laneLabels).toEqual({ 1: 'Acyclovir (ACV)', 2: 'Foscarnet' });
    expect(virusA.points).toHaveLength(2);
    expect(virusA.points[0].metricLabel).toBe('IC50');
    expect(virusA.points[1].metricLabel).toBe('Fold IC50');
    expect(virusA.footer).toContain('1 non-positive value(s)');
    expect(virusA.xAxisLabel).toBe('IC₅₀ (µM, log scale) / Fold IC₅₀ (log scale)');
  });

  it('pads xDomain when all positive values are identical', () => {
    const sections = buildIc50DistributionSections(
      [{ reference_name: 'ref1', drug: 'Acyclovir', ic50: '10' }],
      [],
      plotMeta,
    );
    expect(sections[0].plots[0].xDomain[0]).toBeCloseTo(Math.log10(5));
    expect(sections[0].plots[0].xDomain[1]).toBeCloseTo(Math.log10(20));
  });
});

describe('buildScoreDistributionSections', () => {
  const baseMeta = {
    references: [{ reference_name: 'ref1', reference_organism: 'Virus A' }],
    drug_aliases: { acyclovir: 'ACV' },
  };

  it('builds a single section when no drug_groups exist', () => {
    const rules = [
      { reference_name: 'ref1', drug: 'Acyclovir', score: '10' },
      { reference_name: 'ref1', drug: 'Acyclovir', score: '2' },
    ];
    const formulaRules = [{ reference_name: 'ref1', drug: 'Acyclovir', score: '10' }];
    const sections = buildScoreDistributionSections(rules, formulaRules, baseMeta);
    expect(sections).toHaveLength(1);
    expect(sections[0].sectionKey).toBe('score-distribution');
    expect(sections[0].plots[0].title).toBe('Acyclovir (ACV)');
    expect(sections[0].plots[0].bars.map((b) => [b.scoreLabel, b.count])).toEqual([
      ['2', 1],
      ['10', 2],
    ]);
  });

  it('splits into grouped sections and sorts Ungrouped last', () => {
    const meta = {
      ...baseMeta,
      drug_groups: { Acyclovir: 'Nucleoside' },
    };
    const rules = [
      { reference_name: 'ref1', drug: 'Foscarnet', score: '1' },
      { reference_name: 'ref1', drug: 'Acyclovir', score: '2' },
    ];
    const sections = buildScoreDistributionSections(rules, [], meta);
    expect(sections.map((s) => s.sectionHeading)).toEqual(['Nucleoside', 'Ungrouped']);
    expect(sections[0].plots[0].bars.map((b) => [b.scoreLabel, b.count])).toEqual([['1', 0], ['2', 1]]);
  });
});
