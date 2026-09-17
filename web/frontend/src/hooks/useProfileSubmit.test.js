import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useProfileSubmit } from './useProfileSubmit';

// useProfileSubmit owns the single-sample upload flow. These tests verify that
// cancelUpload aborts an in-flight upload and clears the in-flight flag, and
// that a canceled upload does not surface a status error.
describe('useProfileSubmit — upload cancellation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  function makeStubs() {
    return {
      setStatusError: vi.fn(),
      addReportPath: vi.fn(),
      addUploadedPath: vi.fn(),
      addResultArtifactPaths: vi.fn(),
      setSessionResults: vi.fn(),
      setSelectedProfileReportPath: vi.fn(),
      setInlineReportPath: vi.fn(),
      setInlineReportLabel: vi.fn(),
      databases: [{ id: 'db1', display_name: 'Test DB' }],
      selectedDatabaseId: 'db1',
      activeProfileMode: 'fasta',
      analyzeSubMode: 'single',
      setUploadProgress: vi.fn(),
      beginUpload: vi.fn(),
      endUpload: vi.fn(),
    };
  }

  it('cancelUpload aborts the in-flight upload and clears the in-flight flag', async () => {
    // A real browser's xhr.abort() fires the ``abort`` event, not ``error``.
    // The stub mirrors that so the test fails if apiUpload does not settle its
    // promise on abort (which would leave the in-flight flag stuck on).
    const xhr = {
      open: vi.fn(),
      send: vi.fn(),
      setRequestHeader: vi.fn(),
      upload: { onprogress: null },
      onload: () => {},
      readyState: 4,
      status: 0,
      responseText: '',
      abort: () => {
        xhr.onabort();
      },
    };
    vi.stubGlobal('XMLHttpRequest', class {
      constructor() {
        return xhr;
      }
    });

    const stubs = makeStubs();
    const { result } = renderHook(() => useProfileSubmit(stubs));

    const file = new File(['ATCG'], 'cancel.fasta', { type: 'application/octet-stream' });

    // Start the upload without awaiting; beginUpload runs and the promise is pending.
    let uploadPromise;
    act(() => {
      uploadPromise = result.current.uploadFastaFile(file);
    });
    expect(stubs.beginUpload).toHaveBeenCalledTimes(1);

    // Cancel: apiUpload aborts the XHR, the upload rejects, and the finally block
    // calls endUpload. A cancellation is not a status error.
    act(() => {
      result.current.cancelUpload();
    });
    await act(async () => {
      await uploadPromise.catch(() => {});
    });

    expect(stubs.endUpload).toHaveBeenCalledTimes(1);
    expect(stubs.setStatusError).not.toHaveBeenCalled();
  });

  it('cancelUpload is a no-op when no upload is in flight', () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useProfileSubmit(stubs));

    act(() => {
      result.current.cancelUpload();
    });
    // No upload in flight: neither begin nor end is called, and no error surfaces.
    expect(stubs.beginUpload).not.toHaveBeenCalled();
    expect(stubs.endUpload).not.toHaveBeenCalled();
    expect(stubs.setStatusError).not.toHaveBeenCalled();
  });
});
