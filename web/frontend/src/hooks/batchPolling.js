import { FRONTEND_CONFIG } from '../config';
import { apiGet, formatUserError, isAbortError } from '../api';

const TERMINAL_STATUSES = new Set(['succeeded', 'failed']);

export const DEFAULT_BATCH_POLL_CONCURRENCY = 5;
export const DEFAULT_BATCH_POLL_MAX_PER_MINUTE = 80;
export const DEFAULT_MAX_CONSECUTIVE_POLL_ERRORS = 10;

function formatBatchSampleView(sample) {
  const result = sample.result;
  return {
    job_id: sample.job_id,
    sample_name: sample.sample_name,
    status: sample.status,
    errorMessage: sample.errorMessage || null,
    reportHtmlPath: result ? result.report_html_path : null,
    reportPdfPath: result ? result.report_pdf_path : null,
    reportJsonPath: result ? result.report_json_path : null,
    reportTsvPath: result ? result.report_tsv_path : null,
    artifactIds: result ? result.artifact_ids : null,
  };
}

function sleep(ms, signal) {
  if (signal?.aborted) {
    return Promise.resolve(false);
  }
  if (ms <= 0) {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    const timer = setTimeout(() => {
      if (signal && typeof signal.removeEventListener === 'function') {
        signal.removeEventListener('abort', onAbort);
      }
      resolve(!signal?.aborted);
    }, ms);
    if (signal && typeof signal.addEventListener === 'function') {
      signal.addEventListener('abort', onAbort, { once: true });
    }
  });
}

function resolveRetryAfterSeconds(error, consecutive429s) {
  if (Number.isFinite(error?.retryAfterSeconds) && error.retryAfterSeconds > 0) {
    return Math.ceil(error.retryAfterSeconds);
  }
  const defaultRetry = FRONTEND_CONFIG.batch?.defaultRetryAfterSeconds ?? 5;
  const maxBackoffSeconds = Math.max(
    defaultRetry,
    Math.ceil((FRONTEND_CONFIG.batch?.maxPollBackoffMs ?? 60_000) / 1000),
  );
  return Math.min(maxBackoffSeconds, defaultRetry * (2 ** Math.min(consecutive429s, 4)));
}

/** Poll batch jobs within rate-limit budget until every sample reaches a terminal state or polling stops. */
export async function pollBatchJobs(options) {
  const {
    initialSamples,
    onSamplesChange,
    onSucceededResults,
    onRateLimit = null,
    onError = null,
    signal = undefined,
    pollIntervalMs = FRONTEND_CONFIG.profile?.jobPollIntervalMs
      ?? FRONTEND_CONFIG.batch?.minPollIntervalMs
      ?? 2000,
    maxRequestsPerMinute = FRONTEND_CONFIG.batch?.pollRequestsPerMinuteBudget
      ?? DEFAULT_BATCH_POLL_MAX_PER_MINUTE,
    concurrency = FRONTEND_CONFIG.batch?.maxConcurrentPolls
      ?? DEFAULT_BATCH_POLL_CONCURRENCY,
    maxConsecutiveErrors = FRONTEND_CONFIG.batch?.maxConsecutiveErrors
      ?? DEFAULT_MAX_CONSECUTIVE_POLL_ERRORS,
  } = options;

  const explicitMaxRequestsPerMinute = options.maxRequestsPerMinute !== undefined;
  const safePollIntervalMs = Number.isFinite(pollIntervalMs) && pollIntervalMs >= 0 ? pollIntervalMs : 2000;
  const safeMaxPerMinute = Number.isFinite(maxRequestsPerMinute) && maxRequestsPerMinute > 0
    ? maxRequestsPerMinute
    : DEFAULT_BATCH_POLL_MAX_PER_MINUTE;
  const safeConcurrency = Number.isInteger(concurrency) && concurrency > 0
    ? concurrency
    : DEFAULT_BATCH_POLL_CONCURRENCY;
  const safeMaxErrors = Number.isInteger(maxConsecutiveErrors) && maxConsecutiveErrors > 0
    ? maxConsecutiveErrors
    : DEFAULT_MAX_CONSECUTIVE_POLL_ERRORS;
  const enforceBudget = safePollIntervalMs >= 100 || explicitMaxRequestsPerMinute;
  const maxTransientBackoffMs = FRONTEND_CONFIG.batch?.maxPollIntervalMs ?? 30_000;

  let samples = initialSamples.map((sample) => ({ ...sample }));
  const reportedJobIds = new Set(
    samples
      .filter((sample) => sample.status === 'succeeded' && sample.result)
      .map((sample) => sample.job_id),
  );

  let consecutive429s = 0;
  let consecutiveErrorCount = 0;
  let nextRoundDelayMs = safePollIntervalMs;
  let skipNextRoundInitialDelay = false;

  for (;;) {
    if (signal?.aborted) return;
    const pending = samples.filter((sample) => !TERMINAL_STATUSES.has(sample.status));
    if (pending.length === 0) break;

    if (!skipNextRoundInitialDelay) {
      const ok = await sleep(nextRoundDelayMs, signal);
      if (!ok || signal?.aborted) return;
    }
    skipNextRoundInitialDelay = false;

    const chunks = [];
    for (let index = 0; index < pending.length; index += safeConcurrency) {
      chunks.push(pending.slice(index, index + safeConcurrency));
    }

    let roundElapsedMs = 0;
    let roundSuccessCount = 0;
    let roundTransientErrorCount = 0;
    let lastTransientError = null;
    let sessionFatalError = null;
    let rateLimitedWaitSeconds = 0;
    let aborted = false;

    for (let chunkIndex = 0; chunkIndex < chunks.length; chunkIndex += 1) {
      if (chunkIndex > 0 && enforceBudget) {
        const prevChunkLen = chunks[chunkIndex - 1].length;
        const chunkDelayMs = Math.ceil((prevChunkLen * 60_000) / safeMaxPerMinute);
        roundElapsedMs += chunkDelayMs;
        const ok = await sleep(chunkDelayMs, signal);
        if (!ok || signal?.aborted) return;
      }

      const chunk = chunks[chunkIndex];
      let terminal404Error = null;

      const chunkResults = await Promise.all(
        chunk.map(async (sample) => {
          try {
            const payload = await apiGet(
              `/api/jobs/${sample.job_id}`,
              signal !== undefined ? { signal } : undefined,
            );
            roundSuccessCount += 1;
            return {
              ...sample,
              status: payload.status,
              result: payload.result || null,
              errorMessage: payload.error ? formatUserError(payload.error) : null,
            };
          } catch (error) {
            if (isAbortError(error) || signal?.aborted) {
              aborted = true;
              return sample;
            }
            const formattedMessage = formatUserError(error?.message);
            if (error?.status === 429) {
              rateLimitedWaitSeconds = Math.max(
                rateLimitedWaitSeconds,
                resolveRetryAfterSeconds(error, consecutive429s),
              );
              return sample;
            }
            if (error?.status === 401 || error?.status === 403) {
              sessionFatalError = sessionFatalError || formattedMessage;
              return sample;
            }
            if (error?.status === 404) {
              terminal404Error = terminal404Error || formattedMessage;
              return {
                ...sample,
                status: 'failed',
                errorMessage: formattedMessage,
              };
            }
            roundTransientErrorCount += 1;
            lastTransientError = formattedMessage;
            return {
              ...sample,
              errorMessage: formattedMessage,
            };
          }
        }),
      );

      if (aborted || signal?.aborted) return;

      const updatedById = new Map(chunkResults.map((item) => [item.job_id, item]));
      samples = samples.map((sample) => updatedById.get(sample.job_id) || sample);

      const newlySucceeded = samples.filter(
        (sample) => sample.status === 'succeeded' && sample.result && !reportedJobIds.has(sample.job_id),
      );
      if (newlySucceeded.length > 0) {
        newlySucceeded.forEach((sample) => reportedJobIds.add(sample.job_id));
        onSucceededResults?.(newlySucceeded.map((sample) => sample.result));
      }

      if (sessionFatalError) {
        samples = samples.map((sample) => (
          TERMINAL_STATUSES.has(sample.status)
            ? sample
            : { ...sample, status: 'failed', errorMessage: sessionFatalError }
        ));
        onSamplesChange(samples.map(formatBatchSampleView));
        onError?.(sessionFatalError);
        return;
      }

      onSamplesChange(samples.map(formatBatchSampleView));

      if (terminal404Error) {
        onError?.(terminal404Error);
      }

      if (rateLimitedWaitSeconds > 0) {
        break;
      }
    }

    if (rateLimitedWaitSeconds > 0) {
      consecutive429s += 1;
      onRateLimit?.(rateLimitedWaitSeconds);
      const ok = await sleep(rateLimitedWaitSeconds * 1000, signal);
      if (!ok || signal?.aborted) return;
      onRateLimit?.(0);
      skipNextRoundInitialDelay = true;
      continue;
    }

    if (roundSuccessCount > 0) {
      consecutive429s = 0;
      consecutiveErrorCount = 0;
    } else if (roundTransientErrorCount > 0) {
      consecutiveErrorCount += 1;
      if (consecutiveErrorCount >= safeMaxErrors) {
        const finalMessage = lastTransientError || 'Batch status polling stopped after repeated errors.';
        samples = samples.map((sample) => (
          TERMINAL_STATUSES.has(sample.status)
            ? sample
            : { ...sample, status: 'failed', errorMessage: sample.errorMessage || finalMessage }
        ));
        onSamplesChange(samples.map(formatBatchSampleView));
        onError?.(finalMessage);
        break;
      }
      const backoffMs = Math.min(
        maxTransientBackoffMs,
        Math.max(10, safePollIntervalMs) * (2 ** consecutiveErrorCount),
      );
      const ok = await sleep(backoffMs, signal);
      if (!ok || signal?.aborted) return;
      skipNextRoundInitialDelay = true;
      continue;
    }

    const roundFloorMs = enforceBudget
      ? Math.ceil((pending.length * 60_000) / safeMaxPerMinute)
      : safePollIntervalMs;
    nextRoundDelayMs = Math.max(safePollIntervalMs, roundFloorMs - roundElapsedMs);
  }
}
