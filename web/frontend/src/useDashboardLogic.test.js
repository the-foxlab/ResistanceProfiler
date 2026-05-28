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
      result.current.setActiveProfileMode('fasta');
    });

    await act(async () => {
      await result.current.runSelectedProfile();
    });

    // Verify job submission request used FASTA endpoint
    expect(global.fetch.mock.calls.some(([url, options]) => (
      String(url).includes('/api/profile/fasta') && options?.method === 'POST'
    ))).toBe(true);

    // Verify status transitions in polling
    await waitFor(() => {
      expect(result.current.reportOptions.length).toBe(1);
    });

    // Verify result was stored
    expect(result.current.reportOptions[0].path).toBe('/data/results/test.report.html');
    expect(result.current.reportOptions[0].label).toContain('test_sample');
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
      result.current.setActiveProfileMode('fasta');
    });

    await act(async () => {
      await result.current.runSelectedProfile();
    });

    // Verify status reflects failure
    expect(result.current.status).toContain('Unsupported FASTA format');
    expect(result.current.isProfileBusy).toBe(false);
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

    act(() => {
      result.current.setActiveProfileMode('fasta');
    });

    // Start job but don't wait for completion
    act(() => {
      result.current.runSelectedProfile();
    });

    await waitFor(() => {
      expect(result.current.canCancelJob).toBe(true);
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

    await waitFor(() => {
      expect(result.current.status).toContain('cancellation requested');
    });

    expect(global.fetch.mock.calls.some(([url, options]) => (
      url.includes('/api/jobs/job-to-cancel') && options?.method === 'DELETE'
    ))).toBe(true);
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

    // Polling: track status transitions
    const statuses = ['queued', 'running', 'running', 'succeeded'];
    let pollCount = 0;

    global.fetch.mockImplementation((url) => {
      const requestUrl = String(url);

      if (requestUrl.includes('/api/profile/fasta')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            job_id: 'job-status-test',
          }),
        });
      }

      if (requestUrl.includes('/api/jobs/job-status-test')) {
        const status = statuses[pollCount] || 'succeeded';
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
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ data: {} }),
      });
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    await act(async () => {
      result.current.setActiveProfileMode('fasta');
    });

    await act(async () => {
      await result.current.runSelectedProfile();
    });

    const pollCalls = global.fetch.mock.calls.filter(([url]) => String(url).includes('/api/jobs/job-status-test'));
    expect(pollCalls.length).toBeGreaterThanOrEqual(3);
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
    global.fetch.mockImplementation((url, options = {}) => {
      const request_url = String(url);

      if (request_url.includes('/api/ui/config')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ data: {} }),
        });
      }

      if (request_url.includes('/api/databases')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            data: {
              items: [{ id: 'db1', display_name: 'HIV' }],
            },
          }),
        });
      }

      if (request_url.includes('/api/mutations')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            data: {
              items: [],
              columns: [],
            },
          }),
        });
      }

      if (request_url.includes('/api/profile/fasta') && options.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ job_id: 'job-report' }),
        });
      }

      if (request_url.includes('/api/jobs/job-report')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: 'succeeded',
            result: {
              sample_name: 'sample1',
              reference_name: 'HIV',
              database_id: 'db1',
              report_html_path: '/data/results/sample1.report.html',
              report_json_path: '/data/results/sample1.report.json',
              report_pdf_path: '/data/results/sample1.report.pdf',
              created_at: '2026-05-12T14:30:00',
              resistance_hits: 3,
              input_path: '/data/uploads/sample1.fasta',
              mode: 'fasta',
            },
          }),
        });
      }

      return Promise.resolve({
        ok: true,
        json: async () => ({}),
      });
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    await act(async () => {
      result.current.setActiveProfileMode('fasta');
    });

    await waitFor(() => {
      expect(result.current.activeProfileMode).toBe('fasta');
    });

    await act(async () => {
      await result.current.runSelectedProfile();
    });

    // Verify report paths are set
    await waitFor(() => {
      expect(result.current.selectedProfileReportPath).toBe('/data/results/sample1.report.html');
      expect(result.current.inlineReportPath).toBe('/data/results/sample1.report.html');
    });
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
    let profileSubmissionCount = 0;

    global.fetch.mockImplementation((url, options = {}) => {
      const request_url = String(url);

      if (request_url.includes('/api/ui/config')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ data: {} }),
        });
      }

      if (request_url.includes('/api/databases')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            data: {
              items: [{ id: 'db1', display_name: 'HIV' }],
            },
          }),
        });
      }

      if (request_url.includes('/api/mutations')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            data: {
              items: [],
              columns: [],
            },
          }),
        });
      }

      if (request_url.includes('/api/profile/fasta') && options.method === 'POST') {
        profileSubmissionCount += 1;
        const job_id = profileSubmissionCount === 1 ? 'job-1' : 'job-2';
        return Promise.resolve({
          ok: true,
          json: async () => ({ job_id }),
        });
      }

      if (request_url.includes('/api/jobs/job-1')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
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
      }

      if (request_url.includes('/api/jobs/job-2')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
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
      }

      return Promise.resolve({
        ok: true,
        json: async () => ({}),
      });
    });

    const { result } = renderHook(() => useDashboardLogic());

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    await act(async () => {
      result.current.setActiveProfileMode('fasta');
    });

    // Run first job
    await act(async () => {
      await result.current.runSelectedProfile();
    });

    // Run second job
    await act(async () => {
      await result.current.runSelectedProfile();
    });

    // Verify both results are stored and ordered (newest first)
    await waitFor(() => {
      expect(result.current.reportOptions.length).toBe(2);
    });

    expect(result.current.reportOptions[0].label).toContain('sample2');
    expect(result.current.reportOptions[1].label).toContain('sample1');
  });
});
