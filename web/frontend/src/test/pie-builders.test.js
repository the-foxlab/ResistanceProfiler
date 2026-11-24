import { describe, it, expect } from 'vitest';
import { buildSummaryPies } from '../components/database-plots/pie-builders';

describe('buildSummaryPies', () => {
  it('returns null when both rule lists are empty', () => {
    expect(buildSummaryPies([], [], {})).toBeNull();
  });

  it('builds rule-type, drug, deduplicated publication, gene, and organism pies', () => {
    const plotMeta = {
      references: [{ reference_name: 'ref1', reference_organism: 'HSV-1' }],
      drug_aliases: { acyclovir: 'ACV' },
    };
    const rules = [
      { reference_name: 'ref1', feature: 'UL23', drug: 'Acyclovir', publication: '10.1/a; 10.1/b' },
      { reference_name: 'ref1', feature: 'UL23', drug: 'Acyclovir', publication: '10.1/a' },
      { reference_name: 'ref1', feature: 'UL30', drug: 'Foscarnet', publication: '' },
    ];
    const formulaRules = [
      { reference_name: 'ref1', drug: 'Acyclovir', publication: '10.1/b; 10.1/c' },
    ];

    const summary = buildSummaryPies(rules, formulaRules, plotMeta);
    expect(summary.key).toBe('database-summary-pies');
    const byKey = Object.fromEntries(summary.pies.map((pie) => [pie.key, pie]));

    expect(byKey['mutations-per-rule-type'].slices.map((s) => [s.label, s.count])).toEqual([
      ['Single', 3],
      ['Combinatorial', 1],
    ]);
    expect(byKey['rules-per-drug'].total).toBe(2);
    expect(byKey['rules-per-drug'].slices.map((s) => [s.label, s.count])).toEqual([
      ['Acyclovir (ACV)', 2],
      ['Foscarnet', 1],
    ]);
    expect(byKey['dois-per-drug'].total).toBe(3);
    expect(byKey['dois-per-drug'].slices.map((s) => [s.label, s.count])).toEqual([
      ['Acyclovir (ACV)', 3],
    ]);
    expect(byKey['mutations-per-gene'].slices.map((s) => [s.label, s.count])).toEqual([
      ['UL23', 2],
      ['UL30', 1],
    ]);
    expect(byKey['entries-per-organism'].slices.map((s) => [s.label, s.count])).toEqual([
      ['HSV-1', 3],
    ]);
  });
});
