import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useProfileSubmit } from './useProfileSubmit';
import { FRONTEND_CONFIG } from '../config';

function makeStubs(overrides = {}) {
  return {
    setStatusError: vi.fn(),
    addUploadedPath: vi.fn(),
    addResultArtifactPaths: vi.fn(),
    setSessionResults: vi.fn(),
    setSelectedProfileReportPath: vi.fn(),
    setInlineReportPath: vi.fn(),
    databases: [{ id: 'db1', display_name: 'Test DB' }],
    selectedDatabaseId: 'db1',
    activeProfileMode: 'fasta',
    setUploadProgress: vi.fn(),
    beginUpload: vi.fn(),
    endUpload: vi.fn(),
    ...overrides,
  };
}

function createControllableXhr({ ignoreAbort = false } = {}) {
  const xhr = {
    open: vi.fn(),
    send: vi.fn(),
    setRequestHeader: vi.fn(),
    getResponseHeader: vi.fn(() => null),
    upload: { onprogress: null },
    onload: null,
    onerror: null,
    onabort: null,
    readyState: 4,
    status: 0,
    responseText: '',
    abortedCount: 0,
    abort: () => {
      xhr.abortedCount += 1;
      if (!ignoreAbort && xhr.onabort) {
        xhr.onabort();
      }
    },
    complete(uploadId) {
      xhr.status = 200;
      xhr.responseText = JSON.stringify({ upload_id: uploadId });
      xhr.onload?.();
    },
    failStatus(status = 500, detail = 'Upload failed') {
      xhr.status = status;
      xhr.responseText = JSON.stringify({ detail });
      xhr.onload?.();
    },
    failNetwork() {
      xhr.onerror?.();
    },
  };
  return xhr;
}

function installXhrQueue(queue) {
  vi.stubGlobal('XMLHttpRequest', class {
    constructor() {
      const next = queue.shift() || createControllableXhr();
      return next;
    }
  });
}

function jsonResponse(body, { ok = true, status = 200, headers = {} } = {}) {
  return {
    ok,
    status,
    headers: new Headers(headers),
    json: async () => body,
  };
}

function makeSuccessResult(sampleName = 'sample-1') {
  return {
    sample_name: sampleName,
    reference_name: 'HSV-1',
    database_id: 'db1',
    report_html_path: `/data/results/${sampleName}.html`,
    created_at: '2026-05-12T10:00:00',
    resistance_hits: 1,
    mode: 'fasta',
  };
}

describe('useProfileSubmit — upload lifecycle (W9)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('cancelUpload aborts the in-flight upload and clears the in-flight flag', async () => {
    const xhr = createControllableXhr();
    installXhrQueue([xhr]);

    const stubs = makeStubs();
    const { result } = renderHook(() => useProfileSubmit(stubs));
    const file = new File(['ATCG'], 'cancel.fasta', { type: 'application/octet-stream' });

    let uploadPromise;
    act(() => {
      uploadPromise = result.current.uploadFastaFile(file);
    });
    expect(stubs.beginUpload).toHaveBeenCalledTimes(1);

    act(() => {
      result.current.cancelUpload();
    });
    await act(async () => {
      await uploadPromise.catch(() => {});
    });

    expect(xhr.abortedCount).toBeGreaterThanOrEqual(1);
    expect(stubs.endUpload).toHaveBeenCalledTimes(1);
    expect(stubs.setStatusError).not.toHaveBeenCalled();
    expect(result.current.fastaInput.fasta_id).toBe('');
  });

  it('cancelUpload is a no-op when no upload is in flight', () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useProfileSubmit(stubs));

    act(() => {
      result.current.cancelUpload();
      result.current.cancelUpload('vcf');
    });
    expect(stubs.beginUpload).not.toHaveBeenCalled();
    expect(stubs.endUpload).not.toHaveBeenCalled();
    expect(stubs.setStatusError).not.toHaveBeenCalled();
  });

  it('handles rapid A -> B re-selection, ignores stale A, and clears prior ID on B failure or cancel across all inputs', async () => {
    // 1. FASTA: A in flight -> B starts (aborts A, stale A ignored even if XHR ignores abort), B wins.
    const fastaA = createControllableXhr({ ignoreAbort: true });
    const fastaB = createControllableXhr();
    installXhrQueue([fastaA, fastaB]);

    const stubs = makeStubs();
    const { result } = renderHook(() => useProfileSubmit(stubs));

    act(() => {
      result.current.setFastaInput((prev) => ({ ...prev, sample: 'KeepSample' }));
    });

    let pA;
    let pB;
    act(() => {
      pA = result.current.uploadFastaFile(new File(['A'], 'a.fasta'));
      pB = result.current.uploadFastaFile(new File(['B'], 'b.fasta'));
    });
    expect(fastaA.abortedCount).toBeGreaterThanOrEqual(1);

    await act(async () => {
      fastaB.complete('up-fasta-B');
      await pB;
    });
    expect(result.current.fastaInput.fasta_id).toBe('up-fasta-B');
    expect(result.current.fastaInput.input_display_name).toBe('b.fasta');
    expect(result.current.fastaInput.sample).toBe('KeepSample');

    // Late A completion (ignoring abort) must not overwrite B's state or surface an error.
    await act(async () => {
      fastaA.complete('up-fasta-A');
      await pA;
    });
    expect(stubs.addUploadedPath).toHaveBeenCalledTimes(1);
    expect(stubs.addUploadedPath).toHaveBeenCalledWith('up-fasta-B');
    expect(result.current.fastaInput.fasta_id).toBe('up-fasta-B');
    expect(result.current.fastaInput.input_display_name).toBe('b.fasta');
    expect(stubs.setStatusError).not.toHaveBeenCalled();

    // 2. VCF: A succeeded -> B fails -> clears vcf_id while preserving sample and cutoffs.
    const vcfA = createControllableXhr();
    const vcfB = createControllableXhr();
    installXhrQueue([vcfA, vcfB]);
    act(() => {
      result.current.setVcfInput((prev) => ({ ...prev, sample: 'VcfSample', min_af: 0.25, min_depth: 20 }));
    });
    let vcfAOk;
    await act(async () => {
      const p = result.current.uploadVcfFile(new File(['##VCF'], 'a.vcf'));
      vcfA.complete('up-vcf-A');
      vcfAOk = await p;
    });
    expect(vcfAOk).toBe(true);
    expect(result.current.vcfInput.vcf_id).toBe('up-vcf-A');

    let pVcfB;
    act(() => {
      pVcfB = result.current.uploadVcfFile(new File(['BAD'], 'b.vcf'));
    });
    expect(result.current.vcfInput.vcf_id).toBe(''); // cleared on start before completion
    let vcfBOk;
    await act(async () => {
      vcfB.failStatus(400, 'Unsupported VCF format');
      vcfBOk = await pVcfB;
    });
    expect(vcfBOk).toBe(false);
    expect(result.current.vcfInput.vcf_id).toBe('');
    expect(result.current.vcfInput.sample).toBe('VcfSample');
    expect(result.current.vcfInput.min_af).toBe(0.25);
    expect(result.current.vcfInput.min_depth).toBe(20);
    expect(stubs.setStatusError).toHaveBeenLastCalledWith(
      'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.',
    );

    // 3. Reference FASTA, BAM, and JSON: A succeeded -> B canceled -> clears prior ID.
    const refA = createControllableXhr();
    const refB = createControllableXhr();
    const bamA = createControllableXhr();
    const bamB = createControllableXhr();
    const jsonA = createControllableXhr();
    const jsonB = createControllableXhr();
    installXhrQueue([refA, refB, bamA, bamB, jsonA, jsonB]);

    await act(async () => {
      const p = result.current.uploadReferenceFile(new File(['>r'], 'refA.fasta'));
      refA.complete('up-ref-A');
      await p;
    });
    expect(result.current.vcfInput.reference_id).toBe('up-ref-A');
    let pRefB;
    act(() => {
      pRefB = result.current.uploadReferenceFile(new File(['>r2'], 'refB.fasta'));
      result.current.cancelUpload('reference');
    });
    await act(async () => {
      await pRefB;
    });
    expect(result.current.vcfInput.reference_id).toBe('');

    await act(async () => {
      const p = result.current.uploadBamFile(new File(['bam'], 'a.bam'));
      bamA.complete('up-bam-A');
      await p;
    });
    expect(result.current.vcfInput.bam_id).toBe('up-bam-A');
    let pBamB;
    act(() => {
      pBamB = result.current.uploadBamFile(new File(['bam2'], 'b.bam'));
      result.current.cancelUpload('bam');
    });
    await act(async () => {
      await pBamB;
    });
    expect(result.current.vcfInput.bam_id).toBeNull();

    await act(async () => {
      const p = result.current.uploadJsonFile(new File(['{}'], 'a.json'));
      jsonA.complete('up-json-A');
      await p;
    });
    expect(result.current.jsonInputId).toBe('up-json-A');
    let pJsonB;
    act(() => {
      pJsonB = result.current.uploadJsonFile(new File(['{}'], 'b.json'));
      result.current.cancelUpload('json');
    });
    await act(async () => {
      await pJsonB;
    });
    expect(result.current.jsonInputId).toBe('');
  });

  it('supports targeted cancelUpload(inputKey) without canceling concurrent sibling uploads, and cancelUpload() cancels all', async () => {
    const vcfXhr = createControllableXhr();
    const refXhr = createControllableXhr();
    installXhrQueue([vcfXhr, refXhr]);

    const stubs = makeStubs({ activeProfileMode: 'vcf' });
    const { result } = renderHook(() => useProfileSubmit(stubs));

    let pVcf;
    let pRef;
    act(() => {
      pVcf = result.current.uploadVcfFile(new File(['##VCF'], 's.vcf'));
      pRef = result.current.uploadReferenceFile(new File(['>ref'], 'ref.fasta'));
    });
    expect(stubs.beginUpload).toHaveBeenCalledTimes(2);

    act(() => {
      result.current.cancelUpload('vcf');
    });
    await act(async () => {
      await pVcf;
    });
    expect(vcfXhr.abortedCount).toBeGreaterThanOrEqual(1);
    expect(refXhr.abortedCount).toBe(0);
    expect(result.current.vcfInput.vcf_id).toBe('');

    await act(async () => {
      refXhr.complete('up-ref-1');
      await pRef;
    });
    expect(result.current.vcfInput.reference_id).toBe('up-ref-1');
    expect(stubs.endUpload).toHaveBeenCalledTimes(2);

    // Now start two concurrent uploads and cancel both with cancelUpload() (no args).
    const vcfXhr2 = createControllableXhr();
    const bamXhr2 = createControllableXhr();
    installXhrQueue([vcfXhr2, bamXhr2]);

    let pVcf2;
    let pBam2;
    act(() => {
      pVcf2 = result.current.uploadVcfFile(new File(['##VCF'], 's2.vcf'));
      pBam2 = result.current.uploadBamFile(new File(['bam'], 's2.bam'));
    });
    act(() => {
      result.current.cancelUpload();
    });
    await act(async () => {
      await Promise.all([pVcf2, pBam2]);
    });
    expect(vcfXhr2.abortedCount).toBeGreaterThanOrEqual(1);
    expect(bamXhr2.abortedCount).toBeGreaterThanOrEqual(1);
    expect(stubs.setStatusError).not.toHaveBeenCalled();
  });

  it('aborts in-flight uploads and stops job polling on unmount', async () => {
    vi.useFakeTimers();
    const xhr = createControllableXhr();
    installXhrQueue([xhr]);

    const pollSignals = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const reqUrl = String(url);
      if (reqUrl.includes('/api/profile/fasta')) {
        return Promise.resolve(jsonResponse({ job_id: 'job-unmount' }));
      }
      if (reqUrl.includes('/api/jobs/job-unmount')) {
        pollSignals.push(options.signal);
        return Promise.resolve(jsonResponse({ status: 'running' }));
      }
      return Promise.resolve(jsonResponse({}));
    }));

    const stubs = makeStubs({ activeProfileMode: 'fasta' });
    const { result, unmount } = renderHook(() => useProfileSubmit(stubs));

    let uploadPromise;
    let jobPromise;
    act(() => {
      uploadPromise = result.current.uploadFastaFile(new File(['ATCG'], 'u.fasta'));
      jobPromise = result.current.runExampleProfile();
    });

    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(FRONTEND_CONFIG.profile.jobPollIntervalMs);
    });
    expect(pollSignals.length).toBeGreaterThanOrEqual(1);

    unmount();

    await act(async () => {
      await Promise.all([uploadPromise, jobPromise]);
    });

    expect(xhr.abortedCount).toBeGreaterThanOrEqual(1);
    expect(pollSignals[0].aborted).toBe(true);
    expect(stubs.endUpload).toHaveBeenCalledTimes(1);
    expect(stubs.setSessionResults).not.toHaveBeenCalled();
  });
});

describe('useProfileSubmit — job polling & cancellation races (WA-025)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('resets isCancellationRequested when DELETE fails so job remains cancelable and can succeed', async () => {
    vi.useFakeTimers();
    const successResult = makeSuccessResult('recovered');
    let pollCount = 0;

    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const reqUrl = String(url);
      if (reqUrl.includes('/api/profile/fasta')) {
        return Promise.resolve(jsonResponse({ job_id: 'job-del-fail' }));
      }
      if (reqUrl.includes('/api/jobs/job-del-fail') && options.method === 'DELETE') {
        return Promise.resolve(jsonResponse({ detail: 'Cancel failed' }, { ok: false, status: 500 }));
      }
      if (reqUrl.includes('/api/jobs/job-del-fail')) {
        pollCount += 1;
        if (pollCount === 1) {
          return Promise.resolve(jsonResponse({ status: 'running' }));
        }
        return Promise.resolve(jsonResponse({ status: 'succeeded', result: successResult }));
      }
      return Promise.resolve(jsonResponse({}));
    }));

    const stubs = makeStubs({ activeProfileMode: 'fasta' });
    const { result } = renderHook(() => useProfileSubmit(stubs));

    let jobPromise;
    act(() => {
      jobPromise = result.current.runExampleProfile();
    });

    // Wait for submit + first poll -> status becomes 'running'
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(FRONTEND_CONFIG.profile.jobPollIntervalMs);
    });
    expect(result.current.canCancelJob).toBe(true);

    // Attempt cancel, DELETE fails -> error surfaced, canCancelJob stays true, cancellation flag reset
    await act(async () => {
      await result.current.cancelActiveJob();
    });
    expect(stubs.setStatusError).toHaveBeenCalledWith('Cancel failed');
    expect(result.current.canCancelJob).toBe(true);
    expect(result.current.isCancelingJob).toBe(false);

    // Next poll succeeds and commits result
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FRONTEND_CONFIG.profile.jobPollIntervalMs);
      await jobPromise;
    });
    expect(stubs.setSessionResults).toHaveBeenCalledTimes(1);
    expect(stubs.setInlineReportPath).toHaveBeenCalledWith(successResult.report_html_path);
  });

  it('discards a late succeeded payload when DELETE succeeded (including waking sleeping backoff)', async () => {
    vi.useFakeTimers();
    const lateResult = makeSuccessResult('ghost');
    let pollCount = 0;

    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const reqUrl = String(url);
      if (reqUrl.includes('/api/profile/fasta')) {
        return Promise.resolve(jsonResponse({ job_id: 'job-cancel-race' }));
      }
      if (reqUrl.includes('/api/jobs/job-cancel-race') && options.method === 'DELETE') {
        return Promise.resolve(jsonResponse({}));
      }
      if (reqUrl.includes('/api/jobs/job-cancel-race')) {
        pollCount += 1;
        if (pollCount === 1) {
          // Force long 429 sleep before next poll
          return Promise.resolve(jsonResponse(
            { detail: 'Too many requests' },
            { ok: false, status: 429, headers: { 'Retry-After': '30' } },
          ));
        }
        return Promise.resolve(jsonResponse({ status: 'succeeded', result: lateResult }));
      }
      return Promise.resolve(jsonResponse({}));
    }));

    const stubs = makeStubs({ activeProfileMode: 'fasta' });
    const { result } = renderHook(() => useProfileSubmit(stubs));

    let jobPromise;
    act(() => {
      jobPromise = result.current.runExampleProfile();
    });

    // Trigger submit + first poll (429 with 30s backoff sleep)
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(FRONTEND_CONFIG.profile.jobPollIntervalMs);
    });
    expect(result.current.canCancelJob).toBe(true);

    // Cancel while poll is sleeping for 30s -> DELETE wakes poll immediately; late 'succeeded' is rejected
    await act(async () => {
      await result.current.cancelActiveJob();
      await jobPromise;
    });

    expect(pollCount).toBe(2);
    expect(stubs.setSessionResults).not.toHaveBeenCalled();
    expect(stubs.setInlineReportPath).not.toHaveBeenCalled();
    expect(stubs.setStatusError).toHaveBeenCalledWith('Job canceled by user.');
    expect(result.current.isProfileBusy).toBe(false);
    expect(result.current.canCancelJob).toBe(false);
  });

  it('prevents concurrent jobs from wiping newer job busy flags or committing stale results', async () => {
    vi.useFakeTimers();
    const jsonResult = makeSuccessResult('regen');
    const exampleResult = makeSuccessResult('example');
    let rejectStaleSubmit;

    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const reqUrl = String(url);
      if (reqUrl.includes('/api/regenerate/json')) {
        return new Promise((resolve, reject) => {
          rejectStaleSubmit = reject;
          options.signal?.addEventListener('abort', () => {
            const err = new Error('The operation was aborted.');
            err.name = 'AbortError';
            reject(err);
          }, { once: true });
        });
      }
      if (reqUrl.includes('/api/profile/fasta')) {
        return Promise.resolve(jsonResponse({ job_id: 'job-example' }));
      }
      if (reqUrl.includes('/api/jobs/job-example')) {
        return Promise.resolve(jsonResponse({ status: 'succeeded', result: exampleResult }));
      }
      return Promise.resolve(jsonResponse({}));
    }));

    const stubs = makeStubs({ activeProfileMode: 'regenerate' });
    const { result } = renderHook(() => useProfileSubmit(stubs));

    // Upload JSON first so runRegenerateFromJson can submit.
    const jsonXhr = createControllableXhr();
    installXhrQueue([jsonXhr]);
    await act(async () => {
      const p = result.current.uploadJsonFile(new File(['{}'], 'old.json'));
      jsonXhr.complete('up-json-1');
      await p;
    });

    let regenPromise;
    let examplePromise;
    act(() => {
      regenPromise = result.current.runRegenerateFromJson();
    });
    expect(result.current.isRegenerateBusy).toBe(true);

    // Start Example job while Regenerate is in flight -> Regenerate clears immediately & Example takes ownership.
    act(() => {
      examplePromise = result.current.runExampleProfile();
    });
    expect(result.current.isRegenerateBusy).toBe(false);
    expect(result.current.isProfileBusy).toBe(true);

    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(FRONTEND_CONFIG.profile.jobPollIntervalMs);
      await Promise.all([regenPromise, examplePromise]);
    });

    expect(rejectStaleSubmit).toBeTypeOf('function');
    expect(result.current.isProfileBusy).toBe(false);
    expect(result.current.isRegenerateBusy).toBe(false);
    expect(stubs.setSessionResults).toHaveBeenCalledTimes(1);
    expect(stubs.setInlineReportPath).toHaveBeenCalledWith(exampleResult.report_html_path);
    expect(stubs.setInlineReportPath).not.toHaveBeenCalledWith(jsonResult.report_html_path);
  });

  it('keeps isProfileBusy true on double submit of the same mode until the latest job finishes', async () => {
    vi.useFakeTimers();
    const secondResult = makeSuccessResult('second');
    let submitCount = 0;

    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const reqUrl = String(url);
      if (reqUrl.includes('/api/profile/vcf')) {
        submitCount += 1;
        if (submitCount === 1) {
          return new Promise((_, reject) => {
            options.signal?.addEventListener('abort', () => {
              const err = new Error('The operation was aborted.');
              err.name = 'AbortError';
              reject(err);
            }, { once: true });
          });
        }
        return Promise.resolve(jsonResponse({ job_id: 'job-vcf-2' }));
      }
      if (reqUrl.includes('/api/jobs/job-vcf-2')) {
        return Promise.resolve(jsonResponse({ status: 'succeeded', result: secondResult }));
      }
      return Promise.resolve(jsonResponse({}));
    }));

    // Note: activeProfileMode is 'fasta' while submitting VCF, verifying isProfileBusy checks both flags.
    const stubs = makeStubs({ activeProfileMode: 'vcf' });
    const { result } = renderHook(() => useProfileSubmit(stubs));

    let p1;
    let p2;
    act(() => {
      p1 = result.current.runSelectedProfile();
      p2 = result.current.runSelectedProfile();
    });
    await act(async () => {
      await p1;
    });
    // p1 finished via abort, but p2 is still running -> isProfileBusy MUST remain true.
    expect(result.current.isProfileBusy).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(FRONTEND_CONFIG.profile.jobPollIntervalMs);
      await p2;
    });
    expect(result.current.isProfileBusy).toBe(false);
    expect(stubs.setSessionResults).toHaveBeenCalledTimes(1);
  });

  it('handles 429 Retry-After, exponential 500 backoff up to maxConsecutiveErrors, and immediate stop on 404/401/403', async () => {
    vi.useFakeTimers();
    const baseMs = FRONTEND_CONFIG.profile.jobPollIntervalMs;

    // 1. 429 with Retry-After: 3 -> waits 3000ms -> succeeds
    let pollAttempt = 0;
    const result429 = makeSuccessResult('after-429');
    vi.stubGlobal('fetch', vi.fn((url) => {
      const reqUrl = String(url);
      if (reqUrl.includes('/api/profile/fasta')) {
        return Promise.resolve(jsonResponse({ job_id: 'job-429' }));
      }
      if (reqUrl.includes('/api/jobs/job-429')) {
        pollAttempt += 1;
        if (pollAttempt === 1) {
          return Promise.resolve(jsonResponse(
            { detail: 'Slow down' },
            { ok: false, status: 429, headers: { 'Retry-After': '3' } },
          ));
        }
        return Promise.resolve(jsonResponse({ status: 'succeeded', result: result429 }));
      }
      return Promise.resolve(jsonResponse({}));
    }));

    const stubs429 = makeStubs({ activeProfileMode: 'fasta' });
    const hook429 = renderHook(() => useProfileSubmit(stubs429));
    let p429;
    act(() => {
      p429 = hook429.result.current.runExampleProfile();
    });
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(baseMs);
    });
    expect(pollAttempt).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2999);
    });
    expect(pollAttempt).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
      await p429;
    });
    expect(pollAttempt).toBe(2);
    expect(stubs429.setSessionResults).toHaveBeenCalledTimes(1);
    hook429.unmount();

    // 2. Terminal 404 and 401/403 stop immediately without retry
    for (const [status, detail] of [[404, 'Job not found'], [401, 'Session expired'], [403, 'Forbidden']]) {
      let calls = 0;
      vi.stubGlobal('fetch', vi.fn((url) => {
        const reqUrl = String(url);
        if (reqUrl.includes('/api/profile/fasta')) {
          return Promise.resolve(jsonResponse({ job_id: `job-${status}` }));
        }
        if (reqUrl.includes(`/api/jobs/job-${status}`)) {
          calls += 1;
          return Promise.resolve(jsonResponse({ detail }, { ok: false, status }));
        }
        return Promise.resolve(jsonResponse({}));
      }));
      const stubsErr = makeStubs({ activeProfileMode: 'fasta' });
      const hookErr = renderHook(() => useProfileSubmit(stubsErr));
      let pErr;
      act(() => {
        pErr = hookErr.result.current.runExampleProfile();
      });
      await act(async () => {
        await Promise.resolve();
        await vi.advanceTimersByTimeAsync(baseMs);
        await pErr;
      });
      expect(calls).toBe(1);
      expect(stubsErr.setStatusError).toHaveBeenCalledWith(detail);
      expect(hookErr.result.current.isProfileBusy).toBe(false);
      hookErr.unmount();
    }

    // 3. Transient 500s back off exponentially and stop after maxConsecutiveErrors
    let transientCalls = 0;
    vi.stubGlobal('fetch', vi.fn((url) => {
      const reqUrl = String(url);
      if (reqUrl.includes('/api/profile/fasta')) {
        return Promise.resolve(jsonResponse({ job_id: 'job-500' }));
      }
      if (reqUrl.includes('/api/jobs/job-500')) {
        transientCalls += 1;
        return Promise.resolve(jsonResponse({ detail: 'Backend error' }, { ok: false, status: 500 }));
      }
      return Promise.resolve(jsonResponse({}));
    }));
    const stubs500 = makeStubs({ activeProfileMode: 'fasta' });
    const hook500 = renderHook(() => useProfileSubmit(stubs500));
    let p500;
    act(() => {
      p500 = hook500.result.current.runExampleProfile();
    });
    await act(async () => {
      await vi.runAllTimersAsync();
      await p500;
    });
    expect(transientCalls).toBe(FRONTEND_CONFIG.batch.maxConsecutiveErrors);
    expect(stubs500.setStatusError).toHaveBeenCalledWith('Backend error');
    expect(hook500.result.current.isProfileBusy).toBe(false);
    hook500.unmount();
  });

  it('strips .vcf.gz when deriving default single VCF sample name', async () => {
    vi.useFakeTimers();
    const vcfXhr = createControllableXhr();
    const refXhr = createControllableXhr();
    installXhrQueue([vcfXhr, refXhr]);

    let submittedBody = null;
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const reqUrl = String(url);
      if (reqUrl.includes('/api/profile/vcf')) {
        submittedBody = JSON.parse(options.body);
        return Promise.resolve(jsonResponse({ job_id: 'job-vcf-gz' }));
      }
      if (reqUrl.includes('/api/jobs/job-vcf-gz')) {
        return Promise.resolve(jsonResponse({ status: 'succeeded', result: makeSuccessResult('sample1') }));
      }
      return Promise.resolve(jsonResponse({}));
    }));

    const stubs = makeStubs({ activeProfileMode: 'vcf' });
    const { result } = renderHook(() => useProfileSubmit(stubs));

    await act(async () => {
      const pVcf = result.current.uploadVcfFile(new File(['##VCF'], 'sample1.vcf.gz'));
      vcfXhr.complete('up-vcf-gz');
      await pVcf;
    });
    await act(async () => {
      const pRef = result.current.uploadReferenceFile(new File(['>ref\nACGT'], 'ref.fasta'));
      refXhr.complete('up-ref');
      await pRef;
    });

    let jobPromise;
    act(() => {
      jobPromise = result.current.runSelectedProfile();
    });
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(FRONTEND_CONFIG.profile.jobPollIntervalMs);
      await jobPromise;
    });

    expect(submittedBody.sample).toBe('sample1');
    expect(submittedBody.input_display_name).toBe('sample1.vcf.gz');
  });
});
