import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError,
  apiDelete,
  apiGet,
  apiPost,
  apiPostRaw,
  apiUpload,
  compareDatabases,
  downloadArtifactBundle,
  fetchReferenceAccessions,
  fetchSharedReferences,
  isAbortError,
  parseRetryAfter,
  setArtifactBundleLimit,
} from './api';

describe('parseRetryAfter', () => {
  it('parses non-negative integer seconds including zero and whitespace', () => {
    expect(parseRetryAfter('0')).toBe(0);
    expect(parseRetryAfter('5')).toBe(5);
    expect(parseRetryAfter('  120  ')).toBe(120);
  });

  it('rejects negative, decimal, signed, and malformed strings before Date.parse', () => {
    expect(parseRetryAfter('-5')).toBeNull();
    expect(parseRetryAfter('1.5')).toBeNull();
    expect(parseRetryAfter('.5')).toBeNull();
    expect(parseRetryAfter('1.')).toBeNull();
    expect(parseRetryAfter('+.5')).toBeNull();
    expect(parseRetryAfter('-.5')).toBeNull();
    expect(parseRetryAfter('+5')).toBeNull();
    expect(parseRetryAfter('-0')).toBeNull();
    expect(parseRetryAfter('12abc')).toBeNull();
    expect(parseRetryAfter('not-a-date')).toBeNull();
    expect(parseRetryAfter('')).toBeNull();
    expect(parseRetryAfter('   ')).toBeNull();
    expect(parseRetryAfter(null)).toBeNull();
    expect(parseRetryAfter(undefined)).toBeNull();
  });

  it('parses HTTP-date values relative to nowMs', () => {
    const nowMs = Date.parse('Wed, 21 Oct 2015 07:28:00 GMT');
    expect(parseRetryAfter('Wed, 21 Oct 2015 07:28:15 GMT', nowMs)).toBe(15);
    expect(parseRetryAfter('Wed, 21 Oct 2015 07:28:00 GMT', nowMs)).toBe(0);
    expect(parseRetryAfter('Wed, 21 Oct 2015 07:27:50 GMT', nowMs)).toBe(0);
    expect(parseRetryAfter('Wed, 21 Oct 2015 07:28:01 GMT', nowMs - 200)).toBe(2);
  });
});

describe('ApiError and HTTP helpers', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:mock'),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => {
    setArtifactBundleLimit(100);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('throws ApiError with status, parsed Retry-After, and formatted message', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 429,
      headers: new Headers({ 'Retry-After': '42' }),
      json: async () => ({ detail: 'ValueError: Too many requests' }),
    });

    await expect(apiGet('/api/jobs/j1')).rejects.toMatchObject({
      name: 'ApiError',
      status: 429,
      retryAfterSeconds: 42,
      message: 'Too many requests',
    });
  });

  it('handles null JSON payload, plain-object headers, and missing headers safely', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      headers: { 'retry-after': '9' },
      json: async () => null,
    });
    await expect(apiPost('/api/test', { a: 1 })).rejects.toMatchObject({
      name: 'ApiError',
      status: 503,
      retryAfterSeconds: 9,
      message: 'The request failed.',
    });

    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error('bad json');
      },
    });
    await expect(apiDelete('/api/jobs/j1')).rejects.toMatchObject({
      name: 'ApiError',
      status: 500,
      retryAfterSeconds: null,
      message: 'The request failed.',
    });
  });

  it('supports query params and { signal } in apiGet without putting signal into query string', async () => {
    const controller = new AbortController();
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true }),
    });

    await apiGet('/api/mutations', { database_id: 'db1' });
    expect(global.fetch).toHaveBeenLastCalledWith('/api/mutations?database_id=db1', {});

    await apiGet('/api/jobs/j1', { signal: controller.signal });
    expect(global.fetch).toHaveBeenLastCalledWith('/api/jobs/j1', { signal: controller.signal });

    await apiGet('/api/jobs/j1', { signal: undefined });
    expect(global.fetch).toHaveBeenLastCalledWith('/api/jobs/j1', {});

    await apiGet('/api/x', { signal: 'foo' });
    expect(global.fetch).toHaveBeenLastCalledWith('/api/x?signal=foo', {});

    await apiGet('/api/mutations', { database_id: 'db2' }, { signal: controller.signal });
    expect(global.fetch).toHaveBeenLastCalledWith(
      '/api/mutations?database_id=db2',
      { signal: controller.signal },
    );
  });

  it('forwards signal in apiPost, apiPostRaw, apiDelete, and database wrappers when provided', async () => {
    const controller = new AbortController();
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ data: { items: [] } }),
    });

    await apiPost('/api/compare', { a: 1 }, { signal: controller.signal });
    expect(global.fetch).toHaveBeenLastCalledWith('/api/compare', expect.objectContaining({
      method: 'POST',
      signal: controller.signal,
    }));

    await apiPostRaw('/api/profile/batch/vcf', { vcf_ids: [] }, { signal: controller.signal });
    expect(global.fetch).toHaveBeenLastCalledWith('/api/profile/batch/vcf', expect.objectContaining({
      method: 'POST',
      signal: controller.signal,
    }));

    await apiDelete('/api/jobs/j1', { signal: controller.signal });
    expect(global.fetch).toHaveBeenLastCalledWith('/api/jobs/j1', {
      method: 'DELETE',
      signal: controller.signal,
    });

    await fetchSharedReferences(['db1', 'db2'], { signal: controller.signal });
    expect(global.fetch).toHaveBeenLastCalledWith(
      '/api/databases/shared-references?ids=db1%2Cdb2',
      { signal: controller.signal },
    );

    await fetchReferenceAccessions(['db1', 'db2'], { signal: controller.signal });
    expect(global.fetch).toHaveBeenLastCalledWith(
      '/api/databases/reference-accessions?ids=db1%2Cdb2',
      { signal: controller.signal },
    );

    await compareDatabases(['db1', 'db2'], 'acc1', { signal: controller.signal });
    expect(global.fetch).toHaveBeenLastCalledWith(
      '/api/databases/compare',
      expect.objectContaining({ method: 'POST', signal: controller.signal }),
    );
  });

  it('rejects immediately with AbortError when signal is already aborted', async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(apiGet('/api/jobs/j1', { signal: controller.signal })).rejects.toSatisfy(isAbortError);
    await expect(apiPostRaw('/api/profile/batch/vcf', {}, { signal: controller.signal })).rejects.toSatisfy(isAbortError);
    await expect(apiDelete('/api/jobs/j1', { signal: controller.signal })).rejects.toSatisfy(isAbortError);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('stops downloadArtifactBundle between chunks when signal aborts mid-download', async () => {
    setArtifactBundleLimit(2);
    const controller = new AbortController();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      controller.abort();
    });
    global.fetch.mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['zip']),
    });

    await expect(
      downloadArtifactBundle(['a', 'b', 'c'], 'bundle.zip', { signal: controller.signal }),
    ).rejects.toSatisfy(isAbortError);
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('rejects downloadArtifactBundle with AbortError on empty artifactIds when signal is already aborted', async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(
      downloadArtifactBundle([], 'bundle.zip', { signal: controller.signal }),
    ).rejects.toSatisfy(isAbortError);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('preserves ApiError status/Retry-After and AbortSignal in apiUpload', async () => {
    const instances = [];
    vi.stubGlobal('XMLHttpRequest', class {
      constructor() {
        this.open = vi.fn();
        this.send = vi.fn();
        this.abort = vi.fn(() => this.onabort?.({}));
        this.upload = { onprogress: null };
        this.getResponseHeader = vi.fn(() => '12');
        this.status = 429;
        this.responseText = JSON.stringify({ detail: 'Rate limit exceeded' });
        instances.push(this);
      }
    });

    const file = new File(['data'], 'sample.vcf');
    const p1 = apiUpload('/api/upload/vcf', file);
    instances[0].onload();
    await expect(p1).rejects.toMatchObject({
      name: 'ApiError',
      status: 429,
      retryAfterSeconds: 12,
      message: 'Rate limit exceeded',
    });

    const controller = new AbortController();
    const p2 = apiUpload('/api/upload/vcf', file, null, null, { signal: controller.signal });
    controller.abort();
    await expect(p2).rejects.toSatisfy(isAbortError);
    expect(isAbortError(new ApiError('fail', { status: 500 }))).toBe(false);
    expect(new ApiError('fail') instanceof Error).toBe(true);
  });
});
