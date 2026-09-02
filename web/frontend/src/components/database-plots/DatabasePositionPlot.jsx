import Plotly from 'plotly.js-dist-min';
import { useRef, useEffect, useState } from 'react';

import { CLASSIFICATION_COLORS, CLASSIFICATION_LABELS } from './shared';

export function DatabasePositionPlot({ plot }) {
  const containerRef = useRef(null);
  const [plotError, setPlotError] = useState('');

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    setPlotError('');

    try {
      const xLabels = plot.positions.map((p) => p.label);

      const traces = plot.tones.map((tone) => ({
        type: 'bar',
        x: xLabels,
        y: plot.positions.map((p) => p[tone] || 0),
        name: CLASSIFICATION_LABELS[tone] || 'Rules',
        marker: {
          color: CLASSIFICATION_COLORS[tone] || CLASSIFICATION_COLORS.count,
        },
        customdata: plot.positions.map((p) => [p.rangeStart, p.rangeEnd]),
        hovertemplate: 'Range: %{customdata[0]}-%{customdata[1]}<br>' + (CLASSIFICATION_LABELS[tone] || 'Rules') + ': %{y}<extra></extra>',
      }));

      const positionsLength = plot.positions.length;
      const xaxisConfig = positionsLength > 24
        ? { tickmode: 'auto', nticks: 25 }
        : { tickmode: 'auto', dtick: 1 };

      const layout = {
        barmode: 'stack',
        bargap: 0.15,
        xaxis: {
          title: { text: plot.xAxisLabel || 'Amino-acid position', font: { size: 12, color: '#4c6072' } },
          tickangle: -45,
          showgrid: false,
          ...xaxisConfig,
        },
        yaxis: {
          title: { text: 'Mutation count', font: { size: 12, color: '#4c6072' } },
          showgrid: true,
          gridcolor: '#dbe6ee',
          gridwidth: 1,
          griddash: 'dot',
          rangemode: 'tozero',
        },
        margin: { l: 50, r: 12, t: 8, b: 58 },
        height: 250,
        showlegend: false,
        dragmode: false,
      };

      const config = { responsive: true, displayModeBar: false, scrollZoom: false };

      Plotly.react(containerRef.current, traces, layout, config);
    } catch (err) {
      setPlotError(String(err.message || err));
    }

    return () => {
      if (containerRef.current) {
        Plotly.purge(containerRef.current);
      }
    };
  }, [plot]);

  // Re-render on window resize (debounced)
  useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    let timeoutId = null;
    const handleResize = () => {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        if (containerRef.current) {
          try {
            Plotly.react(containerRef.current, containerRef.current.data, containerRef.current.layout, containerRef.current._config);
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
  }, [plot]);

  return (
    <section className="database-plot-card">
      <div className="database-plot-header">
        <h3>{plot.title}</h3>
        <p>{plot.subtitle}</p>
      </div>
      <div className="database-chart-scroll">
        <div className="database-plot-canvas">
          <div ref={containerRef} />
        </div>
      </div>
      <p className="database-plot-footer">{plot.footer}</p>
      <div className="database-plot-legend">
        {plot.tones.map((tone) => (
          <span key={tone} className="database-legend-item">
            <span className="database-legend-dot" style={{ backgroundColor: CLASSIFICATION_COLORS[tone] }} aria-hidden="true" />
            {CLASSIFICATION_LABELS[tone]}
          </span>
        ))}
      </div>
      {plotError && (
        <p className="status" style={{ color: '#c2410c' }}>Chart error: {plotError}</p>
      )}
    </section>
  );
}