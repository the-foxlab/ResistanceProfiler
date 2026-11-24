import Plotly from 'plotly.js-dist-min';

import { baseLayout } from './database-plots/shared';
import { usePlotlyChart } from './database-plots/usePlotlyChart';
import { Spinner } from './Spinner';
import {
  prepareHeatmapData,
  consequenceColor,
  featureColorPalette,
  uniqueConsequenceTypes,
} from './comparison-heatmap-data';

const ALLELE_FREQ_COLORSCALE = [
  [0, '#f0fdfa'],
  [0.25, '#99f6e4'],
  [0.5, '#2dd4bf'],
  [0.75, '#0f766e'],
  [1.0, '#7f1d1d'],
];

const HEATMAP_PLOT_CONFIG = {
  responsive: true,
  displayModeBar: true,
  modeBarButtonsToAdd: ['toImage'],
  toImageButtonOptions: {
    format: 'svg',
    filename: 'comparison-heatmap',
  },
};

function heatmapTrace(xLabels, lineColor, overrides) {
  return {
    x: xLabels,
    type: 'heatmap',
    zmin: 0,
    zmax: 1,
    showscale: false,
    xgap: 1,
    ygap: 1,
    xaxis: 'x',
    line: { color: lineColor, width: 1 },
    ...overrides,
  };
}

function annotationAxis(start, end, color) {
  return {
    showticklabels: true,
    showgrid: false,
    domain: [start, end],
    tickfont: { size: 10, color },
  };
}

function renderHeatmap(container, data, theme) {
  const prepared = prepareHeatmapData(data);
  if (!prepared) {
    Plotly.purge(container);
    return;
  }

  const isDark = theme === 'dark';
  const gridLineColor = isDark ? '#09090b' : '#ffffff';
  const traces = [
    // Coverage gap overlay behind main allele-frequency trace.
    heatmapTrace(prepared.xLabels, gridLineColor, {
      z: prepared.gapZ,
      y: prepared.samples,
      yaxis: 'y',
      colorscale: isDark ? [[0, '#27272a'], [1, '#71717a']] : [[0, '#e4e4e7'], [1, '#a1a1aa']],
      customdata: prepared.gapCustomdata,
      hovertemplate: 'Sample: %{y}<br>Mutation: %{customdata[0]}<br>Feature: %{customdata[1]}<br>Coverage gap<extra></extra>',
    }),
    heatmapTrace(prepared.xLabels, isDark ? '#09090b' : '#333', {
      z: prepared.mainZ,
      y: prepared.samples,
      yaxis: 'y',
      colorscale: ALLELE_FREQ_COLORSCALE,
      showscale: true,
      colorbar: {
        title: { text: 'Allele freq', side: 'right' },
        tickvals: [0, 0.25, 0.5, 0.75, 1.0],
        ticktext: ['0', '0.25', '0.5', '0.75', '1.0'],
        thickness: 15,
        tickfont: { size: 10, color: isDark ? '#a1a1aa' : '#71717a' },
        outlinewidth: 0,
      },
      customdata: prepared.mainCustomdata,
      hovertemplate: 'Sample: %{y}<br>Mutation: %{customdata[0]}<br>Feature: %{customdata[1]}<br>Allele freq: %{z}<extra></extra>',
    }),
  ];

  if (prepared.hasFeatures) {
    traces.push(heatmapTrace(prepared.xLabels, gridLineColor, {
      z: prepared.featureZValues,
      y: ['Feature'],
      yaxis: 'y2',
      colorscale: prepared.featureColorscale,
      zmax: Math.max(data.features.length - 1, 1),
      customdata: prepared.featureCustomdata,
      hovertemplate: 'Feature: %{customdata[0]}<br>Mutation: %{customdata[1]}<extra></extra>',
    }));
  }

  if (prepared.hasDbHits) {
    traces.push(heatmapTrace(prepared.xLabels, gridLineColor, {
      z: prepared.dbHitZ,
      y: ['DB hit'],
      yaxis: 'y3',
      colorscale: [[0, '#f4f4f5'], [1, '#dc2626']],
      customdata: prepared.dbHitCustomdata,
      hovertemplate: '%{customdata[0]}<extra></extra>',
    }));
  }

  if (prepared.hasConsequences) {
    traces.push(heatmapTrace(prepared.xLabels, gridLineColor, {
      z: prepared.consequenceZValues,
      y: ['Consequence'],
      yaxis: 'y4',
      colorscale: prepared.consequenceColorscale,
      zmax: Math.max(prepared.consequenceTypes.length - 1, 1),
      customdata: prepared.consequenceCustomdata,
      hovertemplate: 'Consequence: %{customdata[0]}<extra></extra>',
    }));
  }

  const base = baseLayout();
  const axisLabelColor = isDark ? '#f4f4f5' : '#18181b';
  const layout = {
    ...base,
    margin: { l: prepared.leftMargin, r: 80, t: 20, b: 60 },
    xaxis: {
      ...base.xaxis,
      showgrid: false,
      tickangle: 45,
      side: 'bottom',
      tickfont: { ...base.xaxis.tickfont, color: axisLabelColor },
    },
    yaxis: {
      ...base.yaxis,
      showgrid: false,
      domain: [0, prepared.heatmapDomainEnd],
      tickfont: { ...base.yaxis.tickfont, color: axisLabelColor },
    },
    height: prepared.height,
    autosize: true,
  };

  if (prepared.hasFeatures) {
    layout.yaxis2 = annotationAxis(prepared.featureDomainStart, prepared.featureDomainEnd, axisLabelColor);
  }
  if (prepared.hasDbHits) {
    layout.yaxis3 = annotationAxis(prepared.dbHitDomainStart, prepared.dbHitDomainEnd, axisLabelColor);
  }
  if (prepared.hasConsequences) {
    layout.yaxis4 = annotationAxis(prepared.consequenceDomainStart, prepared.consequenceDomainEnd, axisLabelColor);
  }

  Plotly.react(container, traces, layout, HEATMAP_PLOT_CONFIG);
}

/**
 * Interactive comparison heatmap rendered with Plotly.js.
 */
export function ComparisonHeatmap({ data, isBusy }) {
  // Full re-render on resize preserves multi-yaxis domain alignment.
  const { containerRef, plotError } = usePlotlyChart({
    enabled: Boolean(data),
    deps: [data],
    resizeMode: 'rerender',
    render: (el, activeTheme) => renderHeatmap(el, data, activeTheme),
  });

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

  const featureColors = data.features?.length ? featureColorPalette(data.features.length) : [];
  const displayNames = data.feature_display_names || {};

  return (
    <>
      <div className="comparison-heatmap-container" ref={containerRef} />
      <div className="comparison-legend-row">
        {data.features && data.features.length > 0 && (
          <>
            <span className="comparison-feature-legend-title">Features:</span>
            {data.features.map((feature, idx) => (
              <span key={feature} className="comparison-feature-legend-item">
                <span className="comparison-feature-swatch" style={{ backgroundColor: featureColors[idx] }} />
                {displayNames[feature] || feature}
              </span>
            ))}
          </>
        )}
        <span className="comparison-legend-separator" />
        <span className="comparison-cell-legend-item">
          <span className="comparison-cell-swatch" style={{ backgroundColor: '#a1a1aa' }} /> Coverage gap
        </span>
        <span className="comparison-cell-legend-item">
          <span className="comparison-cell-swatch" style={{ backgroundColor: '#dc2626' }} /> Database hit
        </span>
        {data.consequences && data.consequences.length > 0 && (
          <>
            <span className="comparison-legend-separator" />
            <span className="comparison-consequence-legend-title">Consequences:</span>
            {uniqueConsequenceTypes(data.consequences).map((ctype) => (
              <span key={ctype} className="comparison-consequence-legend-item">
                <span
                  className="comparison-consequence-swatch"
                  style={{ backgroundColor: consequenceColor(ctype) }}
                />
                {ctype}
              </span>
            ))}
          </>
        )}
      </div>
    </>
  );
}
