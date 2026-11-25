import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  buildApiUrl,
  chunkIds,
  downloadArtifactBundle,
  formatPathStem,
  formatUserError,
  setArtifactBundleLimit,
} from '../api';
import {
  isPopulated,
  buildDrugAliasLookup,
  groupDrugThresholds,
  labelToRank,
  orderedThresholdLabels,
  cleanTsvCell,
  sanitizeTsvFormulaCell,
  downloadTsv,
  triggerBlobDownload,
  sanitizeHttpUrl,
  sanitizeEmailAddress,
} from '../utils';

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

  describe('formatPathStem', () => {
    it('strips .vcf.gz case-insensitively while preserving multi-dot stems like .results.json', () => {
      expect(formatPathStem('sample1.vcf.gz')).toBe('sample1');
      expect(formatPathStem('SAMPLE1.VCF.GZ')).toBe('SAMPLE1');
      expect(formatPathStem('/uploads/cohort/sample1.vcf.gz')).toBe('sample1');
      expect(formatPathStem('sample1.vcf')).toBe('sample1');
      expect(formatPathStem('sample1.bam')).toBe('sample1');
      expect(formatPathStem('sample1.results.json')).toBe('sample1.results');
    });
  });
});

describe('artifact bundle chunking', () => {
  let downloadedNames;

  beforeEach(() => {
    downloadedNames = [];
    setArtifactBundleLimit(2);
    global.fetch = vi.fn(async () => ({
      ok: true,
      blob: async () => new Blob(['zip-bytes']),
    }));
    global.URL.createObjectURL = vi.fn(() => 'blob:mock');
    global.URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
      downloadedNames.push(this.download);
    });
  });

  afterEach(() => {
    setArtifactBundleLimit(100);
    vi.restoreAllMocks();
  });

  it('splits artifact ids into chunks at the configured limit', () => {
    expect(chunkIds(['a', 'b', 'c', 'd', 'e'], 2)).toEqual([
      ['a', 'b'],
      ['c', 'd'],
      ['e'],
    ]);
  });

  it('downloads one numbered archive per chunk when the selection exceeds the limit', async () => {
    await downloadArtifactBundle(
      ['a', 'b', 'c', 'd', 'e'],
      'respro-session-artifacts.zip',
    );

    expect(global.fetch).toHaveBeenCalledTimes(3);
    expect(downloadedNames).toEqual([
      'respro-session-artifacts-part1of3.zip',
      'respro-session-artifacts-part2of3.zip',
      'respro-session-artifacts-part3of3.zip',
    ]);
    expect(global.fetch.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringMatching(/\/api\/artifact-bundle\?part=1$/),
      expect.stringMatching(/\/api\/artifact-bundle\?part=2$/),
      expect.stringMatching(/\/api\/artifact-bundle\?part=3$/),
    ]);
    const sentBodies = global.fetch.mock.calls.map(([, options]) => JSON.parse(options.body));
    expect(sentBodies).toEqual([
      { artifact_ids: ['a', 'b'] },
      { artifact_ids: ['c', 'd'] },
      { artifact_ids: ['e'] },
    ]);
  });

  it('keeps the plain archive name when everything fits in one request', async () => {
    await downloadArtifactBundle(['a', 'b'], 'respro-session-artifacts.zip');

    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(downloadedNames).toEqual(['respro-session-artifacts.zip']);
    expect(String(global.fetch.mock.calls[0][0])).toMatch(/\/api\/artifact-bundle$/);
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

describe('labelToRank', () => {
  it('resolves canonical labels to their ranks', () => {
    expect(labelToRank('susceptible')).toBe(1);
    expect(labelToRank('intermediate')).toBe(4);
    expect(labelToRank('resistant')).toBe(5);
  });

  it('resolves synonyms and multi-word labels', () => {
    expect(labelToRank('Susceptible')).toBe(1);
    expect(labelToRank('  high-level resistance ')).toBe(5);
    expect(labelToRank('potential low-level resistance')).toBe(2);
    expect(labelToRank('low-level resistance')).toBe(3);
    expect(labelToRank('intermediate resistance')).toBe(4);
  });

  it('resolves sentinels', () => {
    expect(labelToRank('unknown')).toBe(0);
    expect(labelToRank('contradictory')).toBe(-1);
  });

  it('returns null for unrecognized or missing labels', () => {
    expect(labelToRank('foo')).toBeNull();
    expect(labelToRank(null)).toBeNull();
    expect(labelToRank(undefined)).toBeNull();
    expect(labelToRank('')).toBeNull();
  });
});

describe('orderedThresholdLabels', () => {
  it('orders labels weakest → strongest by rank', () => {
    const thresholds = {
      resistant: 60,
      susceptible: 0,
      'low-level resistance': 15,
      'high-level resistance': 60,
      'potential low-level resistance': 10,
    };
    expect(orderedThresholdLabels(thresholds)).toEqual([
      'susceptible',
      'potential low-level resistance',
      'low-level resistance',
      'resistant',
      'high-level resistance',
    ]);
  });

  it('handles a simple two-tier config', () => {
    const thresholds = { resistant: 10, intermediate: 3 };
    expect(orderedThresholdLabels(thresholds)).toEqual(['intermediate', 'resistant']);
  });

  it('excludes sentinels from the severity ordering', () => {
    const thresholds = { susceptible: 0, resistant: 10, unknown: -1 };
    expect(orderedThresholdLabels(thresholds)).toEqual(['susceptible', 'resistant']);
  });

  it('excludes unrecognized labels (not in the rank vocabulary)', () => {
    const thresholds = { resistant: 10, 'custom-tier': 5, susceptible: 0 };
    expect(orderedThresholdLabels(thresholds)).toEqual(['susceptible', 'resistant']);
  });

  it('returns empty array for null or non-object', () => {
    expect(orderedThresholdLabels(null)).toEqual([]);
    expect(orderedThresholdLabels([])).toEqual([]);
  });
});

describe('TSV cleaning and formula neutralization', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps cleanTsvCell pure for HTML rendering without prefixing formula characters', () => {
    expect(cleanTsvCell('=SUM(A1)')).toBe('=SUM(A1)');
    expect(cleanTsvCell('-1')).toBe('-1');
    expect(cleanTsvCell('a\tb\r\nc\nd')).toBe('a b c d');
  });

  it('neutralizes leading formula triggers in sanitizeTsvFormulaCell', () => {
    expect(sanitizeTsvFormulaCell('=CMD()')).toBe("'=CMD()");
    expect(sanitizeTsvFormulaCell('+1+1')).toBe("'+1+1");
    expect(sanitizeTsvFormulaCell('-1+1')).toBe("'-1+1");
    expect(sanitizeTsvFormulaCell('-1')).toBe("'-1");
    expect(sanitizeTsvFormulaCell('@SUM(A1)')).toBe("'@SUM(A1)");
    expect(sanitizeTsvFormulaCell('\t=1+1')).toBe("' =1+1");
    expect(sanitizeTsvFormulaCell('\r=1+1')).toBe("' =1+1");
    expect(sanitizeTsvFormulaCell('  =1+1')).toBe("'  =1+1");
    expect(sanitizeTsvFormulaCell('\n=1+1')).toBe("' =1+1");

    expect(sanitizeTsvFormulaCell('UL23')).toBe('UL23');
    expect(sanitizeTsvFormulaCell('1.5')).toBe('1.5');
    expect(sanitizeTsvFormulaCell('a=b+c-d@e')).toBe('a=b+c-d@e');
    expect(sanitizeTsvFormulaCell(null)).toBe('');
    expect(sanitizeTsvFormulaCell(undefined)).toBe('');
  });

  it('sanitizes headers and rows when exporting TSV via downloadTsv', async () => {
    const downloadedBlobs = [];
    const origCreateObjectURL = global.URL.createObjectURL;
    const origRevokeObjectURL = global.URL.revokeObjectURL;
    global.URL.createObjectURL = vi.fn((blob) => {
      downloadedBlobs.push(blob);
      return 'blob:tsv';
    });
    global.URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    try {
      downloadTsv(
        ['Feature', '=HEADER'],
        [['=1+1', '+cmd', '-5', '@SUM', '\t=evil', 'normal']],
        'export.tsv',
      );
      expect(downloadedBlobs).toHaveLength(1);
      const text = await downloadedBlobs[0].text();
      expect(text).toBe("Feature\t'=HEADER\n'=1+1\t'+cmd\t'-5\t'@SUM\t' =evil\tnormal\n");
    } finally {
      global.URL.createObjectURL = origCreateObjectURL;
      global.URL.revokeObjectURL = origRevokeObjectURL;
    }
  });
});

describe('sanitizeHttpUrl and sanitizeEmailAddress', () => {
  it('accepts valid http/https URLs and rejects dangerous or relative inputs', () => {
    expect(sanitizeHttpUrl('https://example.org/path?a=1')).toBe('https://example.org/path?a=1');
    expect(sanitizeHttpUrl('  http://example.org  ')).toBe('http://example.org');
    expect(sanitizeHttpUrl('javascript:alert(1)')).toBeNull();
    expect(sanitizeHttpUrl('data:text/html,<script>')).toBeNull();
    expect(sanitizeHttpUrl('vbscript:msgbox(1)')).toBeNull();
    expect(sanitizeHttpUrl('file:///etc/passwd')).toBeNull();
    expect(sanitizeHttpUrl('//evil.example')).toBeNull();
    expect(sanitizeHttpUrl('http:example.com')).toBeNull();
    expect(sanitizeHttpUrl('https://user:pass@evil.com')).toBeNull();
    expect(sanitizeHttpUrl('https://www.who.int@evil.com/path')).toBeNull();
    expect(sanitizeHttpUrl('https://user@example.org')).toBeNull();
    expect(sanitizeHttpUrl('https://:pass@example.org')).toBeNull();
    expect(sanitizeHttpUrl('/relative')).toBeNull();
    expect(sanitizeHttpUrl(null)).toBeNull();
  });

  it('accepts bare emails and rejects mailto/query/header injection', () => {
    expect(sanitizeEmailAddress('user.name+tag@sub.example.org')).toBe('user.name+tag@sub.example.org');
    expect(sanitizeEmailAddress('user@example.org?bcc=evil@x.org')).toBeNull();
    expect(sanitizeEmailAddress('user@example.org#frag')).toBeNull();
    expect(sanitizeEmailAddress('user@example.org\nbcc:x')).toBeNull();
    expect(sanitizeEmailAddress('mailto:user@example.org')).toBeNull();
    expect(sanitizeEmailAddress('Alice <user@example.org>')).toBeNull();
    expect(sanitizeEmailAddress('.user@example.org')).toBeNull();
    expect(sanitizeEmailAddress('user..name@example.org')).toBeNull();
    expect(sanitizeEmailAddress(null)).toBeNull();
  });
});

describe('triggerBlobDownload deferred revocation (WA-050)', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('defers URL.revokeObjectURL by 1000ms instead of revoking synchronously on click', () => {
    vi.useFakeTimers();
    const origCreate = global.URL.createObjectURL;
    const origRevoke = global.URL.revokeObjectURL;
    global.URL.createObjectURL = vi.fn(() => 'blob:deferred-1');
    const revokeMock = vi.fn();
    global.URL.revokeObjectURL = revokeMock;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    try {
      triggerBlobDownload(new Blob(['abc'], { type: 'text/plain' }), 'sample.txt');

      expect(clickSpy).toHaveBeenCalledTimes(1);
      expect(revokeMock).not.toHaveBeenCalled();

      vi.advanceTimersByTime(999);
      expect(revokeMock).not.toHaveBeenCalled();

      vi.advanceTimersByTime(1);
      expect(revokeMock).toHaveBeenCalledWith('blob:deferred-1');
    } finally {
      global.URL.createObjectURL = origCreate;
      global.URL.revokeObjectURL = origRevoke;
    }
  });
});
