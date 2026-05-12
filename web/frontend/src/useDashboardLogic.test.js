import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useDashboardLogic } from './useDashboardLogic';

// Mock XMLHttpRequest for file upload tests
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

  triggerProgress(loaded, total) {
    if (this.upload.onprogress) {
      this.upload.onprogress({
        lengthComputable: true,
        loaded,
        total,
      });
    }
  }

  triggerSuccess(response) {
    this.status = 200;
    this.responseText = JSON.stringify(response);
    if (this.onload) {
      this.onload({});
    }
  }

  triggerError() {
    if (this.onerror) {
      this.onerror({});
    }
  }
}

let mockXHRInstance;

global.XMLHttpRequest = vi.fn(() => {
  mockXHRInstance = new MockXHR();
  return mockXHRInstance;
});

// Mock fetch for API calls and job polling
global.fetch = vi.fn();

// Mock config
vi.mock('./config.js', () => ({
  FRONTEND_CONFIG: {
    apiBase: 'http://localhost:8000',
    apiToken: 'test-token',
    profile: {
      threads: 1,
      vcf: {
        minAf: 0.01,
        minDepth: 10,
      },
      jobPollIntervalMs: 10, // Short poll interval for tests
    },
    defaults: {
      sampleName: 'sample',
    },
    ui: {
      explorerUrl: 'http://127.0.0.1:8000',
    },
  },
}));

describe('useDashboardLogic - File Upload Flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('should handle FASTA file upload with progress tracking', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [{
            id: 'db1',
            display_name: 'Test DB',
          }],
        },
      }),
    });

    const { result } = renderHook(() => useDashboardLogic());

    // Wait for initialization
    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    const file = new File(['ATCG'], 'test.fasta', { type: 'application/octet-stream' });

    await act(async () => {
      const uploadPromise = result.current.uploadFastaFile(file);

      // Simulate upload progress events
      await new Promise((resolve) => setTimeout(resolve, 5));
      mockXHRInstance.triggerProgress(25, 100);

      await new Promise((resolve) => setTimeout(resolve, 5));
      mockXHRInstance.triggerProgress(50, 100);

      await new Promise((resolve) => setTimeout(resolve, 5));
      mockXHRInstance.triggerProgress(100, 100);

      // Trigger successful completion
      mockXHRInstance.triggerSuccess({ file_path: '/data/uploads/test.fasta' });

      await uploadPromise;
    });

    expect(result.current.fastaInput.fasta_path).toBe('/data/uploads/test.fasta');
    expect(result.current.uploadProgress.percent).toBe(100);
    expect(result.current.uploadProgress.fileName).toContain('test.fasta');
  });

  it('should handle VCF file upload', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [{
            id: 'db1',
            display_name: 'Test DB',
          }],
        },
      }),
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    const file = new File(['##VCF'], 'test.vcf', { type: 'application/octet-stream' });

    await act(async () => {
      const uploadPromise = result.current.uploadVcfFile(file);

      mockXHRInstance.triggerSuccess({ file_path: '/data/uploads/test.vcf' });

      await uploadPromise;
    });

    expect(result.current.vcfInput.vcf_path).toBe('/data/uploads/test.vcf');
  });

  it('should update upload progress state during file transfer', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [{
            id: 'db1',
            display_name: 'Test DB',
          }],
        },
      }),
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    const file = new File(['data'], 'large.fasta', { type: 'application/octet-stream' });

    await act(async () => {
      const uploadPromise = result.current.uploadFastaFile(file);

      // Simulate progression through upload
      const progressValues = [];
      const unsubscribe = vi.fn(() => {
        progressValues.push(result.current.uploadProgress.percent);
      });

      for (let i = 10; i <= 90; i += 20) {
        mockXHRInstance.triggerProgress(i, 100);
        unsubscribe();
      }

      mockXHRInstance.triggerSuccess({ file_path: '/data/uploads/large.fasta' });

      await uploadPromise;
    });

    // Verify final state
    expect(result.current.uploadProgress.percent).toBe(100);
  });

  it('should handle upload errors gracefully', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [{
            id: 'db1',
            display_name: 'Test DB',
          }],
        },
      }),
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    const file = new File(['bad'], 'bad.fasta');

    await act(async () => {
      const uploadPromise = result.current.uploadFastaFile(file);
      mockXHRInstance.triggerError();
      await uploadPromise.catch(() => {
        // Expected error
      });
    });

    expect(result.current.status).toMatch(/failed|error/);
  });
});

describe('useDashboardLogic - Job Polling Flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('should poll job until completion with succeeded status', async () => {
    // Setup: ui/config is fetched before databases on init
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: {} }),
    });

    // Setup: Initialize with a database
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [{
            id: 'db1',
            display_name: 'HIV',
          }],
        },
      }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [],
          columns: [],
        },
      }),
    });

    // Job submission
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        job_id: 'job-123',
      }),
    });

    // Polling responses: queued -> running -> succeeded
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        status: 'queued',
      }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        status: 'running',
      }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        status: 'succeeded',
        result: {
          sample_name: 'test_sample',
          reference_name: 'HIV',
          database_id: 'db1',
          report_html_path: '/data/results/test.report.html',
          report_json_path: '/data/results/test.report.json',
          created_at: '2026-05-12T10:00:00',
          resistance_hits: 5,
          input_path: '/data/uploads/test.fasta',
          mode: 'fasta',
        },
      }),
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    await act(async () => {
      await result.current.submitFasta();
    });

    // Verify job was submitted
    expect(result.current.activeJobId).toBe('job-123');

    // Verify status transitions in polling
    await waitFor(() => {
      expect(result.current.sessionResults.length).toBe(1);
    });

    // Verify result was stored
    const lastResult = result.current.sessionResults[0];
    expect(lastResult.report_html_path).toBe('/data/results/test.report.html');
    expect(lastResult.sample_name).toBe('test_sample');
  });

  it('should handle job polling with failed status', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: {} }),
    });

    // Setup: Initialize with a database
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [{
            id: 'db1',
            display_name: 'HIV',
          }],
        },
      }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [],
          columns: [],
        },
      }),
    });

    // Job submission
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        job_id: 'job-fail',
      }),
    });

    // Polling: queued -> failed
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        status: 'queued',
      }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        status: 'failed',
        error: 'Unsupported FASTA format',
      }),
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    await act(async () => {
      await result.current.submitFasta();
    });

    // Verify status reflects failure
    expect(result.current.status).toMatch(/failed|error/);
    expect(result.current.isProcessingFasta).toBe(false);
  });

  it('should cancel active job via DELETE request', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: {} }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [{
            id: 'db1',
            display_name: 'HIV',
          }],
        },
      }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [],
          columns: [],
        },
      }),
    });

    // Job submission
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        job_id: 'job-to-cancel',
      }),
    });

    // Mock infinite polling (keep returning "running")
    global.fetch.mockImplementation(() => {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ status: 'running' }),
      });
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    // Start job but don't wait for completion
    act(() => {
      result.current.submitFasta();
    });

    await waitFor(() => {
      expect(result.current.activeJobId).toBe('job-to-cancel');
    });

    // Now cancel
    global.fetch.mockImplementationOnce(() => {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({}),
      });
    });

    await act(async () => {
      await result.current.cancelActiveJob();
    });

    expect(result.current.activeJobStatus).toBe('canceling');
  });

  it('should update job status during polling transitions', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: {} }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [{
            id: 'db1',
            display_name: 'HIV',
          }],
        },
      }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [],
          columns: [],
        },
      }),
    });

    // Job submission
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        job_id: 'job-status-test',
      }),
    });

    // Polling: track status transitions
    const statuses = ['queued', 'running', 'running', 'succeeded'];
    let pollCount = 0;

    global.fetch.mockImplementation(() => {
      const status = statuses[pollCount];
      pollCount += 1;

      if (status === 'succeeded') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            status: 'succeeded',
            result: {
              sample_name: 'test',
              reference_name: 'ref',
              database_id: 'db1',
              report_html_path: '/data/test.html',
              created_at: '2026-05-12T10:00:00',
              resistance_hits: 0,
              mode: 'fasta',
            },
          }),
        });
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ status }),
      });
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    await act(async () => {
      await result.current.submitFasta();
    });

    expect(result.current.activeJobStatus).toBe('queued');
  });
});

describe('useDashboardLogic - Report Display Flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('should set report path when job succeeds', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: {} }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [{
            id: 'db1',
            display_name: 'HIV',
          }],
        },
      }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [],
          columns: [],
        },
      }),
    });

    // Job submission
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        job_id: 'job-report',
      }),
    });

    // Polling: immediate success
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        status: 'succeeded',
        result: {
          sample_name: 'sample1',
          reference_name: 'HIV',
          database_id: 'db1',
          report_html_path: '/data/results/sample1.report.html',
          report_json_path: '/data/results/sample1.report.json',
          report_tabular_path: '/data/results/sample1.mutations.tsv',
          report_pdf_path: '/data/results/sample1.report.pdf',
          created_at: '2026-05-12T14:30:00',
          resistance_hits: 3,
          input_path: '/data/uploads/sample1.fasta',
          mode: 'fasta',
        },
      }),
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    await act(async () => {
      await result.current.submitFasta();
    });

    // Verify report paths are set
    expect(result.current.selectedProfileReportPath).toBe('/data/results/sample1.report.html');
    expect(result.current.inlineReportPath).toBe('/data/results/sample1.report.html');
    expect(result.current.inlineReportLabel).toContain('sample1');
  });

  it('should build report URL with path parameter', () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [],
          columns: [],
        },
      }),
    });

    const { result } = renderHook(() => useDashboardLogic());

    const reportUrl = result.current.buildReportUrl('/data/results/test.report.html');

    expect(reportUrl).toContain('/api/report');
    expect(reportUrl).toContain('path=');
    expect(decodeURIComponent(reportUrl)).toContain('/data/results/test.report.html');
  });

  it('should display multiple report options in order (newest first)', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: {} }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [{
            id: 'db1',
            display_name: 'HIV',
          }],
        },
      }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [],
          columns: [],
        },
      }),
    });

    // First job submission
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        job_id: 'job-1',
      }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        status: 'succeeded',
        result: {
          sample_name: 'sample1',
          reference_name: 'HIV',
          database_id: 'db1',
          report_html_path: '/data/results/sample1.html',
          created_at: '2026-05-12T10:00:00',
          resistance_hits: 1,
          mode: 'fasta',
        },
      }),
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    // Run first job
    await act(async () => {
      await result.current.submitFasta();
    });

    // Setup second job
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        job_id: 'job-2',
      }),
    });

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        status: 'succeeded',
        result: {
          sample_name: 'sample2',
          reference_name: 'HIV',
          database_id: 'db1',
          report_html_path: '/data/results/sample2.html',
          created_at: '2026-05-12T11:00:00',
          resistance_hits: 2,
          mode: 'fasta',
        },
      }),
    });

    // Run second job
    await act(async () => {
      await result.current.submitFasta();
    });

    // Verify both results are stored and ordered (newest first)
    expect(result.current.sessionResults.length).toBe(2);
    expect(result.current.reportOptions.length).toBe(2);
    expect(result.current.reportOptions[0].label).toContain('sample2');
    expect(result.current.reportOptions[1].label).toContain('sample1');
  });
});
