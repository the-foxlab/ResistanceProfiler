import { describe, it, expect } from 'vitest';
import { buildApiUrl, formatUserError } from '../useDashboardLogic';

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
  });
});
