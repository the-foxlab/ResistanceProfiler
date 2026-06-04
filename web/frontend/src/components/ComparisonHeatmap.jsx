import Plotly from 'plotly.js-dist-min';
import { useRef, useEffect, useState } from 'react';
import { Spinner } from './Spinner';

/**
 * Interactive comparison heatmap rendered with Plotly.js.
 *
 * Props:
 * - data: CompareResponse object from the backend, or null
 * - isBusy: boolean — show spinner while loading
 */
export function ComparisonHeatmap({ data, isBusy }) {
  const containerRef = useRef(null);
  const [plotError, setPlotError] = useState('');

  useEffect(() => {
    if (!data || !containerRef.current) {
      // Clear previous plot if data is removed
      if (containerRef.current) {
        Plotly.purge(containerRef.current);
      }
      return;
    }

    setPlotError('');
    try {
      renderHeatmap(containerRef.current, data);
    } catch (err) {
      setPlotError(String(err.message || err));
    }

    return () => {
      if (containerRef.current) {
        Plotly.purge(containerRef.current);
      }
    };
  }, [data]);

  // Re-render heatmap on window resize (debounced) — Plots.resize() is insufficient
  // for multi-yaxis domain layouts; a full re-render matches the "Compare selected" behavior
  useEffect(() => {
    if (!data || !containerRef.current) return;

    let timeoutId = null;
    const handleResize = () => {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        if (containerRef.current && data) {
          try {
            renderHeatmap(containerRef.current, data);
          } catch (_) {
            // ignore re-render errors on resize
          }
        }
      }, 300);
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      clearTimeout(timeoutId);
    };
  }, [data]);

  if (isBusy) {
    return (
      <div className="comparison-heatmap-loading">
        <Spinner /> Loading comparison data...
      </div>
    );
  }

  if (plotError) {
    return (
      <div className="comparison-heatmap-error">
        <p>Failed to render heatmap: {plotError}</p>
      </div>
    );
  }

  if (!data) {
    return null;
  }

  // Build feature legend and simplified cell legend below the heatmap
  let featureLegend = null;

  if (data.features && data.features.length > 0) {
    const featureColors = _featureColorPalette(Math.max(data.features.length, 1));
    const displayNames = data.feature_display_names || {};
    featureLegend = (
      <>
        <span className="comparison-feature-legend-title">Features:</span>
        {data.features.map((feature, idx) => (
          <span key={feature} className="comparison-feature-legend-item">
            <span className="comparison-feature-swatch" style={{ backgroundColor: featureColors[idx] }} />
            {displayNames[feature] || feature}
          </span>
        ))}
      </>
    );
  }

  const cellLegend = (
    <>
      <span className="comparison-cell-legend-item">
        <span className="comparison-cell-swatch" style={{ backgroundColor: '#b0b0b0' }} /> Coverage gap
      </span>
      <span className="comparison-cell-legend-item">
        <span className="comparison-cell-swatch" style={{ backgroundColor: '#d62728' }} /> Database hit
      </span>
    </>
  );

  // Consequence legend
  const consequenceLegend = data.consequences && data.consequences.length > 0 ? (
    <>
      <span className="comparison-consequence-legend-title">Consequences:</span>
      {_uniqueConsequenceTypes(data.consequences).map((ctype) => (
        <span key={ctype} className="comparison-consequence-legend-item">
          <span
            className="comparison-consequence-swatch"
            style={{ backgroundColor: _consequenceColor(ctype) }}
          />
          {ctype}
        </span>
      ))}
    </>
  ) : null;

  return (
    <>
      <div className="comparison-heatmap-container" ref={containerRef} />
      <div className="comparison-legend-row">
        {featureLegend}
        <span className="comparison-legend-separator" />
        {cellLegend}
        {consequenceLegend && (
          <>
            <span className="comparison-legend-separator" />
            {consequenceLegend}
          </>
        )}
      </div>
    </>
  );
}

function renderHeatmap(container, data) {
  const {
    samples,
    mutation_labels,
    mutation_tick_labels,
    features,
    feature_map,
    feature_display_names,
    consequences,
    db_hit_map,
    matrix,
  } = data;

  const hasFeatures = features && features.length > 0;
  const hasDbHits = db_hit_map && db_hit_map.length > 0;
  const hasConsequences = consequences && consequences.length > 0;
  const displayNames = feature_display_names || {};

  // Create unique x labels that display as amino acid changes only.
  // When the same label appears in multiple features, append invisible
  // zero-width spaces to make them unique Plotly categories while
  // displaying identically.
  const xLabels = [];
  const labelCounts = {};
  for (const label of (mutation_tick_labels || mutation_labels)) {
    labelCounts[label] = (labelCounts[label] || 0) + 1;
  }
  const labelSeen = {};
  for (const label of (mutation_tick_labels || mutation_labels)) {
    labelSeen[label] = (labelSeen[label] || 0) + 1;
    if (labelCounts[label] > 1) {
      xLabels.push(label + '\u200B'.repeat(labelSeen[label] - 1));
    } else {
      xLabels.push(label);
    }
  }

  // Auto-calculate left margin to avoid clipping long sample names
  const maxSampleLen = Math.max(...samples.map((s) => s.length), 1);
  const leftMargin = Math.max(150, Math.min(300, maxSampleLen * 8));

  // Pre-compute display names for features (used in customdata)
  const featureDisplayList = features
    ? features.map((f) => displayNames[f] || f)
    : [];

  const traces = [];

  // 1. Coverage gap overlay trace (behind main trace)
  //    z=1 for gap cells, null for non-gap cells
  const gapZ = samples.map((_si, si) =>
    mutation_labels.map((_mi, mi) => {
      const af = matrix[si][mi].allele_freq;
      return (af === null || af === undefined) ? 1 : null;
    })
  );
  const gapCustomdata = samples.map((_si, si) =>
    mutation_labels.map((mi, miIdx) => {
      const feature = features ? features[feature_map[miIdx]] : '';
      const featureDisplayName = displayNames[feature] || feature;
      const tickLabel = mutation_tick_labels ? mutation_tick_labels[miIdx] : mi;
      return [tickLabel, featureDisplayName];
    })
  );
  const gapTrace = {
    z: gapZ,
    x: xLabels,
    y: samples,
    type: 'heatmap',
    colorscale: [[0, '#e0e0e0'], [1, '#b0b0b0']],
    showscale: false,
    zmin: 0,
    zmax: 1,
    xgap: 1,
    ygap: 1,
    xaxis: 'x',
    yaxis: 'y',
    customdata: gapCustomdata,
    hovertemplate: 'Sample: %{y}<br>Mutation: %{customdata[0]}<br>Feature: %{customdata[1]}<br>Coverage gap<extra></extra>',
    line: { color: '#333', width: 1 },
  };
  traces.push(gapTrace);

  // 2. Main allele frequency trace: null for gaps, 0-1 for values
  const mainZ = samples.map((_si, si) =>
    mutation_labels.map((_mi, mi) => {
      const af = matrix[si][mi].allele_freq;
      return (af === null || af === undefined) ? null : af;
    })
  );
  const mainCustomdata = samples.map((_si, si) =>
    mutation_labels.map((mi, miIdx) => {
      const feature = features ? features[feature_map[miIdx]] : '';
      const featureDisplayName = displayNames[feature] || feature;
      const tickLabel = mutation_tick_labels ? mutation_tick_labels[miIdx] : mi;
      return [tickLabel, featureDisplayName];
    })
  );
  const mainTrace = {
    z: mainZ,
    x: xLabels,
    y: samples,
    type: 'heatmap',
    colorscale: [
      [0, '#440154'],
      [0.25, '#3b528b'],
      [0.5, '#21918c'],
      [0.75, '#5ec962'],
      [1.0, '#fde725'],
    ],
    zmin: 0,
    zmax: 1,
    showscale: true,
    colorbar: {
      title: { text: 'Allele freq', side: 'right' },
      tickvals: [0, 0.25, 0.5, 0.75, 1.0],
      ticktext: ['0', '0.25', '0.5', '0.75', '1.0'],
      thickness: 15,
    },
    xgap: 1,
    ygap: 1,
    xaxis: 'x',
    yaxis: 'y',
    customdata: mainCustomdata,
    hovertemplate: 'Sample: %{y}<br>Mutation: %{customdata[0]}<br>Feature: %{customdata[1]}<br>Allele freq: %{z}<extra></extra>',
    line: { color: '#333', width: 1 },
  };
  traces.push(mainTrace);

  // 3. Feature annotation row (only if features exist)
  if (hasFeatures) {
    const featureColors = _featureColorPalette(Math.max(features.length, 1));
    const featureZValues = [mutation_labels.map((_mi, mi) => feature_map[mi])];
    const featureCustomdata = [mutation_labels.map((mi, miIdx) => {
      const feature = features[feature_map[miIdx]];
      const featureDisplayName = displayNames[feature] || feature;
      const tickLabel = mutation_tick_labels ? mutation_tick_labels[miIdx] : mi;
      return [featureDisplayName, tickLabel];
    })];
    const featureColorscale = features.length === 1
      ? [[0, featureColors[0]], [1, featureColors[0]]]
      : featureColors.map((color, idx) => [idx / (features.length - 1), color]);

    const featureTrace = {
      z: featureZValues,
      x: xLabels,
      y: ['Feature'],
      type: 'heatmap',
      colorscale: featureColorscale,
      zmin: 0,
      zmax: Math.max(features.length - 1, 1),
      showscale: false,
      xgap: 1,
      ygap: 1,
      xaxis: 'x',
      yaxis: 'y2',
      customdata: featureCustomdata,
      hovertemplate: 'Feature: %{customdata[0]}<br>Mutation: %{customdata[1]}<extra></extra>',
      line: { color: '#333', width: 1 },
    };
    traces.push(featureTrace);
  }

  // 4. DB hit annotation bar
  if (hasDbHits) {
    const dbHitZ = [db_hit_map.map((hit) => (hit ? 1 : 0))];
    const dbHitCustomdata = [db_hit_map.map((hit) => [hit ? 'In database' : 'Not in database'])];
    const dbHitTrace = {
      z: dbHitZ,
      x: xLabels,
      y: ['DB hit'],
      type: 'heatmap',
      colorscale: [[0, '#f0f0f0'], [1, '#d62728']],
      zmin: 0,
      zmax: 1,
      showscale: false,
      xgap: 1,
      ygap: 1,
      xaxis: 'x',
      yaxis: 'y3',
      customdata: dbHitCustomdata,
      hovertemplate: '%{customdata[0]}<extra></extra>',
      line: { color: '#333', width: 1 },
    };
    traces.push(dbHitTrace);
  }

  // 5. Consequence annotation row
  if (hasConsequences) {
    const consequenceTypes = _uniqueConsequenceTypes(consequences);
    const consequenceIndex = {};
    consequenceTypes.forEach((ctype, idx) => { consequenceIndex[ctype] = idx; });

    const consequenceZValues = [consequences.map((c) => consequenceIndex[c] || 0)];
    const consequenceColorscale = consequenceTypes.length === 1
      ? [[0, _consequenceColor(consequenceTypes[0])], [1, _consequenceColor(consequenceTypes[0])]]
      : consequenceTypes.map((ctype, idx) => [idx / (consequenceTypes.length - 1), _consequenceColor(ctype)]);

    const consequenceCustomdata = [consequences.map((c) => [c])];

    const consequenceTrace = {
      z: consequenceZValues,
      x: xLabels,
      y: ['Consequence'],
      type: 'heatmap',
      colorscale: consequenceColorscale,
      zmin: 0,
      zmax: Math.max(consequenceTypes.length - 1, 1),
      showscale: false,
      xgap: 1,
      ygap: 1,
      xaxis: 'x',
      yaxis: 'y4',
      customdata: consequenceCustomdata,
      hovertemplate: 'Consequence: %{customdata[0]}<extra></extra>',
      line: { color: '#333', width: 1 },
    };
    traces.push(consequenceTrace);
  }

  // Layout with domains for all axes — proportional to total conceptual rows
  const annotationRows = (hasFeatures ? 1 : 0) + (hasDbHits ? 1 : 0) + (hasConsequences ? 1 : 0);
  const totalRows = samples.length + annotationRows;
  const gapBudget = annotationRows > 0 ? 0.04 : 0;
  const gapBetweenSections = gapBudget / Math.max(annotationRows, 1);
  const availableFraction = 1 - gapBudget;
  const cellFraction = availableFraction / totalRows;

  // Main heatmap domain
  const heatmapDomainEnd = samples.length * cellFraction;
  let currentY = heatmapDomainEnd;

  // Feature row domain (if present)
  let featureDomainStart = 0;
  let featureDomainEnd = 0;
  if (hasFeatures) {
    currentY += gapBetweenSections;
    featureDomainStart = currentY;
    featureDomainEnd = currentY + cellFraction;
    currentY = featureDomainEnd;
  }

  // DB hit row domain (if present)
  let dbHitDomainStart = 0;
  let dbHitDomainEnd = 0;
  if (hasDbHits) {
    currentY += gapBetweenSections;
    dbHitDomainStart = currentY;
    dbHitDomainEnd = currentY + cellFraction;
    currentY = dbHitDomainEnd;
  }

  // Consequence row domain (if present)
  let consequenceDomainStart = 0;
  let consequenceDomainEnd = 0;
  if (hasConsequences) {
    currentY += gapBetweenSections;
    consequenceDomainStart = currentY;
    consequenceDomainEnd = currentY + cellFraction;
    currentY = consequenceDomainEnd;
  }

  const layout = {
    margin: { l: leftMargin, r: 80, t: 20, b: 160 },
    xaxis: {
      tickangle: 45,
      side: 'bottom',
      showgrid: false,
    },
    yaxis: { showgrid: false, domain: [0, heatmapDomainEnd] },
    height: Math.max(300, totalRows * 40 + 200),
    autosize: true,
  };

  if (hasFeatures) {
    layout.yaxis2 = {
      showticklabels: true,
      showgrid: false,
      domain: [featureDomainStart, featureDomainEnd],
      tickfont: { size: 10 },
    };
  }

  if (hasDbHits) {
    layout.yaxis3 = {
      showticklabels: true,
      showgrid: false,
      domain: [dbHitDomainStart, dbHitDomainEnd],
      tickfont: { size: 10 },
    };
  }

  if (hasConsequences) {
    layout.yaxis4 = {
      showticklabels: true,
      showgrid: false,
      domain: [consequenceDomainStart, consequenceDomainEnd],
      tickfont: { size: 10 },
    };
  }

  Plotly.react(container, traces, layout, {
    responsive: true,
    displayModeBar: true,
    modeBarButtonsToAdd: ['toImage'],
    toImageButtonOptions: {
      format: 'svg',
      filename: 'comparison-heatmap',
    },
  });
}

/** Predefined consequence-to-color mapping. */
const CONSEQUENCE_COLORS = {
  missense: '#f39c12',
  synonymous: '#27ae60',
  stop_gained: '#1d1e1f',
  stop_loss: '#3e0c8d',
  start_loss: '#9e821d',
  frameshift: '#e74c3c',
  insertion: '#16a085',
  deletion: '#2980b9',
  splice_region: '#95a5a6',
  unknown: '#bdc3c7',
};

function _consequenceColor(ctype) {
  return CONSEQUENCE_COLORS[ctype] || '#cccccc';
}

function _uniqueConsequenceTypes(consequences) {
  const seen = new Set();
  const result = [];
  for (const c of consequences) {
    if (!seen.has(c)) {
      seen.add(c);
      result.push(c);
    }
  }
  // Sort for deterministic ordering
  result.sort();
  return result;
}

function _featureColorPalette(count) {
  // Generate a distinct categorical color palette for features
  const baseColors = [
    '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
    '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac',
    '#86bcb6', '#8cd17d', '#b6992d', '#499894', '#eabe7a',
    '#d37295', '#a0cbe8', '#f1ce63', '#d4a6c8', '#9d7660',
  ];
  if (count <= baseColors.length) {
    return baseColors.slice(0, count);
  }
  // For more features than base colors, cycle through them
  const result = [];
  for (let i = 0; i < count; i++) {
    result.push(baseColors[i % baseColors.length]);
  }
  return result;
}
