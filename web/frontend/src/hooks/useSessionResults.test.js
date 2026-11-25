import { StrictMode } from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { setArtifactBundleLimit } from '../api';
import { useSessionResults } from './useSessionResults';

function makeDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('useSessionResults — artifact tracking and bundles', () => {
  beforeEach(() => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      blob: async () => new Blob(['zip-bytes']),
    }));
    global.URL.createObjectURL = vi.fn(() => 'blob:mock');
    global.URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    setArtifactBundleLimit(100);
  });

  afterEach(() => {
    setArtifactBundleLimit(100);
    vi.restoreAllMocks();
  });

  it('tracks every artifact id the backend returned for cleanup', () => {
    const { result } = renderHook(() => useSessionResults());

    act(() => {
      result.current.addResultArtifactPaths({ artifact_ids: ['id-html', 'id-json'] });
    });

    expect(result.current.reportPaths).toEqual(['id-html', 'id-json']);
  });

  it('bundles only the selected results', async () => {
    const { result } = renderHook(() => useSessionResults());

    act(() => {
      result.current.setSessionResults([
        { artifact_ids: ['a-html'] },
        { artifact_ids: ['b-html'] },
      ]);
      result.current.setSelectedResultIndices(new Set([1]));
    });

    await act(async () => {
      await result.current.downloadSelectedArtifacts(() => {});
    });

    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
      artifact_ids: ['b-html'],
    });
  });

  it('toggles isSessionDownloadBusy and guards concurrent duplicate clicks', async () => {
    const deferred = makeDeferred();
    global.fetch = vi.fn(() => deferred.promise);
    const { result } = renderHook(() => useSessionResults());

    act(() => {
      result.current.setSessionResults([{ artifact_ids: ['a-html'] }]);
      result.current.setSelectedResultIndices(new Set([0]));
    });

    let p1;
    let p2;
    act(() => {
      p1 = result.current.downloadSelectedArtifacts();
      p2 = result.current.downloadSelectedArtifacts();
    });

    expect(result.current.isSessionDownloadBusy).toBe(true);
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/artifact-bundle',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    await act(async () => {
      deferred.resolve({
        ok: true,
        blob: async () => new Blob(['zip']),
      });
      await Promise.all([p1, p2]);
    });

    expect(result.current.isSessionDownloadBusy).toBe(false);

    // Subsequent click after completion is allowed.
    await act(async () => {
      await result.current.downloadSelectedArtifacts();
    });
    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(result.current.isSessionDownloadBusy).toBe(false);
  });

  it('returns early without fetching when selected results have no artifact_ids', async () => {
    const { result } = renderHook(() => useSessionResults());

    act(() => {
      result.current.setSessionResults([{ artifact_ids: [] }]);
      result.current.setSelectedResultIndices(new Set([0]));
    });

    await act(async () => {
      await result.current.downloadSelectedArtifacts();
    });

    expect(global.fetch).not.toHaveBeenCalled();
    expect(result.current.isSessionDownloadBusy).toBe(false);
  });

  it('surfaces errors via setStatusError, resets busy in finally, and clears only prior download error on retry', async () => {
    global.fetch = vi.fn()
      .mockRejectedValueOnce(new Error('Bundle download failed'))
      .mockResolvedValueOnce({
        ok: true,
        blob: async () => new Blob(['zip']),
      })
      .mockResolvedValueOnce({
        ok: true,
        blob: async () => new Blob(['zip']),
      });

    const setStatusError = vi.fn();
    const { result } = renderHook(() => useSessionResults());

    act(() => {
      result.current.setSessionResults([{ artifact_ids: ['a-html'] }]);
      result.current.setSelectedResultIndices(new Set([0]));
    });

    await act(async () => {
      await result.current.downloadSelectedArtifacts(setStatusError);
    });

    expect(result.current.isSessionDownloadBusy).toBe(false);
    expect(setStatusError).toHaveBeenCalledWith('Bundle download failed');

    // Retry clears prior download error.
    setStatusError.mockClear();
    await act(async () => {
      await result.current.downloadSelectedArtifacts(setStatusError);
    });
    expect(setStatusError).toHaveBeenCalledTimes(1);
    expect(setStatusError).toHaveBeenCalledWith('');

    // Next download when no prior download error does NOT clear unrelated statusError.
    setStatusError.mockClear();
    await act(async () => {
      await result.current.downloadSelectedArtifacts(setStatusError);
    });
    expect(setStatusError).not.toHaveBeenCalled();
  });

  it('aborts in-flight download on unmount, suppresses AbortError, and supports React.StrictMode', async () => {
    const deferred = makeDeferred();
    let capturedSignal;
    global.fetch = vi.fn((_url, options) => {
      capturedSignal = options?.signal;
      return deferred.promise;
    });
    const setStatusError = vi.fn();

    const { result, unmount } = renderHook(() => useSessionResults());
    act(() => {
      result.current.setSessionResults([{ artifact_ids: ['a-html'] }]);
      result.current.setSelectedResultIndices(new Set([0]));
    });

    let pending;
    act(() => {
      pending = result.current.downloadSelectedArtifacts(setStatusError);
    });
    expect(capturedSignal.aborted).toBe(false);

    unmount();
    expect(capturedSignal.aborted).toBe(true);

    await act(async () => {
      const abortErr = new Error('The operation was aborted.');
      abortErr.name = 'AbortError';
      deferred.reject(abortErr);
      await pending;
    });
    expect(setStatusError).not.toHaveBeenCalled();

    // StrictMode mount/unmount/remount keeps isMountedRef active.
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['zip']),
    });
    const { result: strictResult } = renderHook(() => useSessionResults(), {
      wrapper: StrictMode,
    });
    act(() => {
      strictResult.current.setSessionResults([{ artifact_ids: ['s-html'] }]);
      strictResult.current.setSelectedResultIndices(new Set([0]));
    });
    await act(async () => {
      await strictResult.current.downloadSelectedArtifacts(setStatusError);
    });
    await waitFor(() => {
      expect(strictResult.current.isSessionDownloadBusy).toBe(false);
    });
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('orders reportOptions newest-first, syncs inlineReportPath from selectedProfileReportPath, and deduplicates uploaded/artifact paths', () => {
    const { result } = renderHook(() => useSessionResults());

    act(() => {
      result.current.addUploadedPath('up-1');
      result.current.addUploadedPath('up-1');
      result.current.addUploadedPath('up-2');
      result.current.addResultArtifactPaths({ artifact_ids: ['a1', '', null, 'a1', 'a2'] });
      result.current.setSessionResults([
        {
          sample_name: 'Old',
          reference_name: 'REF1',
          mode: 'vcf',
          report_html_path: 'old.html',
          report_json_path: 'old.json',
          report_pdf_path: 'old.pdf',
          report_tsv_path: 'old.tsv',
        },
        {
          sample_name: 'New',
          reference_name: 'REF2',
          mode: 'fasta',
          report_html_path: 'new.html',
        },
      ]);
    });

    expect(result.current.uploadedPaths).toEqual(['up-1', 'up-2']);
    expect(result.current.reportPaths).toEqual(['a1', 'a2']);
    expect(result.current.reportOptions).toHaveLength(2);
    expect(result.current.reportOptions[0]).toMatchObject({
      path: 'new.html',
      jsonPath: '',
      pdfPath: '',
      tsvPath: '',
      mode: 'fasta',
    });
    expect(result.current.reportOptions[0].label).toContain('New');
    expect(result.current.reportOptions[1]).toMatchObject({
      path: 'old.html',
      jsonPath: 'old.json',
      pdfPath: 'old.pdf',
      tsvPath: 'old.tsv',
      mode: 'vcf',
    });

    act(() => {
      result.current.setSelectedProfileReportPath('new.html');
    });
    expect(result.current.inlineReportPath).toBe('new.html');

    // Setting selectedProfileReportPath to '' preserves existing inlineReportPath.
    act(() => {
      result.current.setSelectedProfileReportPath('');
    });
    expect(result.current.inlineReportPath).toBe('new.html');
  });
});
