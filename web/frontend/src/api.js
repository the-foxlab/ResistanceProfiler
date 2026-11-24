import { FRONTEND_CONFIG } from './config';
import { formatResultTimestamp, triggerBlobDownload } from './utils';

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
  const dotIndex = basename.lastIndexOf('.');
  return dotIndex <= 0 ? basename : basename.slice(0, dotIndex);
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
  }
  return response;
}

export async function apiGet(path, params = {}) {
  const response = await apiRequest(buildApiUrl(path, params), { headers: {} });
  return response.json();
}

export async function apiPost(path, body) {
  const response = await apiRequest(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return response.json();
}

export function apiPostRaw(path, body) {
  return fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function apiDelete(path) {
  await apiRequest(`${API_BASE}${path}`, { method: 'DELETE', headers: {} });
}

export function apiUpload(path, file, onProgress = null, onAbort = null) {
  // XHR is used so upload progress events and mid-flight cancellation can be surfaced.
  const formData = new FormData();
  formData.append('file', file);

  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', `${API_BASE}${path}`);

    if (onProgress) {
      request.upload.onprogress = (event) => {
        if (!event.lengthComputable) return;
        onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)));
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

    request.onerror = () => reject(new Error('Upload failed: network error'));
    // Settle on abort so callers' finally blocks clear in-flight state after a cancel.
    request.onabort = () => reject(new Error('Upload canceled'));

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

export async function downloadArtifactBundle(artifactIds, downloadName) {
  const chunks = chunkIds(artifactIds);
  for (let index = 0; index < chunks.length; index += 1) {
    const name = chunks.length === 1 ? downloadName : withPartSuffix(downloadName, index + 1, chunks.length);
    const response = await apiRequest(buildApiUrl('/api/artifact-bundle', { part: chunks.length > 1 ? index + 1 : null }), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifact_ids: chunks[index] }),
    });
    triggerBlobDownload(await response.blob(), name);
  }
}

export async function fetchSharedReferences(ids) {
  return (await apiGet('/api/databases/shared-references', { ids: ids.join(',') })).data;
}

export async function fetchReferenceAccessions(ids) {
  return (await apiGet('/api/databases/reference-accessions', { ids: ids.join(',') })).data;
}

export async function compareDatabases(databaseIds, accession) {
  return (await apiPost('/api/databases/compare', { database_ids: databaseIds, accession })).data;
}

export { API_BASE, formatResultTimestamp, triggerBlobDownload };
