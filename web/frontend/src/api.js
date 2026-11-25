import { FRONTEND_CONFIG } from './config';
import { triggerBlobDownload } from './utils';

const API_BASE = FRONTEND_CONFIG.apiBase;

const ERROR_MATCHERS = [
  [['unsupported fasta format'], 'Unsupported FASTA format. Upload a text FASTA file with a header line starting with >.'],
  [['unsupported vcf format'], 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.'],
  [['unsupported bam format'], 'Unsupported BAM format. Upload a BGZF-compressed BAM file.'],
  [['vcf and reference fasta do not match'], 'VCF and reference FASTA do not match. Use files derived from the same reference sequence.'],
  [['vcf contig names do not match'], 'VCF and reference FASTA do not match. Use files derived from the same reference sequence.'],
  [['coverage annotation needs a coordinate-sorted bam'], 'Coverage annotation needs a coordinate-sorted BAM. The server could not create an index for this file.'],
  [['bam and reference fasta do not match'], 'BAM and reference FASTA do not match. Use files derived from the same reference sequence.'],
  [['unsupported json format'], 'Unsupported JSON format. Upload a valid ResistanceProfiler results JSON file.'],
  [['project database uuid mismatch'], 'Project database UUID mismatch. Database updates currently do not allow regeneration of reports from older database versions.'],
  [['no cds matches found'], 'No matches to references in the database found.'],
];

export function buildApiUrl(path, params = {}) {
  const filtered = Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== ''),
  );
  const queryString = new URLSearchParams(filtered).toString();
  return queryString ? `${API_BASE}${path}?${queryString}` : `${API_BASE}${path}`;
}

export const buildReportUrl = (artifactId) => buildApiUrl('/api/report', { artifact_id: artifactId });
export const buildArtifactUrl = (artifactId) => buildApiUrl('/api/artifact', { artifact_id: artifactId });

export function formatUserError(message) {
  const lines = String(message || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  let normalized = lines.length > 0 ? lines[lines.length - 1] : 'The operation failed.';
  normalized = normalized.replace(/^(ValueError|RuntimeError|Exception|OSError):\s*/, '');

  const lowered = normalized.toLowerCase();
  for (const [terms, friendly] of ERROR_MATCHERS) {
    if (terms.every((term) => lowered.includes(term))) {
      return friendly;
    }
  }
  return normalized.startsWith('Request failed:') ? 'The request failed.' : normalized;
}

function formatPathBasename(path) {
  if (!path) return 'n/a';
  const normalized = String(path).replace(/\\/g, '/');
  const parts = normalized.split('/').filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : normalized;
}

export function formatPathStem(path) {
  const basename = formatPathBasename(path);
  if (basename.toLowerCase().endsWith('.vcf.gz') && basename.length > 7) {
    return basename.slice(0, -7);
  }
  const dotIndex = basename.lastIndexOf('.');
  return dotIndex <= 0 ? basename : basename.slice(0, dotIndex);
}

export class ApiError extends Error {
  constructor(message, { status = undefined, retryAfterSeconds = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export function isAbortError(error) {
  return Boolean(error && error.name === 'AbortError');
}

export function parseRetryAfter(headerValue, nowMs = Date.now()) {
  if (headerValue === null || headerValue === undefined) {
    return null;
  }
  const trimmed = String(headerValue).trim();
  if (!trimmed) {
    return null;
  }
  if (/^\d+$/.test(trimmed)) {
    const seconds = Number.parseInt(trimmed, 10);
    return Number.isSafeInteger(seconds) ? seconds : null;
  }
  // Reject signed/decimal numeric strings (e.g. "-5", "1.5", ".5", "1.") before Date.parse,
  // which V8 parses as year-2001 timestamps.
  if (/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(trimmed)) {
    return null;
  }
  const parsedMs = Date.parse(trimmed);
  if (!Number.isFinite(parsedMs)) {
    return null;
  }
  return Math.max(0, Math.ceil((parsedMs - nowMs) / 1000));
}

function createAbortError(signal) {
  const reason = signal?.reason;
  if (reason instanceof Error && reason.name === 'AbortError') {
    return reason;
  }
  const error = new Error(typeof reason === 'string' && reason ? reason : 'The operation was aborted.');
  error.name = 'AbortError';
  return error;
}

function readHeader(headers, name) {
  if (!headers || !name) return null;
  if (typeof headers.get === 'function') {
    return headers.get(name);
  }
  if (typeof headers === 'object') {
    const target = String(name).toLowerCase();
    for (const [key, value] of Object.entries(headers)) {
      if (String(key).toLowerCase() === target) {
        return value ?? null;
      }
    }
  }
  return null;
}

async function apiRequest(url, options = {}) {
  if (options?.signal?.aborted) {
    throw createAbortError(options.signal);
  }
  const response = await fetch(url, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload && typeof payload === 'object' ? payload.detail : undefined;
    const retryAfterSeconds = parseRetryAfter(readHeader(response.headers, 'Retry-After'));
    throw new ApiError(formatUserError(detail || `Request failed: ${response.status}`), {
      status: response.status,
      retryAfterSeconds,
    });
  }
  return response;
}

function resolveGetArgs(paramsOrOptions = {}, maybeOptions = undefined) {
  if (maybeOptions !== undefined) {
    return { params: paramsOrOptions || {}, options: maybeOptions || {} };
  }
  if (
    paramsOrOptions
    && typeof paramsOrOptions === 'object'
    && !Array.isArray(paramsOrOptions)
  ) {
    const keys = Object.keys(paramsOrOptions);
    if (
      keys.length === 1
      && keys[0] === 'signal'
      && (
        paramsOrOptions.signal === undefined
        || (
          typeof paramsOrOptions.signal === 'object'
          && paramsOrOptions.signal !== null
          && 'aborted' in paramsOrOptions.signal
        )
      )
    ) {
      return { params: {}, options: paramsOrOptions };
    }
  }
  return { params: paramsOrOptions || {}, options: {} };
}

export async function apiGet(path, paramsOrOptions = {}, maybeOptions = undefined) {
  const { params, options } = resolveGetArgs(paramsOrOptions, maybeOptions);
  const fetchOptions = options?.signal !== undefined ? { signal: options.signal } : undefined;
  const response = await apiRequest(buildApiUrl(path, params), fetchOptions);
  return response.json();
}

export async function apiPost(path, body, options = {}) {
  const fetchOptions = {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  if (options?.signal !== undefined) {
    fetchOptions.signal = options.signal;
  }
  const response = await apiRequest(`${API_BASE}${path}`, fetchOptions);
  return response.json();
}

export function apiPostRaw(path, body, options = {}) {
  if (options?.signal?.aborted) {
    return Promise.reject(createAbortError(options.signal));
  }
  const fetchOptions = {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  if (options?.signal !== undefined) {
    fetchOptions.signal = options.signal;
  }
  return fetch(`${API_BASE}${path}`, fetchOptions);
}

export async function apiDelete(path, options = {}) {
  const fetchOptions = { method: 'DELETE' };
  if (options?.signal !== undefined) {
    fetchOptions.signal = options.signal;
  }
  await apiRequest(`${API_BASE}${path}`, fetchOptions);
}

export function apiUpload(path, file, onProgress = null, onAbort = null, options = {}) {
  const signal = options?.signal;
  if (signal?.aborted) {
    return Promise.reject(createAbortError(signal));
  }
  // XHR is used so upload progress events and mid-flight cancellation can be surfaced.
  const formData = new FormData();
  formData.append('file', file);

  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    let settled = false;
    let abortedBySignal = false;

    const cleanupSignal = () => {
      if (signal && typeof signal.removeEventListener === 'function') {
        signal.removeEventListener('abort', handleSignalAbort);
      }
    };

    const handleSignalAbort = () => {
      abortedBySignal = true;
      request.abort();
      if (!settled) {
        settled = true;
        cleanupSignal();
        reject(createAbortError(signal));
      }
    };

    if (signal && typeof signal.addEventListener === 'function') {
      signal.addEventListener('abort', handleSignalAbort, { once: true });
    }

    request.open('POST', `${API_BASE}${path}`);

    if (onProgress) {
      request.upload.onprogress = (event) => {
        if (!event.lengthComputable) return;
        onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)));
      };
    }

    request.onload = () => {
      if (settled) return;
      settled = true;
      cleanupSignal();
      let payload = {};
      if (request.responseText) {
        try {
          payload = JSON.parse(request.responseText);
        } catch {
          payload = {};
        }
      }
      if (request.status >= 200 && request.status < 300) {
        resolve(payload);
        return;
      }
      const detail = payload && typeof payload === 'object' ? payload.detail : undefined;
      const retryAfterSeconds = parseRetryAfter(request.getResponseHeader?.('Retry-After'));
      reject(new ApiError(formatUserError(detail || `Upload failed: ${request.status}`), {
        status: request.status,
        retryAfterSeconds,
      }));
    };

    request.onerror = () => {
      if (settled) return;
      settled = true;
      cleanupSignal();
      reject(new Error('Upload failed: network error'));
    };
    // Settle on abort so callers' finally blocks clear in-flight state after a cancel.
    request.onabort = () => {
      if (settled) return;
      settled = true;
      cleanupSignal();
      if (abortedBySignal || signal?.aborted) {
        reject(createAbortError(signal));
      } else {
        reject(new Error('Upload canceled'));
      }
    };

    if (onAbort) onAbort(request);
    request.send(formData);
  });
}

// Pre-config fallback; /api/ui/config advertises the authoritative backend limit.
const DEFAULT_ARTIFACT_BUNDLE_LIMIT = 100;
let artifactBundleLimit = DEFAULT_ARTIFACT_BUNDLE_LIMIT;

export function setArtifactBundleLimit(limit) {
  artifactBundleLimit = Number.isInteger(limit) && limit > 0 ? limit : DEFAULT_ARTIFACT_BUNDLE_LIMIT;
}

export function getArtifactBundleLimit() {
  return artifactBundleLimit;
}

export function chunkIds(ids, limit = artifactBundleLimit) {
  const size = Number.isInteger(limit) && limit > 0 ? limit : DEFAULT_ARTIFACT_BUNDLE_LIMIT;
  const chunks = [];
  for (let index = 0; index < ids.length; index += size) {
    chunks.push(ids.slice(index, index + size));
  }
  return chunks;
}

function withPartSuffix(downloadName, part, total) {
  const dot = downloadName.lastIndexOf('.');
  return dot <= 0
    ? `${downloadName}-part${part}of${total}`
    : `${downloadName.slice(0, dot)}-part${part}of${total}${downloadName.slice(dot)}`;
}

export async function downloadArtifactBundle(artifactIds, downloadName, options = {}) {
  const signal = options?.signal;
  if (signal?.aborted) {
    throw createAbortError(signal);
  }
  const chunks = chunkIds(artifactIds);
  for (let index = 0; index < chunks.length; index += 1) {
    if (signal?.aborted) {
      throw createAbortError(signal);
    }
    const name = chunks.length === 1 ? downloadName : withPartSuffix(downloadName, index + 1, chunks.length);
    const requestOptions = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifact_ids: chunks[index] }),
    };
    if (signal !== undefined) {
      requestOptions.signal = signal;
    }
    const response = await apiRequest(
      buildApiUrl('/api/artifact-bundle', { part: chunks.length > 1 ? index + 1 : null }),
      requestOptions,
    );
    const blob = await response.blob();
    if (signal?.aborted) {
      throw createAbortError(signal);
    }
    triggerBlobDownload(blob, name);
  }
}

export async function fetchSharedReferences(ids, options = undefined) {
  return (await apiGet('/api/databases/shared-references', { ids: ids.join(',') }, options)).data;
}

export async function fetchReferenceAccessions(ids, options = undefined) {
  return (await apiGet('/api/databases/reference-accessions', { ids: ids.join(',') }, options)).data;
}

export async function compareDatabases(databaseIds, accession, options = undefined) {
  return (await apiPost('/api/databases/compare', { database_ids: databaseIds, accession }, options)).data;
}

export { API_BASE };
