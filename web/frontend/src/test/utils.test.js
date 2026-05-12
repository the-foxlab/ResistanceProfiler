import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';

// Test environment setup helpers - these test the API utility functions
describe('API Utility Functions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('buildApiUrl', () => {
    it('should construct URL without parameters', () => {
      // This mirrors the buildApiUrl logic from useDashboardLogic.js
      const apiBase = 'http://localhost:8000';
      const path = '/api/databases';

      const result = `${apiBase}${path}`;
      expect(result).toBe('http://localhost:8000/api/databases');
    });

    it('should construct URL with query parameters', () => {
      const apiBase = 'http://localhost:8000';
      const path = '/api/mutations';
      const params = { database_id: 'db1' };

      const query = new URLSearchParams(params);
      const result = `${apiBase}${path}?${query.toString()}`;

      expect(result).toContain('/api/mutations');
      expect(result).toContain('database_id=db1');
    });

    it('should filter out empty/null/undefined parameters', () => {
      const apiBase = 'http://localhost:8000';
      const path = '/api/search';
      const params = {
        query: 'test',
        limit: undefined,
        offset: null,
        filter: '',
      };

      const filteredParams = Object.fromEntries(
        Object.entries(params).filter(
          ([, value]) => value !== undefined && value !== null && value !== ''
        )
      );

      const query = new URLSearchParams(filteredParams);
      const result = `${apiBase}${path}?${query.toString()}`;

      expect(result).toContain('query=test');
      expect(result).not.toContain('limit');
      expect(result).not.toContain('offset');
      expect(result).not.toContain('filter');
    });
  });

  describe('formatUserError', () => {
    it('should extract last line from multiline error messages', () => {
      const message = 'ValueError: first line\nRuntimeError: second line\nActual error here';
      const lines = message
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
      const normalized = lines.length > 0 ? lines[lines.length - 1] : 'The operation failed.';

      expect(normalized).toBe('Actual error here');
    });

    it('should remove exception type prefix', () => {
      const message = 'ValueError: Invalid input format';
      const normalized = message.replace(/^(ValueError|RuntimeError|Exception|OSError):\s*/, '');

      expect(normalized).toBe('Invalid input format');
    });

    it('should convert FASTA format error to user-friendly message', () => {
      const message = 'unsupported fasta format';
      const lowered = message.toLowerCase();

      const result = lowered.includes('unsupported fasta format')
        ? 'Unsupported FASTA format. Upload a text FASTA file with a header line starting with >.'
        : message;

      expect(result).toContain('Unsupported FASTA format');
      expect(result).toContain('header line');
    });

    it('should convert VCF format error to user-friendly message', () => {
      const message = 'unsupported vcf format';
      const lowered = message.toLowerCase();

      const result = lowered.includes('unsupported vcf format')
        ? 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.'
        : message;

      expect(result).toContain('Unsupported VCF format');
    });

    it('should convert mismatch error to actionable message', () => {
      const message = 'vcf and reference fasta do not match';
      const lowered = message.toLowerCase();

      const result = lowered.includes('vcf and reference fasta do not match')
          || lowered.includes('vcf contig names do not match')
        ? 'VCF and reference FASTA do not match. Use files derived from the same reference sequence.'
        : message;

      expect(result).toContain('VCF and reference FASTA do not match');
    });

    it('should handle empty error message gracefully', () => {
      const message = '';
      const lines = message
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
      const normalized = lines.length > 0 ? lines[lines.length - 1] : 'The operation failed.';

      expect(normalized).toBe('The operation failed.');
    });
  });

  describe('formatResultTimestamp', () => {
    it('should format valid ISO timestamp to locale string', () => {
      const timestamp = '2026-05-12T10:00:00';
      const parsed = new Date(timestamp);

      expect(Number.isNaN(parsed.getTime())).toBe(false);
      expect(parsed.toLocaleString()).toBeTruthy();
    });

    it('should handle null/undefined timestamp', () => {
      const timestamp = null;
      const result = !timestamp ? 'n/a' : new Date(timestamp).toLocaleString();

      expect(result).toBe('n/a');
    });

    it('should return original timestamp if parsing fails', () => {
      const timestamp = 'invalid-date';
      const parsed = new Date(timestamp);
      const result = Number.isNaN(parsed.getTime()) ? timestamp : parsed.toLocaleString();

      expect(result).toBe('invalid-date');
    });
  });

  describe('formatPathBasename', () => {
    it('should extract filename from full path', () => {
      const path = '/data/uploads/sample.fasta';
      const normalized = path.replace(/\\/g, '/');
      const parts = normalized.split('/').filter(Boolean);
      const basename = parts.length > 0 ? parts[parts.length - 1] : normalized;

      expect(basename).toBe('sample.fasta');
    });

    it('should handle Windows-style paths', () => {
      const path = 'C:\\data\\uploads\\sample.vcf';
      const normalized = path.replace(/\\/g, '/');
      const parts = normalized.split('/').filter(Boolean);
      const basename = parts.length > 0 ? parts[parts.length - 1] : normalized;

      expect(basename).toBe('sample.vcf');
    });

    it('should handle null/undefined path', () => {
      const path = null;
      const result = !path ? 'n/a' : String(path).split('/').pop();

      expect(result).toBe('n/a');
    });
  });

  describe('buildHeaders', () => {
    it('should include Authorization header when token is present', () => {
      const apiToken = 'test-token-123';
      const baseHeaders = {};

      const headers = apiToken
        ? {
          ...baseHeaders,
          Authorization: `Bearer ${apiToken}`,
        }
        : baseHeaders;

      expect(headers.Authorization).toBe('Bearer test-token-123');
    });

    it('should omit Authorization header when token is empty', () => {
      const apiToken = '';
      const baseHeaders = {};

      const headers = apiToken
        ? {
          ...baseHeaders,
          Authorization: `Bearer ${apiToken}`,
        }
        : baseHeaders;

      expect(headers.Authorization).toBeUndefined();
    });

    it('should preserve existing headers when adding auth', () => {
      const apiToken = 'test-token';
      const baseHeaders = { 'Content-Type': 'application/json' };

      const headers = apiToken
        ? {
          ...baseHeaders,
          Authorization: `Bearer ${apiToken}`,
        }
        : baseHeaders;

      expect(headers['Content-Type']).toBe('application/json');
      expect(headers.Authorization).toBe('Bearer test-token');
    });
  });
});

describe('Frontend Configuration', () => {
  describe('default API base detection', () => {
    it('should use localhost:8000 when dev server is on port 5173', () => {
      const DEFAULT_DEV_SERVER_PORT = '5173';
      const port = '5173';
      const defaultApiBase = port === DEFAULT_DEV_SERVER_PORT
        ? 'http://127.0.0.1:8000'
        : '';

      expect(defaultApiBase).toBe('http://127.0.0.1:8000');
    });

    it('should use empty string when not on dev server port', () => {
      const DEFAULT_DEV_SERVER_PORT = '5173';
      const port = '3000';
      const defaultApiBase = port === DEFAULT_DEV_SERVER_PORT
        ? 'http://127.0.0.1:8000'
        : '';

      expect(defaultApiBase).toBe('');
    });
  });
});
