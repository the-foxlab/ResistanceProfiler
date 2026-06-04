import Plotly from 'plotly.js-dist-min';
import { useRef, useEffect, useState } from 'react';

function _formatIc50Tick(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return String(value);
  }
  const ic50Value = 10 ** numeric;
  if (ic50Value >= 100) {
    return String(Math.round(ic50Value));
  }
  if (ic50Value >= 10) {
    return String(Math.round(ic50Value * 10) / 10);
  }
  return String(Math.round(ic50Value * 100) / 100);
}

function _formatRawMeasurement(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return String(value);
  }
  if (numeric >= 100) {
    return String(Math.round(numeric));
  }
  if (numeric >= 10) {
    return String(Math.round(numeric * 10) / 10);
  }
  return String(Math.round(numeric * 100) / 100);
}

function _renderScoreCountPlot(container, plot) {
  const trace = {
    type: 'bar',
    x: plot.bars.map((b) => b.scoreLabel),
    y: plot.bars.map((b) => b.count),
    marker: {
      color: plot.bars.map((b) => b.color),
      opacity: 0.82,
    },
    hovertemplate: 'Score: %{x}<br>Count: %{y}<extra></extra>',
  };

  const layout = {
    bargap: 0.2,
    xaxis: {
      title: { text: plot.xAxisLabel || 'Score', font: { size: 12, color: '#4c6072' } },
      tickangle: 0,
      showgrid: false,
    },
    yaxis: {
      title: { text: plot.yAxisLabel || 'Rule count', font: { size: 12, color: '#4c6072' } },
      showgrid: true,
      gridcolor: '#dbe6ee',
      gridwidth: 1,
      griddash: 'dot',
      rangemode: 'tozero',
    },
    barmode: 'group',
    autosize: true,
    margin: { l: 50, r: 16, t: 10, b: 40 },
    height: 320,
    showlegend: false,
    dragmode: false,
  };

  const config = { responsive: true, displayModeBar: false, scrollZoom: false };
  Plotly.react(container, [trace], layout, config);
}

function _renderIc50DistributionPlot(container, plot) {
  const majorTickSet = new Set(
    (plot.majorTicks || []).map((tick) => Math.round(tick * 1000000) / 1000000)
  );

  // Build x-axis tick text: only show labels at major tick positions
  const ticktext = (plot.xTicks || []).map((tickVal) => {
    const rounded = Math.round(Number(tickVal) * 1000000) / 1000000;
    if (!majorTickSet.has(rounded)) {
      return '';
    }
    return _formatIc50Tick(tickVal);
  });

  // Build y-axis tick labels from lane labels
  const yTickLabels = (plot.yTicks || []).map((t) => plot.laneLabels[String(Math.round(t))] || '');

  const trace = {
    type: 'scatter',
    mode: 'markers',
    x: plot.points.map((p) => p.x),
    y: plot.points.map((p) => p.y),
    marker: {
      color: plot.points.map((p) => p.color),
      opacity: 0.78,
      size: 8,
    },
    customdata: plot.points.map((p) => {
      const label = String(p.metricLabel || 'IC50').replace('IC50', 'IC₅₀');
      return [p.drug, p.value, label];
    }),
    hovertemplate: '<b>%{customdata[0]}</b><br>%{customdata[2]}: %{customdata[1]:.2f}<extra></extra>',
  };

  const xlabel = plot.xAxisLabel || 'IC₅₀ (log scale)';

  const layout = {
    xaxis: {
      type: 'linear',
      title: { text: xlabel, font: { size: 12, color: '#4c6072' } },
      tickvals: plot.xTicks,
      ticktext,
      range: plot.xDomain,
      autorange: false,
      showgrid: true,
      gridcolor: '#dbe6ee',
      gridwidth: 1,
      griddash: 'dot',
      zeroline: false,
    },
    yaxis: {
      tickvals: plot.yTicks,
      ticktext: yTickLabels,
      showgrid: false,
      range: plot.yDomain,
      autorange: false,
    },
    autosize: true,
    margin: { l: 110, r: 16, t: 10, b: 40 },
    height: 320,
    showlegend: false,
    dragmode: false,
  };

  const config = { responsive: true, displayModeBar: false, scrollZoom: false };
  Plotly.react(container, [trace], layout, config);
}

export function DatabaseDrugDistributionPlot({ plot }) {
  const containerRef = useRef(null);
  const [plotError, setPlotError] = useState('');

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    setPlotError('');

    try {
      if (plot.kind === 'score-counts') {
        _renderScoreCountPlot(containerRef.current, plot);
      } else {
        _renderIc50DistributionPlot(containerRef.current, plot);
      }
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
      {plotError && (
        <p className="status" style={{ color: '#c2410c' }}>Chart error: {plotError}</p>
      )}
    </section>
  );
}
