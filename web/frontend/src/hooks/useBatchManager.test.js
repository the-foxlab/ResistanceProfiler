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
    this.status = 200;
    this.responseText = '';
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

global.XMLHttpRequest = vi.fn(() => {
  mockXHRInstance = new MockXHR();
  mockXHRInstances.push(mockXHRInstance);
  return mockXHRInstance;
});

// Mock fetch used by apiPostRaw (submitBatch) and apiGet (job polling).
global.fetch = vi.fn();

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
    vi.clearAllMocks();
    global.fetch.mockReset();
    mockXHRInstances.length = 0;
  });

  afterEach(() => {
    vi.restoreAllMocks();
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
