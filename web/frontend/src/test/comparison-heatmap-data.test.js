import { describe, it, expect } from 'vitest';
import {
  consequenceColor,
  uniqueConsequenceTypes,
  featureColorPalette,
  classifyHeatmapCell,
  prepareHeatmapData,
  CONSEQUENCE_COLORS,
  DARK_CONSEQUENCE_COLORS,
} from '../components/comparison-heatmap-data';

describe('consequenceColor', () => {
  it('returns the correct color for known consequence types', () => {
    expect(consequenceColor('missense')).toBe(CONSEQUENCE_COLORS.missense);
    expect(consequenceColor('synonymous')).toBe(CONSEQUENCE_COLORS.synonymous);
    expect(consequenceColor('frameshift')).toBe(CONSEQUENCE_COLORS.frameshift);
    expect(consequenceColor('stop_gained')).toBe(CONSEQUENCE_COLORS.stop_gained);
  });

  it('returns fallback color for unknown type', () => {
    expect(consequenceColor('nonexistent')).toBe('#e4e4e7');
  });

  it('returns fallback for empty string', () => {
    expect(consequenceColor('')).toBe('#e4e4e7');
  });

  it('returns dark-safe consequence colors when theme is dark', () => {
    expect(consequenceColor('stop_gained', 'dark')).toBe(DARK_CONSEQUENCE_COLORS.stop_gained);
    expect(consequenceColor('stop_gained', 'dark')).toBe('#f4f4f5');
    expect(consequenceColor('stop_lost', 'dark')).toBe('#a78bfa');
    expect(consequenceColor('start_lost', 'dark')).toBe('#eab308');
    expect(consequenceColor('deletion', 'dark')).toBe('#60a5fa');
    expect(consequenceColor('deletion', 'light')).toBe('#2563eb');
    expect(consequenceColor('unknown', 'dark')).toBe('#64748b');
    expect(consequenceColor('nonexistent', 'dark')).toBe('#71717a');
    for (const color of Object.values(DARK_CONSEQUENCE_COLORS)) {
      expect(color).not.toBe('#18181b');
    }
  });
});

describe('uniqueConsequenceTypes', () => {
  it('deduplicates consequence types', () => {
    expect(uniqueConsequenceTypes(['missense', 'synonymous', 'missense'])).toEqual([
      'missense',
      'synonymous',
    ]);
  });

  it('sorts consequence types alphabetically', () => {
    const result = uniqueConsequenceTypes(['frameshift', 'missense', 'deletion']);
    expect(result).toEqual(['deletion', 'frameshift', 'missense']);
  });

  it('returns empty array for empty input', () => {
    expect(uniqueConsequenceTypes([])).toEqual([]);
  });

  it('preserves first occurrence order before sort', () => {
    const result = uniqueConsequenceTypes(['synonymous', 'missense', 'synonymous', 'frameshift']);
    expect(result).toEqual(['frameshift', 'missense', 'synonymous']);
  });
});

describe('featureColorPalette', () => {
  it('returns base colors for count <= 20', () => {
    const palette = featureColorPalette(5);
    expect(palette).toHaveLength(5);
    expect(palette[0]).toBe('#4e79a7');
  });

  it('returns all base colors for count == 20', () => {
    const palette = featureColorPalette(20);
    expect(palette).toHaveLength(20);
  });

  it('wraps colors when count > 20', () => {
    const palette = featureColorPalette(25);
    expect(palette).toHaveLength(25);
    // The 21st color should wrap to the first base color
    expect(palette[20]).toBe(palette[0]);
  });

  it('returns empty array for count 0', () => {
    expect(featureColorPalette(0)).toEqual([]);
  });
});

describe('prepareHeatmapData', () => {
  it('returns null for null data', () => {
    expect(prepareHeatmapData(null)).toBeNull();
  });

  it('returns null for empty matrix', () => {
    expect(prepareHeatmapData({ matrix: [] })).toBeNull();
  });

  it('returns null for data without matrix', () => {
    expect(prepareHeatmapData({ samples: [] })).toBeNull();
  });

  it('returns expected fields for minimal valid data', () => {
    const mockData = {
      samples: ['Sample1', 'Sample2'],
      mutation_labels: ['M1', 'M2', 'M3'],
      mutation_tick_labels: ['M1', 'M2', 'M3'],
      features: ['GeneA'],
      feature_map: [0, 0, 0],
      feature_display_names: { GeneA: 'Gene A' },
      consequences: ['missense', 'synonymous', 'missense'],
      db_hit_map: [1, 0, 1],
      matrix: [
        [{ allele_freq: 0.5 }, { allele_freq: null }, { allele_freq: 0.8 }],
        [{ allele_freq: 0.3 }, { allele_freq: 0.1 }, { allele_freq: null }],
      ],
    };
    const result = prepareHeatmapData(mockData);

    expect(result).not.toBeNull();
    expect(result.samples).toEqual(['Sample1', 'Sample2']);
    expect(result.hasFeatures).toBe(true);
    expect(result.hasDbHits).toBe(true);
    expect(result.hasConsequences).toBe(true);

    // mainZ: null for missing allele_freq
    expect(result.mainZ[0][0]).toBe(0.5);
    expect(result.mainZ[0][1]).toBeNull();
    expect(result.mainZ[0][2]).toBe(0.8);
    expect(result.mainZ[1][2]).toBeNull();

    // gapZ: 1 for gaps (null allele_freq), null for present
    expect(result.gapZ[0][1]).toBe(1);
    expect(result.gapZ[0][0]).toBeNull();
    expect(result.gapZ[1][2]).toBe(1);

    // Feature colors should be populated
    expect(result.featureColors).toBeDefined();
    expect(result.featureColors.length).toBeGreaterThan(0);

    // Consequence types should be sorted unique
    expect(result.consequenceTypes).toEqual(['missense', 'synonymous']);

    // Domain calculations
    expect(result.heatmapDomainEnd).toBeGreaterThan(0);
    expect(result.totalRows).toBeGreaterThan(0);
    expect(result.height).toBeGreaterThan(0);
  });

  it('handles data without features, consequences, or db_hits', () => {
    const minimalData = {
      samples: ['S1'],
      mutation_labels: ['M1'],
      mutation_tick_labels: ['M1'],
      features: [],
      feature_map: [],
      feature_display_names: {},
      consequences: [],
      db_hit_map: [],
      matrix: [[{ allele_freq: 0.5 }]],
    };
    const result = prepareHeatmapData(minimalData);

    expect(result).not.toBeNull();
    expect(result.hasFeatures).toBe(false);
    expect(result.hasDbHits).toBe(false);
    expect(result.hasConsequences).toBe(false);
    expect(result.featureColors).toBeNull();
    expect(result.consequenceTypes).toBeNull();
    expect(result.dbHitZ).toBeNull();
  });

  it('calculates left margin from sample name length', () => {
    const data = {
      samples: ['VeryLongSampleNameHere'],
      mutation_labels: ['M1'],
      mutation_tick_labels: ['M1'],
      features: [],
      feature_map: [],
      feature_display_names: {},
      consequences: [],
      db_hit_map: [],
      matrix: [[{ allele_freq: 0.1 }]],
    };
    const result = prepareHeatmapData(data);
    expect(result.leftMargin).toBeGreaterThanOrEqual(150);
  });

  it('handles undefined allele_freq as gap', () => {
    const data = {
      samples: ['S1'],
      mutation_labels: ['M1'],
      mutation_tick_labels: ['M1'],
      features: [],
      feature_map: [],
      feature_display_names: {},
      consequences: [],
      db_hit_map: [],
      matrix: [[{ allele_freq: undefined }]],
    };
    const result = prepareHeatmapData(data);
    expect(result.mainZ[0][0]).toBeNull();
    expect(result.gapZ[0][0]).toBe(1);
  });

  it('distinguishes coverage_gap: true from coverage_gap: false with allele_freq: null (WA-016)', () => {
    const data = {
      samples: ['S1', 'S2'],
      mutation_labels: ['M1', 'M2', 'M3', 'M4', 'M5'],
      mutation_tick_labels: ['M1', 'M2', 'M3', 'M4', 'M5'],
      features: [],
      feature_map: [],
      feature_display_names: {},
      consequences: [],
      db_hit_map: [],
      matrix: [
        [
          { allele_freq: null, db_hit: false, coverage_gap: true },
          { allele_freq: null, db_hit: true, coverage_gap: false },
          { allele_freq: 0.0, db_hit: false, coverage_gap: false },
          { allele_freq: 0.75, db_hit: true, coverage_gap: false },
          { allele_freq: null, db_hit: false }, // legacy cell without coverage_gap
        ],
        [
          { allele_freq: 0.25, db_hit: false, coverage_gap: true }, // coverage_gap: true wins defensively
          { allele_freq: undefined, db_hit: false, coverage_gap: false },
          { allele_freq: 0.5, db_hit: false },
          null,
          { allele_freq: 1.0, db_hit: true, coverage_gap: false },
        ],
      ],
    };

    const result = prepareHeatmapData(data);

    // Row 0: [gap, unknown-AF, absent 0.0, quantified 0.75, legacy gap]
    expect(result.gapZ[0]).toEqual([1, null, null, null, 1]);
    expect(result.mainZ[0]).toEqual([null, null, 0.0, 0.75, null]);
    expect(result.unknownAfZ[0]).toEqual([null, 1, null, null, null]);

    // Row 1: [gap, unknown-AF, legacy quantified, null cell -> gap, quantified 1.0]
    expect(result.gapZ[1]).toEqual([1, null, null, 1, null]);
    expect(result.mainZ[1]).toEqual([null, null, 0.5, null, 1.0]);
    expect(result.unknownAfZ[1]).toEqual([null, 1, null, null, null]);

    expect(result.hasUnknownAf).toBe(true);
  });

  it('sets hasUnknownAf to false when no cell has coverage_gap === false and null allele_freq', () => {
    const data = {
      samples: ['S1'],
      mutation_labels: ['M1', 'M2', 'M3'],
      mutation_tick_labels: ['M1', 'M2', 'M3'],
      features: [],
      feature_map: [],
      feature_display_names: {},
      consequences: [],
      db_hit_map: [],
      matrix: [[
        { allele_freq: null, db_hit: false, coverage_gap: true },
        { allele_freq: 0.0, db_hit: false, coverage_gap: false },
        { allele_freq: 0.4, db_hit: true, coverage_gap: false },
      ]],
    };

    const result = prepareHeatmapData(data);
    expect(result.hasUnknownAf).toBe(false);
    expect(result.unknownAfZ).toEqual([[null, null, null]]);
  });

  it('uses dark consequence colors in consequenceColorscale when theme is dark', () => {
    const data = {
      samples: ['S1'],
      mutation_labels: ['M1', 'M2'],
      mutation_tick_labels: ['M1', 'M2'],
      features: [],
      feature_map: [],
      feature_display_names: {},
      consequences: ['stop_gained', 'stop_lost'],
      db_hit_map: [],
      matrix: [[
        { allele_freq: 0.5, coverage_gap: false },
        { allele_freq: null, coverage_gap: false },
      ]],
    };

    const darkPrepared = prepareHeatmapData(data, 'dark');
    expect(darkPrepared.consequenceTypes).toEqual(['stop_gained', 'stop_lost']);
    expect(darkPrepared.consequenceColorscale).toEqual([
      [0, '#f4f4f5'],
      [1, '#a78bfa'],
    ]);
    expect(darkPrepared.hasUnknownAf).toBe(true);
    expect(darkPrepared.unknownAfZ).toEqual([[null, 1]]);
  });
});

describe('classifyHeatmapCell', () => {
  it('classifies explicit coverage_gap, unknown-AF, quantified, and legacy cells', () => {
    expect(classifyHeatmapCell({ allele_freq: null, coverage_gap: true })).toEqual({
      gapZ: 1,
      mainZ: null,
      unknownAfZ: null,
      isUnknownAf: false,
    });
    expect(classifyHeatmapCell({ allele_freq: null, coverage_gap: false })).toEqual({
      gapZ: null,
      mainZ: null,
      unknownAfZ: 1,
      isUnknownAf: true,
    });
    expect(classifyHeatmapCell({ allele_freq: 0.35, coverage_gap: false })).toEqual({
      gapZ: null,
      mainZ: 0.35,
      unknownAfZ: null,
      isUnknownAf: false,
    });
    expect(classifyHeatmapCell({ allele_freq: null })).toEqual({
      gapZ: 1,
      mainZ: null,
      unknownAfZ: null,
      isUnknownAf: false,
    });
    expect(classifyHeatmapCell(null)).toEqual({
      gapZ: 1,
      mainZ: null,
      unknownAfZ: null,
      isUnknownAf: false,
    });
  });
});
