import { StrictMode } from 'react';
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useBatchManager } from './useBatchManager';

// Mock XMLHttpRequest used by apiUpload for BAM/VCF uploads.
class MockXHR {
  constructor() {
    this.open = vi.fn();
    this.send = vi.fn();
    this.setRequestHeader = vi.fn();
    this.upload = { onprogress: null };
    this.onload = null;
    this.onerror = null;
    this.onabort = null;
    this.status = 200;
    this.responseText = '';
  }

  // A real browser fires the ``abort`` event (not ``error``) on abort.
  abort() {
    if (this.onabort) {
      this.onabort({});
    }
  }

  triggerSuccess(response) {
    this.status = 200;
    this.responseText = JSON.stringify(response);
    if (this.onload) {
      this.onload({});
    }
  }
}

let mockXHRInstance;
// Collect every XHR instance created so multi-upload tests can resolve them in order even
// though ``mockXHRInstance`` is reassigned on each ``new XMLHttpRequest()`` call.
const mockXHRInstances = [];

// Plain class stub — vi.fn(() => ...) loses its implementation when vi.clearAllMocks()
// runs in Vitest 5, so a plain constructor is used instead and re-stubbed per test.
function setupXhrStub() {
  vi.stubGlobal('XMLHttpRequest', class {
    constructor() {
      mockXHRInstance = new MockXHR();
      mockXHRInstances.push(mockXHRInstance);
      return mockXHRInstance;
    }
  });
}

// Mock fetch used by apiPostRaw (submitBatch) and apiGet (job polling).
vi.stubGlobal('fetch', vi.fn());

vi.mock('../config.js', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    FRONTEND_CONFIG: {
      ...actual.FRONTEND_CONFIG,
      profile: {
        ...actual.FRONTEND_CONFIG.profile,
        jobPollIntervalMs: 10,
      },
      batch: {
        ...actual.FRONTEND_CONFIG.batch,
        minPollIntervalMs: 10,
      },
    },
  };
});

// Minimal stubs for the session/upload callbacks the hook receives.
function makeStubs() {
  return {
    selectedDatabaseId: 'db1',
    addUploadedPath: vi.fn(),
    addResultArtifactPaths: vi.fn(),
    setSessionResults: vi.fn(),
    setUploadProgress: vi.fn(),
  };
}

// Upload a VCF file into the hook via addBatchVcfFiles, resolving the XHR with a server upload_id.
async function uploadVcf(result, fileName, uploadId) {
  const file = new File(['##VCF'], fileName, { type: 'application/octet-stream' });
  let promise;
  await act(async () => {
    promise = result.current.addBatchVcfFiles([file]);
    mockXHRInstance.triggerSuccess({ upload_id: uploadId, file_type: 'vcf', size_bytes: 5 });
    await promise;
  });
}

// Upload a BAM file into the hook via addBatchBamFiles, resolving the XHR with a server upload_id.
async function uploadBam(result, fileName, uploadId) {
  const file = new File(['BAM'], fileName, { type: 'application/octet-stream' });
  let promise;
  let pairing;
  await act(async () => {
    promise = result.current.addBatchBamFiles([file]);
    mockXHRInstance.triggerSuccess({ upload_id: uploadId, file_type: 'bam', size_bytes: 3 });
    pairing = await promise;
  });
  return pairing;
}

// Flush the microtask queue so an awaited apiUpload can create its XHR before the test resolves it.
function flushPromises() {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

describe('useBatchManager — batch BAM auto-pairing and per-row override', () => {
  beforeEach(() => {
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
    mockXHRInstances.length = 0;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('auto-pairs a multi-select BAM to the VCF row with a matching filename stem', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');

    const pairing = await uploadBam(result, 'sample1.bam', 'up-bam-1');

    expect(pairing.paired).toEqual(['sample1.bam']);
    expect(pairing.unmatched).toEqual([]);
    expect(pairing.collisions).toEqual([]);
    expect(result.current.batchVcfFiles[0].bamId).toBe('up-bam-1');
    expect(result.current.batchVcfFiles[0].bamName).toBe('sample1.bam');
  });

  it('reports a BAM with no matching VCF stem as unmatched and leaves rows unchanged', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');

    const pairing = await uploadBam(result, 'orphan.bam', 'up-bam-orphan');

    expect(pairing.paired).toEqual([]);
    expect(pairing.unmatched).toEqual(['orphan.bam']);
    expect(pairing.collisions).toEqual([]);
    expect(result.current.batchVcfFiles[0].bamId).toBeNull();
  });

  it('reports a collision when a BAM stem matches an already-paired row and preserves the existing pairing', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');
    await uploadBam(result, 'sample1.bam', 'up-bam-1');

    const pairing = await uploadBam(result, 'sample1.bam', 'up-bam-dup');

    expect(pairing.paired).toEqual([]);
    expect(pairing.unmatched).toEqual([]);
    expect(pairing.collisions).toEqual(['sample1.bam']);
    expect(result.current.batchVcfFiles[0].bamId).toBe('up-bam-1');
    expect(result.current.batchVcfFiles[0].bamName).toBe('sample1.bam');
  });

  it('reports a collision for two same-stem BAMs uploaded in a single multi-select call', async () => {
    // Regression: collision detection must see pairings made by earlier iterations of the same
    // addBatchBamFiles loop, not just pairings from prior calls. Reading back from the stale
    // closure snapshot would miss the first BAM's just-applied pairing and silently overwrite it.
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');

    const file1 = new File(['BAM'], 'sample1.bam', { type: 'application/octet-stream' });
    const file2 = new File(['BAM'], 'sample1.bam', { type: 'application/octet-stream' });
    const startCount = mockXHRInstances.length;
    let pairing;
    await act(async () => {
      const promise = result.current.addBatchBamFiles([file1, file2]);
      // The loop awaits apiUpload for file1 first; flush microtasks so XHR #1 is created and
      // the loop is parked on its onload. Resolve it, then flush again so the loop advances to
      // apiUpload for file2 (creating XHR #2) before resolving that too.
      await flushPromises();
      mockXHRInstances[startCount].triggerSuccess({ upload_id: 'up-bam-1', file_type: 'bam', size_bytes: 3 });
      await flushPromises();
      mockXHRInstances[startCount + 1].triggerSuccess({ upload_id: 'up-bam-dup', file_type: 'bam', size_bytes: 3 });
      pairing = await promise;
    });

    expect(pairing.paired).toEqual(['sample1.bam']);
    expect(pairing.unmatched).toEqual([]);
    expect(pairing.collisions).toEqual(['sample1.bam']);
    // The first BAM's pairing is preserved; the second did not overwrite it.
    expect(result.current.batchVcfFiles[0].bamId).toBe('up-bam-1');
    expect(result.current.batchVcfFiles[0].bamName).toBe('sample1.bam');
  });

  it('does not set batchError on a clean BAM pairing (no narration for the happy path)', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');
    await uploadBam(result, 'sample1.bam', 'up-bam-1');

    expect(result.current.batchError).toBeNull();
  });

  it('sets batchError listing unmatched and collision filenames (error-only reporting)', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');
    // orphan.bam matches no VCF row -> unmatched; sample1.bam pairs, then a second sample1.bam
    // in the same call collides. Both cases must be surfaced via batchError, not narrated inline.
    const orphan = new File(['BAM'], 'orphan.bam', { type: 'application/octet-stream' });
    const first = new File(['BAM'], 'sample1.bam', { type: 'application/octet-stream' });
    const second = new File(['BAM'], 'sample1.bam', { type: 'application/octet-stream' });
    const startCount = mockXHRInstances.length;
    await act(async () => {
      const promise = result.current.addBatchBamFiles([orphan, first, second]);
      await flushPromises();
      mockXHRInstances[startCount].triggerSuccess({ upload_id: 'up-bam-orphan', file_type: 'bam', size_bytes: 3 });
      await flushPromises();
      mockXHRInstances[startCount + 1].triggerSuccess({ upload_id: 'up-bam-1', file_type: 'bam', size_bytes: 3 });
      await flushPromises();
      mockXHRInstances[startCount + 2].triggerSuccess({ upload_id: 'up-bam-dup', file_type: 'bam', size_bytes: 3 });
      await promise;
    });

    expect(result.current.batchError).toContain('orphan.bam');
    expect(result.current.batchError).toContain('sample1.bam');
    expect(result.current.batchError).toMatch(/matched no VCF row/);
    expect(result.current.batchError).toMatch(/already paired/);
  });

  it('attachBatchBam overwrites a row BAM regardless of prior auto-pairing', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');
    await uploadBam(result, 'sample1.bam', 'up-bam-1');

    const rowId = result.current.batchVcfFiles[0].rowId;
    const override = new File(['BAM'], 'manual.bam', { type: 'application/octet-stream' });
    await act(async () => {
      const promise = result.current.attachBatchBam(rowId, override);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-bam-manual', file_type: 'bam', size_bytes: 3 });
      await promise;
    });

    expect(result.current.batchVcfFiles[0].bamId).toBe('up-bam-manual');
    expect(result.current.batchVcfFiles[0].bamName).toBe('manual.bam');
  });

  it('removeBatchFile drops the VCF row together with its BAM', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', '/uploads/sample1.vcf');
    await uploadVcf(result, 'sample2.vcf', '/uploads/sample2.vcf');
    await uploadBam(result, 'sample1.bam', '/uploads/sample1.bam');

    const firstRowId = result.current.batchVcfFiles[0].rowId;
    act(() => {
      result.current.removeBatchFile(firstRowId);
    });

    expect(result.current.batchVcfFiles).toHaveLength(1);
    expect(result.current.batchVcfFiles[0].name).toBe('sample2.vcf');
  });

  it('submitBatch VCF branch sends a bam_ids array aligned with vcf_ids', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');
    await uploadVcf(result, 'sample2.vcf', 'up-vcf-2');
    await uploadBam(result, 'sample2.bam', 'up-bam-2');

    // Upload the shared reference FASTA and wait for it to land in state before submitting.
    const refFile = new File(['>r\nACGT'], 'ref.fasta', { type: 'application/octet-stream' });
    await act(async () => {
      const refPromise = result.current.uploadBatchReferenceFasta(refFile);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-ref-1', file_type: 'fasta', size_bytes: 8 });
      await refPromise;
    });
    await waitFor(() => {
      expect(result.current.batchReferenceFasta).not.toBeNull();
    });

    // apiPostRaw (submit) and apiGet (polling) both use fetch; stub an accepted batch response.
    global.fetch.mockImplementation(async (_url, options) => {
      if (options && options.body) {
        const parsed = JSON.parse(options.body);
        // Assert the contract here against the actual submitted body.
        expect(parsed.bam_ids).toEqual([null, 'up-bam-2']);
        expect(parsed.bam_ids).toHaveLength(parsed.vcf_ids.length);
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          samples: [
            { job_id: 'j1', sample_name: 'sample1', status: 'succeeded' },
            { job_id: 'j2', sample_name: 'sample2', status: 'succeeded' },
          ],
          total: 2,
        }),
      };
    });

    await act(async () => {
      await result.current.submitBatch();
    });

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });
  });
});

describe('useBatchManager — JSON regenerate batch', () => {
  beforeEach(() => {
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
    mockXHRInstances.length = 0;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  // Upload a results-JSON file into the hook via addBatchJsonFiles.
  async function uploadJson(result, fileName, uploadId) {
    const file = new File(['{"run":{}}'], fileName, { type: 'application/json' });
    let promise;
    await act(async () => {
      promise = result.current.addBatchJsonFiles([file]);
      mockXHRInstance.triggerSuccess({ upload_id: uploadId, file_type: 'json', size_bytes: 12 });
      await promise;
    });
  }

  it('addBatchJsonFiles uploads via /api/upload/json and records the file', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadJson(result, 'sample1.results.json', 'up-json-1');

    expect(result.current.batchJsonFiles).toHaveLength(1);
    expect(result.current.batchJsonFiles[0].uploadId).toBe('up-json-1');
    expect(result.current.batchJsonFiles[0].name).toBe('sample1.results.json');
    expect(stubs.addUploadedPath).toHaveBeenCalledWith('up-json-1');
  });

  it('removeBatchFile drops a JSON row in json mode', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadJson(result, 'sample1.results.json', 'up-json-1');
    await uploadJson(result, 'sample2.results.json', 'up-json-2');
    act(() => {
      result.current.setBatchMode('json');
    });

    const firstRowId = result.current.batchJsonFiles[0].rowId;
    act(() => {
      result.current.removeBatchFile(firstRowId);
    });

    expect(result.current.batchJsonFiles).toHaveLength(1);
    expect(result.current.batchJsonFiles[0].name).toBe('sample2.results.json');
  });

  it('submitBatch JSON branch POSTs /api/regenerate/batch with json_ids and database_id', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadJson(result, 'sample1.results.json', 'up-json-1');
    await uploadJson(result, 'sample2.results.json', 'up-json-2');
    act(() => {
      result.current.setBatchMode('json');
    });

    let submittedBody;
    global.fetch.mockImplementation(async (url, options) => {
      if (options && options.body) {
        submittedBody = JSON.parse(options.body);
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          samples: [
            { job_id: 'j1', sample_name: 'sample1', status: 'succeeded' },
            { job_id: 'j2', sample_name: 'sample2', status: 'succeeded' },
          ],
          total: 2,
        }),
      };
    });

    await act(async () => {
      await result.current.submitBatch();
    });

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });
    expect(submittedBody.json_ids).toEqual(['up-json-1', 'up-json-2']);
    expect(submittedBody.sample_names).toEqual(['sample1.results', 'sample2.results']);
    expect(submittedBody.input_display_names).toEqual(['sample1.results.json', 'sample2.results.json']);
    expect(submittedBody.database_id).toBe('db1');
    // JSON regenerate has no shared reference or cutoffs.
    expect(submittedBody.reference_id).toBeUndefined();
    expect(submittedBody.db_path).toBeUndefined();
    expect(submittedBody.min_af).toBeUndefined();
  });

  it('downloadAllBatchArtifacts bundles the artifact_ids reported for each sample', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadJson(result, 'sample1.results.json', 'up-json-1');
    act(() => {
      result.current.setBatchMode('json');
    });

    global.fetch.mockImplementation(async (url) => {
      if (String(url).includes('/api/regenerate/batch')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            samples: [{ job_id: 'j1', sample_name: 'sample1', status: 'queued' }],
            total: 1,
          }),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'succeeded',
          result: { artifact_ids: ['id-html', 'id-json', 'id-pdf', 'id-tsv'] },
        }),
      };
    });

    await act(async () => {
      await result.current.submitBatch();
    });
    await waitFor(() => {
      expect(result.current.batchSamples[0].artifactIds).toEqual([
        'id-html',
        'id-json',
        'id-pdf',
        'id-tsv',
      ]);
    });

    global.URL.createObjectURL = vi.fn(() => 'blob:mock');
    global.URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    await act(async () => {
      await result.current.downloadAllBatchArtifacts();
    });

    const bundleCall = global.fetch.mock.calls.find(([url]) =>
      String(url).includes('/api/artifact-bundle')
    );
    expect(bundleCall).toBeDefined();
    expect(JSON.parse(bundleCall[1].body)).toEqual({
      artifact_ids: ['id-html', 'id-json', 'id-pdf', 'id-tsv'],
    });
  });

  it('submitBatch JSON branch surfaces a 429 rate limit via batchError', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadJson(result, 'sample1.results.json', 'up-json-1');
    act(() => {
      result.current.setBatchMode('json');
    });

    global.fetch.mockImplementation(async () => ({
      ok: false,
      status: 429,
      json: async () => ({ detail: 'rate limited' }),
    }));

    await act(async () => {
      await result.current.submitBatch();
    });

    await waitFor(() => {
      expect(result.current.batchError).toMatch(/Rate limit/);
      expect(result.current.batchRateLimitCooldown).toBe(60);
    });
  });

  it('resetBatch clears JSON files', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadJson(result, 'sample1.results.json', 'up-json-1');
    act(() => {
      result.current.resetBatch();
    });

    expect(result.current.batchJsonFiles).toHaveLength(0);
  });
});

describe('useBatchManager — upload in-flight gating', () => {
  beforeEach(() => {
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
    mockXHRInstances.length = 0;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('clears isBatchUploading after a multi-file loop completes', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    const file1 = new File(['##VCF'], 'sample1.vcf', { type: 'application/octet-stream' });
    const file2 = new File(['##VCF'], 'sample2.vcf', { type: 'application/octet-stream' });
    const startCount = mockXHRInstances.length;
    let promise;
    await act(async () => {
      promise = result.current.addBatchVcfFiles([file1, file2]);
      // Resolve the first file so the loop advances to the second.
      await flushPromises();
      mockXHRInstances[startCount].triggerSuccess({ upload_id: 'up-vcf-1', file_type: 'vcf', size_bytes: 5 });
      // Resolve the second file so the loop finishes.
      await flushPromises();
      mockXHRInstances[startCount + 1].triggerSuccess({ upload_id: 'up-vcf-2', file_type: 'vcf', size_bytes: 5 });
      await promise;
    });

    // After every file in the loop has completed the in-flight flag is cleared.
    expect(result.current.isBatchUploading).toBe(false);
    expect(result.current.batchVcfFiles).toHaveLength(2);
  });

  it('isBatchUploading is false when no batch upload has run', () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    expect(result.current.isBatchUploading).toBe(false);
  });

  it('cancelBatchUpload aborts an in-flight batch upload and clears isBatchUploading', async () => {
    // apiUpload assigns its own onload/onerror handlers on the XHR, so aborting
    // must fire the handler apiUpload installed. A real browser's xhr.abort()
    // fires the ``abort`` event, not ``error``; the stub mirrors that so the
    // test fails if apiUpload does not settle its promise on abort (which
    // would leave the in-flight flag stuck on).
    // A real browser's xhr.abort() fires the ``abort`` event, not ``error``.
    // The stub mirrors that so the test fails if apiUpload does not settle its
    // promise on abort (which would leave the in-flight flag stuck on).
    const xhr = {
      open: () => {},
      send: () => {},
      setRequestHeader: () => {},
      upload: { onprogress: null },
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
    const { result } = renderHook(() => useBatchManager(stubs));

    const file = new File(['##VCF'], 'sample1.vcf', { type: 'application/octet-stream' });
    // apiUpload creates the XHR and captures it via onAbort synchronously, so the
    // cancel ref is set before this act returns.
    let uploadPromise;
    act(() => {
      uploadPromise = result.current.addBatchVcfFiles([file]);
    });
    expect(result.current.isBatchUploading).toBe(true);

    await act(async () => {
      result.current.cancelBatchUpload();
      await uploadPromise;
    });
    expect(result.current.isBatchUploading).toBe(false);
    // A canceled upload is not a batch error.
    expect(result.current.batchError).toBeNull();
  });

  it('cancelBatchUpload stops the remaining files in a multi-file loop', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    const file1 = new File(['##VCF'], 'sample1.vcf', { type: 'application/octet-stream' });
    const file2 = new File(['##VCF'], 'sample2.vcf', { type: 'application/octet-stream' });
    // Start the loop without resolving the first upload; the first XHR is
    // created synchronously before the first await.
    let loopPromise;
    act(() => {
      loopPromise = result.current.addBatchVcfFiles([file1, file2]);
    });
    expect(mockXHRInstances).toHaveLength(1);
    expect(result.current.isBatchUploading).toBe(true);

    await act(async () => {
      result.current.cancelBatchUpload();
      await loopPromise;
    });

    // Canceling must stop the loop: no second XHR is created for file2, the
    // in-flight flag clears, nothing is added to the overview, and the cancel
    // is not reported as a batch error.
    expect(mockXHRInstances).toHaveLength(1);
    expect(result.current.isBatchUploading).toBe(false);
    expect(result.current.batchVcfFiles).toHaveLength(0);
    expect(result.current.batchError).toBeNull();
  });

  it('a canceled BAM upload does not suppress later genuine batch errors', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    // 1. Cancel an in-flight BAM upload (attachBatchBam) — no error surfaced.
    const bamFile = new File(['BAM'], 'sample1.bam', { type: 'application/octet-stream' });
    let bamPromise;
    act(() => {
      bamPromise = result.current.attachBatchBam('batch-row-1', bamFile);
    });
    await act(async () => {
      result.current.cancelBatchUpload();
      await bamPromise;
    });
    expect(result.current.batchError).toBeNull();

    // 2. Trigger a genuine failure afterwards; it must still be reported.
    // attachBatchBam uploads via XHR, so fail the new MockXHR directly. The
    // status must be set after attachBatchBam creates its XHR (a fresh
    // MockXHR defaults to status 200).
    const failFile = new File(['BAM'], 'broken.bam', { type: 'application/octet-stream' });
    await act(async () => {
      const failPromise = result.current.attachBatchBam('batch-row-1', failFile);
      mockXHRInstance.status = 500;
      mockXHRInstance.responseText = JSON.stringify({ detail: 'Upload failed: 500' });
      mockXHRInstance.onload({});
      await failPromise;
    });
    expect(result.current.batchError).not.toBeNull();
  });

  it('a canceled reference upload does not suppress later genuine batch errors', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    // 1. Cancel an in-flight reference FASTA upload — no error surfaced.
    const refFile = new File(['>r\nACGT'], 'ref.fasta', { type: 'application/octet-stream' });
    let refPromise;
    act(() => {
      refPromise = result.current.uploadBatchReferenceFasta(refFile);
    });
    await act(async () => {
      result.current.cancelBatchUpload();
      await refPromise;
    });
    expect(result.current.batchError).toBeNull();

    // 2. Trigger a genuine failure afterwards; it must still be reported.
    // uploadBatchReferenceFasta uploads via XHR, so fail the new MockXHR
    // directly. The status must be set after the XHR is created (a fresh
    // MockXHR defaults to status 200).
    const failFile = new File(['>r\nGGGG'], 'broken-ref.fasta', { type: 'application/octet-stream' });
    await act(async () => {
      const failPromise = result.current.uploadBatchReferenceFasta(failFile);
      mockXHRInstance.status = 500;
      mockXHRInstance.responseText = JSON.stringify({ detail: 'Upload failed: 500' });
      mockXHRInstance.onload({});
      await failPromise;
    });
    expect(result.current.batchError).not.toBeNull();
  });

  it('submitBatch clears the uploaded files once the batch is analyzed', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');
    // VCF mode requires a shared reference FASTA; upload one so submitBatch does
    // not throw reading batchReferenceFasta.uploadId.
    const refFile = new File(['>r\nACGT'], 'ref.fasta', { type: 'application/octet-stream' });
    await act(async () => {
      const refPromise = result.current.uploadBatchReferenceFasta(refFile);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-ref-1', file_type: 'fasta', size_bytes: 8 });
      await refPromise;
    });
    await waitFor(() => {
      expect(result.current.batchReferenceFasta).not.toBeNull();
    });

    global.fetch.mockImplementation(async (_url, options) => {
      if (options && options.body) {
        JSON.parse(options.body);
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          samples: [{ job_id: 'j1', sample_name: 'sample1', status: 'succeeded' }],
          total: 1,
        }),
      };
    });

    await act(async () => {
      await result.current.submitBatch();
    });

    // After the batch is analyzed the upload overview is cleared (files gone, but
    // the analysis results in batchSamples remain).
    expect(result.current.batchVcfFiles).toHaveLength(0);
    expect(result.current.batchSamples).toHaveLength(1);
    expect(result.current.batchSamples[0]).not.toHaveProperty('report_url');
  });
});

describe('useBatchManager — M4a rowId, capacity, generation, Retry-After & stop polling', () => {
  beforeEach(() => {
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
    mockXHRInstances.length = 0;
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it('W1/WA-007: attachBatchBam targets immutable rowId when an earlier row is removed mid-upload', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');
    await uploadVcf(result, 'sample2.vcf', 'up-vcf-2');
    const row1Id = result.current.batchVcfFiles[0].rowId;
    const row2Id = result.current.batchVcfFiles[1].rowId;

    const bamFile = new File(['BAM'], 'override.bam', { type: 'application/octet-stream' });
    let bamPromise;
    act(() => {
      bamPromise = result.current.attachBatchBam(row2Id, bamFile);
    });

    // Delete row 1 while row 2's BAM upload is in flight, shifting row 2 to index 0.
    act(() => {
      result.current.removeBatchFile(row1Id);
    });

    await act(async () => {
      mockXHRInstance.triggerSuccess({ upload_id: 'up-bam-2', file_type: 'bam', size_bytes: 3 });
      await bamPromise;
    });

    expect(result.current.batchVcfFiles).toHaveLength(1);
    expect(result.current.batchVcfFiles[0].rowId).toBe(row2Id);
    expect(result.current.batchVcfFiles[0].bamId).toBe('up-bam-2');
  });

  it('W1/WA-007: addBatchBamFiles attaches by rowId after earlier row deletion and skips deleted target row', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');
    await uploadVcf(result, 'sample2.vcf', 'up-vcf-2');
    const row1Id = result.current.batchVcfFiles[0].rowId;
    const row2Id = result.current.batchVcfFiles[1].rowId;

    const bam1 = new File(['BAM'], 'sample1.bam', { type: 'application/octet-stream' });
    const bam2 = new File(['BAM'], 'sample2.bam', { type: 'application/octet-stream' });
    const startCount = mockXHRInstances.length;
    let pairing;
    await act(async () => {
      const promise = result.current.addBatchBamFiles([bam1, bam2]);
      await flushPromises();
      // Remove row 1 before bam1 completes; bam1 becomes unmatched, bam2 still pairs to row 2 (now index 0).
      result.current.removeBatchFile(row1Id);
      mockXHRInstances[startCount].triggerSuccess({ upload_id: 'up-bam-1', file_type: 'bam', size_bytes: 3 });
      await flushPromises();
      mockXHRInstances[startCount + 1].triggerSuccess({ upload_id: 'up-bam-2', file_type: 'bam', size_bytes: 3 });
      pairing = await promise;
    });

    expect(pairing.unmatched).toEqual(['sample1.bam']);
    expect(pairing.paired).toEqual(['sample2.bam']);
    expect(result.current.batchVcfFiles).toHaveLength(1);
    expect(result.current.batchVcfFiles[0].rowId).toBe(row2Id);
    expect(result.current.batchVcfFiles[0].bamId).toBe('up-bam-2');
  });

  it('WA-048: performs zero uploads when existing files meet or exceed batchMaxSamples', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');
    await uploadVcf(result, 'sample2.vcf', 'up-vcf-2');
    act(() => {
      result.current.setBatchMaxSamples(1);
    });

    const beforeCount = mockXHRInstances.length;
    await act(async () => {
      await result.current.addBatchVcfFiles([
        new File(['##VCF'], 'extra1.vcf'),
        new File(['##VCF'], 'extra2.vcf'),
      ]);
    });

    expect(mockXHRInstances).toHaveLength(beforeCount);
    expect(result.current.batchVcfFiles).toHaveLength(2);
  });

  it('WA-048: cancelBatchUpload aborts concurrent VCF and reference FASTA uploads cleanly', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    const vcf = new File(['##VCF'], 's1.vcf');
    const ref = new File(['>r\nACGT'], 'ref.fasta');
    const abortSpies = [];
    vi.stubGlobal('XMLHttpRequest', class {
      constructor() {
        const xhr = new MockXHR();
        const origAbort = xhr.abort.bind(xhr);
        xhr.abort = vi.fn(() => origAbort());
        abortSpies.push(xhr.abort);
        mockXHRInstances.push(xhr);
        return xhr;
      }
    });

    let p1;
    let p2;
    act(() => {
      p1 = result.current.addBatchVcfFiles([vcf]);
      p2 = result.current.uploadBatchReferenceFasta(ref);
    });
    expect(result.current.isBatchUploading).toBe(true);
    expect(abortSpies).toHaveLength(2);

    await act(async () => {
      result.current.cancelBatchUpload();
      await Promise.all([p1, p2]);
    });

    expect(abortSpies[0]).toHaveBeenCalledTimes(1);
    expect(abortSpies[1]).toHaveBeenCalledTimes(1);
    expect(result.current.isBatchUploading).toBe(false);
    expect(result.current.batchError).toBeNull();
  });

  it('removes a FASTA row by rowId in fasta mode', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));
    act(() => {
      result.current.setBatchMode('fasta');
    });

    const f1 = new File(['>a\nACGT'], 'a.fasta');
    const f2 = new File(['>b\nACGT'], 'b.fasta');
    await act(async () => {
      const p = result.current.addBatchFastaFiles([f1, f2]);
      await flushPromises();
      mockXHRInstances[0].triggerSuccess({ upload_id: 'up-fa-1', file_type: 'fasta', size_bytes: 8 });
      await flushPromises();
      mockXHRInstances[1].triggerSuccess({ upload_id: 'up-fa-2', file_type: 'fasta', size_bytes: 8 });
      await p;
    });

    const firstRowId = result.current.batchFastaFiles[0].rowId;
    act(() => {
      result.current.removeBatchFile(firstRowId);
    });
    expect(result.current.batchFastaFiles).toHaveLength(1);
    expect(result.current.batchFastaFiles[0].name).toBe('b.fasta');
  });

  it('W2: setBatchMode preserves files on same-mode call and aborts in-flight upload on real mode change', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'keep.vcf', 'up-keep');
    act(() => {
      result.current.setBatchMode('vcf');
    });
    expect(result.current.batchVcfFiles).toHaveLength(1);

    let pendingUpload;
    act(() => {
      pendingUpload = result.current.addBatchVcfFiles([new File(['##VCF'], 'late.vcf')]);
    });
    await act(async () => {
      result.current.setBatchMode('fasta');
      await pendingUpload;
    });

    expect(result.current.batchMode).toBe('fasta');
    expect(result.current.batchVcfFiles).toHaveLength(1);
    expect(result.current.isBatchUploading).toBe(false);
  });

  it('W2: resetBatch during polling aborts Batch A so stale responses cannot clobber Batch B', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    act(() => {
      result.current.setBatchMode('fasta');
    });
    await act(async () => {
      const p = result.current.addBatchFastaFiles([new File(['>a\nACGT'], 'batchA.fasta')]);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-a', file_type: 'fasta', size_bytes: 8 });
      await p;
    });

    let pollAbortSignal;
    global.fetch.mockImplementation(async (url, options) => {
      if (String(url).includes('/api/profile/batch/fasta')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            samples: [{ job_id: 'job-a', sample_name: 'batchA', status: 'queued' }],
          }),
        };
      }
      pollAbortSignal = options?.signal;
      return new Promise((_, reject) => {
        options?.signal?.addEventListener('abort', () => {
          const err = new Error('aborted');
          err.name = 'AbortError';
          reject(err);
        }, { once: true });
      });
    });

    let submitPromise;
    act(() => {
      submitPromise = result.current.submitBatch();
    });
    await waitFor(() => expect(result.current.isBatchPolling).toBe(true));
    await waitFor(() => expect(pollAbortSignal).toBeDefined());

    await act(async () => {
      result.current.resetBatch();
      await submitPromise;
    });

    expect(pollAbortSignal.aborted).toBe(true);
    expect(result.current.batchSubmitted).toBe(false);
    expect(result.current.batchSamples).toEqual([]);
    expect(result.current.batchError).toBeNull();
  });

  it('W4: submitBatch honors Retry-After header on 429 and counts down cooldown on a single interval', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    act(() => {
      result.current.setBatchMode('json');
    });
    await act(async () => {
      const p = result.current.addBatchJsonFiles([new File(['{}'], 's1.json')]);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-j1', file_type: 'json', size_bytes: 2 });
      await p;
    });

    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 429,
      headers: new Headers({ 'Retry-After': '17' }),
      json: async () => ({ detail: 'rate limited' }),
    });

    vi.useFakeTimers();
    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval');

    await act(async () => {
      await result.current.submitBatch();
    });

    expect(result.current.batchRateLimitCooldown).toBe(17);
    expect(setIntervalSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(result.current.batchRateLimitCooldown).toBe(14);
    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
  });

  it('W4: stopBatchPolling halts polling while keeping batchSamples visible', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    act(() => {
      result.current.setBatchMode('fasta');
    });
    await act(async () => {
      const p = result.current.addBatchFastaFiles([new File(['>a\nACGT'], 's1.fasta')]);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-f1', file_type: 'fasta', size_bytes: 8 });
      await p;
    });

    global.fetch.mockImplementation(async (url, options) => {
      if (String(url).includes('/api/profile/batch/fasta')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            samples: [{ job_id: 'job-stop-1', sample_name: 's1', status: 'running' }],
          }),
        };
      }
      return new Promise((_, reject) => {
        options?.signal?.addEventListener('abort', () => {
          const err = new Error('aborted');
          err.name = 'AbortError';
          reject(err);
        }, { once: true });
      });
    });

    let submitPromise;
    act(() => {
      submitPromise = result.current.submitBatch();
    });
    await waitFor(() => expect(result.current.isBatchPolling).toBe(true));

    await act(async () => {
      result.current.stopBatchPolling();
      await submitPromise;
    });

    expect(result.current.isBatchPolling).toBe(false);
    expect(result.current.batchSubmitting).toBe(false);
    expect(result.current.batchSubmitted).toBe(true);
    expect(result.current.batchSamples).toHaveLength(1);
    expect(result.current.batchSamples[0].status).toBe('running');
  });

  it('surfaces a clean error if submitBatch is called in VCF mode without reference FASTA', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 's1.vcf', 'up-vcf-1');
    await act(async () => {
      await result.current.submitBatch();
    });

    expect(result.current.batchError).toMatch(/reference FASTA/i);
    expect(result.current.batchSubmitting).toBe(false);
  });

  it('W2: aborts active polling when hook unmounts', async () => {
    const stubs = makeStubs();
    const { result, unmount } = renderHook(() => useBatchManager(stubs));

    act(() => {
      result.current.setBatchMode('fasta');
    });
    await act(async () => {
      const p = result.current.addBatchFastaFiles([new File(['>a\nACGT'], 's1.fasta')]);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-f1', file_type: 'fasta', size_bytes: 8 });
      await p;
    });

    let capturedSignal;
    global.fetch.mockImplementation(async (url, options) => {
      if (String(url).includes('/api/profile/batch/fasta')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            samples: [{ job_id: 'job-u1', sample_name: 's1', status: 'queued' }],
          }),
        };
      }
      capturedSignal = options?.signal;
      return new Promise((_, reject) => {
        options?.signal?.addEventListener('abort', () => {
          const err = new Error('aborted');
          err.name = 'AbortError';
          reject(err);
        }, { once: true });
      });
    });

    let submitPromise;
    act(() => {
      submitPromise = result.current.submitBatch();
    });
    await waitFor(() => expect(result.current.isBatchPolling).toBe(true));
    await waitFor(() => expect(capturedSignal).toBeDefined());

    unmount();
    expect(capturedSignal.aborted).toBe(true);
    await submitPromise;
  });

  it('W1/WA-007: preserves rowId parity under React.StrictMode and same-tick pre-commit VCF + BAM pairing', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs), { wrapper: StrictMode });

    const vcf1 = new File(['##VCF'], 'sample1.vcf', { type: 'application/octet-stream' });
    const vcf2 = new File(['##VCF'], 'sample2.vcf', { type: 'application/octet-stream' });
    const bam1 = new File(['BAM'], 'sample1.bam', { type: 'application/octet-stream' });

    // Trigger VCF uploads and BAM pairing inside a single act() before useEffect commits,
    // verifying ref and React state share identical, pure rowIds under StrictMode.
    let pairing;
    await act(async () => {
      const pVcf1 = result.current.addBatchVcfFiles([vcf1]);
      mockXHRInstances[0].triggerSuccess({ upload_id: 'up-vcf-1', file_type: 'vcf', size_bytes: 5 });
      await pVcf1;

      const pBam1 = result.current.addBatchBamFiles([bam1]);
      mockXHRInstances[1].triggerSuccess({ upload_id: 'up-bam-1', file_type: 'bam', size_bytes: 3 });
      pairing = await pBam1;
    });

    expect(pairing.paired).toEqual(['sample1.bam']);
    expect(result.current.batchVcfFiles).toHaveLength(1);
    expect(result.current.batchVcfFiles[0].rowId).toBe('batch-row-1');
    expect(result.current.batchVcfFiles[0].bamId).toBe('up-bam-1');
    expect(result.current.batchVcfFiles[0].bamName).toBe('sample1.bam');

    await act(async () => {
      const pVcf2 = result.current.addBatchVcfFiles([vcf2]);
      mockXHRInstances[2].triggerSuccess({ upload_id: 'up-vcf-2', file_type: 'vcf', size_bytes: 5 });
      await pVcf2;
    });
    expect(result.current.batchVcfFiles[1].rowId).toBe('batch-row-2');

    // Verify FASTA and JSON row creation under StrictMode also assign clean sequential rowIds.
    act(() => {
      result.current.setBatchMode('fasta');
    });
    await act(async () => {
      const pFa = result.current.addBatchFastaFiles([new File(['>a\nACGT'], 'a.fasta')]);
      mockXHRInstances[3].triggerSuccess({ upload_id: 'up-fa-1', file_type: 'fasta', size_bytes: 8 });
      await pFa;
    });
    expect(result.current.batchFastaFiles[0].rowId).toBe('batch-row-3');

    act(() => {
      result.current.setBatchMode('json');
    });
    await act(async () => {
      const pJson = result.current.addBatchJsonFiles([new File(['{}'], 'a.json')]);
      mockXHRInstances[4].triggerSuccess({ upload_id: 'up-j-1', file_type: 'json', size_bytes: 2 });
      await pJson;
    });
    expect(result.current.batchJsonFiles[0].rowId).toBe('batch-row-4');
  });

  it('W2: ignores late 429 response in postBatchRequest after resetBatch() without leaking cooldown or error', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    act(() => {
      result.current.setBatchMode('json');
    });
    await act(async () => {
      const p = result.current.addBatchJsonFiles([new File(['{}'], 's1.json')]);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-j1', file_type: 'json', size_bytes: 2 });
      await p;
    });

    let resolveFetch;
    global.fetch.mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );

    let submitPromise;
    act(() => {
      submitPromise = result.current.submitBatch();
    });
    await waitFor(() => expect(resolveFetch).toBeDefined());

    await act(async () => {
      result.current.resetBatch();
      resolveFetch({
        ok: false,
        status: 429,
        headers: new Headers({ 'Retry-After': '45' }),
        json: async () => ({ detail: 'rate limited' }),
      });
      await submitPromise;
    });

    expect(result.current.batchRateLimitCooldown).toBe(0);
    expect(result.current.batchError).toBeNull();
    expect(result.current.batchSubmitting).toBe(false);
  });

  it('W2: rapid double submitBatch() keeps batchSubmitting true until the second call completes', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    act(() => {
      result.current.setBatchMode('fasta');
    });
    await act(async () => {
      const p = result.current.addBatchFastaFiles([new File(['>a\nACGT'], 's1.fasta')]);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-f1', file_type: 'fasta', size_bytes: 8 });
      await p;
    });

    let resolveSecondSubmit;
    let callCount = 0;
    global.fetch.mockImplementation((_url, options) => {
      callCount += 1;
      if (callCount === 1) {
        return new Promise((_, reject) => {
          options?.signal?.addEventListener('abort', () => {
            const err = new Error('aborted');
            err.name = 'AbortError';
            reject(err);
          }, { once: true });
        });
      }
      return new Promise((resolve) => {
        resolveSecondSubmit = resolve;
      });
    });

    let p1;
    let p2;
    act(() => {
      p1 = result.current.submitBatch();
      p2 = result.current.submitBatch();
    });

    // Wait for Call 1 to settle in its finally block while Call 2 is still awaiting postBatchRequest.
    await act(async () => {
      await p1;
    });
    expect(result.current.batchSubmitting).toBe(true);

    await act(async () => {
      resolveSecondSubmit({
        ok: true,
        status: 200,
        json: async () => ({
          samples: [{ job_id: 'j2', sample_name: 's1', status: 'succeeded' }],
        }),
      });
      await p2;
    });

    expect(result.current.batchSubmitting).toBe(false);
    expect(result.current.batchSubmitted).toBe(true);
  });

  it('auto-pairs .bam files with .vcf.gz rows and strips .vcf.gz from batch sample_names', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await act(async () => {
      const vcfPromise = result.current.addBatchVcfFiles([
        new File(['##fileformat=VCFv4.2'], 'sample1.vcf.gz', { type: 'application/gzip' }),
      ]);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-vcf-gz-1', file_type: 'vcf', size_bytes: 20 });
      await vcfPromise;
    });

    await act(async () => {
      const refPromise = result.current.uploadBatchReferenceFasta(
        new File(['>ref\nACGT'], 'ref.fasta', { type: 'text/plain' }),
      );
      mockXHRInstance.triggerSuccess({ upload_id: 'up-ref-1', file_type: 'fasta', size_bytes: 9 });
      await refPromise;
    });

    let pairing;
    await act(async () => {
      const bamPromise = result.current.addBatchBamFiles([
        new File(['BAM'], 'sample1.bam', { type: 'application/octet-stream' }),
      ]);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-bam-1', file_type: 'bam', size_bytes: 3 });
      pairing = await bamPromise;
    });

    expect(pairing).toEqual({ paired: ['sample1.bam'], unmatched: [], collisions: [] });
    expect(result.current.batchVcfFiles).toHaveLength(1);
    expect(result.current.batchVcfFiles[0].bamId).toBe('up-bam-1');
    expect(result.current.batchError).toBeNull();

    let submittedBody;
    global.fetch.mockImplementation(async (_url, options) => {
      if (options?.body) {
        submittedBody = JSON.parse(options.body);
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          samples: [{ job_id: 'j1', sample_name: 'sample1', status: 'succeeded' }],
          total: 1,
        }),
      };
    });

    await act(async () => {
      await result.current.submitBatch();
    });

    expect(submittedBody.sample_names).toEqual(['sample1']);
    expect(submittedBody.input_display_names).toEqual(['sample1.vcf.gz']);
    expect(submittedBody.bam_ids).toEqual(['up-bam-1']);
  });

  it('aborts downloadAllBatchArtifacts on resetBatch/setBatchMode, enforces re-entrancy, and suppresses stale errors', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await act(async () => {
      const p = result.current.addBatchVcfFiles([new File(['##VCF'], 'sample1.vcf')]);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-vcf-1', file_type: 'vcf', size_bytes: 5 });
      await p;
    });
    await act(async () => {
      const p = result.current.uploadBatchReferenceFasta(new File(['>r\nACGT'], 'ref.fasta'));
      mockXHRInstance.triggerSuccess({ upload_id: 'up-ref-1', file_type: 'fasta', size_bytes: 8 });
      await p;
    });

    global.fetch.mockImplementation(async (url) => {
      if (String(url).includes('/api/profile/batch/vcf')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            samples: [{ job_id: 'j1', sample_name: 'sample1', status: 'queued' }],
            total: 1,
          }),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'succeeded',
          result: { artifact_ids: ['art-1', 'art-2'] },
        }),
      };
    });

    await act(async () => {
      await result.current.submitBatch();
    });
    await waitFor(() => {
      expect(result.current.batchSamples[0]?.artifactIds).toEqual(['art-1', 'art-2']);
    });

    const bundleSignals = [];
    let rejectLateBundle;
    global.fetch.mockImplementation((_url, options = {}) => {
      bundleSignals.push(options.signal);
      return new Promise((_resolve, reject) => {
        rejectLateBundle = reject;
      });
    });

    let dl1;
    let dl2;
    act(() => {
      dl1 = result.current.downloadAllBatchArtifacts();
      // Re-entrancy guard: second call while in-flight must not start another fetch.
      dl2 = result.current.downloadAllBatchArtifacts();
    });

    expect(bundleSignals).toHaveLength(1);
    expect(bundleSignals[0]).toBeInstanceOf(AbortSignal);
    expect(bundleSignals[0].aborted).toBe(false);
    expect(result.current.isBatchDownloadBusy).toBe(true);

    // resetBatch() aborts the active download and clears busy state immediately.
    act(() => {
      result.current.resetBatch();
    });
    expect(bundleSignals[0].aborted).toBe(true);
    expect(result.current.isBatchDownloadBusy).toBe(false);

    // Even if the stale fetch rejects with a non-Abort error, it must not leak into batchError.
    await act(async () => {
      rejectLateBundle(new Error('Old batch failure'));
      await Promise.all([dl1, dl2]);
    });

    expect(result.current.batchError).toBeNull();
    expect(result.current.isBatchDownloadBusy).toBe(false);

    // Also verify setBatchMode() aborts an active download and suppresses stale errors.
    await act(async () => {
      const p = result.current.addBatchVcfFiles([new File(['##VCF'], 'sample2.vcf')]);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-vcf-2', file_type: 'vcf', size_bytes: 5 });
      await p;
    });
    await act(async () => {
      const p = result.current.uploadBatchReferenceFasta(new File(['>r\nACGT'], 'ref2.fasta'));
      mockXHRInstance.triggerSuccess({ upload_id: 'up-ref-2', file_type: 'fasta', size_bytes: 8 });
      await p;
    });
    global.fetch.mockImplementation(async (url) => {
      if (String(url).includes('/api/profile/batch/vcf')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            samples: [{ job_id: 'j2', sample_name: 'sample2', status: 'queued' }],
            total: 1,
          }),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'succeeded',
          result: { artifact_ids: ['art-3'] },
        }),
      };
    });
    await act(async () => {
      await result.current.submitBatch();
    });
    await waitFor(() => {
      expect(result.current.batchSamples[0]?.artifactIds).toEqual(['art-3']);
    });

    let modeSignal;
    let rejectModeBundle;
    global.fetch.mockImplementation((_url, options = {}) => {
      modeSignal = options.signal;
      return new Promise((_resolve, reject) => {
        rejectModeBundle = reject;
      });
    });

    let dlMode;
    act(() => {
      dlMode = result.current.downloadAllBatchArtifacts();
    });
    expect(result.current.isBatchDownloadBusy).toBe(true);

    act(() => {
      result.current.setBatchMode('fasta');
    });
    expect(modeSignal.aborted).toBe(true);
    expect(result.current.isBatchDownloadBusy).toBe(false);

    await act(async () => {
      rejectModeBundle(new Error('Mode switch stale error'));
      await dlMode;
    });
    expect(result.current.batchError).toBeNull();
    expect(result.current.isBatchDownloadBusy).toBe(false);
  });
});
