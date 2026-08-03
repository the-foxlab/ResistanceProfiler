/**
 * Shared utility helpers used across multiple frontend modules.
 */

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
