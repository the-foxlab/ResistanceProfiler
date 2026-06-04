import { describe, it, expect } from 'vitest';
import {
  displayValue,
  hasUsableAnnotation,
  resolvePhenotypeMode,
  getRuleAnnotationByMode,
  classificationTone,
  hasTypedClassification,
  limitPieSlices,
  dominantTone,
  extractDoiTokens,
  buildDrugGroupLookup,
  formatDrugNameWithAlias,
} from '../components/database-plots/shared-helpers';

describe('displayValue', () => {
  it('returns string representation of populated values', () => {
    expect(displayValue('hello')).toBe('hello');
    expect(displayValue(42)).toBe('42');
    expect(displayValue(0)).toBe('0');
  });

  it('returns fallback for null', () => {
    expect(displayValue(null)).toBe('n/a');
  });

  it('returns fallback for undefined', () => {
    expect(displayValue(undefined)).toBe('n/a');
  });

  it('returns fallback for whitespace-only strings', () => {
    expect(displayValue('   ')).toBe('n/a');
    expect(displayValue('\t\n')).toBe('n/a');
  });

  it('returns custom fallback when provided', () => {
    expect(displayValue(null, '—')).toBe('—');
    expect(displayValue(undefined, 'missing')).toBe('missing');
  });

  it('returns fallback for empty string', () => {
    expect(displayValue('')).toBe('n/a');
  });
});

describe('hasUsableAnnotation', () => {
  it('returns false for null', () => {
    expect(hasUsableAnnotation(null)).toBe(false);
  });

  it('returns false for undefined', () => {
    expect(hasUsableAnnotation(undefined)).toBe(false);
  });

  it('returns false for whitespace-only strings', () => {
    expect(hasUsableAnnotation('   ')).toBe(false);
    expect(hasUsableAnnotation('\t')).toBe(false);
  });

  it('returns false for "unknown" (case insensitive)', () => {
    expect(hasUsableAnnotation('unknown')).toBe(false);
    expect(hasUsableAnnotation('Unknown')).toBe(false);
    expect(hasUsableAnnotation('UNKNOWN')).toBe(false);
  });

  it('returns true for valid strings', () => {
    expect(hasUsableAnnotation('resistant')).toBe(true);
    expect(hasUsableAnnotation('some annotation')).toBe(true);
  });

  it('returns true for string "0"', () => {
    expect(hasUsableAnnotation('0')).toBe(true);
  });
});

describe('resolvePhenotypeMode', () => {
  const rulesClinical = [
    { phenotype: '', clinical_phenotype: 'Resistant' },
    { phenotype: '', clinical_phenotype: 'Susceptible' },
  ];
  const rulesPhenotype = [
    { phenotype: 'Resistant', clinical_phenotype: '' },
    { phenotype: 'Susceptible', clinical_phenotype: '' },
  ];
  const rulesBoth = [
    { phenotype: 'Resistant', clinical_phenotype: 'Resistant' },
    { phenotype: 'Intermediate', clinical_phenotype: 'Susceptible' },
  ];
  const rulesEmpty = [
    { phenotype: '', clinical_phenotype: '' },
    { phenotype: 'unknown', clinical_phenotype: 'unknown' },
  ];

  it('returns clinical mode when requested and clinical data exists', () => {
    const result = resolvePhenotypeMode(rulesClinical, 'clinical');
    expect(result.activeMode).toBe('clinical');
    expect(result.requestedMode).toBe('clinical');
    expect(result.hasClinical).toBe(true);
  });

  it('returns phenotype mode when requested and phenotype data exists', () => {
    const result = resolvePhenotypeMode(rulesPhenotype, 'phenotype');
    expect(result.activeMode).toBe('phenotype');
    expect(result.requestedMode).toBe('phenotype');
    expect(result.hasPhenotype).toBe(true);
  });

  it('falls back to phenotype when clinical is requested but no clinical data', () => {
    const result = resolvePhenotypeMode(rulesPhenotype, 'clinical');
    expect(result.activeMode).toBe('phenotype');
    expect(result.requestedMode).toBe('clinical');
  });

  it('falls back to clinical when phenotype is requested but no phenotype data', () => {
    const result = resolvePhenotypeMode(rulesClinical, 'phenotype');
    expect(result.activeMode).toBe('clinical');
    expect(result.requestedMode).toBe('phenotype');
  });

  it('defaults to phenotype mode when both are empty', () => {
    const result = resolvePhenotypeMode(rulesEmpty, 'clinical');
    expect(result.activeMode).toBe('phenotype');
    expect(result.hasPhenotype).toBe(false);
    expect(result.hasClinical).toBe(false);
  });

  it('prefers phenotype as fallback when both exist and neither matches request', () => {
    const result = resolvePhenotypeMode(rulesBoth, 'some-other');
    expect(result.activeMode).toBe('phenotype');
  });
});

describe('getRuleAnnotationByMode', () => {
  it('returns clinical_phenotype for clinical mode', () => {
    const rule = { phenotype: 'Sensitive', clinical_phenotype: 'Resistant' };
    expect(getRuleAnnotationByMode(rule, 'clinical')).toBe('Resistant');
  });

  it('returns phenotype for phenotype mode', () => {
    const rule = { phenotype: 'Sensitive', clinical_phenotype: 'Resistant' };
    expect(getRuleAnnotationByMode(rule, 'phenotype')).toBe('Sensitive');
  });

  it('returns empty string when annotation is unknown', () => {
    const rule = { phenotype: 'unknown', clinical_phenotype: 'Unknown' };
    expect(getRuleAnnotationByMode(rule, 'phenotype')).toBe('');
    expect(getRuleAnnotationByMode(rule, 'clinical')).toBe('');
  });

  it('returns empty string for null/undefined annotations', () => {
    const rule = { phenotype: null, clinical_phenotype: undefined };
    expect(getRuleAnnotationByMode(rule, 'phenotype')).toBe('');
    expect(getRuleAnnotationByMode(rule, 'clinical')).toBe('');
  });

  it('trims whitespace from annotations', () => {
    const rule = { phenotype: '  Resistant  ', clinical_phenotype: '  Sensitive ' };
    expect(getRuleAnnotationByMode(rule, 'phenotype')).toBe('Resistant');
    expect(getRuleAnnotationByMode(rule, 'clinical')).toBe('Sensitive');
  });
});

describe('classificationTone', () => {
  it('returns "resistant" for resistant keywords', () => {
    expect(classificationTone('resistant')).toBe('resistant');
    expect(classificationTone('Resistant')).toBe('resistant');
    expect(classificationTone('decreased susceptibility')).toBe('resistant');
    expect(classificationTone('reduced susceptibility')).toBe('resistant');
    expect(classificationTone('non-susceptible')).toBe('resistant');
  });

  it('returns "intermediate" for intermediate keywords', () => {
    expect(classificationTone('intermediate')).toBe('intermediate');
    expect(classificationTone('Intermediate')).toBe('intermediate');
    expect(classificationTone('partial')).toBe('intermediate');
    expect(classificationTone('borderline susceptible')).toBe('intermediate');
  });

  it('returns "susceptible" for susceptible keywords', () => {
    expect(classificationTone('susceptible')).toBe('susceptible');
    expect(classificationTone('Susceptible')).toBe('susceptible');
    expect(classificationTone('sensitive')).toBe('susceptible');
    expect(classificationTone('wildtype')).toBe('susceptible');
  });

  it('returns "unknown" for empty or unrecognized labels', () => {
    expect(classificationTone('')).toBe('unknown');
    expect(classificationTone(null)).toBe('unknown');
    expect(classificationTone(undefined)).toBe('unknown');
    expect(classificationTone('something else')).toBe('unknown');
    expect(classificationTone('Wild type')).toBe('unknown');
  });

  it('detects resistant before intermediate due to priority ordering', () => {
    expect(classificationTone('partial resistance')).toBe('resistant');
  });
});

describe('hasTypedClassification', () => {
  it('returns true when resistant count > 0', () => {
    const counts = new Map([['resistant', 3], ['intermediate', 0], ['susceptible', 0]]);
    expect(hasTypedClassification(counts)).toBe(true);
  });

  it('returns true when intermediate count > 0', () => {
    const counts = new Map([['resistant', 0], ['intermediate', 1], ['susceptible', 0]]);
    expect(hasTypedClassification(counts)).toBe(true);
  });

  it('returns true when susceptible count > 0', () => {
    const counts = new Map([['resistant', 0], ['intermediate', 0], ['susceptible', 5]]);
    expect(hasTypedClassification(counts)).toBe(true);
  });

  it('returns false for empty Map', () => {
    expect(hasTypedClassification(new Map())).toBe(false);
  });

  it('returns false for Map with only unknown entries', () => {
    const counts = new Map([['unknown', 10]]);
    expect(hasTypedClassification(counts)).toBe(false);
  });
});

describe('limitPieSlices', () => {
  it('returns all entries when fewer than 6', () => {
    const entries = [['A', 10], ['B', 5], ['C', 3]];
    const result = limitPieSlices(entries);
    expect(result).toHaveLength(3);
    expect(result[0].label).toBe('A');
    expect(result[0].count).toBe(10);
  });

  it('returns all entries when exactly 6', () => {
    const entries = [['A', 10], ['B', 8], ['C', 6], ['D', 4], ['E', 3], ['F', 2]];
    const result = limitPieSlices(entries);
    expect(result).toHaveLength(6);
  });

  it('aggregates entries beyond 6 into "Other"', () => {
    const entries = [
      ['A', 10], ['B', 8], ['C', 6], ['D', 4], ['E', 3], ['F', 2], ['G', 1], ['H', 1],
    ];
    const result = limitPieSlices(entries);
    expect(result).toHaveLength(7);
    expect(result[6].label).toBe('Other');
    expect(result[6].count).toBe(2);
  });

  it('handles empty array', () => {
    const result = limitPieSlices([]);
    expect(result).toHaveLength(0);
  });

  it('assigns colors from PIE_COLORS', () => {
    const entries = [['X', 5], ['Y', 3]];
    const result = limitPieSlices(entries);
    expect(result[0].color).toBeDefined();
    expect(result[1].color).toBeDefined();
    expect(result[0].color).not.toBe(result[1].color);
  });
});

describe('dominantTone', () => {
  it('returns the tone with highest count', () => {
    const counts = new Map([['resistant', 5], ['intermediate', 2], ['susceptible', 1]]);
    expect(dominantTone(counts)).toBe('resistant');
  });

  it('breaks ties by priority (resistant > intermediate > susceptible > unknown)', () => {
    const counts = new Map([['susceptible', 3], ['intermediate', 3], ['resistant', 3]]);
    expect(dominantTone(counts)).toBe('resistant');
  });

  it('breaks tie between intermediate and susceptible', () => {
    const counts = new Map([['susceptible', 4], ['intermediate', 4]]);
    expect(dominantTone(counts)).toBe('intermediate');
  });

  it('returns "unknown" for empty Map', () => {
    expect(dominantTone(new Map())).toBe('unknown');
  });
});

describe('extractDoiTokens', () => {
  it('splits by semicolons', () => {
    expect(extractDoiTokens('doi1;doi2;doi3')).toEqual(['doi1', 'doi2', 'doi3']);
  });

  it('splits by commas', () => {
    expect(extractDoiTokens('doi1,doi2')).toEqual(['doi1', 'doi2']);
  });

  it('splits by newlines', () => {
    expect(extractDoiTokens('doi1\ndoi2')).toEqual(['doi1', 'doi2']);
  });

  it('trims whitespace from tokens', () => {
    expect(extractDoiTokens('  doi1  ;  doi2  ')).toEqual(['doi1', 'doi2']);
  });

  it('filters empty tokens', () => {
    expect(extractDoiTokens('doi1;;doi2')).toEqual(['doi1', 'doi2']);
    expect(extractDoiTokens('doi1,,doi2')).toEqual(['doi1', 'doi2']);
  });

  it('returns empty array for null', () => {
    expect(extractDoiTokens(null)).toEqual([]);
  });

  it('returns empty array for undefined', () => {
    expect(extractDoiTokens(undefined)).toEqual([]);
  });

  it('returns empty array for empty string', () => {
    expect(extractDoiTokens('')).toEqual([]);
  });

  it('returns empty array for whitespace-only string', () => {
    expect(extractDoiTokens('   ')).toEqual([]);
  });
});

describe('buildDrugGroupLookup', () => {
  it('builds lookup from drug_groups object', () => {
    const plotMeta = { drug_groups: { Acyclovir: 'Nucleoside', Foscarnet: 'Pyrophosphate' } };
    const lookup = buildDrugGroupLookup(plotMeta);
    expect(lookup.get('acyclovir')).toBe('Nucleoside');
    expect(lookup.get('foscarnet')).toBe('Pyrophosphate');
    expect(lookup.size).toBe(2);
  });

  it('returns empty Map for missing plotMeta', () => {
    const lookup = buildDrugGroupLookup(null);
    expect(lookup.size).toBe(0);
  });

  it('returns empty Map for missing drug_groups', () => {
    const lookup = buildDrugGroupLookup({});
    expect(lookup.size).toBe(0);
  });

  it('skips entries with empty drug name or group', () => {
    const plotMeta = { drug_groups: { '': 'Group', Drug: '' } };
    const lookup = buildDrugGroupLookup(plotMeta);
    expect(lookup.size).toBe(0);
  });
});

describe('formatDrugNameWithAlias', () => {
  it('appends alias in parentheses when alias exists', () => {
    const aliasLookup = new Map([['acyclovir', 'ACV']]);
    expect(formatDrugNameWithAlias('Acyclovir', aliasLookup)).toBe('Acyclovir (ACV)');
  });

  it('returns drug name alone when no alias matches', () => {
    const aliasLookup = new Map();
    expect(formatDrugNameWithAlias('Acyclovir', aliasLookup)).toBe('Acyclovir');
  });

  it('uses fallback for empty name', () => {
    const aliasLookup = new Map();
    expect(formatDrugNameWithAlias(null, aliasLookup)).toBe('Unspecified drug');
  });

  it('looks up alias case-insensitively', () => {
    const aliasLookup = new Map([['ganciclovir', 'GCV']]);
    expect(formatDrugNameWithAlias('Ganciclovir', aliasLookup)).toBe('Ganciclovir (GCV)');
  });
});
