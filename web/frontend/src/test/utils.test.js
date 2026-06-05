import { describe, it, expect } from 'vitest';
import { buildApiUrl, formatUserError } from '../api';
import { isPopulated, buildDrugAliasLookup } from '../utils';

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
