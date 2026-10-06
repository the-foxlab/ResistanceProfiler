/**
 * Venn area computation and colour palettes for the database-comparison
 * diagram.
 *
 * Kept in its own module so ComparisonVenn.jsx stays a pure component
 * (required for React Fast Refresh) and so the conversion is unit-testable.
 */

function regionKey(region) {
  return [...region].sort().join(',');
}

function nonEmptySubsets(items) {
  const subsets = [];
  for (let size = 1; size <= items.length; size += 1) {
    const visit = (start, current) => {
      if (current.length === size) {
        subsets.push([...current]);
        return;
      }
      for (let index = start; index <= items.length - (size - current.length); index += 1) {
        current.push(items[index]);
        visit(index + 1, current);
        current.pop();
      }
    };
    visit(0, []);
  }
  return subsets;
}

// Per-database fill colours for the Venn sets. Light mode uses muted,
// translucent fills with saturated strokes; dark mode uses brighter fills
// with light strokes so regions stay obvious on the dark surface. The dark
// hues are spaced further apart (teal / blue / amber) so selected regions
// and their overlap read unambiguously.
export const VENN_PALETTES = {
  light: [
    { fill: 'rgba(13, 148, 136, 0.30)', line: '#0d9488' },
    { fill: 'rgba(59, 130, 246, 0.30)', line: '#3b82f6' },
    { fill: 'rgba(217, 119, 6, 0.30)', line: '#d97706' },
  ],
  dark: [
    { fill: 'rgba(45, 212, 191, 0.45)', line: '#2dd4bf' },
    { fill: 'rgba(147, 197, 253, 0.45)', line: '#93c5fd' },
    { fill: 'rgba(252, 211, 77, 0.45)', line: '#fcd34d' },
  ],
};

/**
 * Convert exact-membership backend counts to inclusive sizes for venn.js.
 *
 * venn.js sizes each set as the total area it covers, so a subset's size is
 * the sum of the counts of all exact regions that contain it (supersets).
 * Each area carries `label` (the in-circle count) and `description` (full
 * text for title/aria-label).
 */
export function buildVennAreas(data) {
  const databaseIds = data.databases.map((database) => database.id);
  const databaseById = new Map(data.databases.map((database) => [database.id, database]));
  const exactRegions = (data.venn || []).map((entry) => ({
    databases: new Set(entry.region),
    count: entry.count,
  }));
  const exactCountByRegion = new Map(
    exactRegions.map((region) => [regionKey([...region.databases]), region.count]),
  );
  // Size-0 areas (a database with no rules, or an empty intersection) are
  // dropped: venn.js geometry for degenerate zero-area circles is undefined.
  return nonEmptySubsets(databaseIds).map((subset) => {
    const count = exactCountByRegion.get(regionKey(subset)) || 0;
    const names = subset.map((id) => databaseById.get(id)?.name || id).join(' ∩ ');
    const description = `${names} · ${count}`;
    return {
      sets: subset,
      size: exactRegions.reduce(
        (total, region) => total + (subset.every((id) => region.databases.has(id)) ? region.count : 0),
        0,
      ),
      label: String(count),
      description,
    };
  }).filter((area) => area.size > 0);
}
