import Plotly from 'plotly.js-dist-min';

import { baseLayout, axisTitleFont } from './shared';
import { STATIC_PLOT_CONFIG, usePlotlyChart } from './usePlotlyChart';

function formatIc50Tick(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return String(value);
  }
  const ic50Value = 10 ** numeric;
  if (ic50Value >= 10000) {
    return ic50Value.toExponential(0).replace('e+', 'e');
  }
  if (ic50Value >= 100) {
    return String(Math.round(ic50Value));
  }
  if (ic50Value >= 10) {
    return String(Math.round(ic50Value * 10) / 10);
  }
  return String(Math.round(ic50Value * 100) / 100);
}

function renderScoreCountPlot(container, plot) {
  const base = baseLayout();
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
    ...base,
    bargap: 0.2,
    xaxis: {
      ...base.xaxis,
      showgrid: false,
      title: { text: plot.xAxisLabel || 'Score', font: axisTitleFont() },
      tickangle: 0,
    },
    yaxis: {
      ...base.yaxis,
      title: { text: plot.yAxisLabel || 'Rule count', font: axisTitleFont() },
      showgrid: true,
      rangemode: 'tozero',
    },
    barmode: 'group',
    autosize: true,
    margin: { l: 50, r: 16, t: 10, b: 40 },
    height: 320,
    showlegend: false,
    dragmode: false,
  };

  Plotly.react(container, [trace], layout, STATIC_PLOT_CONFIG);
}

function renderIc50DistributionPlot(container, plot) {
  const base = baseLayout();
  const majorTickSet = new Set(
    (plot.majorTicks || []).map((tick) => Math.round(tick * 1e6) / 1e6),
  );

  // Only label major (power-of-ten) tick positions along the log axis.
  const ticktext = (plot.xTicks || []).map((tickVal) => {
    const rounded = Math.round(Number(tickVal) * 1e6) / 1e6;
    return majorTickSet.has(rounded) ? formatIc50Tick(tickVal) : '';
  });

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
    customdata: plot.points.map((p) => [
      p.drug,
      p.value,
      String(p.metricLabel || 'IC50').replace('IC50', 'IC₅₀'),
    ]),
    hovertemplate: '<b>%{customdata[0]}</b><br>%{customdata[2]}: %{customdata[1]:.2f}<extra></extra>',
  };

  const layout = {
    ...base,
    xaxis: {
      ...base.xaxis,
      type: 'linear',
      title: { text: plot.xAxisLabel || 'IC₅₀ (log scale)', font: axisTitleFont(), standoff: 18 },
      tickvals: plot.xTicks,
      ticktext,
      range: plot.xDomain,
      autorange: false,
      automargin: true,
      showgrid: true,
      zeroline: false,
    },
    yaxis: {
      ...base.yaxis,
      tickvals: plot.yTicks,
      ticktext: yTickLabels,
      showgrid: false,
      range: plot.yDomain,
      autorange: false,
      automargin: true,
    },
    autosize: true,
    margin: { l: 55, r: 16, t: 10, b: 60 },
    height: 320,
    showlegend: false,
    dragmode: false,
  };

  Plotly.react(container, [trace], layout, STATIC_PLOT_CONFIG);
}

export function DatabaseDrugDistributionPlot({ plot }) {
  const { containerRef, plotError } = usePlotlyChart({
    deps: [plot],
    render: (el) => {
      if (plot.kind === 'score-counts') {
        renderScoreCountPlot(el, plot);
      } else {
        renderIc50DistributionPlot(el, plot);
      }
    },
  });

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
