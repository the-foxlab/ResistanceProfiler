import Plotly from 'plotly.js-dist-min';
import { useRef, useEffect, useState } from 'react';
import { Spinner } from './Spinner';
import {
  prepareHeatmapData,
  consequenceColor,
  featureColorPalette,
  uniqueConsequenceTypes,
} from './comparison-heatmap-data';

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
    const featureColors = featureColorPalette(Math.max(data.features.length, 1));
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
  const prepared = prepareHeatmapData(data);
  if (!prepared) {
    Plotly.purge(container);
    return;
  }

  const traces = [];

  // 1. Coverage gap overlay trace (behind main trace)
  const gapTrace = {
    z: prepared.gapZ,
    x: prepared.xLabels,
    y: prepared.samples,
    type: 'heatmap',
    colorscale: [[0, '#e0e0e0'], [1, '#b0b0b0']],
    showscale: false,
    zmin: 0,
    zmax: 1,
    xgap: 1,
    ygap: 1,
    xaxis: 'x',
    yaxis: 'y',
    customdata: prepared.gapCustomdata,
    hovertemplate: 'Sample: %{y}<br>Mutation: %{customdata[0]}<br>Feature: %{customdata[1]}<br>Coverage gap<extra></extra>',
    line: { color: '#333', width: 1 },
  };
  traces.push(gapTrace);

  // 2. Main allele frequency trace
  const mainTrace = {
    z: prepared.mainZ,
    x: prepared.xLabels,
    y: prepared.samples,
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
    customdata: prepared.mainCustomdata,
    hovertemplate: 'Sample: %{y}<br>Mutation: %{customdata[0]}<br>Feature: %{customdata[1]}<br>Allele freq: %{z}<extra></extra>',
    line: { color: '#333', width: 1 },
  };
  traces.push(mainTrace);

  // 3. Feature annotation row
  if (prepared.hasFeatures) {
    const featureTrace = {
      z: prepared.featureZValues,
      x: prepared.xLabels,
      y: ['Feature'],
      type: 'heatmap',
      colorscale: prepared.featureColorscale,
      zmin: 0,
      zmax: Math.max(data.features.length - 1, 1),
      showscale: false,
      xgap: 1,
      ygap: 1,
      xaxis: 'x',
      yaxis: 'y2',
      customdata: prepared.featureCustomdata,
      hovertemplate: 'Feature: %{customdata[0]}<br>Mutation: %{customdata[1]}<extra></extra>',
      line: { color: '#333', width: 1 },
    };
    traces.push(featureTrace);
  }

  // 4. DB hit annotation bar
  if (prepared.hasDbHits) {
    const dbHitTrace = {
      z: prepared.dbHitZ,
      x: prepared.xLabels,
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
      customdata: prepared.dbHitCustomdata,
      hovertemplate: '%{customdata[0]}<extra></extra>',
      line: { color: '#333', width: 1 },
    };
    traces.push(dbHitTrace);
  }

  // 5. Consequence annotation row
  if (prepared.hasConsequences) {
    const consequenceTrace = {
      z: prepared.consequenceZValues,
      x: prepared.xLabels,
      y: ['Consequence'],
      type: 'heatmap',
      colorscale: prepared.consequenceColorscale,
      zmin: 0,
      zmax: Math.max(prepared.consequenceTypes.length - 1, 1),
      showscale: false,
      xgap: 1,
      ygap: 1,
      xaxis: 'x',
      yaxis: 'y4',
      customdata: prepared.consequenceCustomdata,
      hovertemplate: 'Consequence: %{customdata[0]}<extra></extra>',
      line: { color: '#333', width: 1 },
    };
    traces.push(consequenceTrace);
  }

  // Layout with domains for all axes
  const layout = {
    margin: { l: prepared.leftMargin, r: 80, t: 20, b: 60 },
    xaxis: {
      tickangle: 45,
      side: 'bottom',
      showgrid: false,
    },
    yaxis: { showgrid: false, domain: [0, prepared.heatmapDomainEnd] },
    height: prepared.height,
    autosize: true,
  };

  if (prepared.hasFeatures) {
    layout.yaxis2 = {
      showticklabels: true,
      showgrid: false,
      domain: [prepared.featureDomainStart, prepared.featureDomainEnd],
      tickfont: { size: 10 },
    };
  }

  if (prepared.hasDbHits) {
    layout.yaxis3 = {
      showticklabels: true,
      showgrid: false,
      domain: [prepared.dbHitDomainStart, prepared.dbHitDomainEnd],
      tickfont: { size: 10 },
    };
  }

  if (prepared.hasConsequences) {
    layout.yaxis4 = {
      showticklabels: true,
      showgrid: false,
      domain: [prepared.consequenceDomainStart, prepared.consequenceDomainEnd],
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


