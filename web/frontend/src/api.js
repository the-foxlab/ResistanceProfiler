import { FRONTEND_CONFIG } from './config';

const API_BASE = FRONTEND_CONFIG.apiBase;
const API_TOKEN = FRONTEND_CONFIG.apiToken;

function buildHeaders(baseHeaders = {}) {
  // Attach auth header only when a token exists; local dev can run without auth.
  if (!API_TOKEN) {
    return baseHeaders;
  }
  return {
    ...baseHeaders,
    Authorization: `Bearer ${API_TOKEN}`,
  };
}

export function buildApiUrl(path, params = {}) {
  // Drop empty query params so generated URLs stay compact and predictable.
  const filteredParams = Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== '')
  );
  const query = new URLSearchParams(filteredParams);
  const queryString = query.toString();
  return queryString ? `${API_BASE}${path}?${queryString}` : `${API_BASE}${path}`;
}

export function formatUserError(message) {
  // Convert backend/internal error wording into actionable UI-friendly messages.
  const lines = String(message || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  let normalized = lines.length > 0 ? lines[lines.length - 1] : 'The operation failed.';
  normalized = normalized.replace(/^(ValueError|RuntimeError|Exception|OSError):\s*/, '');

  const lowered = normalized.toLowerCase();
  if (lowered.includes('unsupported fasta format')) {
    return 'Unsupported FASTA format. Upload a text FASTA file with a header line starting with >.';
  }
  if (lowered.includes('unsupported vcf format')) {
    return 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.';
  }
  if (lowered.includes('unsupported bam format')) {
    return 'Unsupported BAM format. Upload a BGZF-compressed BAM file.';
  }
  if (lowered.includes('vcf and reference fasta do not match') || lowered.includes('vcf contig names do not match')) {
    return 'VCF and reference FASTA do not match. Use files derived from the same reference sequence.';
  }
  if (lowered.includes('coverage annotation needs a coordinate-sorted bam')) {
    return 'Coverage annotation needs a coordinate-sorted BAM. The server could not create an index for this file.';
  }
  if (lowered.includes('bam and reference fasta do not match')) {
    return 'BAM and reference FASTA do not match. Use files derived from the same reference sequence.';
  }
  if (lowered.includes('unsupported json format')) {
    return 'Unsupported JSON format. Upload a valid ResistanceProfiler results JSON file.';
  }
  if (lowered.includes('project database uuid mismatch')) {
    return (
      'Project database UUID mismatch. Database updates currently do not allow regeneration '
      + 'of reports from older database versions.'
    );
  }
  if (lowered.includes('no cds matches found')) {
    return 'No matches to references in the database found.';
  }
  if (normalized.startsWith('Request failed:')) {
    return 'The request failed.';
  }
  return normalized;
}

export function formatResultTimestamp(timestamp) {
  if (!timestamp) {
    return 'n/a';
  }

  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) {
    return timestamp;
  }

  return parsed.toLocaleString();
}

export function formatPathBasename(path) {
  if (!path) {
    return 'n/a';
  }
  const normalized = String(path).replace(/\\/g, '/');
  const parts = normalized.split('/').filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : normalized;
}

export function formatPathStem(path) {
  const basename = formatPathBasename(path);
  const dotIndex = basename.lastIndexOf('.');
  if (dotIndex <= 0) {
    return basename;
  }
  return basename.slice(0, dotIndex);
}

export async function apiGet(path, params = {}) {
  // Shared fetch helper keeps all GET error handling consistent.
  const response = await fetch(buildApiUrl(path, params), {
    headers: buildHeaders(),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
  }
  return response.json();
}

export async function apiPost(path, body) {
  // Shared POST helper centralizes auth/error handling.
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: buildHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
  }
  return response.json();
}

export async function apiPostRaw(path, body) {
  // Returns the raw Response so callers can inspect status before parsing.
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: buildHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  return response;
}

export async function apiDelete(path) {
  // Shared DELETE helper centralizes auth/error handling.
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'DELETE',
    headers: buildHeaders(),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
  }
}

export async function apiUpload(path, file, onProgress = null) {
  // Use XHR so upload progress events can be surfaced in the UI.
  const formData = new FormData();
  formData.append('file', file);

  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', `${API_BASE}${path}`);

    const headers = buildHeaders();
    Object.entries(headers).forEach(([key, value]) => {
      request.setRequestHeader(key, value);
    });

    if (onProgress) {
      request.upload.onprogress = (event) => {
        if (!event.lengthComputable) {
          return;
        }
        const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
        onProgress(percent);
      };
    }

    request.onload = () => {
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
      reject(new Error(formatUserError(payload.detail || `Upload failed: ${request.status}`)));
    };

    request.onerror = () => {
      reject(new Error('Upload failed: network error'));
    };

    request.send(formData);
  });
}

export async function downloadArtifactBundle(paths, downloadName) {
  const response = await fetch(`${API_BASE}/api/artifact-bundle`, {
    method: 'POST',
    headers: buildHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ paths }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
  }
  const href = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = downloadName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(href);
}

export { API_BASE, API_TOKEN, buildHeaders };
