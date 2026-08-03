import { describe, it, expect } from 'vitest';
import { buildApiUrl, formatUserError } from '../api';
import { isPopulated, buildDrugAliasLookup, groupDrugThresholds, formatAlgorithmThresholds } from '../utils';

describe('API Utility Functions', () => {
  describe('buildApiUrl', () => {
    it('should construct URL without parameters', () => {
      const result = buildApiUrl('/api/databases');
      expect(result).toMatch(/\/api\/databases$/);
    });

    it('should construct URL with query parameters', () => {
      const result = buildApiUrl('/api/mutations', { database_id: 'db1' });
      expect(result).toContain('/api/mutations');
      expect(result).toContain('database_id=db1');
    });

    it('should filter out empty/null/undefined parameters', () => {
      const result = buildApiUrl('/api/search', {
        query: 'test',
        limit: undefined,
        offset: null,
        filter: '',
      });

      expect(result).toContain('query=test');
      expect(result).not.toContain('limit');
      expect(result).not.toContain('offset');
      expect(result).not.toContain('filter');
    });
  });

  describe('formatUserError', () => {
    it('should extract last line from multiline error messages', () => {
      const message = 'ValueError: first line\nRuntimeError: second line\nActual error here';
      const result = formatUserError(message);
      expect(result).toBe('Actual error here');
    });

    it('should remove exception type prefix', () => {
      const result = formatUserError('ValueError: Invalid input format');
      expect(result).toBe('Invalid input format');
    });

    it('should convert FASTA format error to user-friendly message', () => {
      const result = formatUserError('unsupported fasta format');
      expect(result).toContain('Unsupported FASTA format');
      expect(result).toContain('header line');
    });

    it('should convert VCF format error to user-friendly message', () => {
      const result = formatUserError('unsupported vcf format');
      expect(result).toContain('Unsupported VCF format');
    });

    it('should convert mismatch error to actionable message', () => {
      const result = formatUserError('vcf and reference fasta do not match');
      expect(result).toContain('VCF and reference FASTA do not match');
    });

    it('should handle empty error message gracefully', () => {
      const result = formatUserError('');
      expect(result).toBe('The operation failed.');
    });

    it('should convert no CDS matches error to user-friendly message', () => {
      const result = formatUserError('ValueError: No CDS matches found in tmp123.fa');
      expect(result).toBe('No matches to references in the database found.');
    });
  });
});

describe('isPopulated', () => {
  it('returns false for null', () => {
    expect(isPopulated(null)).toBe(false);
  });

  it('returns false for undefined', () => {
    expect(isPopulated(undefined)).toBe(false);
  });

  it('returns false for empty string', () => {
    expect(isPopulated('')).toBe(false);
  });

  it('returns false for whitespace-only string', () => {
    expect(isPopulated('   ')).toBe(false);
    expect(isPopulated('\t\n')).toBe(false);
  });

  it('returns true for non-empty string', () => {
    expect(isPopulated('hello')).toBe(true);
  });

  it('returns true for 0', () => {
    expect(isPopulated(0)).toBe(true);
  });

  it('returns true for false', () => {
    expect(isPopulated(false)).toBe(true);
  });
});

describe('buildDrugAliasLookup', () => {
  it('builds lookup from drug_aliases', () => {
    const plotMeta = { drug_aliases: { Acyclovir: 'ACV', Ganciclovir: 'GCV' } };
    const lookup = buildDrugAliasLookup(plotMeta);
    expect(lookup.get('acyclovir')).toBe('ACV');
    expect(lookup.get('ganciclovir')).toBe('GCV');
    expect(lookup.size).toBe(2);
  });

  it('handles empty aliases object', () => {
    const plotMeta = { drug_aliases: {} };
    const lookup = buildDrugAliasLookup(plotMeta);
    expect(lookup.size).toBe(0);
  });

  it('returns empty Map for null plotMeta', () => {
    const lookup = buildDrugAliasLookup(null);
    expect(lookup.size).toBe(0);
  });

  it('returns empty Map for missing drug_aliases', () => {
    const lookup = buildDrugAliasLookup({});
    expect(lookup.size).toBe(0);
  });

  it('skips entries with empty drug name or alias', () => {
    const plotMeta = { drug_aliases: { '': 'Alias', Drug: '' } };
    const lookup = buildDrugAliasLookup(plotMeta);
    expect(lookup.size).toBe(0);
  });
});

describe('groupDrugThresholds', () => {
  it('returns empty array for null input', () => {
    expect(groupDrugThresholds(null)).toEqual([]);
  });

  it('returns empty array for empty array', () => {
    expect(groupDrugThresholds([])).toEqual([]);
  });

  it('groups entries sharing the same reference and thresholds', () => {
    const overrides = [
      { reference: 'ref1', drug: 'ACV', thresholds: { resistant: 2, intermediate: 1 } },
      { reference: 'ref1', drug: 'PCV', thresholds: { resistant: 2, intermediate: 1 } },
    ];
    const result = groupDrugThresholds(overrides);
    expect(result).toHaveLength(1);
    expect(result[0].reference).toBe('ref1');
    expect(result[0].drugs).toEqual(['ACV', 'PCV']);
    expect(result[0].thresholds).toEqual({ resistant: 2, intermediate: 1 });
  });

  it('separates groups with different thresholds', () => {
    const overrides = [
      { reference: 'ref1', drug: 'ACV', thresholds: { resistant: 2 } },
      { reference: 'ref1', drug: 'PCV', thresholds: { resistant: 3 } },
    ];
    const result = groupDrugThresholds(overrides);
    expect(result).toHaveLength(2);
  });

  it('separates groups with different references', () => {
    const overrides = [
      { reference: 'ref1', drug: 'ACV', thresholds: { resistant: 2 } },
      { reference: 'ref2', drug: 'ACV', thresholds: { resistant: 2 } },
    ];
    const result = groupDrugThresholds(overrides);
    expect(result).toHaveLength(2);
    const refs = result.map((r) => r.reference).sort();
    expect(refs).toEqual(['ref1', 'ref2']);
  });

  it('uses "(all)" for entries without a reference', () => {
    const overrides = [
      { drug: 'ACV', thresholds: { resistant: 2 } },
      { drug: 'PCV', thresholds: { resistant: 2 } },
    ];
    const result = groupDrugThresholds(overrides);
    expect(result).toHaveLength(1);
    expect(result[0].reference).toBe('(all)');
    expect(result[0].drugs).toEqual(['ACV', 'PCV']);
  });

  it('sorts drugs within a group', () => {
    const overrides = [
      { reference: 'ref1', drug: 'PCV', thresholds: { resistant: 2 } },
      { reference: 'ref1', drug: 'ACV', thresholds: { resistant: 2 } },
    ];
    const result = groupDrugThresholds(overrides);
    expect(result[0].drugs).toEqual(['ACV', 'PCV']);
  });
});

describe('formatAlgorithmThresholds', () => {
  it('returns "Not configured" for null', () => {
    expect(formatAlgorithmThresholds(null)).toBe('Not configured');
  });

  it('returns "Not configured" for empty object', () => {
    expect(formatAlgorithmThresholds({})).toBe('Not configured');
  });

  it('formats flat thresholds sorted by key', () => {
    expect(formatAlgorithmThresholds({ resistant: 2, intermediate: 1 })).toBe('intermediate=1; resistant=2');
  });

  it('formats nested thresholds', () => {
    const result = formatAlgorithmThresholds({ ACV: { intermediate: 3.0, resistant: 10.0 } });
    expect(result).toBe('ACV: intermediate=3, resistant=10');
  });
});
