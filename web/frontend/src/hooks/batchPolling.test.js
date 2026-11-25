import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api';
import * as apiModule from '../api';
import { pollBatchJobs } from './batchPolling';

describe('pollBatchJobs', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('polls samples to completion, omits report_url, and reports succeeded results at most once per job', async () => {
    let tick = 0;
    const apiGetSpy = vi.spyOn(apiModule, 'apiGet').mockImplementation(async (path) => {
      if (path === '/api/jobs/j1') {
        return {
          status: 'succeeded',
          result: {
            run_id: 'run-1',
            report_html_path: 'art-html-1',
            report_pdf_path: 'art-pdf-1',
            report_json_path: 'art-json-1',
            report_tsv_path: 'art-tsv-1',
            artifact_ids: ['art-html-1'],
          },
          error: null,
        };
      }
      tick += 1;
      if (tick === 1) {
        return { status: 'running', result: null, error: null };
      }
      return {
        status: 'succeeded',
        result: {
          run_id: 'run-2',
          report_html_path: 'art-html-2',
          artifact_ids: ['art-html-2'],
        },
        error: null,
      };
    });

    const snapshots = [];
    const succeededBatches = [];
    await pollBatchJobs({
      initialSamples: [
        { job_id: 'j1', sample_name: 's1', status: 'queued' },
        { job_id: 'j2', sample_name: 's2', status: 'queued' },
      ],
      pollIntervalMs: 5,
      onSamplesChange: (samples) => snapshots.push(samples),
      onSucceededResults: (results) => succeededBatches.push(...results),
    });

    expect(apiGetSpy).toHaveBeenCalledTimes(3);
    expect(succeededBatches.map((r) => r.run_id)).toEqual(['run-1', 'run-2']);
    const finalSamples = snapshots.at(-1);
    expect(finalSamples).toHaveLength(2);
    expect(finalSamples[0]).not.toHaveProperty('report_url');
    expect(finalSamples[0].reportHtmlPath).toBe('art-html-1');
    expect(finalSamples[1].status).toBe('succeeded');
  });

  it('keeps 25-job polling strictly under 120 requests/minute over a 60s window', async () => {
    vi.useFakeTimers();
    let requestCount = 0;
    let roundCounter = 0;
    vi.spyOn(apiModule, 'apiGet').mockImplementation(async (path) => {
      requestCount += 1;
      if (path === '/api/jobs/j25') {
        roundCounter += 1;
      }
      // Finish all 25 jobs after 75s so polling terminates cleanly after the 60s measurement window.
      if (roundCounter >= 5) {
        return { status: 'succeeded', result: { run_id: path }, error: null };
      }
      return { status: 'running', result: null, error: null };
    });

    const promise = pollBatchJobs({
      initialSamples: Array.from({ length: 25 }, (_, i) => ({
        job_id: `j${i + 1}`,
        sample_name: `s${i + 1}`,
        status: 'queued',
      })),
      pollIntervalMs: 2000,
      maxRequestsPerMinute: 80,
      concurrency: 5,
      onSamplesChange: () => {},
      onSucceededResults: () => {},
    });

    await vi.advanceTimersByTimeAsync(60_000);
    expect(requestCount).toBeLessThan(120);
    expect(requestCount).toBeGreaterThan(0);

    await vi.advanceTimersByTimeAsync(60_000);
    await promise;
  });

  it('honors 429 Retry-After header, circuit-breaks remaining chunks, keeps samples non-terminal, and clears cooldown', async () => {
    vi.useFakeTimers();
    const rateLimitEvents = [];
    const snapshots = [];
    let attempt = 0;

    const apiGetSpy = vi.spyOn(apiModule, 'apiGet').mockImplementation(async (path) => {
      attempt += 1;
      if (attempt <= 5) {
        throw new ApiError('Rate limit exceeded', { status: 429, retryAfterSeconds: 12 });
      }
      return { status: 'succeeded', result: { run_id: path }, error: null };
    });

    const promise = pollBatchJobs({
      initialSamples: Array.from({ length: 6 }, (_, i) => ({
        job_id: `j${i + 1}`,
        sample_name: `s${i + 1}`,
        status: 'queued',
      })),
      pollIntervalMs: 1000,
      concurrency: 5,
      maxRequestsPerMinute: 80,
      onSamplesChange: (s) => snapshots.push(s),
      onSucceededResults: () => {},
      onRateLimit: (sec) => rateLimitEvents.push(sec),
    });

    // Advance past initial 1000ms delay -> chunk 1 hits 429; chunk 2 (j6) must NOT run in the same round.
    await vi.advanceTimersByTimeAsync(1000);
    expect(apiGetSpy).toHaveBeenCalledTimes(5);
    expect(rateLimitEvents).toEqual([12]);
    expect(snapshots.at(-1).every((s) => s.status === 'queued')).toBe(true);

    // Advance 12s Retry-After -> resumes, clears cooldown, and finishes all 6 jobs.
    await vi.advanceTimersByTimeAsync(12_000);
    expect(rateLimitEvents).toEqual([12, 0]);
    await vi.advanceTimersByTimeAsync(10_000);
    await promise;
    expect(snapshots.at(-1).every((s) => s.status === 'succeeded')).toBe(true);
  });

  it('falls back to exponential backoff on 429 without Retry-After header', async () => {
    vi.useFakeTimers();
    const rateLimitEvents = [];
    let call = 0;
    vi.spyOn(apiModule, 'apiGet').mockImplementation(async () => {
      call += 1;
      if (call === 1) {
        throw new ApiError('Rate limited', { status: 429, retryAfterSeconds: null });
      }
      return { status: 'succeeded', result: { run_id: 'r1' }, error: null };
    });

    const promise = pollBatchJobs({
      initialSamples: [{ job_id: 'j1', sample_name: 's1', status: 'queued' }],
      pollIntervalMs: 100,
      onSamplesChange: () => {},
      onSucceededResults: () => {},
      onRateLimit: (sec) => rateLimitEvents.push(sec),
    });

    await vi.advanceTimersByTimeAsync(100);
    expect(rateLimitEvents).toEqual([5]);
    await vi.advanceTimersByTimeAsync(5000);
    await promise;
    expect(rateLimitEvents).toEqual([5, 0]);
  });

  it('marks 404 sample failed immediately and stops once no pending jobs remain', async () => {
    const errors = [];
    const snapshots = [];
    const apiGetSpy = vi.spyOn(apiModule, 'apiGet').mockRejectedValueOnce(
      new ApiError('Job not found.', { status: 404 }),
    );

    await pollBatchJobs({
      initialSamples: [{ job_id: 'missing', sample_name: 's1', status: 'queued' }],
      pollIntervalMs: 5,
      onSamplesChange: (s) => snapshots.push(s),
      onSucceededResults: () => {},
      onError: (msg) => errors.push(msg),
    });

    expect(apiGetSpy).toHaveBeenCalledTimes(1);
    expect(snapshots.at(-1)[0].status).toBe('failed');
    expect(snapshots.at(-1)[0].errorMessage).toBe('Job not found.');
    expect(errors).toEqual(['Job not found.']);
  });

  it('stops immediately on 401/403 session loss and marks all remaining pending samples failed', async () => {
    const errors = [];
    const snapshots = [];
    const apiGetSpy = vi.spyOn(apiModule, 'apiGet').mockRejectedValue(
      new ApiError('Forbidden', { status: 403 }),
    );

    await pollBatchJobs({
      initialSamples: [
        { job_id: 'j1', sample_name: 's1', status: 'queued' },
        { job_id: 'j2', sample_name: 's2', status: 'running' },
        { job_id: 'j3', sample_name: 's3', status: 'succeeded', result: { run_id: 'r3' } },
      ],
      pollIntervalMs: 5,
      concurrency: 1,
      onSamplesChange: (s) => snapshots.push(s),
      onSucceededResults: () => {},
      onError: (msg) => errors.push(msg),
    });

    expect(apiGetSpy).toHaveBeenCalledTimes(1);
    expect(errors).toEqual(['Forbidden']);
    const last = snapshots.at(-1);
    expect(last.map((s) => s.status)).toEqual(['failed', 'failed', 'succeeded']);
  });

  it('bounds consecutive transient 500 errors and terminates with failed samples', async () => {
    vi.useFakeTimers();
    const errors = [];
    const snapshots = [];
    const apiGetSpy = vi.spyOn(apiModule, 'apiGet').mockRejectedValue(
      new ApiError('Server error', { status: 500 }),
    );

    const promise = pollBatchJobs({
      initialSamples: [{ job_id: 'j1', sample_name: 's1', status: 'queued' }],
      pollIntervalMs: 100,
      maxConsecutiveErrors: 3,
      onSamplesChange: (s) => snapshots.push(s),
      onSucceededResults: () => {},
      onError: (msg) => errors.push(msg),
    });

    await vi.advanceTimersByTimeAsync(10_000);
    await promise;

    expect(apiGetSpy).toHaveBeenCalledTimes(3);
    expect(snapshots.at(-1)[0].status).toBe('failed');
    expect(errors).toEqual(['Server error']);
  });

  it('exits cleanly on AbortSignal during sleep and during in-flight apiGet without marking failed', async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const snapshots = [];
    const errors = [];

    const promise1 = pollBatchJobs({
      initialSamples: [{ job_id: 'j1', sample_name: 's1', status: 'queued' }],
      pollIntervalMs: 5000,
      signal: controller.signal,
      onSamplesChange: (s) => snapshots.push(s),
      onSucceededResults: () => {},
      onError: (msg) => errors.push(msg),
    });

    controller.abort();
    await promise1;
    expect(snapshots).toHaveLength(0);
    expect(errors).toHaveLength(0);

    const controller2 = new AbortController();
    vi.spyOn(apiModule, 'apiGet').mockImplementationOnce(
      (_url, options) => new Promise((_, reject) => {
        options.signal.addEventListener('abort', () => {
          const err = new Error('aborted');
          err.name = 'AbortError';
          reject(err);
        }, { once: true });
      }),
    );

    const promise2 = pollBatchJobs({
      initialSamples: [{ job_id: 'j1', sample_name: 's1', status: 'queued' }],
      pollIntervalMs: 100,
      signal: controller2.signal,
      onSamplesChange: (s) => snapshots.push(s),
      onSucceededResults: () => {},
      onError: (msg) => errors.push(msg),
    });

    await vi.advanceTimersByTimeAsync(100);
    controller2.abort();
    await promise2;
    expect(snapshots).toHaveLength(0);
    expect(errors).toHaveLength(0);
  });

  it('escalates 429 exponential backoff up to maxPollBackoffMs, rounds fractional Retry-After, and resets after success', async () => {
    vi.useFakeTimers();
    const rateLimitEvents = [];
    let call = 0;
    vi.spyOn(apiModule, 'apiGet').mockImplementation(async () => {
      call += 1;
      if (call <= 5) {
        // 0 and negative retryAfterSeconds fall back to exponential backoff (5, 10, 20, 40, 60 cap).
        throw new ApiError('Rate limited', { status: 429, retryAfterSeconds: call === 1 ? 0 : null });
      }
      if (call === 6) {
        // Success resets consecutive429s.
        return { status: 'running', result: null, error: null };
      }
      if (call === 7) {
        // After reset, another 429 without Retry-After starts back at 5s.
        throw new ApiError('Rate limited', { status: 429, retryAfterSeconds: null });
      }
      if (call === 8) {
        // Fractional retryAfterSeconds rounds up via Math.ceil.
        throw new ApiError('Rate limited', { status: 429, retryAfterSeconds: 1.2 });
      }
      return { status: 'succeeded', result: { run_id: 'r1' }, error: null };
    });

    const promise = pollBatchJobs({
      initialSamples: [{ job_id: 'j1', sample_name: 's1', status: 'queued' }],
      pollIntervalMs: 100,
      minPollIntervalMs: 100,
      defaultRetryAfterSeconds: 5,
      maxPollBackoffMs: 60_000,
      onSamplesChange: () => {},
      onSucceededResults: () => {},
      onRateLimit: (sec) => rateLimitEvents.push(sec),
    });

    await vi.advanceTimersByTimeAsync(100);
    expect(rateLimitEvents).toEqual([5]);
    await vi.advanceTimersByTimeAsync(5000);
    expect(rateLimitEvents).toEqual([5, 0, 10]);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(rateLimitEvents).toEqual([5, 0, 10, 0, 20]);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(rateLimitEvents).toEqual([5, 0, 10, 0, 20, 0, 40]);
    await vi.advanceTimersByTimeAsync(40_000);
    expect(rateLimitEvents).toEqual([5, 0, 10, 0, 20, 0, 40, 0, 60]);

    // Call 6 succeeds and resets rate-limit counter.
    await vi.advanceTimersByTimeAsync(60_000);
    expect(rateLimitEvents).toEqual([5, 0, 10, 0, 20, 0, 40, 0, 60, 0]);

    // Call 7 (after 750ms budget-enforced round delay) -> 5s, Call 8 -> Math.ceil(1.2) = 2s, Call 9 -> succeeded.
    await vi.advanceTimersByTimeAsync(750);
    expect(rateLimitEvents).toEqual([5, 0, 10, 0, 20, 0, 40, 0, 60, 0, 5]);
    await vi.advanceTimersByTimeAsync(5000);
    expect(rateLimitEvents).toEqual([5, 0, 10, 0, 20, 0, 40, 0, 60, 0, 5, 0, 2]);
    await vi.advanceTimersByTimeAsync(2000);
    await promise;
    expect(rateLimitEvents).toEqual([5, 0, 10, 0, 20, 0, 40, 0, 60, 0, 5, 0, 2, 0]);
  });

  it('exits with 0 fetches for all-terminal initialSamples and does not duplicate pre-succeeded results', async () => {
    const apiGetSpy = vi.spyOn(apiModule, 'apiGet').mockResolvedValue({
      status: 'succeeded',
      result: { run_id: 'r2' },
      error: null,
    });
    const succeededBatches = [];

    await pollBatchJobs({
      initialSamples: [
        { job_id: 'j1', sample_name: 's1', status: 'succeeded', result: { run_id: 'r1' } },
        { job_id: 'j2', sample_name: 's2', status: 'failed', errorMessage: 'old' },
      ],
      pollIntervalMs: 5,
      onSamplesChange: () => {},
      onSucceededResults: (res) => succeededBatches.push(res),
    });
    expect(apiGetSpy).not.toHaveBeenCalled();
    expect(succeededBatches).toEqual([]);

    await pollBatchJobs({
      initialSamples: [
        { job_id: 'j1', sample_name: 's1', status: 'succeeded', result: { run_id: 'r1' } },
        { job_id: 'j2', sample_name: 's2', status: 'queued' },
      ],
      pollIntervalMs: 5,
      onSamplesChange: () => {},
      onSucceededResults: (res) => succeededBatches.push(res),
    });
    expect(succeededBatches).toEqual([[{ run_id: 'r2' }]]);
  });

  it('continues polling remaining samples on partial 404, stops fatally on 401, resets 500 streak on 200, and formats 200 failed payloads', async () => {
    vi.useFakeTimers();
    const errors = [];
    const snapshots = [];
    let j2Calls = 0;
    vi.spyOn(apiModule, 'apiGet').mockImplementation(async (url) => {
      if (url.includes('j1')) {
        throw new ApiError('Job j1 missing', { status: 404 });
      }
      j2Calls += 1;
      if (j2Calls === 1) {
        throw new ApiError('Transient 500', { status: 500 });
      }
      if (j2Calls === 2) {
        return { status: 'running', result: null, error: null };
      }
      if (j2Calls === 3) {
        throw new ApiError('Transient 500 again', { status: 500 });
      }
      return {
        status: 'failed',
        result: null,
        error: 'Traceback in /var/app/secret.py\nValueError: unsupported fasta format',
      };
    });

    const p1 = pollBatchJobs({
      initialSamples: [
        { job_id: 'j1', sample_name: 's1', status: 'queued' },
        { job_id: 'j2', sample_name: 's2', status: 'queued' },
      ],
      pollIntervalMs: 50,
      maxConsecutiveErrors: 2,
      onSamplesChange: (s) => snapshots.push(s),
      onSucceededResults: () => {},
      onError: (msg) => errors.push(msg),
    });
    await vi.advanceTimersByTimeAsync(5000);
    await p1;

    expect(errors).toEqual(['Job j1 missing']);
    expect(snapshots.at(-1)[0].status).toBe('failed');
    expect(snapshots.at(-1)[1].status).toBe('failed');
    expect(snapshots.at(-1)[1].errorMessage).toContain('Unsupported FASTA format');
    expect(snapshots.at(-1)[1].errorMessage).not.toContain('/var/app/secret.py');

    // 401 triggers fatal stop
    vi.useRealTimers();
    const errors401 = [];
    const snapshots401 = [];
    vi.spyOn(apiModule, 'apiGet').mockRejectedValue(new ApiError('Unauthorized', { status: 401 }));
    await pollBatchJobs({
      initialSamples: [
        { job_id: 'a1', sample_name: 's1', status: 'queued' },
        { job_id: 'a2', sample_name: 's2', status: 'running' },
      ],
      pollIntervalMs: 5,
      concurrency: 1,
      onSamplesChange: (s) => snapshots401.push(s),
      onSucceededResults: () => {},
      onError: (msg) => errors401.push(msg),
    });
    expect(errors401).toEqual(['Unauthorized']);
    expect(snapshots401.at(-1).map((s) => s.status)).toEqual(['failed', 'failed']);
  });

  it('aborts cleanly during 429 cooldown sleep and transient backoff sleep without extra callbacks', async () => {
    vi.useFakeTimers();
    const rateLimits = [];
    const errors = [];

    const c429 = new AbortController();
    vi.spyOn(apiModule, 'apiGet').mockRejectedValue(new ApiError('Slow down', { status: 429, retryAfterSeconds: 10 }));
    const p429 = pollBatchJobs({
      initialSamples: [{ job_id: 'j1', sample_name: 's1', status: 'queued' }],
      pollIntervalMs: 50,
      signal: c429.signal,
      onSamplesChange: () => {},
      onSucceededResults: () => {},
      onRateLimit: (sec) => rateLimits.push(sec),
      onError: (msg) => errors.push(msg),
    });
    await vi.advanceTimersByTimeAsync(50);
    expect(rateLimits).toEqual([10]);
    c429.abort();
    await p429;
    expect(rateLimits).toEqual([10]);
    expect(errors).toEqual([]);

    const c500 = new AbortController();
    vi.spyOn(apiModule, 'apiGet').mockRejectedValue(new ApiError('Server error', { status: 500 }));
    const p500 = pollBatchJobs({
      initialSamples: [{ job_id: 'j1', sample_name: 's1', status: 'queued' }],
      pollIntervalMs: 50,
      signal: c500.signal,
      onSamplesChange: () => {},
      onSucceededResults: () => {},
      onError: (msg) => errors.push(msg),
    });
    await vi.advanceTimersByTimeAsync(50);
    c500.abort();
    await p500;
    expect(errors).toEqual([]);
  });
});
