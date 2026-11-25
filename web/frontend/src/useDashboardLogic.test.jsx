import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { useDashboardLogic, _resolveLegalLink, _resolveContactEmail } from './useDashboardLogic';
import { getArtifactBundleLimit, setArtifactBundleLimit } from './api';

// useDashboardLogic reads the URL via react-router; tests render the hook
// inside a MemoryRouter so no browser history is needed.
function renderDashboardHook() {
  return renderHook(() => useDashboardLogic(), {
    wrapper: ({ children }) => <MemoryRouter initialEntries={['/analysis']}>{children}</MemoryRouter>,
  });
}

// Mock XMLHttpRequest for file upload tests
class MockXHR {
  constructor() {
    this.open = vi.fn();
    this.send = vi.fn();
    this.setRequestHeader = vi.fn();
    this.getResponseHeader = vi.fn(() => null);
    this.upload = { onprogress: null };
    this.onload = null;
    this.onerror = null;
    this.onabort = null;
    this.status = 200;
    this.responseText = '';
    this.abort = vi.fn(() => {
      if (this.onabort) {
        this.onabort({});
      }
    });
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

// Plain class stub — vi.fn(() => ...) loses its implementation when vi.clearAllMocks()
// runs in Vitest 5, so a plain constructor is used instead and re-stubbed per test.
function setupXhrStub() {
  vi.stubGlobal('XMLHttpRequest', class {
    constructor() {
      mockXHRInstance = new MockXHR();
      return mockXHRInstance;
    }
  });
}

// Mock fetch for API calls and job polling
vi.stubGlobal('fetch', vi.fn());

// Mock config preserving production defaults (e.g. apiBase: '', batch, ui) while speeding up job polling
vi.mock('./config.js', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    FRONTEND_CONFIG: {
      ...actual.FRONTEND_CONFIG,
      profile: {
        ...actual.FRONTEND_CONFIG.profile,
        jobPollIntervalMs: 10, // Short poll interval for tests
      },
    },
  };
});

describe('useDashboardLogic - File Upload Flow', () => {
  beforeEach(() => {
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
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

    const { result } = renderDashboardHook();

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
      mockXHRInstance.triggerSuccess({ upload_id: 'up-fastA-1' });

      await uploadPromise;
    });

    expect(result.current.fastaInput.fasta_id).toBe('up-fastA-1');
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

    const { result } = renderDashboardHook();

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    const file = new File(['##VCF'], 'test.vcf', { type: 'application/octet-stream' });

    await act(async () => {
      const uploadPromise = result.current.uploadVcfFile(file);

      mockXHRInstance.triggerSuccess({ upload_id: 'up-vcf-1' });

      await uploadPromise;
    });

    expect(result.current.vcfInput.vcf_id).toBe('up-vcf-1');
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

    const { result } = renderDashboardHook();

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    const file = new File(['data'], 'large.fasta', { type: 'application/octet-stream' });

    let uploadPromise;
    await act(async () => {
      uploadPromise = result.current.uploadFastaFile(file);
    });

    for (let i = 10; i <= 90; i += 20) {
      act(() => {
        mockXHRInstance.triggerProgress(i, 100);
      });
      expect(result.current.uploadProgress.percent).toBe(i);
    }

    await act(async () => {
      mockXHRInstance.triggerSuccess({ upload_id: 'up-fastA-large' });
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

    const { result } = renderDashboardHook();

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

    expect(result.current.statusError).toMatch(/upload failed|network error/i);
  });

  it('clears isUploading once a single upload completes', async () => {
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

    const { result } = renderDashboardHook();

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    const file = new File(['ATCG'], 'gating.fasta', { type: 'application/octet-stream' });

    await act(async () => {
      const uploadPromise = result.current.uploadFastaFile(file);
      mockXHRInstance.triggerSuccess({ upload_id: 'up-gating-1' });
      await uploadPromise;
    });

    expect(result.current.fastaInput.fasta_id).toBe('up-gating-1');
    // Once the upload resolves the in-flight flag is cleared so submit re-enables.
    expect(result.current.isUploading).toBe(false);
  });

  it('clears isUploading when a single upload errors', async () => {
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

    const { result } = renderDashboardHook();

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

    expect(result.current.statusError).toMatch(/upload failed|network error/i);
    // The in-flight flag is cleared even when the upload errors, so submit re-enables.
    expect(result.current.isUploading).toBe(false);
  });
});

describe('useDashboardLogic - Job Polling Flow', () => {
  beforeEach(() => {
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  const setupUrlRoutedJobFetch = (onJobRoute) => {
    global.fetch.mockImplementation((url, options) => {
      const requestUrl = String(url);
      if (requestUrl.includes('/api/ui/config')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ data: {} }) });
      }
      if (requestUrl.includes('/api/ui/legal')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ data: { enabled: false } }) });
      }
      if (requestUrl.includes('/api/ui/contact')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ data: { enabled: false, email: null } }) });
      }
      if (requestUrl.includes('/api/databases')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            data: { items: [{ id: 'db1', display_name: 'HIV' }] },
          }),
        });
      }
      if (requestUrl.includes('/api/mutations')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ data: { items: [], columns: [] } }),
        });
      }
      return onJobRoute(requestUrl, options);
    });
  };

  it('should poll job until completion with succeeded status', async () => {
    const pollResponses = [
      { status: 'queued' },
      { status: 'running' },
      {
        status: 'succeeded',
        result: {
          sample_name: 'test_sample',
          reference_name: 'HIV',
          database_id: 'db1',
          report_html_path: '/data/results/test.report.html',
          report_json_path: '/data/results/test.report.json',
          report_tsv_path: '/data/results/test.results.tsv',
          created_at: '2026-05-12T10:00:00',
          resistance_hits: 5,
          input_path: '/data/uploads/test.fasta',
          mode: 'fasta',
        },
      },
    ];
    let pollIdx = 0;

    setupUrlRoutedJobFetch((requestUrl) => {
      if (requestUrl.includes('/api/profile/fasta')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ job_id: 'job-123' }) });
      }
      if (requestUrl.includes('/api/jobs/job-123')) {
        const next = pollResponses[Math.min(pollIdx, pollResponses.length - 1)];
        pollIdx += 1;
        return Promise.resolve({ ok: true, json: () => Promise.resolve(next) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ data: {} }) });
    });

    const { result } = renderDashboardHook();

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
    expect(result.current.reportOptions[0].tsvPath).toBe('/data/results/test.results.tsv');
  });

  it('should handle job polling with failed status', async () => {
    const pollResponses = [
      { status: 'queued' },
      { status: 'failed', error: 'Unsupported FASTA format' },
    ];
    let pollIdx = 0;

    setupUrlRoutedJobFetch((requestUrl) => {
      if (requestUrl.includes('/api/profile/fasta')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ job_id: 'job-fail' }) });
      }
      if (requestUrl.includes('/api/jobs/job-fail')) {
        const next = pollResponses[Math.min(pollIdx, pollResponses.length - 1)];
        pollIdx += 1;
        return Promise.resolve({ ok: true, json: () => Promise.resolve(next) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ data: {} }) });
    });

    const { result } = renderDashboardHook();

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
    expect(result.current.statusError).toContain('Unsupported FASTA format');
    expect(result.current.isProfileBusy).toBe(false);
  });

  it('should cancel active job via DELETE request', async () => {
    setupUrlRoutedJobFetch((requestUrl, options) => {
      if (requestUrl.includes('/api/profile/fasta')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ job_id: 'job-to-cancel' }) });
      }
      if (requestUrl.includes('/api/jobs/job-to-cancel') && options?.method === 'DELETE') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: 'running' }) });
    });

    const { result } = renderDashboardHook();

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

    await act(async () => {
      await result.current.cancelActiveJob();
    });

    await waitFor(() => {
      expect(result.current.canCancelJob).toBe(false);
    });

    expect(global.fetch.mock.calls.some(([url, options]) => (
      url.includes('/api/jobs/job-to-cancel') && options?.method === 'DELETE'
    ))).toBe(true);
  });

  it('should update job status during polling transitions', async () => {
    // Polling: track status transitions
    const statuses = ['queued', 'running', 'running', 'succeeded'];
    let pollCount = 0;

    setupUrlRoutedJobFetch((requestUrl) => {
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

    const { result } = renderDashboardHook();

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
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
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

    const { result } = renderDashboardHook();

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
  });

  it('should build report URL with path parameter', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        data: {
          items: [],
          columns: [],
        },
      }),
    });

    const { result } = renderDashboardHook();

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledTimes(4);
    });

    const reportUrl = result.current.buildReportUrl('/data/results/test.report.html');

    expect(reportUrl).toContain('/api/report');
    expect(reportUrl).toContain('artifact_id=');
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

    const { result } = renderDashboardHook();

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

describe('useDashboardLogic - Example FASTA profile', () => {
  beforeEach(() => {
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('runExampleProfile posts use_example:true to /api/profile/fasta and stores the result', async () => {
    global.fetch.mockImplementation((url, options = {}) => {
      const requestUrl = String(url);

      if (requestUrl.includes('/api/ui/config')) {
        return Promise.resolve({ ok: true, json: async () => ({ data: {} }) });
      }

      if (requestUrl.includes('/api/ui/legal')) {
        return Promise.resolve({ ok: true, json: async () => ({ data: { enabled: false } }) });
      }

      if (requestUrl.includes('/api/databases')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            data: {
              items: [{
                id: 'db1',
                display_name: 'HIVdb',
                has_example: true,
              }],
            },
          }),
        });
      }

      if (requestUrl.includes('/api/mutations')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ data: { items: [], columns: [] } }),
        });
      }

      if (requestUrl.includes('/api/profile/fasta') && options.method === 'POST') {
        const body = JSON.parse(options.body || '{}');
        expect(body.use_example).toBe(true);
        expect(body.fasta_id).toBeUndefined();
        return Promise.resolve({ ok: true, json: async () => ({ job_id: 'job-example' }) });
      }

      if (requestUrl.includes('/api/jobs/job-example')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: 'succeeded',
            result: {
              sample_name: 'HIVdb example',
              reference_name: 'HIV',
              database_id: 'db1',
              report_html_path: '/data/results/example.report.html',
              created_at: '2026-05-12T10:00:00',
              resistance_hits: 3,
              mode: 'fasta',
            },
          }),
        });
      }

      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    const { result } = renderDashboardHook();

    await waitFor(() => {
      expect(result.current.databases.length).toBeGreaterThan(0);
    });

    await act(async () => {
      result.current.setSelectedDatabaseId('db1');
    });

    await act(async () => {
      await result.current.runExampleProfile();
    });

    await waitFor(() => {
      expect(result.current.reportOptions.length).toBe(1);
    });

    expect(result.current.reportOptions[0].label).toContain('HIVdb example');
  });
});

describe('_resolveLegalLink', () => {
  // The footer "Legal notice" href is derived from the /api/ui/legal payload.
  // Disabled → null (no footer). URL mode → external link. Path mode → /legal route.
  // Same-origin deployment defaults apiBase to '', so the self-hosted route resolves to '/legal'.
  const SELF_HOSTED_LEGAL = '/legal';

  it('returns null when the imprint feature is disabled', () => {
    expect(_resolveLegalLink({ enabled: false })).toBeNull();
  });

  it('returns null when the payload is missing', () => {
    expect(_resolveLegalLink(null)).toBeNull();
    expect(_resolveLegalLink(undefined)).toBeNull();
  });

  it('returns the external URL for url mode', () => {
    const externalUrl = 'https://example.org/impressum';
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: externalUrl })).toBe(externalUrl);
  });

  it('falls back to the self-hosted /legal route for path mode', () => {
    expect(_resolveLegalLink({ enabled: true, kind: 'path' })).toBe(SELF_HOSTED_LEGAL);
  });

  it('falls back to /legal for a stale backend that omits kind', () => {
    // Backward compatibility: an older backend returning only {enabled: true}.
    expect(_resolveLegalLink({ enabled: true })).toBe(SELF_HOSTED_LEGAL);
  });

  it('falls back to /legal when url mode lacks a url value', () => {
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: null })).toBe(SELF_HOSTED_LEGAL);
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: '' })).toBe(SELF_HOSTED_LEGAL);
  });

  it('rejects non-http(s) legal URLs in url mode', () => {
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: 'javascript:alert(1)' })).toBeNull();
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: 'data:text/html,<script>' })).toBeNull();
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: 'vbscript:msgbox(1)' })).toBeNull();
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: 'file:///etc/passwd' })).toBeNull();
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: '//evil.example/legal' })).toBeNull();
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: 'https://user:pass@evil.com' })).toBeNull();
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: 'https://www.who.int@evil.com/legal' })).toBeNull();
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: 'https://user@example.org/legal' })).toBeNull();
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: '/legal' })).toBeNull();
  });
});

describe('_resolveContactEmail', () => {
  // The footer contact e-mail is derived from the /api/ui/contact payload.
  // Disabled → null (no footer link). Enabled → the bare address string.

  it('returns null when the contact feature is disabled', () => {
    expect(_resolveContactEmail({ enabled: false, email: null })).toBeNull();
  });

  it('returns null when the payload is missing', () => {
    expect(_resolveContactEmail(null)).toBeNull();
    expect(_resolveContactEmail(undefined)).toBeNull();
  });

  it('returns null when enabled but email is empty/missing', () => {
    expect(_resolveContactEmail({ enabled: true, email: null })).toBeNull();
    expect(_resolveContactEmail({ enabled: true, email: '' })).toBeNull();
    expect(_resolveContactEmail({ enabled: true })).toBeNull();
  });

  it('returns the email address when enabled', () => {
    expect(_resolveContactEmail({ enabled: true, email: 'support@example.org' })).toBe('support@example.org');
  });

  it('rejects malformed or query-injected contact emails', () => {
    expect(_resolveContactEmail({ enabled: true, email: 'user@example.org?bcc=evil@x.org' })).toBeNull();
    expect(_resolveContactEmail({ enabled: true, email: 'mailto:user@example.org' })).toBeNull();
    expect(_resolveContactEmail({ enabled: true, email: 'Alice <user@example.org>' })).toBeNull();
  });
});

describe('cliVersion', () => {
  // The footer always renders; the version string is sourced from
  // /api/ui/config so it reflects the running backend, not the build.
  beforeEach(() => {
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('exposes the version reported by /api/ui/config', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: { cli_version: '1.2.3' } }),
    });
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: { enabled: false } }),
    });
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: { enabled: false, email: null } }),
    });
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: { items: [] } }),
    });

    const { result } = renderDashboardHook();

    await waitFor(() => {
      expect(result.current.cliVersion).toBe('1.2.3');
    });
  });

  it('stays null when the backend omits the version', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: {} }),
    });
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: { enabled: false } }),
    });
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: { enabled: false, email: null } }),
    });
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: { items: [] } }),
    });

    const { result } = renderDashboardHook();

    await waitFor(() => {
      expect(result.current.databases.length).toBe(0);
    });
    expect(result.current.cliVersion).toBeNull();
  });
});

describe('artifact bundle limit', () => {
  beforeEach(() => {
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
  });

  afterEach(() => {
    setArtifactBundleLimit(100);
    vi.clearAllMocks();
  });

  it('applies the bundle limit reported by /api/ui/config', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: { artifact_bundle_max_artifacts: 7 } }),
    });
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: { enabled: false } }),
    });
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: { enabled: false, email: null } }),
    });
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: { items: [] } }),
    });

    renderDashboardHook();

    await waitFor(() => {
      expect(getArtifactBundleLimit()).toBe(7);
    });
  });
});

describe('pagehide session cleanup', () => {
  let warning;
  const originalNavigator = global.navigator;

  beforeEach(() => {
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
    global.fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ data: { items: [] } }),
    });
    warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    setArtifactBundleLimit(100);
    vi.restoreAllMocks();
    vi.stubGlobal('navigator', originalNavigator);
  });

  async function dashboardWithUploads(ids) {
    const { result } = renderDashboardHook();
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(4));
    for (const id of ids) {
      await act(async () => {
        const upload = result.current.uploadFastaFile(new File(['ATCG'], 'test.fasta'));
        mockXHRInstance.triggerSuccess({ upload_id: id });
        await upload;
      });
    }
    global.fetch.mockClear();
  }

  it('does not delete live files when the page enters the back-forward cache', async () => {
    const beacon = vi.fn(() => true);
    vi.stubGlobal('navigator', { ...navigator, sendBeacon: beacon });
    await dashboardWithUploads(['upload-1']);

    window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));

    expect(beacon).not.toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('reports a rejected beacon and attempts the same bounded payload with keepalive', async () => {
    const beacon = vi.fn(() => false);
    vi.stubGlobal('navigator', { ...navigator, sendBeacon: beacon });
    await dashboardWithUploads(['upload-1']);

    window.dispatchEvent(new PageTransitionEvent('pagehide'));

    expect(beacon).toHaveBeenCalledTimes(1);
    expect(warning).toHaveBeenCalledWith(expect.stringMatching(/beacon.*rejected/i));
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/session/cleanup',
      expect.objectContaining({
        method: 'POST',
        keepalive: true,
        body: JSON.stringify({ upload_ids: ['upload-1'], artifact_ids: [] }),
      }),
    );
  });

  it('attempts keepalive if beacon throws', async () => {
    vi.stubGlobal('navigator', { ...navigator, sendBeacon: vi.fn(() => { throw new Error('blocked'); }) });
    await dashboardWithUploads(['upload-1']);

    window.dispatchEvent(new PageTransitionEvent('pagehide'));

    expect(warning).toHaveBeenCalledWith(expect.stringMatching(/beacon.*failed/i), expect.any(Error));
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/session/cleanup'),
      expect.objectContaining({ keepalive: true, body: JSON.stringify({ upload_ids: ['upload-1'], artifact_ids: [] }) }),
    );
  });

  it('reports an observable rate-limit response from keepalive cleanup', async () => {
    vi.stubGlobal('navigator', { ...navigator, sendBeacon: undefined });
    await dashboardWithUploads(['upload-1']);
    global.fetch.mockResolvedValueOnce({ ok: false, status: 429 });

    window.dispatchEvent(new PageTransitionEvent('pagehide'));

    await waitFor(() => expect(warning).toHaveBeenCalledWith(expect.stringMatching(/429/)));
  });

  it('reports a rejected keepalive request', async () => {
    vi.stubGlobal('navigator', { ...navigator, sendBeacon: undefined });
    await dashboardWithUploads(['upload-1']);
    global.fetch.mockRejectedValueOnce(new Error('quota exceeded'));

    window.dispatchEvent(new PageTransitionEvent('pagehide'));

    await waitFor(() => expect(warning).toHaveBeenCalledWith(
      expect.stringMatching(/cleanup.*failed/i), expect.any(Error),
    ));
  });

  it('warns when multiple bounded requests may exceed the rate limit', async () => {
    vi.stubGlobal('navigator', { ...navigator, sendBeacon: vi.fn(() => true) });
    await dashboardWithUploads(['upload-1', 'upload-2']);
    setArtifactBundleLimit(1);

    window.dispatchEvent(new PageTransitionEvent('pagehide'));

    expect(navigator.sendBeacon).toHaveBeenCalledTimes(2);
    expect(warning).toHaveBeenCalledWith(expect.stringMatching(/rate.limit/i));
  });

  it('retries only rejected chunks with their own ids', async () => {
    vi.stubGlobal('navigator', { ...navigator, sendBeacon: vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true) });
    await dashboardWithUploads(['upload-1', 'upload-2']);
    setArtifactBundleLimit(1);

    window.dispatchEvent(new PageTransitionEvent('pagehide'));

    expect(navigator.sendBeacon).toHaveBeenCalledTimes(2);
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/session/cleanup'),
      expect.objectContaining({ body: JSON.stringify({ upload_ids: ['upload-1'], artifact_ids: [] }) }),
    );
  });
});

describe('useDashboardLogic - startup concurrency, abort lifecycle, and M4d exports (WA-050 / W3)', () => {
  beforeEach(() => {
    setupXhrStub();
    vi.clearAllMocks();
    global.fetch.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function makeDeferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  }

  it('fetches the 4 startup endpoints in parallel and exposes M4d busy/polling keys', async () => {
    const configDef = makeDeferred();
    const legalDef = makeDeferred();
    const contactDef = makeDeferred();
    const databasesDef = makeDeferred();

    global.fetch.mockImplementation((url) => {
      const reqUrl = String(url);
      if (reqUrl.includes('/api/ui/config')) return configDef.promise;
      if (reqUrl.includes('/api/ui/legal')) return legalDef.promise;
      if (reqUrl.includes('/api/ui/contact')) return contactDef.promise;
      if (reqUrl.includes('/api/databases')) return databasesDef.promise;
      if (reqUrl.includes('/api/mutations')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ data: { items: [], formula_items: [], plot_meta: { references: [], features: [] } } }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    const { result } = renderDashboardHook();

    // All 4 startup GETs must be dispatched concurrently before any settles.
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledTimes(4);
    });
    const requestedUrls = global.fetch.mock.calls.map(([url]) => String(url));
    expect(requestedUrls).toEqual([
      '/api/ui/config',
      '/api/ui/legal',
      '/api/ui/contact',
      '/api/databases',
    ]);

    await act(async () => {
      configDef.resolve({ ok: true, json: async () => ({ data: { cli_version: '2.0.0' } }) });
      legalDef.resolve({ ok: true, json: async () => ({ data: { enabled: true, kind: 'path' } }) });
      contactDef.resolve({ ok: true, json: async () => ({ data: { enabled: true, email: 'team@example.org' } }) });
      databasesDef.resolve({
        ok: true,
        json: async () => ({ data: { items: [{ id: 'db1', display_name: 'HSVdb' }] } }),
      });
    });

    await waitFor(() => {
      expect(result.current.selectedDatabaseId).toBe('db1');
    });
    expect(result.current.cliVersion).toBe('2.0.0');
    expect(result.current.legalLink).toBe('/legal');
    expect(result.current.contactEmail).toBe('team@example.org');
    expect(result.current.databases).toHaveLength(1);

    // Verify M4d AppShell keys exist with expected types
    expect(typeof result.current.isProfileBusy).toBe('boolean');
    expect(typeof result.current.isRegenerateBusy).toBe('boolean');
    expect(typeof result.current.isUploading).toBe('boolean');
    expect(typeof result.current.batchSubmitting).toBe('boolean');
    expect(typeof result.current.isBatchUploading).toBe('boolean');
    expect(typeof result.current.isBatchPolling).toBe('boolean');
    expect(typeof result.current.stopBatchPolling).toBe('function');
  });

  it('aborts all 4 startup GETs on unmount and ignores late AbortError or resolution', async () => {
    const signals = [];
    const deferreds = [];
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    global.fetch.mockImplementation((_, options = {}) => {
      signals.push(options.signal);
      const def = makeDeferred();
      deferreds.push(def);
      return def.promise;
    });

    const { unmount } = renderDashboardHook();
    await waitFor(() => {
      expect(signals).toHaveLength(4);
    });
    expect(signals.every((signal) => signal instanceof AbortSignal && !signal.aborted)).toBe(true);

    unmount();
    expect(signals.every((signal) => signal.aborted)).toBe(true);

    await act(async () => {
      const abortErr = new Error('The operation was aborted.');
      abortErr.name = 'AbortError';
      deferreds[0].reject(abortErr);
      deferreds[1].reject(abortErr);
      deferreds[2].reject(abortErr);
      deferreds[3].resolve({ ok: true, json: async () => ({ data: { items: [{ id: 'db1' }] } }) });
      await Promise.resolve();
    });
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('surfaces statusError when /api/databases fails while tolerating optional /api/ui/* failures', async () => {
    global.fetch.mockImplementation((url) => {
      const reqUrl = String(url);
      if (reqUrl.includes('/api/ui/config')) return Promise.reject(new Error('Config unavailable'));
      if (reqUrl.includes('/api/ui/legal')) return Promise.reject(new Error('Legal unavailable'));
      if (reqUrl.includes('/api/ui/contact')) return Promise.reject(new Error('Contact unavailable'));
      if (reqUrl.includes('/api/databases')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ data: { items: [{ id: 'db1', display_name: 'DB1' }] } }),
        });
      }
      if (reqUrl.includes('/api/mutations')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ data: { items: [], formula_items: [], plot_meta: { references: [], features: [] } } }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    const first = renderDashboardHook();
    await waitFor(() => {
      expect(first.result.current.selectedDatabaseId).toBe('db1');
    });
    expect(first.result.current.statusError).toBe('');
    expect(first.result.current.legalLink).toBeNull();
    expect(first.result.current.contactEmail).toBeNull();
    first.unmount();

    global.fetch.mockImplementation((url) => {
      if (String(url).includes('/api/databases')) {
        return Promise.reject(new Error('DB down'));
      }
      return Promise.resolve({ ok: true, json: async () => ({ data: {} }) });
    });

    const second = renderDashboardHook();
    await waitFor(() => {
      expect(second.result.current.statusError).toBe('Error loading data: DB down');
    });
    second.unmount();
  });

  it('aborts in-flight mutations fetch when switching selectedDatabaseId', async () => {
    const mutationSignals = [];
    const mutationDeferreds = [];

    global.fetch.mockImplementation((url, options = {}) => {
      const reqUrl = String(url);
      if (reqUrl.includes('/api/databases')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            data: {
              items: [
                { id: 'db1', display_name: 'DB 1' },
                { id: 'db2', display_name: 'DB 2' },
              ],
            },
          }),
        });
      }
      if (reqUrl.includes('/api/mutations')) {
        mutationSignals.push(options.signal);
        const def = makeDeferred();
        mutationDeferreds.push(def);
        return def.promise;
      }
      return Promise.resolve({ ok: true, json: async () => ({ data: {} }) });
    });

    const { result } = renderDashboardHook();

    await waitFor(() => {
      expect(mutationSignals).toHaveLength(1);
    });
    expect(mutationSignals[0].aborted).toBe(false);

    act(() => {
      result.current.setSelectedDatabaseId('db2');
    });

    await waitFor(() => {
      expect(mutationSignals).toHaveLength(2);
    });
    expect(mutationSignals[0].aborted).toBe(true);
    expect(mutationSignals[1].aborted).toBe(false);

    await act(async () => {
      mutationDeferreds[1].resolve({
        ok: true,
        json: async () => ({
          data: {
            items: [{ id: 10, feature: 'UL54', position: 0, mutation: 'M1V', drug: 'FOS' }],
            formula_items: [],
            plot_meta: { references: [], features: [] },
          },
        }),
      });
    });

    await waitFor(() => {
      expect(result.current.loadedDatabaseId).toBe('db2');
    });
    expect(result.current.rules).toHaveLength(1);
  });
});
