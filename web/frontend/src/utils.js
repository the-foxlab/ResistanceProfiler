/**
 * Shared utility helpers used across multiple frontend modules.
 */

/**
 * Rank-based phenotype severity vocabulary.
 *
 * Mirrors `respro/db/phenotype_ranks.py` so the frontend can order threshold
 * labels by severity (weakest → strongest) without a round-trip to the backend.
 * Ranks 1–5 denote increasing severity; sentinels 0 (unknown) and -1
 * (contradictory) sit outside the severity ladder. Labels are matched
 * case-insensitively and whitespace-tolerantly, exactly as in Python.
 */
const PHENOTYPE_RANKS = {
  // Rank 1 — susceptible
  susceptible: 1,
  sensitive: 1,
  'normal inhibition': 1,
  ni: 1,
  normal: 1,
  // Rank 2 — potential low-level resistance
  'potential low-level resistance': 2,
  'possibly resistant': 2,
  'suspected reduced': 2,
  // Rank 3 — low-level resistance
  'low-level resistance': 3,
  'reduced susceptibility': 3,
  'limited susceptibility': 3,
  // Rank 4 — intermediate
  intermediate: 4,
  'intermediate resistance': 4,
  'reduced inhibition': 4,
  ri: 4,
  // Rank 5 — resistant
  resistant: 5,
  'high-level resistance': 5,
  'highly reduced inhibition': 5,
  hri: 5,
  // Sentinel — unknown / not analysed
  unknown: 0,
  'not analysed': 0,
  none: 0,
  '': 0,
  // Sentinel — contradictory / conflicting
  contradictory: -1,
  conflicting: -1,
};

/**
 * Resolve a phenotype label to its severity rank.
 *
 * @param {string} label - phenotype label (case-insensitive, whitespace-tolerant)
 * @returns {number|null} rank (1–5, or 0/-1 sentinels), or null if unrecognized
 */
export function labelToRank(label) {
  const key = String(label ?? '').trim().toLowerCase();
  if (!key) {
    // Empty/missing input is not a label at all — distinguish from the literal
    // labels "unknown"/"none" which legitimately resolve to rank 0.
    return null;
  }
  if (key in PHENOTYPE_RANKS) {
    return PHENOTYPE_RANKS[key];
  }
  return null;
}

/**
 * Return threshold labels ordered by severity rank (weakest → strongest).
 *
 * Sentinels (unknown/contradictory) are excluded — they are not severity
 * breakpoints. Unrecognized labels sort last (after all known ranks) in
 * alphabetical order so they are still shown rather than dropped.
 *
 * @param {Object} thresholds - thresholds dict (label → value)
 * @returns {string[]} ordered label keys
 */
export function orderedThresholdLabels(thresholds) {
  if (!thresholds || typeof thresholds !== 'object') {
    return [];
  }
  const entries = Object.keys(thresholds)
    .map((label) => ({ label, rank: labelToRank(label) }))
    // Exclude sentinels (unknown/contradictory, rank <= 0) — they are not
    // severity breakpoints and mirror the Python `_assessment_description`
    // which only iterates labels with rank > 0.
    .filter((e) => e.rank !== null && e.rank > 0);
  entries.sort((a, b) => {
    // Known positive ranks sort weakest → strongest.
    if (a.rank > 0 && b.rank > 0) {
      return a.rank - b.rank;
    }
    // Unrecognized labels (rank === null) sort last, alphabetically.
    if (a.rank === null && b.rank !== null) return 1;
    if (b.rank === null && a.rank !== null) return -1;
    return a.label.localeCompare(b.label);
  });
  return entries.map((e) => e.label);
}

/**
 * Returns true if the value is neither null, undefined, nor whitespace-only.
 */
export function isPopulated(value) {
  return value !== null && value !== undefined && String(value).trim() !== '';
}

/**
 * Build a lookup map from drug name (lowercased) to its display alias.
 * Mirrors the drug_aliases structure from the backend plot metadata.
 */
export function buildDrugAliasLookup(plotMeta) {
  const aliases = plotMeta?.drug_aliases || {};
  const lookup = new Map();
  Object.entries(aliases).forEach(([drugName, alias]) => {
    const canonical = String(drugName || '').trim().toLowerCase();
    const displayAlias = String(alias || '').trim();
    if (canonical && displayAlias) {
      lookup.set(canonical, displayAlias);
    }
  });
  return lookup;
}

/**
 * Condense a `drug_thresholds` override list for dashboard display.
 *
 * Groups override entries that share the same `reference` (or "(all)" when
 * absent) and the same `thresholds` values, collapsing their `drug` names into
 * a sorted set — mirroring the condensing applied to `effect_as_resistant`
 * rules. Returns one row per `(reference, thresholds)` group.
 *
 * @param {Array<Object>} drugThresholds - list of override entries
 * @returns {Array<{reference: string, drugs: string[], thresholds: Object}>}
 */
export function groupDrugThresholds(drugThresholds) {
  if (!Array.isArray(drugThresholds) || drugThresholds.length === 0) {
    return [];
  }

  const grouped = new Map();
  for (const entry of drugThresholds) {
    const reference = String(entry?.reference || '').trim() || '(all)';
    const drug = String(entry?.drug || '').trim();
    const thresholds = entry?.thresholds || {};
    const thresholdsKey = JSON.stringify(thresholds, Object.keys(thresholds).sort());
    const key = `${reference}|||${thresholdsKey}`;
    if (!grouped.has(key)) {
      grouped.set(key, { reference, thresholds, drugs: new Set() });
    }
    if (drug) {
      grouped.get(key).drugs.add(drug);
    }
  }

  return [...grouped.values()]
    .map((row) => ({
      reference: row.reference,
      drugs: [...row.drugs].sort((a, b) => a.localeCompare(b)),
      thresholds: row.thresholds,
    }))
    .sort((a, b) => {
      const refOrder = a.reference.localeCompare(b.reference);
      if (refOrder !== 0) {
        return refOrder;
      }
      return a.drugs.join(',').localeCompare(b.drugs.join(','));
    });
}

/**
 * Format a thresholds object as a compact string for dashboard display.
 *
 * @param {Object} thresholds - thresholds dict (e.g. {resistant: 1, intermediate: 1})
 * @returns {string}
 */
export function formatAlgorithmThresholds(thresholds) {
  if (!thresholds || typeof thresholds !== 'object') {
    return 'Not configured';
  }

  const keys = Object.keys(thresholds).sort((a, b) => a.localeCompare(b));
  if (keys.length === 0) {
    return 'Not configured';
  }

  const values = keys.map((key) => {
    const value = thresholds[key];
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const nestedKeys = Object.keys(value).sort((a, b) => a.localeCompare(b));
      const nestedText = nestedKeys
        .map((nestedKey) => `${nestedKey}=${value[nestedKey]}`)
        .join(', ');
      return `${key}: ${nestedText}`;
    }
    return `${key}=${value}`;
  });
  return values.join('; ');
}
