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
