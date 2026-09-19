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

vi.mock('../config.js', () => ({
  FRONTEND_CONFIG: {
    apiBase: 'http://localhost:8000',
    profile: {
      threads: 1,
      vcf: {
        minAf: 0.01,
        minDepth: 10,
      },
      jobPollIntervalMs: 10,
    },
    defaults: {
      sampleName: 'sample',
    },
    ui: {
      explorerUrl: 'http://127.0.0.1:8000',
    },
  },
}));

// Minimal stubs for the session/upload callbacks the hook receives.
function makeStubs() {
  return {
    selectedDatabaseId: 'db1',
    addReportPath: vi.fn(),
    addUploadedPath: vi.fn(),
    addResultArtifactPaths: vi.fn(),
    setSessionResults: vi.fn(),
    setUploadProgress: vi.fn(),
    setStatusError: vi.fn(),
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

    const override = new File(['BAM'], 'manual.bam', { type: 'application/octet-stream' });
    await act(async () => {
      const promise = result.current.attachBatchBam(0, override);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-bam-manual', file_type: 'bam', size_bytes: 3 });
      await promise;
    });

    expect(result.current.batchVcfFiles[0].bamId).toBe('up-bam-manual');
    expect(result.current.batchVcfFiles[0].bamName).toBe('manual.bam');
  });

  it('removeBatchBam clears only the BAM on the targeted row', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', 'up-vcf-1');
    await uploadVcf(result, 'sample2.vcf', 'up-vcf-2');
    await uploadBam(result, 'sample1.bam', 'up-bam-1');

    act(() => {
      result.current.removeBatchBam(0);
    });

    expect(result.current.batchVcfFiles[0].bamId).toBeNull();
    expect(result.current.batchVcfFiles[0].bamName).toBeNull();
    // Other row untouched.
    expect(result.current.batchVcfFiles[1].bamId).toBeNull();
  });

  it('removeBatchFile drops the VCF row together with its BAM', async () => {
    const stubs = makeStubs();
    const { result } = renderHook(() => useBatchManager(stubs));

    await uploadVcf(result, 'sample1.vcf', '/uploads/sample1.vcf');
    await uploadVcf(result, 'sample2.vcf', '/uploads/sample2.vcf');
    await uploadBam(result, 'sample1.bam', '/uploads/sample1.bam');

    act(() => {
      result.current.removeBatchFile(0);
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

    act(() => {
      result.current.removeBatchFile(0);
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
      bamPromise = result.current.attachBatchBam(0, bamFile);
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
      const failPromise = result.current.attachBatchBam(0, failFile);
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
  });
});
