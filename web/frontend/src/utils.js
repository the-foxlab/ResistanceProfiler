/**
 * Rank-based phenotype severity vocabulary mirroring `respro/core/phenotype_ranks.py`.
 * Ranks 1–5 denote increasing severity; 0 (unknown) and -1 (contradictory) are sentinels.
 */
const PHENOTYPE_RANKS = {
  susceptible: 1,
  sensitive: 1,
  'normal inhibition': 1,
  ni: 1,
  normal: 1,
  'potential low-level resistance': 2,
  'possibly resistant': 2,
  'suspected reduced': 2,
  'low-level resistance': 3,
  'reduced susceptibility': 3,
  'limited susceptibility': 3,
  intermediate: 4,
  'intermediate resistance': 4,
  'reduced inhibition': 4,
  ri: 4,
  resistant: 5,
  'high-level resistance': 5,
  'highly reduced inhibition': 5,
  hri: 5,
  unknown: 0,
  'not analysed': 0,
  none: 0,
  '': 0,
  contradictory: -1,
  conflicting: -1,
};

export function labelToRank(label) {
  const key = String(label ?? '').trim().toLowerCase();
  if (!key) return null;
  return key in PHENOTYPE_RANKS ? PHENOTYPE_RANKS[key] : null;
}

/** Return recognized severity-ranked threshold labels (rank > 0) ordered weakest → strongest. */
export function orderedThresholdLabels(thresholds) {
  if (!thresholds || typeof thresholds !== 'object') return [];
  return Object.keys(thresholds)
    .map((label) => ({ label, rank: labelToRank(label) }))
    .filter((entry) => entry.rank !== null && entry.rank > 0)
    .sort((a, b) => a.rank - b.rank)
    .map((entry) => entry.label);
}

export function isPopulated(value) {
  return value !== null && value !== undefined && String(value).trim() !== '';
}

export function buildDrugAliasLookup(plotMeta) {
  const lookup = new Map();
  Object.entries(plotMeta?.drug_aliases || {}).forEach(([drugName, alias]) => {
    const canonical = String(drugName || '').trim().toLowerCase();
    const displayAlias = String(alias || '').trim();
    if (canonical && displayAlias) lookup.set(canonical, displayAlias);
  });
  return lookup;
}

/** Group `drug_thresholds` override entries sharing `(reference, thresholds)`. */
export function groupDrugThresholds(drugThresholds) {
  if (!Array.isArray(drugThresholds) || drugThresholds.length === 0) return [];

  const grouped = new Map();
  for (const entry of drugThresholds) {
    const reference = String(entry?.reference || '').trim() || '(all)';
    const drug = String(entry?.drug || '').trim();
    const thresholds = entry?.thresholds || {};
    const key = `${reference}|||${JSON.stringify(thresholds, Object.keys(thresholds).sort())}`;
    if (!grouped.has(key)) {
      grouped.set(key, { reference, thresholds, drugs: new Set() });
    }
    if (drug) grouped.get(key).drugs.add(drug);
  }

  return [...grouped.values()]
    .map((row) => ({
      reference: row.reference,
      drugs: [...row.drugs].sort((a, b) => a.localeCompare(b)),
      thresholds: row.thresholds,
    }))
    .sort((a, b) => a.reference.localeCompare(b.reference) || a.drugs.join(',').localeCompare(b.drugs.join(',')));
}

export function cleanTsvCell(value) {
  return String(value ?? '').replace(/\t/g, ' ').replace(/\r?\n/g, ' ');
}

export function sanitizeTsvFormulaCell(value) {
  const raw = String(value ?? '');
  const cleaned = cleanTsvCell(raw).replace(/\r/g, ' ');
  if (/^[\t\r]/.test(raw) || /^\s*[=+\-@]/.test(cleaned)) {
    return `'${cleaned}`;
  }
  return cleaned;
}

const SAFE_EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;

export function sanitizeHttpUrl(value) {
  if (value === null || value === undefined) return null;
  const trimmed = String(value).trim();
  if (!trimmed || /[\s\u0000-\u001F\u007F]/.test(trimmed) || !/^https?:\/\//i.test(trimmed)) {
    return null;
  }
  try {
    const parsed = new URL(trimmed);
    if (
      (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')
      || !parsed.hostname
      || parsed.username
      || parsed.password
    ) {
      return null;
    }
    return trimmed;
  } catch {
    return null;
  }
}

export function sanitizeEmailAddress(value) {
  if (value === null || value === undefined) return null;
  const trimmed = String(value).trim();
  if (!trimmed || /[\s\u0000-\u001F\u007F?&#=:;,<>\\/"']/.test(trimmed)) {
    return null;
  }
  if (!SAFE_EMAIL_RE.test(trimmed)) {
    return null;
  }
  const [local] = trimmed.split('@');
  if (!local || local.startsWith('.') || local.endsWith('.') || local.includes('..')) {
    return null;
  }
  return trimmed;
}

export function triggerBlobDownload(blob, filename) {
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = filename;
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    // Defer object URL revocation so WebKit/Safari finishes reading the blob after click().
    const timer = setTimeout(() => {
      if (typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') {
        URL.revokeObjectURL(href);
      }
    }, 1000);
    timer?.unref?.();
  }
}

export function downloadTsv(headers, rows, filename) {
  const lines = [
    headers.map(sanitizeTsvFormulaCell).join('\t'),
    ...rows.map((row) => row.map(sanitizeTsvFormulaCell).join('\t')),
  ];
  const blob = new Blob([`${lines.join('\n')}\n`], { type: 'text/tab-separated-values;charset=utf-8' });
  triggerBlobDownload(blob, filename);
}

export function formatResultTimestamp(timestamp) {
  if (!timestamp) return 'n/a';
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime()) ? timestamp : parsed.toLocaleString();
}

export function validateVcfCutoffs(minAf, minDepth) {
  if (!Number.isFinite(minAf) || minAf < 0 || minAf > 1) {
    throw new Error('Frequency cutoff (min AF) must be a number between 0 and 1.');
  }
  if (!Number.isInteger(minDepth) || minDepth < 0) {
    throw new Error('Coverage cutoff (min depth) must be an integer greater than or equal to 0.');
  }
}

export function formatReportLabel(result) {
  return `${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`;
}

export function selectIndexedItems(items, indices) {
  return [...indices].map((idx) => items[idx]).filter(Boolean);
}
